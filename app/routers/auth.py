from fastapi import APIRouter, HTTPException, Request
import json
import uuid as _uuid

from ..dependencies import (
    supabase,
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
)

router = APIRouter(prefix="")


@router.post("/api/v1/auth/signup")
@limiter.limit("5/hour")
async def teacher_signup(body: TeacherSignupIn, request: Request):
    """Create a new teacher account via Supabase Auth."""
    email = body.email.strip().lower()
    name = body.full_name.strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")
    if not name:
        raise HTTPException(status_code=400, detail="Full name is required")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    # Check if teacher already exists in our table
    existing = supabase.table("teachers").select("id").eq("email", email).execute()
    if existing.data:
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    try:
        # Create Supabase Auth user (email_confirm=True skips verification for v1)
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
        print(f"[TeacherSignup] Supabase Auth error: {e}")
        raise HTTPException(status_code=500, detail="Failed to create account")

    # Insert teacher record — if this fails, roll back the Auth user
    teacher_row = {
        "email": email,
        "full_name": name,
        "supabase_uid": str(supabase_uid),
    }
    try:
        result = supabase.table("teachers").insert(teacher_row).execute()
        teacher = result.data[0]
    except Exception as e:
        print(f"[TeacherSignup] DB insert error: {e}")
        # Roll back: delete the orphaned Supabase Auth user
        try:
            supabase.auth.admin.delete_user(str(supabase_uid))
            print(f"[TeacherSignup] Rolled back Auth user {supabase_uid}")
        except Exception as rollback_err:
            print(f"[TeacherSignup] CRITICAL: Failed to rollback Auth user {supabase_uid}: {rollback_err}")
        raise HTTPException(status_code=500, detail="Failed to create teacher record")

    # Create default exam_config for this teacher
    try:
        supabase.table("exam_config").insert({
            "exam_id": str(_uuid.uuid4()),
            "teacher_id": teacher["id"],
            "exam_title": "Exam",
            "duration_minutes": 60,
        }).execute()
    except Exception:
        pass  # Non-fatal — teacher can set this later

    print(f"[TeacherSignup] {name} <{email}> created")
    return {"teacher_id": teacher["id"], "email": email, "full_name": name}


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
        print(f"[TeacherLogin] Auth error: {e}")
        raise HTTPException(status_code=401, detail="Invalid email or password")

    supabase_uid = str(auth_resp.user.id)
    teacher = _get_teacher_by_uid(supabase_uid)
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
    teacher = require_admin(request)
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
        print(f"[TeacherRefresh] Error: {e}")
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    if not auth_resp or not auth_resp.user or not auth_resp.session:
        raise HTTPException(status_code=401, detail="Invalid refresh response")

    supabase_uid = str(auth_resp.user.id)
    teacher = _get_teacher_by_uid(supabase_uid)
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
        print(f"[PasswordReset] Error for {email}: {e}")
        # Don't reveal whether the email exists or not
    return {"status": "ok", "message": "If that email is registered, a reset link has been sent."}


# ─── STUDENT DASHBOARD AUTH ──────────────────────────────────────

@router.get("/api/v1/student/account-exists")
@limiter.limit("120/minute")
async def student_account_exists(request: Request, email: str = ""):
    """Check if a student dashboard account exists for this email."""
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return {"exists": False}
    result = supabase.table("student_accounts")\
        .select("id", count="exact")\
        .eq("email", email)\
        .execute()
    return {"exists": (result.count or 0) > 0}


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

    existing = supabase.table("student_accounts").select("id").eq("email", email).execute()
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
        print(f"[StudentSignup] Supabase Auth error: {e}")
        raise HTTPException(status_code=500, detail="Failed to create account")

    try:
        result = supabase.table("student_accounts").insert({
            "email":        email,
            "full_name":    name,
            "supabase_uid": str(supabase_uid),
        }).execute()
        account = result.data[0]
    except Exception as e:
        print(f"[StudentSignup] DB insert error: {e}")
        # Roll back: delete the orphaned Supabase Auth user
        try:
            supabase.auth.admin.delete_user(str(supabase_uid))
            print(f"[StudentSignup] Rolled back Auth user {supabase_uid}")
        except Exception as rollback_err:
            print(f"[StudentSignup] CRITICAL: Failed to rollback Auth user {supabase_uid}: {rollback_err}")
        raise HTTPException(status_code=500, detail="Failed to create student record")

    # Auto-link any existing enrollments by matching email (case-insensitive).
    try:
        supabase.table("students")\
            .update({"account_id": account["id"]})\
            .eq("email", email)\
            .is_("account_id", "null")\
            .execute()
    except Exception as e:
        print(f"[StudentSignup] Auto-link warning: {e}")

    print(f"[StudentSignup] {name} <{email}> created")
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
        print(f"[StudentLogin] Auth error: {e}")
        raise HTTPException(status_code=401, detail="Invalid email or password")

    supabase_uid = str(auth_resp.user.id)
    account = _get_student_account_by_uid(supabase_uid)
    if not account:
        raise HTTPException(
            status_code=403,
            detail="No student account found for this login. Please sign up first.")

    # Opportunistic auto-link on every login in case the student was
    # registered by a teacher AFTER they created their account.
    try:
        supabase.table("students")\
            .update({"account_id": account["id"]})\
            .eq("email", email)\
            .is_("account_id", "null")\
            .execute()
    except Exception:
        pass

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
    account = require_student_account(request)
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
        print(f"[StudentRefresh] Error: {e}")
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    if not auth_resp or not auth_resp.user or not auth_resp.session:
        raise HTTPException(status_code=401, detail="Invalid refresh response")

    account = _get_student_account_by_uid(str(auth_resp.user.id))
    if not account:
        raise HTTPException(status_code=403, detail="Student account not found")

    return {
        "access_token":  issue_student_auth_token(account),
        "refresh_token": auth_resp.session.refresh_token,
    }
