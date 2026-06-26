from ..log_safe import mask_email, safe
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
from pydantic import BaseModel, ConfigDict, Field

from ..auth import (
    require_admin, require_student_account, verify_student_auth_token, _get_teacher_by_id,
)
from ..database import supabase, async_table as _atable
from ..limiter import limiter
from .. import cache as _cache
from ..models import RegisterIn, SessionStatus, InviteStatus, VerificationStatus
from ..utils import fmt_ist, now_ist
from ..constants import DOWNLOAD_MAC_ARM, DOWNLOAD_MAC_X64, DOWNLOAD_WIN
from ..repositories.questions import load_exam_config as _load_exam_config
from ..invites import _get_invite_base_url, _new_invite_token
from ..services.invite_landing import _render_invite_error, _render_invite_landing
from ..services.release import (
    _RELEASE_CACHE, _RELEASE_CACHE_EXPIRES, _refresh_release_cache, _resolve_release_asset, _download_redirect,
    release_cache_snapshot,
)
from ..jobs import enqueue_job, send_demo_request_notification_job
from ..services.turnstile import verify_or_403


class DemoRequest(BaseModel):
    # Unauthenticated endpoint — bound every field (see RegisterIn).
    model_config = ConfigDict(strict=True)
    name: str = Field(max_length=200)
    email: str = Field(max_length=254)
    institution: str = Field(max_length=200)
    role: str = Field(max_length=100)
    message: str = Field(default="", max_length=2000)
    captcha_token: str = Field(default="", max_length=4096)


class ResolveAccessCodeIn(BaseModel):
    model_config = ConfigDict(strict=True)
    access_code: str


logger = logging.getLogger(__name__)

router = APIRouter(prefix="")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# Cache-bust version stamp for static asset URLs.
# Derived from the NEWEST mtime of the static CSS/JS — NOT a per-process
# time.time(). That timestamp differed per worker / per restart, which (a)
# defeated caching (re-downloaded everything on every deploy even if unchanged)
# and (b) let the HTML reference two differently-versioned copies of the same
# script across a restart. The mtime is identical across all workers for a
# given deploy and changes ONLY when an asset actually changes — so caching
# works and there's one canonical ?v= per asset set.
import os as _os
import time as _time
import re as _re

# Permissive-but-real email shape: local@label(.label)+, no spaces, single @,
# at least one dot in the domain. Rejects obvious garbage ("a@", "@b", "ab",
# "a b@c", "a@b") that the old `"@" in email` check let through on this
# unauthenticated endpoint. Not a full RFC validator (real addresses are standard).
#
# ReDoS-safe by construction: domain LABELS exclude '.' ([^@\s.]), so the literal
# '.' separator is unambiguous — no overlapping quantifiers, linear-time match.
# The earlier `...\.[^@\s]+$` put '.' inside the class, making `[^@\s]+\.[^@\s]+`
# ambiguous → polynomial backtracking (CodeQL flagged it high-severity).
_EMAIL_RE = _re.compile(r"^[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+$")


def _looks_like_email(s: str) -> bool:
    return bool(_EMAIL_RE.match((s or "").strip()))


def _compute_asset_version() -> str:
    try:
        static_dir = _os.path.join(
            _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "static")
        latest = 0.0
        for _root, _dirs, _files in _os.walk(static_dir):
            for _fn in _files:
                if _fn.endswith((".js", ".css")):
                    try:
                        latest = max(latest, _os.path.getmtime(_os.path.join(_root, _fn)))
                    except OSError:
                        pass
        return str(int(latest)) if latest else str(int(_time.time()))
    except Exception:
        return str(int(_time.time()))


_ASSET_VERSION = _compute_asset_version()
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
# Per-IP. Lowered from 120 → 60: this unauthenticated endpoint creates roster
# rows (org-seat-capped) and emails attacker-supplied guardian addresses, so a
# leaked ?t=<teacher_id> link is a spam/abuse vector. 60/min still leaves ample
# headroom for a class self-registering from one NAT'd school IP (registration
# is spread out "before exam day", not a 1-minute burst).
@limiter.limit("60/minute")
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
    if not _looks_like_email(email):
        raise HTTPException(status_code=400, detail="A valid email is required")
    if not body.teacher_id:
        raise HTTPException(
            status_code=400,
            detail="This registration link is missing the teacher identifier. Ask your examiner for the correct link.")

    teacher = await _get_teacher_by_id(body.teacher_id)
    if not teacher:
        raise HTTPException(status_code=404, detail="Unknown teacher")
    teacher_id = str(teacher["id"])
    exam_id_from_body = (body.exam_id or "").strip() if body.exam_id else ""
    if exam_id_from_body:
        exam = (await _atable("exam_config")
                .select("exam_id")
                .eq("teacher_id", teacher_id)
                .eq("exam_id", exam_id_from_body)
                .limit(1)
                .execute()).data or []
        if not exam:
            raise HTTPException(
                status_code=404,
                detail="This registration link does not match an exam owned by this teacher.",
            )

    # The `students` row is the TEACHER-WIDE roster entry (one per roll+teacher,
    # NO exam_id). A student takes MANY subjects/exams under the same teacher, so
    # an existing roster entry must NOT block registering for ANOTHER exam — it's
    # the SAME person. Only a DIFFERENT email on the same roll is a real conflict
    # (someone trying to use another student's roll). Per-exam membership is the
    # student_invites row written below.
    existing = (await _atable("students").select("roll_number,email,guardian_consent_requested_at")
                .eq("roll_number", roll).eq("teacher_id", teacher_id)
                .limit(1).execute())
    returning_student = False
    prev_consent_requested_at = None
    if existing.data:
        existing_email = (existing.data[0].get("email") or "").strip().lower()
        if existing_email and existing_email != email:
            raise HTTPException(
                status_code=409,
                detail="This roll number is already registered to a different email. "
                       "If this is a mistake, contact your examiner.")
        returning_student = True   # same student, another exam — allowed
        prev_consent_requested_at = existing.data[0].get("guardian_consent_requested_at")

    # Typo guard (gap raised 2026-06-17): same email already on THIS teacher's
    # roster under a DIFFERENT roll number. Roll is the teacher's per-student
    # key, so a one-digit slip silently creates a duplicate student. We can't
    # hard-block it (siblings share a parent email; a teacher may legitimately
    # re-roll a student), so surface a non-blocking warning the UI can show.
    dup_roll_warning = None
    if email and not returning_student:
        try:
            same_email = (await _atable("students").select("roll_number")
                          .eq("teacher_id", teacher_id)
                          .ilike("email", email)
                          .neq("roll_number", roll)
                          .limit(1).execute()).data
            if same_email:
                dup_roll_warning = (
                    f"This email is already registered with your examiner under roll "
                    f"{same_email[0].get('roll_number')}. If that's you, double-check "
                    f"your roll number — otherwise you'll be enrolled as a separate student.")
        except Exception:
            _pub_log.debug("[register_student] dup-email roll check failed", exc_info=True)

    # Org student limit — only a genuinely NEW roster entry counts (returning
    # students are already on the roster and counted).
    org_id = teacher.get("org_id")
    if org_id and not returning_student:
        from ..services.sessions import check_org_limits
        await check_org_limits({"org_id": org_id, "org_role": teacher.get("org_role", "teacher")}, delta=1)

    # ── Minor consent gate ───────────────────────────────────────
    # Server re-computes age from date_of_birth.
    date_of_birth_str = (body.date_of_birth or "").strip()
    if not date_of_birth_str:
        raise HTTPException(
            status_code=400,
            detail="Date of birth is required. DOB feeds the under-18 guardian-consent flow.",
        )
    dob = None
    is_minor = False
    if date_of_birth_str:
        try:
            dob = datetime.strptime(date_of_birth_str, "%Y-%m-%d").date()
            # Calendar age — NOT days//365, which overestimates (365 vs
            # 365.25) and would classify a 17-year-old near their birthday
            # as an adult, skipping the minor gate.
            _today = datetime.now(timezone.utc).date()
            age = _today.year - dob.year - ((_today.month, _today.day) < (dob.month, dob.day))
            if age < 0:
                raise ValueError("negative age")
            is_minor = age < 18
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid date_of_birth format — use YYYY-MM-DD")

    guardian_email = (body.guardian_email or "").strip().lower() or None
    if is_minor:
        if not guardian_email:
            raise HTTPException(
                status_code=422,
                detail="A guardian email is required for students under 18. Please provide a parent or guardian email.",
            )
        if not _looks_like_email(guardian_email):
            raise HTTPException(status_code=400, detail="Invalid guardian email format")

    # Cohort/batch (gap #59): a cohort-enrollment link (?t=&b=<batch>) stamps
    # the registrant with this batch. Capped to the column width.
    batch = ((body.batch or "").strip() or None)
    if batch and len(batch) > 120:
        batch = batch[:120]

    if not returning_student:
        row = {
            "roll_number": roll,
            "full_name":   name,
            "email":       email,
            "phone":       phone,
            "teacher_id":  teacher_id,
            "date_of_birth": date_of_birth_str if date_of_birth_str else None,
            "guardian_email": guardian_email,
        }
        if batch:
            row["batch"] = batch
        try:
            await _atable("students").insert(row).execute()
        except httpx.HTTPStatusError as e:
            msg = str(e).lower()
            # A concurrent registration created the roster row first — that's
            # fine, treat as returning and continue to the per-exam invite.
            if "duplicate" in msg or "unique" in msg or e.response.status_code == 409:
                returning_student = True
            else:
                raise HTTPException(status_code=500, detail="Registration failed. Please try again.")
        except Exception as e:
            msg = str(e).lower()
            if "duplicate" in msg or "unique" in msg:
                returning_student = True
            else:
                raise HTTPException(status_code=500, detail="Registration failed. Please try again.")

    # Returning student via a cohort link → (re)stamp their batch. Only when a
    # batch was supplied, so a plain exam/registration link never clears it.
    if returning_student and batch:
        try:
            await _atable("students").update({"batch": batch})\
                .eq("roll_number", roll).eq("teacher_id", teacher_id).execute()
        except Exception:
            logger.warning("[register] batch update failed for roll=%s", roll)

    # Auto-link: if the student already has a login account, set
    # account_id immediately — otherwise they'd have to wait until
    # their next login for the signup/login auto-link to fire.
    try:
        acct = await _atable("student_accounts").select("id").eq("email", email).limit(1).execute()
        if acct.data:
            await _atable("students")\
                .update({"account_id": acct.data[0]["id"]})\
                .eq("roll_number", roll)\
                .eq("teacher_id", teacher_id)\
                .execute()
    except Exception as e:
        _pub_log.warning("[register_student] auto-link failed: %s", e)

    # ── Auto-send guardian consent for minors ─────────────────────
    # If this student is a minor with a guardian_email, generate a
    # consent token, store its SHA-256 hash, and enqueue the email
    # immediately — no teacher action needed.
    #
    # Re-send guard: don't re-issue/re-email if a consent request was already
    # sent for THIS student within the last 24h. Without it, re-POSTing the same
    # minor registration spams the guardian address (abuse vector on this
    # unauthenticated endpoint). Distinct fake students are bounded by the org
    # seat cap checked above.
    _consent_recent = False
    if prev_consent_requested_at:
        try:
            _req_at = datetime.fromisoformat(str(prev_consent_requested_at).replace("Z", "+00:00"))
            if _req_at.tzinfo is None:
                _req_at = _req_at.replace(tzinfo=timezone.utc)
            _consent_recent = (datetime.now(timezone.utc) - _req_at).total_seconds() < 86400
        except (ValueError, TypeError):
            _consent_recent = False

    if is_minor and guardian_email and not _consent_recent:
        import hashlib
        import uuid as _uuid
        from ..jobs import enqueue_job, send_guardian_consent_request_job

        raw_token = str(_uuid.uuid4())
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        now_iso = datetime.now(timezone.utc).isoformat()

        try:
            await (
                _atable("students")
                .update({
                    "guardian_consent_token_hash": token_hash,
                    "guardian_consent_requested_at": now_iso,
                })
                .eq("roll_number", roll)
                .eq("teacher_id", teacher_id)
                .execute()
            )

            base_url = _get_invite_base_url()
            consent_url = f"{base_url}/guardian-consent/{raw_token}"

            enqueue_job(
                send_guardian_consent_request_job,
                to_email=guardian_email,
                to_name=guardian_email.split("@")[0],
                student_name=name,
                consent_url=consent_url,
            )
        except Exception as e:
            _pub_log.warning("[register_student] guardian consent auto-send failed "
                             "(roll=%s): %s", roll, e)

    # Resolve which exam this self-registration attaches to. Normally the
    # link carries &e=<exam_id> (validated above). But that param is commonly
    # lost when the share link is truncated at the '&' by WhatsApp/SMS/in-app
    # browsers — the student then lands on /register?t=... with no exam, gets
    # rostered teacher-wide, and no per-exam invite is written, leaving the
    # exam-scoped registered count at 0 and the lobby empty. Fall back to the
    # teacher's latest non-archived exam — the same resolution the bulk-register
    # path uses — so the student still lands on an exam. (Validation above
    # already confirmed any caller-supplied exam_id belongs to this teacher;
    # the fallback only ever resolves an exam THIS teacher owns.)
    resolved_exam_id = exam_id_from_body
    if not resolved_exam_id:
        try:
            cfgs = (await _atable("exam_config")
                    .select("exam_id,created_at")
                    .eq("teacher_id", teacher_id)
                    .is_("archived_at", "null")
                    .order("created_at", desc=True)
                    .limit(1).execute()).data or []
            if cfgs and cfgs[0].get("exam_id"):
                resolved_exam_id = cfgs[0]["exam_id"]
        except Exception as e:
            _pub_log.warning("[register_student] exam_id fallback resolution failed "
                             "(roll=%s): %s", roll, e)

    # Per-exam association: record (or refresh) a student_invites row so
    # this self-registration is counted in the exam's "registered" roster
    # and the lobby resolves the right exam. This is the schema-honest
    # replacement for the old students.exam_id write. Idempotent on
    # (teacher_id, email, exam_id); marked accepted (the student
    # registered themselves) — NO invite email is sent here. Non-fatal:
    # the student is already registered teacher-wide if this fails.
    if resolved_exam_id:
        try:
            existing_inv = (await _atable("student_invites").select("id")
                            .eq("teacher_id", teacher_id)
                            .eq("email", email)
                            .eq("exam_id", resolved_exam_id)
                            .limit(1).execute()).data or []
            inv_fields = {
                "teacher_id":  teacher_id,
                "email":       email,
                "full_name":   name,
                "roll_number": roll,
                "exam_id":     resolved_exam_id,
                "status":      InviteStatus.ACCEPTED,
                "accepted_at": datetime.now(timezone.utc).isoformat(),
            }
            if existing_inv:
                await (_atable("student_invites").update(inv_fields)
                       .eq("id", existing_inv[0]["id"]).execute())
            else:
                inv_fields["token"] = _new_invite_token()
                await _atable("student_invites").insert(inv_fields).execute()
        except Exception as e:
            _pub_log.warning("[register_student] per-exam roster link failed "
                             "(roll=%s exam=%s): %s", roll, resolved_exam_id, e)

    return {"status": "registered", "roll_number": roll, "full_name": name,
            "exam_id": resolved_exam_id or None,
            **({"warning": dup_roll_warning} if dup_roll_warning else {})}


@router.get("/api/v1/exam-schedule")
@limiter.limit("30/minute")
async def get_public_schedule(request: Request, t: str = None):
    """Public endpoint — returns exam title and schedule for download/register pages.

    Rate-limited to deter scraping/enumeration of exam schedules by teacher_id."""
    # Without a teacher_id there is no exam to describe. Never fall through to
    # load_exam_config(None) — with no filters it returns the FIRST row in the
    # table, leaking an arbitrary teacher's exam title/schedule on this public
    # unauthenticated endpoint.
    if not (t or "").strip():
        return {"exam_title": "Exam", "duration_minutes": 60,
                "starts_at": None, "ends_at": None}
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
    if not _looks_like_email(email):
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

    # access_code has no global uniqueness constraint (every demo exam uses
    # "DEMO"; teachers may reuse codes), so a lookup can match multiple rows.
    # Exclude archived exams — a deleted exam must not be registrable, mirroring
    # the register_student exam_id fallback (line ~568) — and resolve any
    # remaining collision deterministically to the most-recently-created active
    # exam instead of whatever arbitrary row the DB returns first.
    result = await _atable("exam_config").select(
        "teacher_id, exam_id, exam_title, access_code, duration_minutes, starts_at, ends_at"
    ).eq("access_code", code).is_("archived_at", "null").order("created_at", desc=True).limit(1).execute()
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


@router.get("/login")
def login_page():
    """Unified sign-in for students AND teachers. Role toggle picks the
    endpoint (/api/v1/student/auth/login vs /api/v1/auth/login); both set
    their own cookie session and redirect to /student-next or /dashboard-next.
    Generic on purpose — the legacy login lives inside the teacher-flavoured
    /dashboard page, and students had no web login form at all (it was
    Electron-only). Sign-UP stays role-specific (students /register, teachers
    procta.net), so this page only signs people in."""
    return _static_html_response("login.html", "Login page not found")


@router.get("/register")
def register_page():
    """Self-registration page for students before exam day."""
    return _static_html_response("register.html", "Registration page not found")


@router.get("/student")
def student_page():
    """REVERTED to legacy (2026-06-27): the /student-next rebuild was cut over
    prematurely and shipped many unwired/broken surfaces, so /student serves the
    battle-tested legacy student page again. The rebuild stays reachable at
    /student-next for continued fixing, but is NOT the default."""
    return _static_html_response("student.html", "Student dashboard not found")


@router.get("/student-legacy")
def student_page_legacy():
    """Back-compat alias for the legacy student page (== /student)."""
    return _static_html_response("student.html", "Student dashboard not found")


@router.get("/dashboard")
def admin_dashboard():
    # REVERTED to legacy (2026-06-27): the /dashboard-next rebuild was cut over
    # prematurely (PR #193) and shipped many unwired/broken surfaces, so /dashboard
    # serves the battle-tested legacy HTML dashboard again. The rebuild stays
    # reachable at /dashboard-next for continued fixing, but is NOT the default.
    return _static_html_response("dashboard.html", "Dashboard not found")


@router.get("/dashboard-react")
def admin_dashboard_react():
    # The React dashboard (work-in-progress). Not the default teacher surface
    # yet — reachable here for development/testing. Known issue: lazy panel
    # chunks throw React #321 ("invalid hook call") because FastAPI's
    # _stamp_static_urls appends ?v= to the Vite entry <script>, giving the
    # React-containing chunk two ES-module URLs (?v= entry vs bare lazy
    # import) and thus two React instances. Fix before promoting: exclude
    # /static/dashboard-react/ from _stamp_static_urls (the bundle is already
    # content-hashed, so it needs no ?v= cache-bust).
    return _static_html_response(
        "dashboard-react/index.html", "React dashboard not found")


@router.get("/dashboard-next")
def admin_dashboard_next():
    # Vanilla Material-3 rebuild (PR #192). NOW THE DEFAULT: /dashboard redirects
    # here as of the 2026-06-26 cutover. This is the canonical teacher surface;
    # the legacy HTML dashboard is on standby at /dashboard-legacy. Vanilla (no
    # React) sidesteps the #321 _stamp_static_urls double-React-instance bug.
    return _static_html_response(
        "dashboard_next/procta_live_monitor_high_density_view/code.html",
        "Dashboard (next) not found")


@router.get("/dashboard-next/questions")
def admin_dashboard_next_questions():
    return _static_html_response(
        "dashboard_next/procta_question_authoring_coding_wizard/code.html",
        "Dashboard (next) questions not found")


@router.get("/dashboard-next/overview")
def admin_dashboard_next_overview():
    return _static_html_response(
        "dashboard_next/proctorly_teacher_overview/code.html",
        "Dashboard (next) overview not found")


@router.get("/dashboard-next/exams")
def admin_dashboard_next_exams():
    return _static_html_response(
        "dashboard_next/proctorly_all_exams/code.html",
        "Dashboard (next) exams not found")


@router.get("/dashboard-next/students")
def admin_dashboard_next_students():
    return _static_html_response(
        "dashboard_next/proctorly_student_roster/code.html",
        "Dashboard (next) students not found")


@router.get("/dashboard-next/results")
def admin_dashboard_next_results():
    return _static_html_response(
        "dashboard_next/proctorly_results_analytics/code.html",
        "Dashboard (next) results not found")


@router.get("/dashboard-next/integrations")
def admin_dashboard_next_integrations():
    return _static_html_response(
        "dashboard_next/proctorly_integrations/code.html",
        "Dashboard (next) integrations not found")


@router.get("/dashboard-next/settings")
def admin_dashboard_next_settings():
    return _static_html_response(
        "dashboard_next/proctorly_settings/code.html",
        "Dashboard (next) settings not found")


@router.get("/dashboard-next/evidence")
def admin_dashboard_next_evidence():
    # Appeals review queue (privacy-correct: frames only from appeal-attached evidence).
    return _static_html_response(
        "dashboard_next/proctorly_evidence_review/code.html",
        "Dashboard (next) evidence not found")


@router.get("/dashboard-next/members")
def admin_dashboard_next_members():
    return _static_html_response(
        "dashboard_next/procta_admin_members_desktop/code.html", "Members not found")


@router.get("/dashboard-next/org-settings")
def admin_dashboard_next_org_settings():
    return _static_html_response(
        "dashboard_next/procta_admin_org_settings_desktop/code.html", "Org settings not found")


@router.get("/dashboard-next/billing")
def admin_dashboard_next_billing():
    return _static_html_response(
        "dashboard_next/procta_admin_billing_desktop/code.html", "Billing not found")


@router.get("/dashboard-next/all-orgs")
def admin_dashboard_next_all_orgs():
    return _static_html_response(
        "dashboard_next/procta_superadmin_all_organizations_desktop/code.html", "All orgs not found")


@router.get("/dashboard-next/system-health")
def admin_dashboard_next_system_health():
    return _static_html_response(
        "dashboard_next/procta_superadmin_system_health_desktop/code.html", "System health not found")


@router.get("/dashboard-next/issues")
def admin_dashboard_next_issues():
    return _static_html_response(
        "dashboard_next/procta_superadmin_issues_desktop/code.html", "Issues not found")


@router.get("/student-next")
def student_dashboard_next():
    # Responsive student hub (schedule + results + scorecards). Student session;
    # the proctored exam itself still runs in the desktop client. WIP → replaces /student.
    return _static_html_response(
        "student_next/code.html", "Student dashboard (next) not found")


@router.get("/dashboard-legacy")
def admin_dashboard_legacy():
    # Back-compat alias for the legacy dashboard, now identical to /dashboard.
    # Kept so any bookmarked/LTI-cached /dashboard-legacy URL still resolves.
    return _static_html_response("dashboard.html", "Legacy dashboard not found")


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


@router.get("/cookie-policy")
def cookie_policy_redirect():
    return RedirectResponse(url="/privacy-policy#cookies")


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


@router.get("/download/latest-info")
async def download_latest_info():
    """Debug / health endpoint — shows what the server currently resolves
    for each platform and the last seen release tag."""
    await _resolve_release_asset("mac_arm")
    snap = release_cache_snapshot()  # live values — see release_cache_snapshot()
    return {
        "tag":       snap.get("tag", ""),
        "mac_arm":   snap.get("mac_arm", ""),
        "mac_x64":   snap.get("mac_x64", ""),
        "win":       snap.get("win", ""),
        "cache_expires_in_sec": max(0, int(snap.get("_expires", 0) - time.time())),
        "env_overrides": {
            "DOWNLOAD_MAC_ARM": bool(DOWNLOAD_MAC_ARM),
            "DOWNLOAD_MAC_X64": bool(DOWNLOAD_MAC_X64),
            "DOWNLOAD_WIN":     bool(DOWNLOAD_WIN),
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
        registration_url=f"{_get_invite_base_url()}/register?t={inv.get('teacher_id')}&e={inv.get('exam_id')}" if inv.get("teacher_id") and inv.get("exam_id") else "",
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

    exam_cfg = await _load_exam_config(inv.get("teacher_id"), exam_id=inv.get("exam_id")) \
        if inv.get("exam_id") else {}

    return {
        "ok":          True,
        "status":      inv.get("status"),
        "exam_id":     inv.get("exam_id"),
        "exam_title":  (exam_cfg.get("exam_title") if isinstance(exam_cfg, dict) else None) or "",
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

    try:
        # students is the teacher-wide roster row — it has NO exam_id
        # column. Writing exam_id here raised UndefinedColumnError →
        # caught below → a 500 on EVERY invite-link acceptance. The
        # per-exam association is preserved by the student_invites status
        # update right after this (the invite row carries the exam_id).
        enroll_row = {
            "email":       inv.get("email"),
            "full_name":   inv.get("full_name"),
            "roll_number": inv.get("roll_number"),
            "teacher_id":  str(inv["teacher_id"]),
            "account_id":  str(student["id"]),
        }
        await _atable("students").upsert(
            enroll_row,
            on_conflict="roll_number,teacher_id",
        ).execute()
    except Exception as e:
        _pub_log.exception("[accept_invite] failed to upsert student enrollment")
        raise HTTPException(
            status_code=500,
            detail="Invite could not be applied. Please try again or ask your teacher to resend it.",
        ) from e

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

    _pub_log.info("[DemoRequest] %s <%s> from %s", safe(req.name), mask_email(req.email), safe(req.institution))

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


@router.get("/phone-cam")
async def phone_cam_page(request: Request):
    """Serve the phone camera capture page (room monitoring). The student's
    phone loads this over the web from the QR URL, so it must live in the
    SERVER's static dir (app/static) — it used to point at renderer/, which
    isn't in the deployed Docker image, so prod 404'd ('Phone camera page not
    found') and the phone could never pair."""
    return _static_html_response("phone-cam.html", "Phone camera page not found")
