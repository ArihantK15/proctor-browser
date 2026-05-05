from pathlib import Path
import os
import json
import time
import asyncio
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Request, HTTPException, Body
from fastapi.responses import RedirectResponse, FileResponse, HTMLResponse, Response
from pydantic import BaseModel, ConfigDict

from ..dependencies import (
    supabase, limiter, _atable, _cache,
    RegisterIn, require_admin, require_student_account, verify_student_auth_token,
    fmt_ist, now_ist, _load_exam_config, _get_invite_base_url,
    _render_invite_error, _render_invite_landing,
    _refresh_release_cache, _resolve_release_asset, _download_redirect,
    _get_teacher_by_id, _RELEASE_CACHE, _RELEASE_CACHE_EXPIRES,
    DOWNLOAD_MAC_ARM, DOWNLOAD_MAC_X64, DOWNLOAD_WIN,
    SECRET_KEY, SUPER_ADMIN_EMAIL,
    SessionStatus, InviteStatus, VerificationStatus,
)


class DemoRequest(BaseModel):
    model_config = ConfigDict(strict=True)
    name: str
    email: str
    institution: str
    role: str
    message: str = ""

router = APIRouter(prefix="")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


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
        "Disallow: /api/v1/\n"
        "Disallow: /register\n"
        "Disallow: /student\n"
        "Disallow: /static/\n"
        "\n"
        "Sitemap: https://app.procta.net/sitemap.xml\n"
    )
    from starlette.responses import Response
    return Response(content=content, media_type="text/plain")


@router.get("/health")
def health():
    """Lightweight health probe for uptime monitors and load balancers.

    Returns 200 only when Supabase is reachable. Redis is optional
    (the API works without it — SSE just won't broadcast).
    """
    checks = {}
    ok = True

    # Supabase — required
    try:
        supabase.table("exam_config").select("id").limit(1).execute()
        checks["supabase"] = "ok"
    except Exception as e:
        checks["supabase"] = f"error: {e}"
        ok = False

    # Redis — optional
    try:
        import redis as _redis
        r = _redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379"))
        r.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "unavailable"  # non-fatal

    # Memory
    try:
        import psutil
        mem = psutil.virtual_memory()
        checks["memory_pct"] = mem.percent
    except ImportError:
        pass

    status = 200 if ok else 503
    return Response(
        content=json.dumps({"status": "ok" if ok else "degraded", "checks": checks}),
        media_type="application/json",
        status_code=status,
    )


@router.post("/api/v1/register-student")
@limiter.limit("120/minute")
def register_student(request: Request, body: RegisterIn):
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

    teacher = _get_teacher_by_id(body.teacher_id)
    if not teacher:
        raise HTTPException(status_code=404, detail="Unknown teacher")
    teacher_id = str(teacher["id"])

    existing = supabase.table("students")\
        .select("roll_number")\
        .eq("roll_number", roll)\
        .eq("teacher_id", teacher_id)\
        .execute()
    if existing.data:
        raise HTTPException(
            status_code=409,
            detail="This roll number is already registered. If this is a mistake, contact your examiner.")

    row = {
        "roll_number": roll,
        "full_name":   name,
        "email":       email,
        "phone":       phone,
        "teacher_id":  teacher_id,
    }
    try:
        supabase.table("students").insert(row).execute()
    except Exception as e:
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            raise HTTPException(status_code=409, detail="This roll number is already registered.")
        raise HTTPException(status_code=500, detail="Registration failed. Please try again.")

    return {"status": "registered", "roll_number": roll, "full_name": name}


@router.get("/api/v1/exam-schedule")
def get_public_schedule(t: str = None):
    """Public endpoint — returns exam title and schedule for download/register pages."""
    config = _load_exam_config(teacher_id=t)
    return {
        "exam_title":  config.get("exam_title", "Exam"),
        "duration_minutes": config.get("duration_minutes", 60),
        "starts_at":   config.get("starts_at"),
        "ends_at":     config.get("ends_at"),
    }


@router.get("/api/v1/lookup-teacher")
@limiter.limit("30/minute")
def lookup_teacher(request: Request, email: str = ""):
    """Public endpoint — find a teacher by email for self-registration.

    Returns minimal info (id, full_name) so the student registration
    page can populate the hidden teacher_id field. Does NOT return
    email to avoid harvesting.
    """
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")
    result = supabase.table("teachers").select("id,full_name").eq("email", email).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="No teacher found with this email")
    teacher = result.data[0]
    return {
        "teacher_id": teacher["id"],
        "full_name":  teacher.get("full_name", ""),
    }


@router.post("/api/v1/resolve-access-code")
@limiter.limit("30/minute")
def resolve_access_code(request: Request, body: dict):
    """Public endpoint — resolve an exam access code to teacher + exam info.

    Students who received an access code from their teacher can use this
    to find the right registration context without needing a direct link.
    """
    code = (body.get("access_code") or "").strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="Access code is required")

    # Search exam_config for matching access_code
    result = supabase.table("exam_config").select(
        "teacher_id", "exam_id", "exam_title", "access_code",
        "duration_minutes", "starts_at", "ends_at"
    ).eq("access_code", code).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Invalid access code")

    cfg = result.data[0]
    teacher = _get_teacher_by_id(cfg.get("teacher_id"))
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
    html_path = STATIC_DIR / "download.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="Download page not found")
    return HTMLResponse(html_path.read_text())


@router.get("/register")
def register_page():
    """Self-registration page for students before exam day."""
    html_path = STATIC_DIR / "register.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="Registration page not found")
    return HTMLResponse(html_path.read_text())


@router.get("/student")
def student_page():
    """Student-facing dashboard: upcoming exams, practice, profile."""
    html_path = STATIC_DIR / "student.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="Student dashboard not found")
    return HTMLResponse(html_path.read_text())


@router.get("/dashboard")
def admin_dashboard():
    html_path = STATIC_DIR / "dashboard.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return HTMLResponse(html_path.read_text())


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
    return {
        "tag":       _RELEASE_CACHE.get("tag", ""),
        "mac_arm":   _RELEASE_CACHE.get("mac_arm", ""),
        "mac_x64":   _RELEASE_CACHE.get("mac_x64", ""),
        "win":       _RELEASE_CACHE.get("win", ""),
        "cache_expires_in_sec": max(0, int(_RELEASE_CACHE_EXPIRES - time.time())),
        "env_overrides": {
            "DOWNLOAD_MAC_ARM": bool(DOWNLOAD_MAC_ARM),
            "DOWNLOAD_MAC_X64": bool(DOWNLOAD_MAC_X64),
            "DOWNLOAD_WIN":     bool(DOWNLOAD_WIN),
        },
    }


@router.get("/invite/{token}", response_class=HTMLResponse)
def invite_landing(token: str, request: Request):
    """Public landing page for invite recipients."""
    row = (supabase.table("student_invites").select("*")
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
            pass

    if not inv.get("opened_at"):
        try:
            supabase.table("student_invites").update({
                "opened_at": datetime.now(timezone.utc).isoformat(),
                "status": InviteStatus.OPENED if status in (InviteStatus.SENT, "queued") else status,
            }).eq("token", token).execute()
        except Exception:
            pass

    exam_cfg = _load_exam_config(inv.get("teacher_id"), exam_id=inv.get("exam_id")) \
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
def resolve_invite(token: str):
    """Public JSON lookup for an invite token."""
    row = (supabase.table("student_invites").select("*")
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
            pass

    exam_cfg = _load_exam_config(inv.get("teacher_id"), exam_id=inv.get("exam_id")) \
        if inv.get("exam_id") else {}
    exam_title = (exam_cfg.get("exam_title") if isinstance(exam_cfg, dict) else None) or "Your Procta Exam"

    return {
        "ok":           True,
        "email":        inv.get("email"),
        "full_name":    inv.get("full_name"),
        "roll_number":  inv.get("roll_number"),
        "access_code":  inv.get("access_code") or "",
        "exam_id":      inv.get("exam_id"),
        "exam_title":   exam_title,
        "starts_at":    exam_cfg.get("starts_at") if isinstance(exam_cfg, dict) else None,
        "ends_at":      exam_cfg.get("ends_at")   if isinstance(exam_cfg, dict) else None,
        "status":       status or InviteStatus.SENT,
        "accepted":     bool(inv.get("accepted_at")),
    }


@router.post("/api/v1/invite/{token}/accept")
def accept_invite(token: str, request: Request):
    """Link a signed-in student account to an invite."""
    student = require_student_account(request)
    row = (supabase.table("student_invites").select("*")
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
            pass

    inv_email = (inv.get("email") or "").strip().lower()
    stu_email = (student.get("email") or "").strip().lower()
    if not inv_email or inv_email != stu_email:
        raise HTTPException(status_code=403, detail="This invite is for a different email address")

    supabase.table("student_invites").update({
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
        print(f"[webhook] rejected svix-id={sid}", flush=True)
        raise HTTPException(status_code=403, detail="Invalid webhook signature")
    try:
        payload = json.loads(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    evt = (payload.get("type") or "").lower()
    data = payload.get("data") or {}
    msg_id = data.get("email_id") or data.get("id")
    if not msg_id:
        return {"ok": True, "ignored": "no msg id"}

    now_iso = datetime.now(timezone.utc).isoformat()
    _SENT_LIKE = ["queued", InviteStatus.SENT, InviteStatus.OPENED, InviteStatus.CLICKED]

    if evt == "email.bounced":
        supabase.table("student_invites").update({
            "status": InviteStatus.BOUNCED,
            "bounced_at": now_iso,
            "bounce_reason": str(data.get("bounce") or data.get("reason") or "bounced")[:500],
        }).eq("provider_msg_id", msg_id).in_("status", _SENT_LIKE).execute()
    elif evt == "email.complained":
        supabase.table("student_invites").update({
            "status": InviteStatus.FAILED,
            "bounce_reason": "recipient marked as spam",
        }).eq("provider_msg_id", msg_id).in_("status", _SENT_LIKE).execute()
    elif evt == "email.opened":
        try:
            (supabase.table("student_invites")
             .update({"opened_at": now_iso, "status": InviteStatus.OPENED})
             .eq("provider_msg_id", msg_id).eq("status", InviteStatus.SENT).execute())
            (supabase.table("student_invites")
             .update({"opened_at": now_iso})
             .eq("provider_msg_id", msg_id).is_("opened_at", "null").execute())
        except Exception as e:
            print(f"[webhook] opened update failed msg_id={msg_id}: {e}", flush=True)
    elif evt == "email.clicked":
        try:
            existing = (supabase.table("student_invites")
                        .select("id,status,clicked_at,click_count")
                        .eq("provider_msg_id", msg_id).limit(1).execute()).data or []
            if existing:
                row = existing[0]
                update = {"click_count": int(row.get("click_count") or 0) + 1}
                if not row.get("clicked_at"):
                    update["clicked_at"] = now_iso
                supabase.table("student_invites").update(update)\
                    .eq("id", row["id"]).execute()
                supabase.table("student_invites").update({"status": InviteStatus.CLICKED})\
                    .eq("id", row["id"]).in_("status", [InviteStatus.SENT, InviteStatus.OPENED]).execute()
        except Exception as e:
            print(f"[webhook] clicked update failed msg_id={msg_id}: {e}", flush=True)
    elif evt == "email.delivered":
        pass
    print(f"[webhook] {evt} msg_id={msg_id}", flush=True)
    return {"ok": True, "event": evt}


@router.post("/api/v1/demo-request")
@limiter.limit("5/hour")
async def submit_demo_request(req: DemoRequest, request: Request):
    """Store a demo request from the marketing site."""
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
        supabase.table("demo_requests").insert(row).execute()
    except Exception as e:
        print(f"[DemoRequest] Failed to store: {e}")
        raise HTTPException(status_code=500, detail="Failed to store request")

    print(f"[DemoRequest] {req.name} <{req.email}> from {req.institution}")
    return {"status": "ok", "message": "Demo request received"}


@router.get("/api/v1/admin/demo-requests")
async def list_demo_requests(request: Request):
    """List all demo requests — restricted to the configured super-admin."""
    teacher = require_admin(request)
    if not SUPER_ADMIN_EMAIL or teacher.get("email", "").lower() != SUPER_ADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="Forbidden")
    result = supabase.table("demo_requests").select("*").order("created_at", desc=True).execute()
    return {"requests": result.data, "count": len(result.data)}
