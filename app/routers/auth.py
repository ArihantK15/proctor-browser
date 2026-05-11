from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
import json
import logging
import re
_auth_log = logging.getLogger("auth")
import uuid as _uuid
from datetime import datetime, timezone, timedelta

from ..dependencies import (
    supabase,
    _atable,
    limiter,
    TeacherSignupIn,
    TeacherLoginIn,
    RefreshIn,
    StudentSignupIn,
    StudentLoginIn,
    PasswordResetIn,
    issue_admin_token,
    _get_teacher_by_id,
    _get_teacher_by_uid,
    issue_student_auth_token,
    _get_student_account_by_id,
    _get_student_account_by_uid,
    require_admin,
    require_student_account,
    fmt_ist,
    now_ist,
    SessionStatus,
    PLANS, TRIAL_DAYS,
)
from ..utils import _html_escape as _esc
from ..jobs import enqueue_job, send_new_account_notification_job

logger = logging.getLogger(__name__)

router = APIRouter(prefix="")


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower().strip())
    return s.strip("-") or "org"


@router.post("/api/v1/auth/signup")
@limiter.limit("5/hour")
async def teacher_signup(body: TeacherSignupIn, request: Request):
    """Create a new teacher account with org and trial subscription."""
    email = body.email.strip().lower()
    name = body.full_name.strip()
    org_name = (body.org_name or "").strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")
    if not name:
        raise HTTPException(status_code=400, detail="Full name is required")
    if not org_name:
        raise HTTPException(status_code=400, detail="Organization name is required")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    # Check if teacher already exists
    existing = await _atable("teachers").select("id").eq("email", email).execute()
    if existing.data:
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    # Check if org slug already exists
    slug = _slugify(org_name)
    org_exists = await _atable("organizations").select("id,name").eq("slug", slug).execute()
    if org_exists.data:
        raise HTTPException(
            status_code=409,
            detail=f"'{org_name}' is already registered. Ask your admin for an invite."
        )

    try:
        auth_resp = supabase.auth.admin.create_user({
            "email": email,
            "password": body.password,
            "email_confirm": True,
        })
        supabase_uid = auth_resp.user.id
    except Exception as e:
        err_msg = str(e).lower()
        if "already registered" in err_msg or "duplicate" in err_msg:
            raise HTTPException(status_code=409, detail="An account with this email already exists")
        _auth_log.error("[TeacherSignup] Supabase Auth error: %s", e)
        raise HTTPException(status_code=500, detail="Failed to create account")

    # Create org, subscription, teacher — transactional rollback
    try:
        # Create org
        org_result = await _atable("organizations").insert({
            "name": org_name,
            "slug": slug,
            "max_students": PLANS["starter"]["students"],
        }).execute()
        org = org_result.data[0]
        org_id = org["id"]

        # Create trial subscription
        trial_end = (datetime.now(timezone.utc) + timedelta(days=TRIAL_DAYS)).isoformat()
        await _atable("subscriptions").insert({
            "org_id": str(org_id),
            "plan": "starter",
            "status": "trialing",
            "trial_end": trial_end,
        }).execute()

        # Create teacher with org context
        teacher_result = await _atable("teachers").insert({
            "email": email,
            "full_name": name,
            "supabase_uid": str(supabase_uid),
            "org_id": str(org_id),
            "org_role": "admin",
        }).execute()
        teacher = teacher_result.data[0]

        # Create default exam_config
        await _atable("exam_config").insert({
            "exam_id": str(_uuid.uuid4()),
            "teacher_id": teacher["id"],
            "exam_title": "Exam",
            "duration_minutes": 60,
        }).execute()
    except Exception as e:
        _auth_log.error("[TeacherSignup] DB error: %s", e)
        try:
            supabase.auth.admin.delete_user(str(supabase_uid))
        except Exception as rollback_err:
            _auth_log.critical("[TeacherSignup] Rollback failed: %s", rollback_err)
        raise HTTPException(status_code=500, detail="Failed to create account")

    access_token = issue_admin_token(teacher)
    _auth_log.info("[TeacherSignup] %s <%s> created (org=%s)", name, email, org_name)

    enqueue_job(send_new_account_notification_job,
                account_type="teacher", name=name, email=email)

    return {
        "teacher_id":    teacher["id"],
        "email":         email,
        "full_name":     name,
        "org_id":        str(org_id),
        "org_name":      org_name,
        "org_role":      "admin",
        "access_token":  access_token,
    }


@router.post("/api/v1/auth/login")
@limiter.limit("10/minute")
async def teacher_login(body: TeacherLoginIn, request: Request):
    """Log in a teacher via Supabase Auth, return JWT tokens."""
    email = body.email.strip().lower()
    try:
        auth_resp = supabase.auth.sign_in_with_password({
            "email": email,
            "password": body.password,
        })
    except Exception as e:
        _auth_log.warning("[TeacherLogin] Auth error: %s", e)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    supabase_uid = str(auth_resp.user.id)
    teacher = await _get_teacher_by_uid(supabase_uid)
    if not teacher:
        raise HTTPException(status_code=403, detail="Teacher account not found. Please sign up first.")

    return {
        "access_token": issue_admin_token(teacher),
        "refresh_token": auth_resp.session.refresh_token,
        "teacher": {
            "id": teacher["id"],
            "email": teacher["email"],
            "full_name": teacher["full_name"],
        },
    }


@router.get("/api/v1/auth/me")
async def teacher_me(request: Request):
    """Get current teacher profile from Bearer token."""
    teacher = await require_admin(request)
    return {
        "id": teacher["id"],
        "email": teacher["email"],
        "full_name": teacher["full_name"],
    }


@router.post("/api/v1/auth/refresh")
@limiter.limit("20/minute")
async def teacher_refresh(body: RefreshIn, request: Request):
    """Refresh an expired teacher access token via Supabase refresh token.

    The Supabase refresh token is the only credential the client retains
    long-term; we re-validate it via Supabase, look up the teacher, and
    issue a fresh HS256 admin token signed by us.
    """
    try:
        auth_resp = supabase.auth.refresh_session(body.refresh_token)
    except Exception as e:
        _auth_log.warning("[TeacherRefresh] Error: %s", e)
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    if not auth_resp or not auth_resp.user or not auth_resp.session:
        raise HTTPException(status_code=401, detail="Invalid refresh response")

    supabase_uid = str(auth_resp.user.id)
    teacher = await _get_teacher_by_uid(supabase_uid)
    if not teacher:
        raise HTTPException(status_code=403, detail="Teacher account not found")

    return {
        "access_token":  issue_admin_token(teacher),
        "refresh_token": auth_resp.session.refresh_token,
    }


@router.post("/api/v1/auth/password-reset")
@limiter.limit("3/minute")
async def teacher_password_reset(body: PasswordResetIn, request: Request):
    """Send a password reset email via Supabase Auth."""
    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")
    try:
        supabase.auth.reset_password_for_email(email)
    except Exception as e:
        _auth_log.warning("[PasswordReset] Error for %s: %s", email, e)
        # Don't reveal whether the email exists or not
    return {"status": "ok", "message": "If that email is registered, a reset link has been sent."}


# ─── ORG INVITE ACCEPTANCE ───────────────────────────────────────

_INVITE_PAGE = """\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Accept Invitation — Procta</title>
<style>
  *{ margin:0; padding:0; box-sizing:border-box; }
  body{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
        background:#0f172a; display:flex; align-items:center; justify-content:center;
        min-height:100vh; color:#0f172a; }
  .card{ background:#fff; border-radius:16px; padding:40px; max-width:440px; width:90%; }
  h1{ font-size:22px; font-weight:700; margin-bottom:8px; }
  p{ color:#475569; font-size:14px; line-height:1.5; margin-bottom:24px; }
  .field{ margin-bottom:16px; }
  label{ display:block; font-size:13px; font-weight:600; color:#334155; margin-bottom:4px; }
  input{ width:100%; padding:10px 12px; border:1px solid #cbd5e1; border-radius:8px;
         font-size:15px; outline:none; transition:border-color .15s; }
  input:focus{ border-color:#3b82f6; box-shadow:0 0 0 3px rgba(59,130,246,.15); }
  button{ width:100%; padding:12px; background:#3b82f6; color:#fff; border:none;
          border-radius:8px; font-size:15px; font-weight:600; cursor:pointer; }
  button:hover{ background:#2563eb; }
  .error{ background:#fef2f2; color:#991b1b; padding:12px; border-radius:8px;
          font-size:13px; margin-bottom:16px; display:none; }
  .org-badge{ background:#f1f5f9; border-radius:8px; padding:12px; margin-bottom:24px;
              font-size:14px; text-align:center; color:#334155; }
</style></head>
<body>
<div class="card">
  <h1>Join {org_name}</h1>
  <div class="org-badge">You've been invited to <strong>{org_name}</strong></div>
  <div class="error" id="error"></div>
  <form id="acceptForm">
    <div class="field">
      <label for="full_name">Full name</label>
      <input type="text" id="full_name" name="full_name" required placeholder="Your full name">
    </div>
    <div class="field">
      <label for="password">Password</label>
      <input type="password" id="password" name="password" required minlength="8" placeholder="At least 8 characters">
    </div>
    <button type="submit">Accept &amp; Join</button>
  </form>
  <p style="margin-top:16px;font-size:12px;color:#94a3b8;text-align:center;">
    Already have an account? <a href="https://app.procta.net/dashboard" style="color:#3b82f6;">Go to dashboard</a>
  </p>
</div>
<script>
document.getElementById('acceptForm').addEventListener('submit', async function(e){
  e.preventDefault();
  const errEl = document.getElementById('error');
  errEl.style.display = 'none';
  const full_name = document.getElementById('full_name').value.trim();
  const password = document.getElementById('password').value;
  if (!full_name) { errEl.textContent='Name is required'; errEl.style.display='block'; return; }
  if (password.length < 8) { errEl.textContent='Password must be at least 8 characters'; errEl.style.display='block'; return; }
  try {
    const r = await fetch('/api/v1/auth/accept-org-invite', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({token:'{token}',full_name,password})
    });
    if (!r.ok) { const d=await r.json(); errEl.textContent=d.detail||'Failed to accept invite'; errEl.style.display='block'; return; }
    window.location.href = 'https://app.procta.net/dashboard';
  } catch(e) { errEl.textContent='Network error'; errEl.style.display='block'; }
});
</script>
</body>
</html>
"""


@router.get("/org-invite/{token}")
async def get_org_invite_page(token: str, request: Request):
    """Serve the org invite acceptance page."""
    result = await _atable("org_invites").select("id,org_id,email,full_name,status,expires_at").eq("token", token).limit(1).execute()
    if not result.data:
        return HTMLResponse("<h1>Invalid or expired invitation link</h1>", status_code=404)
    invite = result.data[0]
    if invite["status"] != "pending":
        return HTMLResponse("<h1>This invitation has already been used</h1>", status_code=410)
    if invite.get("expires_at"):
        try:
            expires = datetime.fromisoformat(str(invite["expires_at"]).replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > expires:
                return HTMLResponse("<h1>This invitation has expired</h1>", status_code=410)
        except Exception:
            pass
    org_result = await _atable("organizations").select("name").eq("id", str(invite["org_id"])).limit(1).execute()
    org_name = org_result.data[0]["name"] if org_result.data else "an organization"
    page = _INVITE_PAGE.replace("{org_name}", _esc(org_name)).replace("{token}", token)
    return HTMLResponse(page)


@router.post("/api/v1/auth/accept-org-invite")
@limiter.limit("5/hour")
async def accept_org_invite(body: dict, request: Request):
    """Accept an org invite: create teacher account and join org."""
    token = (body.get("token") or "").strip()
    full_name = (body.get("full_name") or "").strip()
    password = (body.get("password") or "").strip()

    if not token:
        raise HTTPException(status_code=400, detail="Invalid token")
    if not full_name:
        raise HTTPException(status_code=400, detail="Full name is required")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    result = await _atable("org_invites").select("*").eq("token", token).eq("status", "pending").limit(1).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Invalid or expired invitation")
    invite = result.data[0]

    if invite.get("expires_at"):
        try:
            expires = datetime.fromisoformat(str(invite["expires_at"]).replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > expires:
                raise HTTPException(status_code=410, detail="Invitation has expired")
        except Exception:
            pass

    email = invite["email"].strip().lower()
    org_id = str(invite["org_id"])

    # Check if teacher already exists
    existing = await _atable("teachers").select("id").eq("email", email).execute()
    if existing.data:
        teacher = existing.data[0]
        if teacher.get("org_id"):
            raise HTTPException(status_code=409, detail="This email is already part of an organization")
        await _atable("teachers").update({"org_id": org_id, "org_role": "teacher"}).eq("id", teacher["id"]).execute()
        teacher["org_id"] = org_id
        teacher["org_role"] = "teacher"
    else:
        try:
            auth_resp = supabase.auth.admin.create_user({
                "email": email,
                "password": password,
                "email_confirm": True,
            })
            supabase_uid = auth_resp.user.id
        except Exception as e:
            err_msg = str(e).lower()
            if "already registered" in err_msg or "duplicate" in err_msg:
                raise HTTPException(status_code=409, detail="An account with this email already exists")
            _auth_log.error("[AcceptInvite] Supabase Auth error: %s", e)
            raise HTTPException(status_code=500, detail="Failed to create account")

        teacher_result = await _atable("teachers").insert({
            "email": email,
            "full_name": full_name,
            "supabase_uid": str(supabase_uid),
            "org_id": org_id,
            "org_role": "teacher",
        }).execute()
        teacher = teacher_result.data[0]

    await _atable("org_invites").update({"status": "accepted", "accepted_at": now_ist().isoformat()}).eq("id", invite["id"]).execute()

    access_token = issue_admin_token(teacher)
    _auth_log.info("[AcceptInvite] %s <%s> joined org %s", full_name, email, org_id)
    enqueue_job(send_new_account_notification_job, account_type="teacher", name=full_name, email=email)

    return {
        "access_token": access_token,
        "teacher_id": teacher["id"],
        "email": email,
        "full_name": full_name,
        "org_id": org_id,
        "org_role": "teacher",
    }


# ─── STUDENT DASHBOARD AUTH ──────────────────────────────────────

@router.get("/api/v1/student/account-exists")
@limiter.limit("120/minute")
async def student_account_exists(request: Request, email: str = ""):
    """Check if a student dashboard account exists for this email."""
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return {"exists": False}
    result = await _atable("student_accounts")\
        .select("id")\
        .eq("email", email)\
        .execute()
    count = len(result.data or [])
    return {"exists": count > 0}


@router.post("/api/v1/student/auth/signup")
@limiter.limit("5/hour")
async def student_signup(body: StudentSignupIn, request: Request):
    """Create a new student dashboard account via Supabase Auth.

    After creating the auth user + student_accounts row, we auto-link any
    pre-existing per-teacher `students` enrollments that match the email
    so the student immediately sees their upcoming exam(s) on first login.
    """
    email = body.email.strip().lower()
    name = body.full_name.strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")
    if not name:
        raise HTTPException(status_code=400, detail="Full name is required")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    existing = await _atable("student_accounts").select("id").eq("email", email).execute()
    if existing.data:
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    try:
        auth_resp = supabase.auth.admin.create_user({
            "email": email,
            "password": body.password,
            "email_confirm": True,
        })
        supabase_uid = auth_resp.user.id
    except Exception as e:
        err_msg = str(e).lower()
        if "already registered" in err_msg or "duplicate" in err_msg:
            raise HTTPException(status_code=409, detail="An account with this email already exists")
        _auth_log.error("[StudentSignup] Supabase Auth error: %s", e)
        raise HTTPException(status_code=500, detail="Failed to create account")

    try:
        result = await _atable("student_accounts").insert({
            "email":        email,
            "full_name":    name,
            "supabase_uid": str(supabase_uid),
        }).execute()
        account = result.data[0]
    except Exception as e:
        _auth_log.error("[StudentSignup] DB insert error: %s", e)
        # Roll back: delete the orphaned Supabase Auth user
        try:
            supabase.auth.admin.delete_user(str(supabase_uid))
            _auth_log.info("[StudentSignup] Rolled back Auth user %s", supabase_uid)
        except Exception as rollback_err:
            _auth_log.critical("[StudentSignup] Failed to rollback Auth user %s: %s", supabase_uid, rollback_err)
        raise HTTPException(status_code=500, detail="Failed to create student record")

    # Auto-link any existing enrollments by matching email (case-insensitive).
    try:
        await _atable("students")\
            .update({"account_id": account["id"]})\
            .eq("email", email)\
            .is_("account_id", "null")\
            .execute()
    except Exception as e:
        _auth_log.warning("[StudentSignup] Auto-link warning: %s", e)

    _auth_log.info("[StudentSignup] %s <%s> created", name, email)
    return {
        "account_id": account["id"],
        "email":      email,
        "full_name":  name,
    }


@router.post("/api/v1/student/auth/login")
@limiter.limit("120/minute")
async def student_login(body: StudentLoginIn, request: Request):
    email = body.email.strip().lower()
    try:
        auth_resp = supabase.auth.sign_in_with_password({
            "email": email,
            "password": body.password,
        })
    except Exception as e:
        _auth_log.warning("[StudentLogin] Auth error: %s", e)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    supabase_uid = str(auth_resp.user.id)
    account = await _get_student_account_by_uid(supabase_uid)
    if not account:
        raise HTTPException(
            status_code=403,
            detail="No student account found for this login. Please sign up first.")

    # Opportunistic auto-link on every login in case the student was
    # registered by a teacher AFTER they created their account.
    try:
        await _atable("students")\
            .update({"account_id": account["id"]})\
            .eq("email", email)\
            .is_("account_id", "null")\
            .execute()
    except Exception as e:
        logger.debug("Auto-link enrollments failed: %s", e)  # Non-fatal

    return {
        "access_token":  issue_student_auth_token(account),
        "refresh_token": auth_resp.session.refresh_token,
        "account": {
            "id":        account["id"],
            "email":     account["email"],
            "full_name": account["full_name"],
        },
    }


@router.get("/api/v1/student/auth/me")
async def student_me(request: Request):
    account = await require_student_account(request)
    return {
        "id":        account["id"],
        "email":     account["email"],
        "full_name": account["full_name"],
    }


@router.post("/api/v1/student/auth/refresh")
@limiter.limit("20/minute")
async def student_refresh(body: RefreshIn, request: Request):
    try:
        auth_resp = supabase.auth.refresh_session(body.refresh_token)
    except Exception as e:
        _auth_log.warning("[StudentRefresh] Error: %s", e)
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    if not auth_resp or not auth_resp.user or not auth_resp.session:
        raise HTTPException(status_code=401, detail="Invalid refresh response")

    account = await _get_student_account_by_uid(str(auth_resp.user.id))
    if not account:
        raise HTTPException(status_code=403, detail="Student account not found")

    return {
        "access_token":  issue_student_auth_token(account),
        "refresh_token": auth_resp.session.refresh_token,
    }


@router.get("/api/student/exams")
async def student_exams(request: Request):
    """Return all exams the authenticated student is enrolled in.

    Looks up the student account from the Bearer token, finds matching
    enrollments in the ``students`` table by email, then enriches each
    with exam_config details and session status.
    """
    account = await require_student_account(request)
    email = account["email"].strip().lower()

    # Find all enrollment rows matching this email.
    # The `students` table has: roll_number, teacher_id (no exam_id column
    # in the legacy schema — each teacher typically has one exam_config).
    enroll_result = await _atable("students").select(
        "roll_number", "teacher_id"
    ).eq("email", email).execute()
    enrollments = enroll_result.data or []
    if not enrollments:
        return {"exams": []}

    exams = []
    now = datetime.now(timezone.utc)

    for enr in enrollments:
        teacher_id = enr.get("teacher_id")
        if not teacher_id:
            continue
        teacher_id = str(teacher_id)

        # Get exam config — filter by teacher_id only.  Most deployments
        # have one config per teacher; if there are multiple we pick the
        # first one (the teacher's primary exam).
        config_result = await _atable("exam_config").select("*").eq(
            "teacher_id", teacher_id
        ).limit(1).execute()
        if not config_result.data:
            continue
        cfg = config_result.data[0]
        exam_id = cfg.get("exam_id")

        # Get teacher name
        teacher = await _get_teacher_by_id(teacher_id)
        teacher_name = teacher.get("full_name", "Teacher") if teacher else "Teacher"

        # Parse exam window
        starts_at = cfg.get("starts_at")
        ends_at = cfg.get("ends_at")
        duration = cfg.get("duration_minutes")

        # Check for existing session — query by roll_number + teacher_id
        # + status instead of constructing session_key (the renderer uses
        # a timestamp-based key, so we can't match on that).
        session_result = await _atable("exam_sessions").select(
            "status", "submitted_at"
        ).eq("teacher_id", teacher_id).eq(
            "roll_number", enr["roll_number"]
        ).eq("status", SessionStatus.IN_PROGRESS).limit(1).execute()
        session = session_result.data[0] if session_result.data else None

        # If no in_progress session, check for a completed one
        if not session:
            done_result = await _atable("exam_sessions").select(
                "status", "submitted_at"
            ).eq("teacher_id", teacher_id).eq(
                "roll_number", enr["roll_number"]
            ).order("created_at", desc=True).limit(1).execute()
            if done_result.data:
                session = done_result.data[0]

        # Compute status
        if session:
            st = (session.get("status") or "").lower()
            if st in (SessionStatus.COMPLETED, SessionStatus.SUBMITTED,
                      SessionStatus.FORCE_SUBMITTED):
                status = "completed"
            else:
                status = _exam_window_status(starts_at, ends_at, now, duration)
        else:
            status = _exam_window_status(starts_at, ends_at, now, duration)

        exams.append({
            "exam_title": cfg.get("exam_title") or cfg.get("title") or "Exam",
            "teacher_name": teacher_name,
            "roll_number": enr["roll_number"],
            "exam_id": exam_id,
            "teacher_id": teacher_id,
            "starts_at": starts_at,
            "ends_at": ends_at,
            "duration_minutes": duration,
            "access_code_required": bool(cfg.get("access_code", "").strip()),
            "status": status,
            "submitted_at": session.get("submitted_at") if session else None,
        })

    return {"exams": exams}


@router.get("/api/student/history")
async def student_history(request: Request):
    """Return the authenticated student's own exam history.

    Shows all completed exams with scores, risk levels, and violation
    counts across all teachers.
    """
    account = await require_student_account(request)
    email = account["email"].strip().lower()

    # Find all enrollment rows matching this email
    enrollments = (await _atable("students")
                   .select("roll_number,teacher_id,full_name")
                   .eq("email", email)
                   .execute()).data or []
    if not enrollments:
        return {"history": []}

    history = []
    for enr in enrollments:
        teacher_id = str(enr["teacher_id"])
        roll = enr["roll_number"]

        # Get completed sessions
        sessions = (await _atable("exam_sessions")
                    .select("session_key,exam_id,roll_number,full_name,email,"
                            "score,total,percentage,time_taken_secs,"
                            "status,started_at,submitted_at,risk_score")
                    .eq("roll_number", roll)
                    .eq("teacher_id", teacher_id)
                    .eq("status", SessionStatus.COMPLETED)
                    .order("submitted_at", desc=True)
                    .execute()).data or []

        for s in sessions:
            # Get exam title
            exam_title = ""
            if s.get("exam_id"):
                cfg_result = (await _atable("exam_config")
                              .select("exam_title")
                              .eq("exam_id", s["exam_id"])
                              .eq("teacher_id", teacher_id)
                              .limit(1)
                              .execute()).data or []
                if cfg_result:
                    exam_title = cfg_result[0].get("exam_title") or ""

            # Get teacher name
            teacher = await _get_teacher_by_id(teacher_id)
            teacher_name = teacher.get("full_name", "Teacher") if teacher else "Teacher"

            # Count violations
            viol_result = await _atable("violations")\
                          .select("id", count="exact")\
                          .eq("session_key", s["session_key"])\
                          .eq("teacher_id", teacher_id)\
                          .execute()
            viol_count = viol_result.count or 0

            history.append({
                "session_id": s["session_key"],
                "exam_title": exam_title or "Exam",
                "teacher_name": teacher_name,
                "roll_number": roll,
                "score": s.get("score", 0),
                "total": s.get("total", 0),
                "percentage": s.get("percentage", 0.0),
                "time_taken_secs": s.get("time_taken_secs", 0),
                "submitted_at": fmt_ist(s.get("submitted_at", "")),
                "violation_count": viol_count,
                "risk_score": s.get("risk_score"),
            })

    # Sort all history by submitted_at descending
    history.sort(key=lambda x: x.get("submitted_at", "") or "", reverse=True)

    return {"history": history}


def _exam_window_status(starts_at, ends_at, now, duration):
    """Determine exam status from time window."""
    if starts_at:
        try:
            if isinstance(starts_at, datetime):
                start_dt = starts_at
            else:
                start_dt = datetime.fromisoformat(
                    str(starts_at).replace("Z", "+00:00"))
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=timezone.utc)
        except Exception:
            start_dt = None  # Malformed timestamp — treat as unset
    else:
        start_dt = None

    if ends_at:
        try:
            if isinstance(ends_at, datetime):
                end_dt = ends_at
            else:
                end_dt = datetime.fromisoformat(
                    str(ends_at).replace("Z", "+00:00"))
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
        except Exception:
            end_dt = None  # Malformed timestamp — treat as unset
    else:
        end_dt = None

    # Compute end from duration if not set explicitly
    if end_dt is None and start_dt is not None and duration:
        end_dt = start_dt + timedelta(minutes=int(duration))

    if start_dt and end_dt:
        if now < start_dt:
            return "upcoming"
        elif now > end_dt:
            return "closed"
        else:
            return "open"

    if start_dt and now < start_dt:
        return "upcoming"
    if start_dt:
        return "open"

    return "open"  # no schedule = always open
