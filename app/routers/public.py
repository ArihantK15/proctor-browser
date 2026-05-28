from ..log_safe import safe
from pathlib import Path
import json
import logging
_pub_log = logging.getLogger("public")
import os
import time
import asyncio
import httpx
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse, FileResponse, HTMLResponse, Response
from pydantic import BaseModel, ConfigDict

from ..auth import (
    require_admin, require_student_account, verify_student_auth_token, _get_teacher_by_id,
)
from ..database import supabase, async_table as _atable
from ..limiter import limiter
from .. import cache as _cache
from ..models import RegisterIn, SessionStatus, InviteStatus, VerificationStatus
from ..utils import fmt_ist, now_ist
from ..constants import DOWNLOAD_MAC_ARM, DOWNLOAD_MAC_X64, DOWNLOAD_WIN, DOWNLOAD_LINUX
from ..repositories.questions import load_exam_config as _load_exam_config
from ..invites import _get_invite_base_url
from ..services.invite_landing import _render_invite_error, _render_invite_landing
from ..services.release import (
    _RELEASE_CACHE, _RELEASE_CACHE_EXPIRES, _refresh_release_cache, _resolve_release_asset, _download_redirect,
)
from ..jobs import enqueue_job, send_demo_request_notification_job
from ..services.turnstile import verify_or_403


class DemoRequest(BaseModel):
    model_config = ConfigDict(strict=True)
    name: str
    email: str
    institution: str
    role: str
    message: str = ""
    captcha_token: str = ""


class ResolveAccessCodeIn(BaseModel):
    model_config = ConfigDict(strict=True)
    access_code: str


logger = logging.getLogger(__name__)

router = APIRouter(prefix="")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# Cache-bust version stamp for static asset URLs.
# Computed once at module import — every API restart (CI deploy) gets
# a new value, forcing browsers + Cloudflare to fetch fresh CSS/JS
# instead of serving stale cached copies. Cheap and self-maintaining.
import time as _time
import re as _re
_ASSET_VERSION = str(int(_time.time()))
_ASSET_VERSION_RE = _re.compile(
    r'(<(?:link|script)\b[^>]*\b(?:href|src)\s*=\s*["\'])(/static/[^"\']+\.(?:css|js))(["\'])'
)


def _stamp_static_urls(html: str) -> str:
    """Inject ?v=<build> into /static/*.css and /static/*.js URLs.
    Skips URLs that already carry a query string so we don't double-stamp."""
    def _sub(m):
        url = m.group(2)
        if "?" in url:
            return m.group(0)
        return f"{m.group(1)}{url}?v={_ASSET_VERSION}{m.group(3)}"
    return _ASSET_VERSION_RE.sub(_sub, html)


def _static_html_response(filename: str, missing_detail: str) -> HTMLResponse:
    html_path = STATIC_DIR / filename
    if not html_path.exists():
        raise HTTPException(status_code=404, detail=missing_detail)
    return HTMLResponse(
        _stamp_static_urls(html_path.read_text()),
        # Auth-gated pages: always re-fetch so post-login redirects + new
        # deploys take effect immediately. Mirrors the Cache-Control that
        # Caddy previously set when it short-circuited these routes
        # (removed from Caddyfile 2026-05-24 to restore CSP + cache-bust).
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@router.get("/")
def root():
    """app.procta.net is the application host (dashboard + APIs).
    Marketing lives at procta.net (separate Vite React site in
    website/, hosted via Cloudflare + Vercel). Anyone landing on
    app.procta.net's bare root probably wanted the marketing page
    so we redirect there.

    Returns a 302 (not 301) so we keep flexibility — if app.procta.net
    ever gets its own dashboard splash, we can switch this without
    fighting browser redirect caches.
    """
    return RedirectResponse(url="https://procta.net/", status_code=302)


@router.get("/sitemap.xml")
def sitemap():
    fpath = os.path.join(os.path.dirname(__file__), "..", "static", "sitemap.xml")
    fpath = os.path.abspath(fpath)
    if not os.path.exists(fpath):
        raise HTTPException(status_code=404, detail="sitemap.xml not found")
    with open(fpath) as f:
        content = f.read()
    from starlette.responses import Response
    return Response(content=content, media_type="application/xml")


@router.get("/robots.txt")
def robots_txt():
    content = (
        "User-agent: *\n"
        "Allow: /download\n"
        "Allow: /dashboard\n"
        "Allow: /trust-center\n"
        "Allow: /proof-assets\n"
        "Allow: /sample-scorecard\n"
        "Disallow: /api/v1/\n"
        "Disallow: /register\n"
        "Disallow: /student\n"
        "Disallow: /static/\n"
        "\n"
        "Sitemap: https://app.procta.net/sitemap.xml\n"
    )
    from starlette.responses import Response
    return Response(content=content, media_type="text/plain")


_health_start = time.time()
_req_total = 0
_req_errors = 0

@router.get("/health")
async def health():
    """Lightweight health probe for uptime monitors and load balancers.

    Returns 200 only when Supabase is reachable and disk has space.
    Redis is optional (the API works without it — SSE just won't broadcast).
    """
    global _req_total, _req_errors
    _req_total += 1
    checks = {}
    ok = True

    # Database — required (skipped when SUPABASE_SKIP_STARTUP_CHECK=1, e.g. CI smoke tests)
    from ..database import database_backend
    db_backend = database_backend()
    _skip_db = os.environ.get("SUPABASE_SKIP_STARTUP_CHECK", "") == "1"
    try:
        await _atable("exam_config").select("id").limit(1).execute()
        checks["database"] = "ok"
        checks["database_backend"] = db_backend
    except Exception as e:
        _pub_log.warning("[health] database check failed: %s", e)
        checks["database"] = "stub" if _skip_db else "error: suppressed"
        checks["database_backend"] = db_backend
        if not _skip_db:
            ok = False

    # Redis — optional (reuse module-level client)
    try:
        if not hasattr(health, "_redis_client"):
            import redis as _redis
            health._redis_client = _redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379"))
        health._redis_client.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "unavailable"  # non-fatal — health check still passes

    # Memory
    try:
        import psutil
        mem = psutil.virtual_memory()
        checks["memory_pct"] = mem.percent
    except ImportError:
        pass

    # Disk space — fail if screenshots dir has < 500 MB free
    try:
        import shutil
        target = os.getenv("SCREENSHOTS_DIR", "/var/lib/proctor/screenshots")
        # makedirs must come before disk_usage — disk_usage raises FileNotFoundError
        # on a path that doesn't exist yet (e.g. /tmp/proctor-screenshots in CI).
        os.makedirs(target, exist_ok=True)
        total, used, free = shutil.disk_usage(target)
        free_mb = free // (1024 * 1024)
        checks["disk_free_mb"] = free_mb
        if free_mb < 500:
            checks["disk"] = "critical"
            ok = False
        elif free_mb < 2000:
            checks["disk"] = "warning"
        else:
            checks["disk"] = "ok"

        # Storage write test — create + delete a temp file
        test_path = os.path.join(target, ".health_write_test")
        with open(test_path, "wb") as f:
            f.write(b"ok")
        os.remove(test_path)
        checks["storage_write"] = "ok"
    except Exception as e:
        checks["disk"] = "error: suppressed"
        checks["storage_write"] = "error"
        ok = False

    # Worker — check last heartbeat via Redis (reuse module-level client)
    try:
        if not hasattr(health, "_redis_client"):
            import redis as _redis
            health._redis_client = _redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379"))
        hb = health._redis_client.get("worker:last_heartbeat")
        if hb:
            age = time.time() - float(hb)
            checks["worker"] = "ok" if age < 60 else "stale"
            if age >= 60:
                ok = False
        else:
            checks["worker"] = "no_heartbeat"
    except Exception:
        checks["worker"] = "unavailable"

    # Email — check provider is configured
    try:
        provider = os.environ.get("EMAIL_PROVIDER", "resend")
        email_from = os.environ.get("EMAIL_FROM", "")
        if provider != "noop" and email_from:
            checks["email"] = "ok"
        elif provider == "noop":
            checks["email"] = "noop"
        else:
            checks["email"] = "misconfigured"
            ok = False
    except Exception:
        checks["email"] = "error"

    uptime_sec = time.time() - _health_start
    status = 200 if ok else 503
    if not ok:
        _req_errors += 1
    return Response(
        content=json.dumps({
            "status": "ok" if ok else "degraded",
            "uptime_sec": round(uptime_sec, 1),
            "health_checks": _req_total,
            "health_errors": _req_errors,
            "checks": checks,
        }),
        media_type="application/json",
        status_code=status,
    )


@router.get("/api/v1/public-config")
async def public_config():
    """Return public (non-secret) frontend configuration.

    Safe to expose to unauthenticated users — only public keys here.
    Dashboard uses this to obtain the Turnstile site key without
    server-side templating.
    """
    return {
        "turnstile_site_key": os.environ.get("TURNSTILE_SITE_KEY", ""),
    }


@router.post("/api/v1/register-student")
@limiter.limit("120/minute")
async def register_student(request: Request, body: RegisterIn):
    """Public self-registration for students before exam day."""
    roll = body.roll_number.strip().upper()
    name = body.full_name.strip()
    email = body.email.strip().lower()
    phone = (body.phone or "").strip() or None

    if not roll:
        raise HTTPException(status_code=400, detail="Roll number is required")
    if not name:
        raise HTTPException(status_code=400, detail="Full name is required")
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")
    if not body.teacher_id:
        raise HTTPException(
            status_code=400,
            detail="This registration link is missing the teacher identifier. Ask your examiner for the correct link.")

    teacher = await _get_teacher_by_id(body.teacher_id)
    if not teacher:
        raise HTTPException(status_code=404, detail="Unknown teacher")
    teacher_id = str(teacher["id"])

    existing = await _atable("students").select("roll_number").eq("roll_number", roll).eq("teacher_id", teacher_id).execute()
    if existing.data:
        raise HTTPException(
            status_code=409,
            detail="This roll number is already registered. If this is a mistake, contact your examiner.")

    # Enforce org student limit
    org_id = teacher.get("org_id")
    if org_id:
        from ..services.sessions import check_org_limits
        await check_org_limits({"org_id": org_id, "org_role": teacher.get("org_role", "teacher")}, delta=1)

    row = {
        "roll_number": roll,
        "full_name":   name,
        "email":       email,
        "phone":       phone,
        "teacher_id":  teacher_id,
    }
    try:
        await _atable("students").insert(row).execute()
    except httpx.HTTPStatusError as e:
        if "duplicate" in str(e).lower() or "unique" in str(e).lower() or e.response.status_code == 409:
            raise HTTPException(status_code=409, detail="This roll number is already registered.")
        raise HTTPException(status_code=500, detail="Registration failed. Please try again.")
    except Exception as e:
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            raise HTTPException(status_code=409, detail="This roll number is already registered.")
        raise HTTPException(status_code=500, detail="Registration failed. Please try again.")

    return {"status": "registered", "roll_number": roll, "full_name": name}


@router.get("/api/v1/exam-schedule")
async def get_public_schedule(t: str = None):
    """Public endpoint — returns exam title and schedule for download/register pages."""
    config = await _load_exam_config(teacher_id=t)
    return {
        "exam_title":  config.get("exam_title", "Exam"),
        "duration_minutes": config.get("duration_minutes", 60),
        "starts_at":   config.get("starts_at"),
        "ends_at":     config.get("ends_at"),
    }


@router.get("/api/v1/lookup-teacher")
@limiter.limit("5/minute")
async def lookup_teacher(request: Request, email: str = ""):
    """Public endpoint — find a teacher by email for self-registration.

    Returns minimal info (id, full_name) so the student registration
    page can populate the hidden teacher_id field. Does NOT return
    email to avoid harvesting.

    Rate-limited to 5/min to prevent teacher enumeration.
    """
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")
    result = await _atable("teachers").select("id,full_name").eq("email", email).execute()
    # H39: Always return 200 to prevent email enumeration
    if not result.data:
        return {"teacher_id": None, "full_name": None}
    teacher = result.data[0]
    return {
        "teacher_id": teacher["id"],
        "full_name":  teacher.get("full_name", ""),
    }


@router.post("/api/v1/resolve-access-code")
@limiter.limit("30/minute")
async def resolve_access_code(request: Request, body: ResolveAccessCodeIn):
    """Public endpoint — resolve an exam access code to teacher + exam info.

    Students who received an access code from their teacher can use this
    to find the right registration context without needing a direct link.
    """
    code = body.access_code.strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="Access code is required")

    result = await _atable("exam_config").select(
        "teacher_id", "exam_id", "exam_title", "access_code",
        "duration_minutes", "starts_at", "ends_at"
    ).eq("access_code", code).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Invalid access code")

    cfg = result.data[0]
    teacher = await _get_teacher_by_id(cfg.get("teacher_id"))
    return {
        "teacher_id":       cfg.get("teacher_id"),
        "teacher_name":     teacher.get("full_name", "") if teacher else "",
        "exam_id":          cfg.get("exam_id"),
        "exam_title":       cfg.get("exam_title", "Exam"),
        "duration_minutes": cfg.get("duration_minutes"),
        "starts_at":        cfg.get("starts_at"),
        "ends_at":          cfg.get("ends_at"),
    }


@router.get("/download")
def download_page():
    """Auto-detect OS and offer the right installer."""
    return _static_html_response("download.html", "Download page not found")


@router.get("/register")
def register_page():
    """Self-registration page for students before exam day."""
    return _static_html_response("register.html", "Registration page not found")


@router.get("/student")
def student_page():
    """Student-facing dashboard: upcoming exams, practice, profile."""
    return _static_html_response("student.html", "Student dashboard not found")


@router.get("/dashboard")
def admin_dashboard():
    return _static_html_response("dashboard.html", "Dashboard not found")


@router.get("/dashboard-react")
def admin_dashboard_react():
    html_path = STATIC_DIR / "dashboard-react" / "index.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="React dashboard not found")
    return HTMLResponse(html_path.read_text())


@router.get("/trust-center")
def trust_center_page():
    return _static_html_response("trust-center.html", "Trust center not found")


@router.get("/proof-assets")
def proof_assets_page():
    return _static_html_response("proof-assets.html", "Proof assets not found")


@router.get("/sample-scorecard")
def sample_scorecard_page():
    return _static_html_response("sample-scorecard.html", "Sample scorecard not found")


@router.get("/dpa")
def dpa_page():
    return _static_html_response("dpa.html", "DPA not found")


@router.get("/privacy-policy")
def privacy_policy_page():
    return _static_html_response("privacy-policy.html", "Privacy policy not found")


@router.get("/privacy")
def privacy_page():
    return _static_html_response("privacy.html", "Privacy center not found")


@router.get("/security-questionnaire")
def security_questionnaire_page():
    return _static_html_response("security-questionnaire.html", "Security questionnaire not found")


@router.get("/api-docs")
def api_docs_page():
    """API docs static HTML — moved here from a Caddy short-circuit
    so it picks up CSP + cache-bust like the rest of the auth pages."""
    return _static_html_response("api-docs.html", "API docs not found")


@router.get("/student-react")
def student_react_page():
    """React student dashboard entrypoint.

    Caddy serves /student-react/assets/* directly (cached); FastAPI
    serves only the bare /student-react URL so the index.html flows
    through SecurityHeadersMiddleware (CSP, Permissions-Policy) and
    _stamp_static_urls (cache-bust). Without this route the URL hit a
    Caddy file_server block that didn't strip the prefix → 404.
    """
    return _static_html_response(
        "student-react/index.html", "Student dashboard not found",
    )


@router.get("/download/mac")
async def download_mac():
    return await _download_redirect(DOWNLOAD_MAC_ARM, "mac_arm",
        "/app/downloads/ProctorBrowser-arm64.dmg", "ProctorBrowser-arm64.dmg")


@router.get("/download/mac-x64")
async def download_mac_x64():
    return await _download_redirect(DOWNLOAD_MAC_X64, "mac_x64",
        "/app/downloads/ProctorBrowser-x64.dmg", "ProctorBrowser-x64.dmg")


@router.get("/download/win")
async def download_win():
    return await _download_redirect(DOWNLOAD_WIN, "win",
        "/app/downloads/ProctorBrowser-Setup.exe", "ProctorBrowser-Setup.exe")


@router.get("/download/linux")
async def download_linux():
    return await _download_redirect(DOWNLOAD_LINUX, "linux",
        "/app/downloads/Procta-Browser-x86_64.AppImage", "Procta-Browser-x86_64.AppImage")


@router.get("/download/latest-info")
async def download_latest_info():
    """Debug / health endpoint — shows what the server currently resolves
    for each platform and the last seen release tag."""
    await _resolve_release_asset("mac_arm")
    return {
        "tag":       _RELEASE_CACHE.get("tag", ""),
        "mac_arm":   _RELEASE_CACHE.get("mac_arm", ""),
        "mac_x64":   _RELEASE_CACHE.get("mac_x64", ""),
        "win":       _RELEASE_CACHE.get("win", ""),
        "linux":     _RELEASE_CACHE.get("linux", ""),
        "cache_expires_in_sec": max(0, int(_RELEASE_CACHE_EXPIRES - time.time())),
        "env_overrides": {
            "DOWNLOAD_MAC_ARM": bool(DOWNLOAD_MAC_ARM),
            "DOWNLOAD_MAC_X64": bool(DOWNLOAD_MAC_X64),
            "DOWNLOAD_WIN":     bool(DOWNLOAD_WIN),
            "DOWNLOAD_LINUX":   bool(DOWNLOAD_LINUX),
        },
    }


@router.get("/invite/{token}", response_class=HTMLResponse)
async def invite_landing(token: str, request: Request):
    """Public landing page for invite recipients."""
    row = (await _atable("student_invites").select("*")
           .eq("token", token).execute()).data
    if not row:
        return HTMLResponse(
            _render_invite_error("This invite link is invalid or has been revoked."),
            status_code=404,
        )
    inv = row[0]
    status = (inv.get("status") or "").lower()
    if status == InviteStatus.REVOKED:
        return HTMLResponse(
            _render_invite_error("This invite has been revoked by your teacher."),
            status_code=410,
        )

    exp = inv.get("expires_at")
    if exp:
        try:
            dt = datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > dt:
                return HTMLResponse(
                    _render_invite_error("This invite has expired. Contact your teacher for a new one."),
                    status_code=410,
                )
        except Exception:
            return HTMLResponse(
                _render_invite_error("This invite has expired. Contact your teacher for a new one."),
                status_code=410,
            )

    if not inv.get("opened_at"):
        try:
            await _atable("student_invites").update({
                "opened_at": datetime.now(timezone.utc).isoformat(),
                "status": InviteStatus.OPENED if status in (InviteStatus.SENT, "queued") else status,
            }).eq("token", token).execute()
        except Exception:
            logger.debug("public: invite opened_at update failed", exc_info=True)

    exam_cfg = await _load_exam_config(inv.get("teacher_id"), exam_id=inv.get("exam_id")) \
        if inv.get("exam_id") else {}
    exam_title = (exam_cfg.get("exam_title") if isinstance(exam_cfg, dict) else None) or "Your Procta Exam"

    return HTMLResponse(_render_invite_landing(
        token=token,
        full_name=inv["full_name"],
        exam_title=exam_title,
        roll_number=inv["roll_number"],
        access_code=inv.get("access_code") or "",
        starts_at=fmt_ist(exam_cfg.get("starts_at")) if exam_cfg.get("starts_at") else "",
        ends_at=fmt_ist(exam_cfg.get("ends_at")) if exam_cfg.get("ends_at") else "",
    ))


@router.get("/api/v1/invite/{token}/resolve")
async def resolve_invite(token: str):
    """Public JSON lookup for an invite token."""
    row = (await _atable("student_invites").select("*")
           .eq("token", token).execute()).data
    if not row:
        raise HTTPException(status_code=404, detail="Invite not found")
    inv = row[0]
    status = (inv.get("status") or "").lower()
    if status == InviteStatus.REVOKED:
        raise HTTPException(status_code=410, detail="Invite revoked")
    exp = inv.get("expires_at")
    if exp:
        try:
            dt = datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > dt:
                raise HTTPException(status_code=410, detail="Invite expired")
        except HTTPException:
            raise
        except Exception:
            # Malformed expiry timestamp — fail closed (reject)
            raise HTTPException(status_code=410, detail="Invite expired")

    return {
        "ok":          True,
        "status":      inv.get("status"),
        "exam_id":     inv.get("exam_id"),
        "roll_number": inv.get("roll_number"),
        "email":       inv.get("email"),
        "full_name":   inv.get("full_name"),
        "access_code": inv.get("access_code") or "",
    }


@router.post("/api/invite/{token}/accept")
@router.post("/api/v1/invite/{token}/accept")
async def accept_invite(token: str, request: Request):
    """Accept an invite for the authenticated student account."""
    student = await require_student_account(request)
    row = (await _atable("student_invites").select("*")
           .eq("token", token).execute()).data
    if not row:
        raise HTTPException(status_code=404, detail="Invite not found")
    inv = row[0]
    status = (inv.get("status") or "").lower()
    if status == InviteStatus.REVOKED:
        raise HTTPException(status_code=410, detail="Invite revoked")
    exp = inv.get("expires_at")
    if exp:
        try:
            dt = datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > dt:
                raise HTTPException(status_code=410, detail="Invite expired")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=410, detail="Invite expired")

    inv_email = (inv.get("email") or "").strip().lower()
    stu_email = (student.get("email") or "").strip().lower()
    if not inv_email or inv_email != stu_email:
        raise HTTPException(status_code=403, detail="This invite is for a different email address")

    await _atable("student_invites").update({
        "status":      InviteStatus.ACCEPTED,
        "accepted_at": datetime.now(timezone.utc).isoformat(),
        "student_id":  str(student["id"]),
    }).eq("token", token).execute()

    return {
        "ok":          True,
        "exam_id":     inv.get("exam_id"),
        "roll_number": inv.get("roll_number"),
        "access_code": inv.get("access_code") or "",
    }


@router.post("/api/v1/webhooks/email")
async def email_webhook(request: Request):
    """Resend bounce/complaint webhook."""
    from ..emailer import verify_webhook
    raw = await request.body()
    if not verify_webhook(raw, request.headers):
        sid = request.headers.get("svix-id") or "?"
        _pub_log.warning("[webhook] rejected svix-id=%s", sid)
        raise HTTPException(status_code=403, detail="Invalid webhook signature")
    try:
        payload = json.loads(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    evt = (payload.get("type") or "").lower()
    data = payload.get("data") or {}
    msg_id = data.get("email_id") or data.get("id")
    if not msg_id:
        return {"ok": True, "ignored": "no msg id"}

    now_iso = datetime.now(timezone.utc).isoformat()
    _SENT_LIKE = ["queued", InviteStatus.SENT, InviteStatus.OPENED, InviteStatus.CLICKED]

    if evt == "email.bounced":
        await _atable("student_invites").update({
            "status": InviteStatus.BOUNCED,
            "bounced_at": now_iso,
            "bounce_reason": str(data.get("bounce") or data.get("reason") or "bounced")[:500],
        }).eq("provider_msg_id", msg_id).in_("status", _SENT_LIKE).execute()
    elif evt == "email.complained":
        await _atable("student_invites").update({
            "status": InviteStatus.FAILED,
            "bounce_reason": "recipient marked as spam",
        }).eq("provider_msg_id", msg_id).in_("status", _SENT_LIKE).execute()
    elif evt == "email.opened":
        try:
            await _atable("student_invites")\
                .update({"opened_at": now_iso, "status": InviteStatus.OPENED})\
                .eq("provider_msg_id", msg_id).eq("status", InviteStatus.SENT).execute()
            await _atable("student_invites")\
                .update({"opened_at": now_iso})\
                .eq("provider_msg_id", msg_id).is_("opened_at", "null").execute()
        except Exception as e:
            _pub_log.error("[webhook] opened update failed msg_id=%s: %s", safe(msg_id), safe(e))
            raise HTTPException(status_code=500, detail="Webhook processing failed — will retry")
    elif evt == "email.clicked":
        try:
            existing = (await _atable("student_invites")
                        .select("id,status,clicked_at,click_count")
                        .eq("provider_msg_id", msg_id).limit(1).execute()).data or []
            if existing:
                row = existing[0]
                update = {"click_count": int(row.get("click_count") or 0) + 1}
                if not row.get("clicked_at"):
                    update["clicked_at"] = now_iso
                await _atable("student_invites").update(update)\
                    .eq("id", row["id"]).execute()
                await _atable("student_invites").update({"status": InviteStatus.CLICKED})\
                    .eq("id", row["id"]).in_("status", [InviteStatus.SENT, InviteStatus.OPENED]).execute()
        except Exception as e:
            _pub_log.error("[webhook] clicked update failed msg_id=%s: %s", safe(msg_id), safe(e))
            raise HTTPException(status_code=500, detail="Webhook processing failed — will retry")
    elif evt == "email.delivered":
        pass
    _pub_log.info("[webhook] %s msg_id=%s", safe(evt), safe(msg_id))
    return {"ok": True, "event": evt}


@router.post("/api/v1/demo-request")
@limiter.limit("5/hour")
async def submit_demo_request(req: DemoRequest, request: Request):
    """Store a demo request from the marketing site."""
    await verify_or_403(request, req.captcha_token)
    if not req.name.strip() or not req.email.strip() or not req.institution.strip():
        raise HTTPException(status_code=400, detail="Name, email, and institution are required")

    row = {
        "name":        req.name.strip(),
        "email":       req.email.strip().lower(),
        "institution": req.institution.strip(),
        "role":        req.role.strip(),
        "message":     req.message.strip(),
        "created_at":  datetime.now(timezone.utc).isoformat(),
    }
    try:
        await _atable("demo_requests").insert(row).execute()
    except Exception as e:
        _pub_log.error("[DemoRequest] Failed to store: %s", e)
        raise HTTPException(status_code=500, detail="Failed to store request")

    _pub_log.info("[DemoRequest] %s <%s> from %s", safe(req.name), safe(req.email), safe(req.institution))

    # Notify super admin (fire-and-forget — the form response should
    # not depend on the email provider being available).
    enqueue_job(send_demo_request_notification_job,
                name=req.name.strip(),
                email=req.email.strip().lower(),
                institution=req.institution.strip(),
                role=req.role.strip(),
                message=req.message.strip(),
    )

    return {"status": "ok", "message": "Demo request received"}


@router.get("/api/v1/admin/demo-requests")
async def list_demo_requests(request: Request):
    """List all demo requests — restricted to DB-backed super-admins."""
    teacher = await require_admin(request)
    if teacher.get("org_role") != "superadmin":
        raise HTTPException(status_code=403, detail="Forbidden")
    result = await _atable("demo_requests").select("*").order("created_at", desc=True).execute()
    return {"requests": result.data, "count": len(result.data)}


_PHONE_CAM_PATH = Path(__file__).parent.parent.parent / "renderer" / "phone-cam.html"


@router.get("/phone-cam")
async def phone_cam_page(request: Request):
    """Serve the phone camera capture page (room monitoring)."""
    from fastapi.responses import FileResponse
    if not _PHONE_CAM_PATH.exists():
        return HTMLResponse("Phone camera page not found", status_code=404)
    return FileResponse(str(_PHONE_CAM_PATH), media_type="text/html",
                        headers={"Cache-Control": "no-cache"})
