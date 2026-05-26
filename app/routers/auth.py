from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
import asyncio
import json
import logging
import os
import re
import time
_auth_log = logging.getLogger("auth")
import uuid as _uuid
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

from ..database import supabase, async_table as _atable, is_postgres_backend
from ..limiter import limiter
from ..models import TeacherSignupIn, TeacherLoginIn, RefreshIn, StudentSignupIn, StudentLoginIn, PasswordResetIn
from ..auth import (
    issue_admin_token, _get_teacher_by_id, _get_teacher_by_uid,
    issue_student_auth_token, _get_student_account_by_id, _get_student_account_by_uid,
    require_admin, require_student_account,
)
from ..utils import fmt_ist, now_ist
from ..models import SessionStatus
from ..constants import ALL_SIGNING_KEYS, PLANS, TRIAL_DAYS
from ..services.passwords import validate_password, PasswordError
from ..services.local_auth import (
    hash_password,
    issue_password_reset_token,
    issue_refresh_token,
    local_password_auth_enabled,
    new_auth_uid,
    supabase_auth_fallback_enabled,
    verify_password,
    verify_password_reset_token,
    verify_refresh_token,
)
from ..services.auth_lockout import check_lockout, record_failure, clear_failures
from ..services.auth_events import record as record_auth_event
from ..services.turnstile import verify_or_403
from ..auth.tokens import (
    _decode_token,
    clear_csrf_token,
    csrf_required_for_claims,
    issue_csrf_token,
    issue_email_verify_token,
    issue_reauth_token,
    verify_email_token,
)
from ..utils import _html_escape as _esc
from ..jobs import enqueue_job, send_new_account_notification_job

logger = logging.getLogger(__name__)

router = APIRouter(prefix="")


def _secure_cookie_enabled() -> bool:
    explicit = os.environ.get("COOKIE_SECURE", "").strip().lower()
    if explicit in {"0", "false", "no", "off"}:
        return False
    if explicit in {"1", "true", "yes", "on"}:
        return True
    env = (os.environ.get("APP_ENV") or os.environ.get("ENV") or "").lower()
    if env in {"prod", "production"}:
        return True
    public_url = (
        os.environ.get("APP_URL")
        or os.environ.get("INVITE_BASE_URL")
        or os.environ.get("PUBLIC_URL")
        or ""
    ).lower()
    return public_url.startswith("https://")


def _set_auth_cookie(response: JSONResponse, name: str, value: str, max_age_seconds: int) -> None:
    same_site = "strict" if name in {"procta_access", "procta_student_access"} else "lax"
    response.set_cookie(
        name,
        value,
        max_age=max_age_seconds,
        httponly=True,
        secure=_secure_cookie_enabled(),
        samesite=same_site,
        path="/",
    )


def _set_teacher_cookies(response: JSONResponse, access_token: str, refresh_token: str) -> None:
    from ..constants import ADMIN_TOKEN_TTL_MINUTES
    _set_auth_cookie(response, "procta_access", access_token, ADMIN_TOKEN_TTL_MINUTES * 60)
    _set_auth_cookie(response, "procta_refresh", refresh_token, 30 * 24 * 60 * 60)


def _set_student_cookies(response: JSONResponse, access_token: str, refresh_token: str) -> None:
    from ..constants import STUDENT_AUTH_TTL_MINUTES
    _set_auth_cookie(response, "procta_student_access", access_token, STUDENT_AUTH_TTL_MINUTES * 60)
    _set_auth_cookie(response, "procta_student_refresh", refresh_token, 30 * 24 * 60 * 60)


def _clear_teacher_cookies(response: JSONResponse) -> None:
    response.delete_cookie("procta_access", path="/")
    response.delete_cookie("procta_refresh", path="/")


def _clear_student_cookies(response: JSONResponse) -> None:
    response.delete_cookie("procta_student_access", path="/")
    response.delete_cookie("procta_student_refresh", path="/")


def _access_token_from_request(request: Request, cookie_name: str) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return request.cookies.get(cookie_name, "")


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower().strip())
    return s.strip("-") or "org"


async def _create_teacher_signup_postgres_tx(
    *,
    email: str,
    name: str,
    org_name: str,
    slug: str,
    supabase_uid: str,
    password_hash: str,
) -> tuple[dict, str, str]:
    """Create org, trial subscription, teacher, and default exam atomically.

    This is the real transactional signup path for the plain-Postgres/local-auth
    deployment. The Supabase/PostgREST deployment cannot provide a multi-table
    transaction through the REST adapter, so it still uses compensating cleanup.
    """
    from ..postgres_table import get_pool

    pool = await get_pool()
    org_id = str(_uuid.uuid4())
    teacher_id = str(_uuid.uuid4())
    subscription_id = str(_uuid.uuid4())
    default_exam_id = str(_uuid.uuid4())
    trial_end = datetime.now(timezone.utc) + timedelta(days=TRIAL_DAYS)
    password_changed_at = now_ist()

    async with pool.acquire() as conn:
        async with conn.transaction():
            # All four conn.execute / conn.fetchrow calls in this
            # transaction pass user-supplied values via $N positional
            # parameters. The hardcoded literals ('starter', 'trialing',
            # 'admin', 'local', 'Exam') are SQL constants, not
            # interpolated input. Semgrep's heuristic over-fires on
            # multi-line parameterized queries that also contain
            # literal text inside the VALUES clause — each call is
            # marked with `# nosemgrep: asyncpg-sqli` on the line
            # IMMEDIATELY preceding it so the suppression sticks.
            # nosemgrep: asyncpg-sqli
            org = await conn.fetchrow(
                """
                INSERT INTO organizations (id, name, slug, max_students)
                VALUES ($1, $2, $3, $4)
                RETURNING id, name, slug, max_students
                """,
                org_id,
                org_name,
                slug,
                PLANS["starter"]["students"],
            )
            if org is None:
                raise RuntimeError("organization insert returned no row")

            # nosemgrep: asyncpg-sqli
            await conn.execute(
                """
                INSERT INTO subscriptions (id, org_id, plan, status, trial_end)
                VALUES ($1, $2, 'starter', 'trialing', $3)
                """,
                subscription_id,
                org_id,
                trial_end,
            )

            # nosemgrep: asyncpg-sqli
            teacher = await conn.fetchrow(
                """
                INSERT INTO teachers (
                    id, email, full_name, supabase_uid, org_id, org_role,
                    password_hash, auth_provider, password_changed_at
                )
                VALUES ($1, $2, $3, $4, $5, 'admin', $6, 'local', $7)
                RETURNING id, email, full_name, supabase_uid, org_id, org_role,
                          password_hash, auth_provider, password_changed_at
                """,
                teacher_id,
                email,
                name,
                str(supabase_uid),
                org_id,
                password_hash,
                password_changed_at,
            )
            if teacher is None:
                raise RuntimeError("teacher insert returned no row")

            # nosemgrep: asyncpg-sqli
            await conn.execute(
                """
                INSERT INTO exam_config (
                    exam_id, teacher_id, exam_title, duration_minutes
                )
                VALUES ($1, $2, 'Exam', 60)
                """,
                default_exam_id,
                teacher_id,
            )

    return dict(teacher), org_id, default_exam_id


async def _get_teacher_by_email_for_auth(email: str) -> dict | None:
    result = await _atable("teachers").select(
        "id,email,full_name,org_id,org_role,password_hash,email_verified_at,password_changed_at,status,email_2fa_enabled_at"
    ).eq("email", email).limit(1).execute()
    if not result.data:
        return None
    # Centralised super-admin promotion — the master account becomes
    # org_role=superadmin regardless of DB-side role. Matches the helper
    # in app/auth/admin_auth.py so /login, /auth/me, require_admin, and
    # scope.resolve_scope() all see the same elevated role.
    from ..auth.admin_auth import _maybe_promote_super_admin
    return _maybe_promote_super_admin(result.data[0])


async def _get_student_by_email_for_auth(email: str) -> dict | None:
    result = await _atable("student_accounts").select(
        "id,email,full_name,password_hash,email_verified_at,password_changed_at"
    ).eq("email", email).limit(1).execute()
    return result.data[0] if result.data else None


def _stringify_pwc(value) -> str | None:
    """Normalise password_changed_at to a stable string for JWT embedding.

    DB returns timezone-aware datetime under postgres, ISO string under
    Supabase REST. We coerce both to ISO string so the token's `pwc`
    claim is a stable comparable value across backends.
    """
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


async def _issue_and_persist_refresh_token(
    user_id: str, kind: str, request: Request
) -> str:
    """Mint a refresh JWT AND record its jti in `refresh_tokens`.

    Returns just the token string for use in the response body. The
    server-side row is what lets us revoke later — without it the JWT
    is valid for its full 30-day TTL with no kill switch.
    """
    token, jti, exp = issue_refresh_token(user_id, kind)
    ip = request.client.host if request.client else None
    ua = (request.headers.get("user-agent") or "")[:500]
    await _atable("refresh_tokens").insert({
        "jti": jti,
        "user_id": str(user_id),
        "kind": kind,
        "issued_at": now_ist().isoformat(),
        "expires_at": exp.isoformat(),
        "ip": ip,
        "user_agent": ua,
    }).execute()
    return token


async def _verify_and_rotate_refresh_token(
    refresh_token: str, expected_kind: str, request: Request
) -> tuple[str, str]:
    """Verify a refresh token, rotate it, return (user_id, new_token).

    Rotation: the old jti is marked revoked + `replaced_by_jti = <new>`
    and a fresh row is inserted. This catches replay (a stolen old
    token still in flight is rejected on its next use), and lets a
    user see the rotation chain in audit if needed.

    Raises HTTPException(401) on any failure — caller doesn't need to
    do its own error handling.
    """
    verified = verify_refresh_token(refresh_token, expected_kind)
    if not verified:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    user_id, jti = verified

    # Look up the server-side row. The JWT signature being valid is
    # necessary but not sufficient — we ALSO need the jti to be present
    # and not revoked. A token that's been rotated (replaced_by_jti
    # set) is by definition revoked.
    row = await _atable("refresh_tokens").select(
        "jti,revoked_at,expires_at,user_id,kind"
    ).eq("jti", jti).limit(1).execute()
    if not row.data:
        # Either never issued (forged jti) or pruned. Reject.
        raise HTTPException(status_code=401, detail="Refresh token not recognised")
    rec = row.data[0]
    if rec.get("revoked_at"):
        # Replay attempt — the token was previously used and rotated.
        # A replay means the refresh-token family may be compromised:
        # revoke active descendants and access sessions for this user.
        now_iso = now_ist().isoformat()
        await _revoke_refresh_tokens_for_user(user_id, expected_kind)
        session_kind = "teacher" if expected_kind == "teacher" else "student"
        await _atable("auth_sessions").update({"revoked_at": now_iso})\
            .eq("user_id", str(user_id)).eq("user_kind", session_kind)\
            .is_("revoked_at", "null").execute()
        raise HTTPException(status_code=401, detail="Refresh token has been revoked")
    if str(rec.get("user_id")) != str(user_id) or rec.get("kind") != expected_kind:
        # Claim/DB mismatch — should not happen unless someone is
        # tampering. Reject without leaking which check failed.
        raise HTTPException(status_code=401, detail="Refresh token invalid")

    # Mint replacement, then revoke the old token with a pointer to the
    # successor. This preserves the rotation chain for audit/replay
    # investigation; the new token row itself has no replacement yet.
    now_iso = now_ist().isoformat()
    new_token, new_jti, new_exp = issue_refresh_token(user_id, expected_kind)
    await _atable("refresh_tokens").update({
        "revoked_at": now_iso,
        "replaced_by_jti": new_jti,
    }).eq("jti", jti).execute()

    ip = request.client.host if request.client else None
    ua = (request.headers.get("user-agent") or "")[:500]
    await _atable("refresh_tokens").insert({
        "jti": new_jti,
        "user_id": str(user_id),
        "kind": expected_kind,
        "issued_at": now_iso,
        "expires_at": new_exp.isoformat(),
        "ip": ip,
        "user_agent": ua,
    }).execute()
    return user_id, new_token


async def _revoke_refresh_tokens_for_user(
    user_id: str, kind: str, *, except_jti: str | None = None
) -> None:
    """Bulk-revoke active refresh tokens for a user.

    Called from the existing access-token revocation endpoints so
    "sign out other devices" actually kills the refresh path too,
    not just the 12h access window.
    """
    q = _atable("refresh_tokens").update({"revoked_at": now_ist().isoformat()})\
        .eq("user_id", str(user_id)).eq("kind", kind).is_("revoked_at", "null")
    if except_jti:
        q = q.neq("jti", except_jti)
    await q.execute()


@router.post("/api/v1/auth/signup")
@limiter.limit("5/hour")
async def teacher_signup(body: TeacherSignupIn, request: Request):
    """Create a new teacher account with org and trial subscription."""
    # Turnstile CAPTCHA — runs first so bots never reach the
    # Supabase Auth admin API (which has its own per-project quota).
    await verify_or_403(request, body.captcha_token)

    email = body.email.strip().lower()
    name = body.full_name.strip()
    org_name = (body.org_name or "").strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")
    if not name:
        raise HTTPException(status_code=400, detail="Full name is required")
    if not org_name:
        raise HTTPException(status_code=400, detail="Organization name is required")
    try:
        validate_password(body.password)
    except PasswordError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Check if teacher already exists — use a generic message to avoid
    # leaking whether a particular email address is registered (L-1).
    existing = await _atable("teachers").select("id").eq("email", email).execute()
    if existing.data:
        raise HTTPException(status_code=409, detail="If an account exists with this email, you can sign in or reset your password.")

    # Check if org slug already exists
    slug = _slugify(org_name)
    org_exists = await _atable("organizations").select("id,name").eq("slug", slug).execute()
    if org_exists.data:
        raise HTTPException(
            status_code=409,
            detail=f"'{org_name}' is already registered. Ask your admin for an invite."
        )

    auth_resp = None
    password_hash = None
    auth_provider = "supabase"
    if local_password_auth_enabled():
        supabase_uid = new_auth_uid()
        password_hash = await hash_password(body.password)
        auth_provider = "local"
    else:
        try:
            auth_resp = supabase.auth.admin.create_user({
                "email": email,
                "password": body.password,
                "email_confirm": False,
            })
            supabase_uid = auth_resp.user.id
        except Exception as e:
            err_msg = str(e).lower()
            if "already registered" in err_msg or "duplicate" in err_msg:
                raise HTTPException(status_code=409, detail="If an account exists with this email, you can sign in or reset your password.")
            _auth_log.error("[TeacherSignup] Supabase Auth error: %s", e)
            raise HTTPException(status_code=500, detail="Failed to create account")

    if local_password_auth_enabled() and is_postgres_backend():
        try:
            teacher, org_id, _default_exam_id = await _create_teacher_signup_postgres_tx(
                email=email,
                name=name,
                org_name=org_name,
                slug=slug,
                supabase_uid=str(supabase_uid),
                password_hash=password_hash or "",
            )
        except Exception as e:
            _auth_log.error("[TeacherSignup] Postgres transaction failed: %s", e)
            err_lower = str(e).lower()
            if (
                "duplicate key" in err_lower
                or "unique constraint" in err_lower
                or "already exists" in err_lower
            ):
                raise HTTPException(
                    status_code=409,
                    detail="If an account exists with this email, you can sign in or reset your password.",
                )
            raise HTTPException(status_code=500, detail="Failed to create account")

        try:
            from ..services.demo_exam import seed_demo_exam
            await seed_demo_exam(str(teacher["id"]), _atable=_atable)
        except Exception as demo_err:
            _auth_log.warning("[TeacherSignup] demo seed failed (non-fatal): %s", demo_err)

        _auth_log.info("[TeacherSignup] %s <%s> created (org=%s)", name, email, org_name)
        await record_auth_event("signup", request, "teacher", teacher["id"], email)
        enqueue_job(send_new_account_notification_job,
                    account_type="teacher", name=name, email=email)

        # Issue email verification
        from ..emailer import send_email_verification
        from ..invites import _get_invite_base_url
        vtoken = issue_email_verify_token(teacher["id"], email, "teacher")
        base = _get_invite_base_url()
        send_email_verification(email, name, f"{base}/verify-email?token={vtoken}")

        return {
            "teacher_id":    teacher["id"],
            "email":         email,
            "full_name":     name,
            "org_id":        str(org_id),
            "org_name":      org_name,
            "org_role":      teacher.get("org_role", "admin"),
            "status":        "pending_verification",
        }

    # Create org, subscription, teacher — transactional rollback
    org_id = None
    teacher_id = None
    default_exam_id = None
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
        teacher_row = {
            "email": email,
            "full_name": name,
            "supabase_uid": str(supabase_uid),
            "org_id": str(org_id),
            "org_role": "admin",
        }
        if local_password_auth_enabled():
            teacher_row.update({
                "password_hash": password_hash,
                "auth_provider": auth_provider,
                "password_changed_at": now_ist().isoformat(),
            })
        teacher_result = await _atable("teachers").insert(teacher_row).execute()
        teacher = teacher_result.data[0]
        teacher_id = teacher["id"]

        # Create default exam_config
        default_exam_id = str(_uuid.uuid4())
        await _atable("exam_config").insert({
            "exam_id": default_exam_id,
            "teacher_id": teacher["id"],
            "exam_title": "Exam",
            "duration_minutes": 60,
        }).execute()

        # Seed demo exam with sample questions
        try:
            from ..services.demo_exam import seed_demo_exam
            await seed_demo_exam(str(teacher["id"]), _atable=_atable)
        except Exception as demo_err:
            _auth_log.warning("[TeacherSignup] demo seed failed (non-fatal): %s", demo_err)
    except Exception as e:
        _auth_log.error("[TeacherSignup] DB error: %s", e)
        # Rollback: delete Supabase auth user + orphaned org/subscription rows
        if auth_resp is not None:
            try:
                supabase.auth.admin.delete_user(str(supabase_uid))
            except Exception as rollback_err:
                _auth_log.critical("[TeacherSignup] Rollback (auth user) failed: %s", rollback_err)
        if default_exam_id is not None:
            try:
                await _atable("exam_config").delete().eq("exam_id", str(default_exam_id)).execute()
            except Exception as rollback_err:
                _auth_log.critical("[TeacherSignup] Rollback (exam_config) failed for exam=%s: %s", default_exam_id, rollback_err)
        if teacher_id is not None:
            try:
                await _atable("teachers").delete().eq("id", str(teacher_id)).execute()
            except Exception as rollback_err:
                _auth_log.critical("[TeacherSignup] Rollback (teacher) failed for teacher=%s: %s", teacher_id, rollback_err)
        if org_id is not None:
            try:
                await _atable("subscriptions").delete().eq("org_id", str(org_id)).execute()
                await _atable("organizations").delete().eq("id", str(org_id)).execute()
            except Exception as rollback_err:
                _auth_log.critical("[TeacherSignup] Rollback (org/sub) failed for org=%s: %s", org_id, rollback_err)
        # Detect race-condition duplicate-key violations and return 409
        # so the second concurrent request gets a meaningful error instead
        # of a 500 (Postgres raises "duplicate key value violates unique constraint").
        err_lower = str(e).lower()
        if "duplicate key" in err_lower or "unique constraint" in err_lower or "already exists" in err_lower:
            raise HTTPException(status_code=409, detail="If an account exists with this email, you can sign in or reset your password.")
        raise HTTPException(status_code=500, detail="Failed to create account")

    _auth_log.info("[TeacherSignup] %s <%s> created (org=%s)", name, email, org_name)

    await record_auth_event("signup", request, "teacher", teacher["id"], email)

    enqueue_job(send_new_account_notification_job,
                account_type="teacher", name=name, email=email)

    # Issue email verification
    from ..emailer import send_email_verification
    from ..invites import _get_invite_base_url
    vtoken = issue_email_verify_token(teacher["id"], email, "teacher")
    base = _get_invite_base_url()
    send_email_verification(email, name, f"{base}/verify-email?token={vtoken}")

    return {
        "teacher_id":    teacher["id"],
        "email":         email,
        "full_name":     name,
        "org_id":        str(org_id),
        "org_name":      org_name,
        "org_role":      "admin",
        "status":        "pending_verification",
    }


@router.post("/api/v1/auth/login")
@limiter.limit("10/minute")
async def teacher_login(body: TeacherLoginIn, request: Request):
    """Log in a teacher via Supabase Auth, return JWT tokens."""
    # CAPTCHA before any expensive auth work
    await verify_or_403(request, body.captcha_token)

    email = body.email.strip().lower()

    # Lockout check
    locked, retry_after = await check_lockout("teacher", email)
    if locked:
        await record_auth_event("login_failed", request, "teacher", "", email, {"reason": "locked_out"})
        # Use a generic message — don't leak exact remaining seconds (L-2).
        raise HTTPException(
            status_code=429,
            detail="Too many failed attempts. Please wait a few minutes and try again."
        )

    auth_resp = None
    teacher = None
    if local_password_auth_enabled():
        teacher = await _get_teacher_by_email_for_auth(email)
        if teacher and teacher.get("password_hash"):
            if not await verify_password(body.password, teacher.get("password_hash")):
                await record_failure("teacher", email)
                await record_auth_event("login_failed", request, "teacher", "", email)
                raise HTTPException(status_code=401, detail="Invalid email or password")
        elif not supabase_auth_fallback_enabled():
            await record_failure("teacher", email)
            await record_auth_event("login_failed", request, "teacher", "", email)
            raise HTTPException(status_code=401, detail="Invalid email or password")
        else:
            teacher = None

    if teacher is None:
        try:
            auth_resp = supabase.auth.sign_in_with_password({
                "email": email,
                "password": body.password,
            })
        except Exception as e:
            await record_failure("teacher", email)
            await record_auth_event("login_failed", request, "teacher", "", email)
            _auth_log.warning("[TeacherLogin] Auth error: %s", e)
            raise HTTPException(status_code=401, detail="Invalid email or password")

        supabase_uid = str(auth_resp.user.id)
        teacher = await _get_teacher_by_uid(supabase_uid)
        if not teacher:
            raise HTTPException(status_code=403, detail="Teacher account not found. Please sign up first.")

    await clear_failures("teacher", email)

    # Email verification check — auto-verify existing accounts (pre-feature)
    # so old users aren't locked out. New signups must verify.
    if not teacher.get("email_verified_at"):
        if (teacher.get("status") or "") == "pending_verification":
            await record_auth_event("login_failed", request, "teacher", teacher["id"], email, {"reason": "email_unverified"})
            return JSONResponse(
                status_code=403,
                content={
                    "error": "EMAIL_UNVERIFIED",
                    "message": "Please verify your email before logging in.",
                    "email": email,
                },
            )
        await _atable("teachers").update({
            "email_verified_at": now_ist().isoformat(),
        }).eq("id", teacher["id"]).execute()
        _auth_log.info("[TeacherLogin] Auto-verified existing account %s <%s>", teacher.get("full_name", ""), email)
        await record_auth_event("email_verified", request, "teacher", teacher["id"], email)

    if teacher.get("email_2fa_enabled_at"):
        # Email-OTP 2FA (replaced TOTP/Google Authenticator on 2026-05-23).
        # First call (no code yet): generate a fresh OTP, email it, return
        # 401 EMAIL_2FA_REQUIRED. The browser shows a code-input UI and
        # re-POSTs with the same email/password + the email_otp_code.
        from ..services.email_otp import issue as otp_issue, verify as otp_verify
        from ..emailer import send_2fa_otp_email

        if not body.email_otp_code:
            code = await otp_issue("teacher", str(teacher["id"]), "2fa_login")
            try:
                send_2fa_otp_email(email, teacher.get("full_name", ""), code)
            except Exception as e:
                # Sending failed — surface a clean error so the user doesn't
                # sit forever waiting for an email that never comes.
                _auth_log.error("[TeacherLogin] 2FA email send failed for %s: %s", email, e)
                raise HTTPException(status_code=502, detail="Could not send 2FA code. Please try again.")
            await record_auth_event("login_failed", request, "teacher", teacher["id"], email, {"reason": "email_2fa_required"})
            return JSONResponse(
                status_code=401,
                content={
                    "error": "EMAIL_2FA_REQUIRED",
                    "message": "We sent a 6-digit code to your email. Enter it to finish signing in.",
                },
            )
        if not await otp_verify("teacher", str(teacher["id"]), "2fa_login", body.email_otp_code.strip()):
            await record_failure("teacher", email)
            await record_auth_event("login_failed", request, "teacher", teacher["id"], email, {"reason": "email_2fa_invalid"})
            raise HTTPException(status_code=401, detail="Invalid or expired 2FA code")

    await record_auth_event("login_success", request, "teacher", teacher["id"], email)

    # Suspicious-login email — non-blocking heads-up if this looks like
    # a new device/location vs the user's last 30 days. Fire-and-forget
    # so we never delay the login response on auth_events lookup + SMTP.
    try:
        import asyncio as _asyncio
        from ..services.suspicious_login import check_and_notify as _sus_check
        _asyncio.create_task(_sus_check(
            user_kind="teacher",
            user_id=teacher["id"],
            user_email=email,
            user_name=teacher.get("full_name", ""),
            request_ip=(request.client.host if request.client else ""),
            user_agent=request.headers.get("user-agent", ""),
        ))
    except Exception:
        pass  # never block login on this

    access_token = issue_admin_token(teacher)
    # Record session
    try:
        from ..constants import ADMIN_SIGNING_KEYS
        claims = _decode_token(access_token, ADMIN_SIGNING_KEYS)
        jti = claims.get("jti", "")
        if jti:
            ip = request.client.host if request.client else ""
            ua = request.headers.get("user-agent", "") if request else ""
            await _atable("auth_sessions").insert({
                "jti": jti, "user_kind": "teacher", "user_id": teacher["id"],
                "ip": ip, "user_agent": ua,
            }).execute()
    except Exception:
        _auth_log.warning("auth: auth_sessions insert failed on login", exc_info=True)

    # Always issue our own persisted refresh token so the revocation table
    # covers both Supabase-auth and local-auth sessions equally.
    # (Previously the Supabase path handed out a raw Supabase token that we
    # couldn't revoke server-side.)
    refresh_tok = await _issue_and_persist_refresh_token(teacher["id"], "teacher", request)
    # `org_role` is already promoted to 'superadmin' for the master email
    # by issue_admin_token() (admin_auth.py:119) before we get here, so we
    # just need to surface the resolved values to the client. Without these
    # fields, the React dashboard (App.jsx) defaults role to 'teacher' and
    # admins/superadmins see the wrong tab matrix.
    response = JSONResponse({
        "access_token": access_token,
        "refresh_token": refresh_tok,
        "teacher": {
            "id": teacher["id"],
            "email": teacher["email"],
            "full_name": teacher["full_name"],
            "org_id": teacher.get("org_id"),
            "org_role": teacher.get("org_role", "teacher"),
            "email_verified_at": teacher.get("email_verified_at"),
        },
    })
    _set_teacher_cookies(response, access_token, refresh_tok)
    return response


@router.get("/api/v1/auth/me")
@limiter.limit("30/minute")
async def teacher_me(request: Request):
    """Get current teacher profile from Bearer token. Shape matches the
    `teacher` object returned by /login so the React dashboard can refresh
    role state on every page load without remembering whether it has the
    login response or the /me response."""
    teacher = await require_admin(request)
    return {
        "id": teacher["id"],
        "email": teacher["email"],
        "full_name": teacher["full_name"],
        "org_id": teacher.get("org_id"),
        "org_role": teacher.get("org_role", "teacher"),
        "email_verified_at": teacher.get("email_verified_at"),
    }


@router.get("/api/v1/auth/csrf")
@limiter.limit("60/minute")
async def issue_csrf(request: Request):
    """Issue an independent server-side CSRF token for browser mutations."""
    token = _access_token_from_request(request, "procta_access") or request.cookies.get("procta_student_access", "")
    if not token:
        raise HTTPException(status_code=401, detail="Missing auth token")
    try:
        claims = _decode_token(token, ALL_SIGNING_KEYS)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if not csrf_required_for_claims(claims):
        raise HTTPException(status_code=403, detail="CSRF token not available for this token type")
    csrf_token = issue_csrf_token(claims)
    if not csrf_token:
        raise HTTPException(status_code=400, detail="Token cannot receive a CSRF secret")
    return {"csrf_token": csrf_token}


@router.post("/api/v1/auth/refresh")
@limiter.limit("20/minute")
async def teacher_refresh(request: Request, body: RefreshIn | None = None):
    """Refresh an expired teacher access token via Supabase refresh token.

    The Supabase refresh token is the only credential the client retains
    long-term; we re-validate it via Supabase, look up the teacher, and
    issue a fresh HS256 admin token signed by us.
    """
    # All auth paths now issue our own persisted refresh tokens, so always
    # use the rotation logic (revocation table + replay detection).
    refresh_token = (body.refresh_token if body else "") or request.cookies.get("procta_refresh", "")
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Missing refresh token")
    teacher_id, new_refresh = await _verify_and_rotate_refresh_token(
        refresh_token, "teacher", request
    )
    teacher = await _get_teacher_by_id(teacher_id)
    if not teacher:
        raise HTTPException(status_code=403, detail="Teacher account not found")
    access_token = issue_admin_token(teacher)
    response = JSONResponse({
        "access_token": access_token,
        "refresh_token": new_refresh,
    })
    _set_teacher_cookies(response, access_token, new_refresh)
    return response


@router.post("/api/v1/auth/password-reset")
@limiter.limit("3/minute")
async def teacher_password_reset(body: PasswordResetIn, request: Request):
    """Send a password reset email."""
    started = time.monotonic()
    # CAPTCHA — password-reset is a common email-bombing vector
    await verify_or_403(request, body.captcha_token)

    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")
    try:
        if local_password_auth_enabled():
            user = await _get_teacher_by_email_for_auth(email)
            if user:
                from ..emailer import send_password_reset_email
                from ..invites import _get_invite_base_url
                token = issue_password_reset_token(
                    user["id"], email, "teacher",
                    password_changed_at=_stringify_pwc(user.get("password_changed_at")),
                )
                base = _get_invite_base_url()
                send_password_reset_email(
                    email,
                    user.get("full_name", ""),
                    f"{base}/reset-password?token={token}",
                )
        else:
            try:
                supabase.auth.reset_password_for_email(email)
            except Exception:
                _auth_log.warning("[PasswordReset] Supabase reset email failed")
                # Don't reveal whether the email exists or not
    finally:
        await asyncio.sleep(max(0.0, 0.35 - (time.monotonic() - started)))
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
      <input type="password" id="password" name="password" required minlength="10" placeholder="At least 10 characters">
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
  if (password.length < 10) { errEl.textContent='Password must be at least 10 characters'; errEl.style.display='block'; return; }
  try {
    const r = await fetch('/api/v1/auth/accept-org-invite', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({token:'{token}',full_name,password})
    });
    if (!r.ok) { const d=await r.json(); errEl.textContent=d.detail||'Failed to accept invite'; errEl.style.display='block'; return; }
    const d = await r.json().catch(()=>({}));
    errEl.style.display='block';
    errEl.style.background='#ecfdf5';
    errEl.style.color='#065f46';
    errEl.textContent=d.message||'Invitation accepted. Check your email to verify before signing in.';
  } catch(e) { errEl.textContent='Network error'; errEl.style.display='block'; }
});
</script>
</body>
</html>
"""


@router.get("/org-invite/{token}")
@limiter.limit("30/minute")
async def get_org_invite_page(token: str, request: Request):
    """Serve the org invite acceptance page.

    Looks up by SHA-256(token) since 2026-05-23 — see migrations/
    phase69_invite_token_hash.sql + audit M13. The raw token never
    needs to leave the URL; we hash on every request to compare.
    """
    import hashlib as _hl
    token_hash = _hl.sha256(token.encode("utf-8")).hexdigest()
    result = await _atable("org_invites").select("id,org_id,email,full_name,status,expires_at").eq("token_hash", token_hash).limit(1).execute()
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
            _auth_log.debug("auth: invite expires parse failed", exc_info=True)
    org_result = await _atable("organizations").select("name").eq("id", str(invite["org_id"])).limit(1).execute()
    org_name = org_result.data[0]["name"] if org_result.data else "an organization"
    page = _INVITE_PAGE.replace("{org_name}", _esc(org_name)).replace("{token}", token)
    return HTMLResponse(page)


@router.post("/api/v1/auth/accept-org-invite")
@limiter.limit("5/hour")
async def accept_org_invite(body: dict, request: Request):
    """Accept an org invite and defer org membership until email verification."""
    token = (body.get("token") or "").strip()
    full_name = (body.get("full_name") or "").strip()
    password = (body.get("password") or "").strip()

    if not token:
        raise HTTPException(status_code=400, detail="Invalid token")
    if not full_name:
        raise HTTPException(status_code=400, detail="Full name is required")
    try:
        validate_password(password)
    except PasswordError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Hash-then-lookup (audit M13) so a DB compromise can't surface
    # usable invite links.
    import hashlib as _hl
    token_hash = _hl.sha256(token.encode("utf-8")).hexdigest()
    result = await _atable("org_invites").select("*").eq("token_hash", token_hash).eq("status", "pending").limit(1).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Invalid or expired invitation")
    invite = result.data[0]

    if invite.get("expires_at"):
        try:
            expires = datetime.fromisoformat(str(invite["expires_at"]).replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > expires:
                raise HTTPException(status_code=410, detail="Invitation has expired")
        except Exception:
            _auth_log.debug("auth: invite expires parse failed", exc_info=True)

    email = invite["email"].strip().lower()
    org_id = str(invite["org_id"])

    # Check if teacher already exists
    existing = await _atable("teachers").select("id,org_id,org_role,full_name,email,email_verified_at").eq("email", email).execute()
    if existing.data:
        teacher = existing.data[0]
        if teacher.get("org_id"):
            raise HTTPException(status_code=409, detail="This email is already part of an organization")
        update_fields = {"full_name": full_name, "status": "pending_verification"}
        if local_password_auth_enabled() and not teacher.get("email_verified_at"):
            update_fields.update({
                "password_hash": await hash_password(password),
                "auth_provider": "local",
                "password_changed_at": now_ist().isoformat(),
            })
        await _atable("teachers").update(update_fields).eq("id", teacher["id"]).execute()
        teacher.update(update_fields)
    else:
        password_hash = None
        auth_provider = "supabase"
        if local_password_auth_enabled():
            supabase_uid = new_auth_uid()
            password_hash = await hash_password(password)
            auth_provider = "local"
        else:
            try:
                auth_resp = supabase.auth.admin.create_user({
                    "email": email,
                    "password": password,
                    "email_confirm": False,
                })
                supabase_uid = auth_resp.user.id
            except Exception as e:
                err_msg = str(e).lower()
                if "already registered" in err_msg or "duplicate" in err_msg:
                    raise HTTPException(status_code=409, detail="If an account exists with this email, you can sign in or reset your password.")
                _auth_log.error("[AcceptInvite] Supabase Auth error: %s", e)
                raise HTTPException(status_code=500, detail="Failed to create account")

        teacher_row = {
            "email": email,
            "full_name": full_name,
            "supabase_uid": str(supabase_uid),
            "org_role": "teacher",
            "status": "pending_verification",
        }
        if local_password_auth_enabled():
            teacher_row.update({
                "password_hash": password_hash,
                "auth_provider": auth_provider,
                "password_changed_at": now_ist().isoformat(),
            })
        teacher_result = await _atable("teachers").insert(teacher_row).execute()
        teacher = teacher_result.data[0]

    resolved_name = teacher.get("full_name") or body.get("full_name", full_name)
    await _atable("org_invites").update({"status": "pending_verification"}).eq("id", invite["id"]).execute()

    vtoken = issue_email_verify_token(teacher["id"], email, "teacher")
    from ..emailer import send_email_verification
    from ..invites import _get_invite_base_url
    base = _get_invite_base_url()
    send_email_verification(email, resolved_name, f"{base}/verify-email?token={vtoken}")
    _auth_log.info("[AcceptInvite] %s <%s> pending verification for org %s", resolved_name, email, org_id)
    enqueue_job(send_new_account_notification_job, account_type="teacher", name=resolved_name, email=email)

    return {
        "teacher_id": teacher["id"],
        "email": email,
        "full_name": resolved_name,
        "org_id": None,
        "org_role": "teacher",
        "email_verification_required": True,
        "message": "Invitation accepted. Check your email to verify before the account is added to the organization.",
    }


# ─── STUDENT DASHBOARD AUTH ──────────────────────────────────────

@router.get("/api/v1/student/account-exists")
@limiter.limit("10/minute")
async def student_account_exists(request: Request, email: str = ""):
    """Compatibility shim that does not reveal account existence.

    Older registration pages used this endpoint to hide password fields for
    existing accounts. That leaked a direct student-email oracle. Keep the
    route so old clients do not break, but always return the same answer.
    The signup endpoint remains idempotent enough for existing-account races:
    callers can treat 409 as "account already exists".
    """
    return {"exists": False}


@router.post("/api/v1/student/auth/signup")
@limiter.limit("5/hour")
async def student_signup(body: StudentSignupIn, request: Request):
    """Create a new student dashboard account via Supabase Auth.

    After creating the auth user + student_accounts row, we auto-link any
    pre-existing per-teacher `students` enrollments that match the email
    so the student immediately sees their upcoming exam(s) on first login.
    """
    # P2.8: bot-resistant signup. Teacher signup, password reset, OTP
    # request and login already gate on verify_or_403; student signup
    # was the last unprotected auth surface. Sandbox/no-secret mode
    # passes through (matches local-dev workflow).
    await verify_or_403(request, body.captcha_token)
    email = body.email.strip().lower()
    name = body.full_name.strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")
    if not name:
        raise HTTPException(status_code=400, detail="Full name is required")
    try:
        validate_password(body.password)
    except PasswordError as e:
        raise HTTPException(status_code=400, detail=str(e))

    existing = await _atable("student_accounts").select("id").eq("email", email).execute()
    if existing.data:
        raise HTTPException(status_code=409, detail="If an account exists with this email, you can sign in or reset your password.")

    auth_resp = None
    password_hash = None
    auth_provider = "supabase"
    if local_password_auth_enabled():
        supabase_uid = new_auth_uid()
        password_hash = await hash_password(body.password)
        auth_provider = "local"
    else:
        try:
            auth_resp = supabase.auth.admin.create_user({
                "email": email,
                "password": body.password,
                "email_confirm": False,
            })
            supabase_uid = auth_resp.user.id
        except Exception as e:
            err_msg = str(e).lower()
            if "already registered" in err_msg or "duplicate" in err_msg:
                raise HTTPException(status_code=409, detail="If an account exists with this email, you can sign in or reset your password.")
            _auth_log.error("[StudentSignup] Supabase Auth error: %s", e)
            raise HTTPException(status_code=500, detail="Failed to create account")

    try:
        account_row = {
            "email":        email,
            "full_name":    name,
            "supabase_uid": str(supabase_uid),
        }
        if local_password_auth_enabled():
            account_row.update({
                "password_hash": password_hash,
                "auth_provider": auth_provider,
                "password_changed_at": now_ist().isoformat(),
            })
        result = await _atable("student_accounts").insert(account_row).execute()
        account = result.data[0]
    except Exception as e:
        _auth_log.error("[StudentSignup] DB insert error: %s", e)
        # Roll back: delete the orphaned Supabase Auth user
        if auth_resp is not None:
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
@limiter.limit("10/minute")
async def student_login(body: StudentLoginIn, request: Request):
    await verify_or_403(request, body.captcha_token)
    email = body.email.strip().lower()
    locked, retry_after = await check_lockout("student", email)
    if locked:
        await record_auth_event("login_failed", request, "student_account", "", email, {"reason": "locked_out"})
        raise HTTPException(
            status_code=429,
            detail="Too many failed attempts. Please wait a few minutes and try again.",
        )

    auth_resp = None
    account = None
    if local_password_auth_enabled():
        account = await _get_student_by_email_for_auth(email)
        if account and account.get("password_hash"):
            if not await verify_password(body.password, account.get("password_hash")):
                await record_failure("student", email)
                await record_auth_event("login_failed", request, "student_account", "", email)
                raise HTTPException(status_code=401, detail="Invalid email or password")
        elif not supabase_auth_fallback_enabled():
            await record_failure("student", email)
            await record_auth_event("login_failed", request, "student_account", "", email)
            raise HTTPException(status_code=401, detail="Invalid email or password")
        else:
            account = None

    if account is None:
        try:
            auth_resp = supabase.auth.sign_in_with_password({
                "email": email,
                "password": body.password,
            })
        except Exception as e:
            _auth_log.warning("[StudentLogin] Auth error: %s", e)
            await record_failure("student", email)
            await record_auth_event("login_failed", request, "student_account", "", email)
            raise HTTPException(status_code=401, detail="Invalid email or password")

        supabase_uid = str(auth_resp.user.id)
        account = await _get_student_account_by_uid(supabase_uid)
        if not account:
            await record_failure("student", email)
            await record_auth_event("login_failed", request, "student_account", "", email, {"reason": "account_missing"})
            raise HTTPException(
                status_code=403,
                detail="No student account found for this login. Please sign up first.")

    await clear_failures("student", email)

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

    access_token = issue_student_auth_token(account)
    try:
        from ..constants import STUDENT_SIGNING_KEYS
        claims = _decode_token(access_token, STUDENT_SIGNING_KEYS)
        jti = claims.get("jti", "")
        if jti:
            await _atable("auth_sessions").insert({
                "jti": jti,
                "user_kind": "student_account",
                "user_id": account["id"],
                "ip": request.client.host if request.client else "",
                "user_agent": request.headers.get("user-agent", ""),
            }).execute()
    except Exception as e:
        _auth_log.warning("[StudentLogin] session record failed: %s", e)

    await record_auth_event("login_success", request, "student_account", account["id"], email)

    # Always issue our own persisted refresh token (revocable, replay-protected).
    refresh_tok = await _issue_and_persist_refresh_token(account["id"], "student", request)
    response = JSONResponse({
        "access_token":  access_token,
        "refresh_token": refresh_tok,
        "account": {
            "id":        account["id"],
            "email":     account["email"],
            "full_name": account["full_name"],
        },
    })
    _set_student_cookies(response, access_token, refresh_tok)
    return response


@router.get("/api/v1/student/auth/me")
@limiter.limit("30/minute")
async def student_me(request: Request):
    account = await require_student_account(request)
    return {
        "id":        account["id"],
        "email":     account["email"],
        "full_name": account["full_name"],
    }


@router.post("/api/v1/student/auth/refresh")
@limiter.limit("20/minute")
async def student_refresh(request: Request, body: RefreshIn | None = None):
    # All auth paths now issue our own persisted refresh tokens.
    refresh_token = (body.refresh_token if body else "") or request.cookies.get("procta_student_refresh", "")
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Missing refresh token")
    account_id, new_refresh = await _verify_and_rotate_refresh_token(
        refresh_token, "student", request
    )
    account = await _get_student_account_by_id(account_id)
    if not account:
        raise HTTPException(status_code=403, detail="Student account not found")
    access_token = issue_student_auth_token(account)
    response = JSONResponse({
        "access_token": access_token,
        "refresh_token": new_refresh,
    })
    _set_student_cookies(response, access_token, new_refresh)
    return response


@router.post("/api/v1/student/auth/logout")
@limiter.limit("30/minute")
async def student_logout(request: Request):
    """Revoke the current student access-session and refresh tokens."""
    account = await require_student_account(request)
    account_id = str(account["id"])
    auth = request.headers.get("Authorization", "")
    access_token = _access_token_from_request(request, "procta_student_access")
    current_jti = ""
    if access_token:
        try:
            from ..constants import STUDENT_SIGNING_KEYS
            claims = _decode_token(access_token, STUDENT_SIGNING_KEYS)
            current_jti = claims.get("jti", "")
        except Exception:
            current_jti = ""
    if current_jti:
        clear_csrf_token({"role": "student_account", "jti": current_jti})
        await _atable("auth_sessions").update({"revoked_at": now_ist().isoformat()})\
            .eq("jti", current_jti).eq("user_kind", "student_account").eq("user_id", account_id).execute()
        try:
            from .. import cache as _cache
            if _cache:
                from ..constants import STUDENT_AUTH_TTL_MINUTES
                _cache.set(
                    f"session:{current_jti}",
                    {"revoked": True},
                    ttl=max(60, STUDENT_AUTH_TTL_MINUTES * 60),
                )
        except Exception:
            _auth_log.debug("auth: student session revocation cache write failed", exc_info=True)
    await _revoke_refresh_tokens_for_user(account_id, "student")
    await record_auth_event("logout", request, "student_account", account_id, account.get("email", ""))
    response = JSONResponse({"ok": True})
    _clear_student_cookies(response)
    return response


@router.post("/api/v1/student/auth/reauth")
@limiter.limit("10/minute")
async def student_reauth(request: Request):
    """Issue a short-lived re-auth token for student-account actions."""
    account = await require_student_account(request)
    account_id = str(account["id"])
    body_data = await request.json()
    password = body_data.get("password", "")
    if not password:
        raise HTTPException(status_code=400, detail="Password required")

    email = account.get("email", "")
    use_supabase_reauth = not local_password_auth_enabled()
    if local_password_auth_enabled():
        row = await _get_student_by_email_for_auth(email)
        if row and row.get("password_hash"):
            if not await verify_password(password, row.get("password_hash")):
                raise HTTPException(status_code=403, detail="Invalid password")
        elif not supabase_auth_fallback_enabled():
            raise HTTPException(status_code=403, detail="Invalid password")
        else:
            use_supabase_reauth = True

    if use_supabase_reauth:
        try:
            supabase.auth.sign_in_with_password({"email": email, "password": password})
        except Exception:
            raise HTTPException(status_code=403, detail="Invalid password")

    reauth_token = issue_reauth_token(account_id)
    return {"reauth_token": reauth_token, "expires_in_seconds": 300}


@router.get("/api/student/exams")
@limiter.limit("30/minute")
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
@limiter.limit("30/minute")
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


# ─── EMAIL VERIFICATION ──────────────────────────────────────────

EMAIL_VERIFY_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Email verified — Procta</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{margin:0;padding:40px 20px;background:#0f172a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;text-align:center}
h2{color:#e2e8f0;margin-bottom:8px}p{color:#94a3b8;font-size:14px;line-height:1.6}
.btn{display:inline-block;padding:12px 32px;border-radius:6px;background:#5b8af0;color:#fff;font-size:15px;font-weight:600;text-decoration:none;margin-top:16px}
.err{color:#ef4444}</style></head>
<body><div style="max-width:480px;margin:0 auto">
  <div style="width:48px;height:48px;border-radius:50%;background:rgba(16,185,129,0.15);margin:0 auto 16px;display:flex;align-items:center;justify-content:center">
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>
  </div>
  <h2 id="title">%(title)s</h2>
  <p id="msg">%(msg)s</p>
  <a class="btn" href="%(login_url)s" id="btn">%(btn)s</a>
</div></body></html>"""


@router.get("/verify-email")
@limiter.limit("5/minute")
async def verify_email(request: Request, token: str = ""):
    """Verify email address via token from verification email."""
    claims = verify_email_token(token)
    if not claims:
        return HTMLResponse(EMAIL_VERIFY_HTML % {
            "title": "Link expired or invalid",
            "msg": "This verification link has expired or is invalid. Request a new one from the login page.",
            "login_url": "/dashboard",
            "btn": "Back to Login",
        }, status_code=400)

    user_id = claims.get("uid", "")
    kind = claims.get("kind", "teacher")
    table = "teachers" if kind == "teacher" else "student_accounts"

    await _atable(table).update({"email_verified_at": now_ist().isoformat()}).eq("id", user_id).execute()

    if kind == "teacher":
        email = str(claims.get("email") or "").strip().lower()
        pending = await (
            _atable("org_invites")
            .select("id,org_id,expires_at")
            .eq("email", email)
            .eq("status", "pending_verification")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if pending.data:
            invite = pending.data[0]
            expired = False
            if invite.get("expires_at"):
                try:
                    expires = datetime.fromisoformat(str(invite["expires_at"]).replace("Z", "+00:00"))
                    expired = datetime.now(timezone.utc) > expires
                except Exception:
                    expired = False
            if not expired:
                teacher_res = await _atable("teachers").select("org_id").eq("id", user_id).limit(1).execute()
                teacher = teacher_res.data[0] if teacher_res.data else {}
                if not teacher.get("org_id"):
                    await _atable("teachers").update({
                        "org_id": str(invite["org_id"]),
                        "org_role": "teacher",
                        "status": "active",
                    }).eq("id", user_id).execute()
                    await _atable("org_invites").update({
                        "status": "accepted",
                        "accepted_at": datetime.now(timezone.utc).isoformat(),
                    }).eq("id", invite["id"]).execute()
                else:
                    await _atable("teachers").update({"status": "active"}).eq("id", user_id).execute()
            else:
                await _atable("org_invites").update({"status": "expired"}).eq("id", invite["id"]).execute()
        else:
            await _atable("teachers").update({"status": "active"}).eq("id", user_id).execute()

    await record_auth_event("email_verified", request, kind, user_id, claims.get("email"))

    return HTMLResponse(EMAIL_VERIFY_HTML % {
        "title": "Email verified!",
        "msg": "Your email has been verified. You can now log in to Procta.",
        "login_url": "/dashboard",
        "btn": "Log In",
    })


@router.post("/api/v1/auth/resend-verification")
@limiter.limit("3/5minute")
async def resend_verification(body: dict, request: Request):
    """Resend the email verification link."""
    # CAPTCHA — resend is the second email-bombing vector
    await verify_or_403(request, (body or {}).get("captcha_token"))

    email = (body.get("email") or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")

    for table, kind in [("teachers", "teacher"), ("student_accounts", "student_account")]:
        row = await _atable(table).select("id,full_name,email_verified_at").eq("email", email).limit(1).execute()
        if row.data:
            user = row.data[0]
            if user.get("email_verified_at"):
                return {"status": "sent"}
            from ..emailer import send_email_verification
            from ..invites import _get_invite_base_url
            vtoken = issue_email_verify_token(user["id"], email, kind)
            base = _get_invite_base_url()
            send_email_verification(email, user.get("full_name", ""), f"{base}/verify-email?token={vtoken}")
            return {"status": "sent"}
    return {"status": "sent"}


# ─── AUTH AUDIT LOG ──────────────────────────────────────────────

@router.get("/api/v1/auth/events")
@limiter.limit("30/minute")
async def auth_events(request: Request):
    """Return the current user's auth audit log (last 50 entries)."""
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    rows = await _atable("auth_events").select("*")\
        .eq("user_kind", "teacher").eq("user_id", tid)\
        .order("created_at", desc=True).limit(50).execute()
    return {"events": rows.data or []}


# ─── RE-AUTHENTICATION ──────────────────────────────────────────

@router.post("/api/v1/auth/reauth")
@limiter.limit("10/minute")
async def reauth(request: Request):
    """Issue a short-lived re-auth token (needs current password or 2FA)."""
    teacher = await require_admin(request)
    tid = str(teacher["id"])

    body_data = await request.json()
    password = body_data.get("password", "")
    if not password:
        raise HTTPException(status_code=400, detail="Password required")

    email = teacher.get("email", "")
    use_supabase_reauth = not local_password_auth_enabled()
    if local_password_auth_enabled():
        row = await _get_teacher_by_email_for_auth(email)
        if row and row.get("password_hash"):
            if not await verify_password(password, row.get("password_hash")):
                raise HTTPException(status_code=403, detail="Invalid password")
        elif not supabase_auth_fallback_enabled():
            raise HTTPException(status_code=403, detail="Invalid password")
        else:
            use_supabase_reauth = True

    if use_supabase_reauth:
        try:
            supabase.auth.sign_in_with_password({"email": email, "password": password})
        except Exception:
            raise HTTPException(status_code=403, detail="Invalid password")

    reauth_token = issue_reauth_token(tid)
    return {"reauth_token": reauth_token, "expires_in_seconds": 300}


# ─── Email-OTP 2FA ────────────────────────────────────────────────
#
# Replaced TOTP / Google Authenticator 2026-05-23. Rationale + plan
# in HANDOFF.md. The flow:
#   1. User clicks Enable 2FA → POST /api/v1/auth/2fa/enable
#      We require an active reauth_token (just confirmed password)
#      AND a verified email. Sets email_2fa_enabled_at = now().
#   2. On subsequent logins, the login handler (teacher_login) emails
#      a 6-digit OTP via email_otp.issue() + send_2fa_otp_email().
#      Client re-POSTs login with email_otp_code populated.
#   3. Disable: POST /api/v1/auth/2fa/disable with a fresh reauth_token.
#
# Status endpoint reports whether 2FA is currently on; no grace
# period (TOTP needed the 30-day grace because it required app
# install — email is universal).


@router.post("/api/v1/auth/2fa/enable")
@limiter.limit("5/minute")
async def email_2fa_enable(body: dict, request: Request):
    """Turn on email-OTP 2FA for the calling teacher."""
    teacher = await require_admin(request)
    tid = str(teacher["id"])

    # Require a fresh reauth token so a stolen access token can't
    # silently enable 2FA on someone else's account.
    reauth_token = (body.get("reauth_token") or "").strip()
    if not reauth_token:
        raise HTTPException(status_code=400, detail="reauth_token required")
    from ..constants import REAUTH_SIGNING_KEYS
    try:
        claims = _decode_token(reauth_token, REAUTH_SIGNING_KEYS)
        if claims.get("scope") != "reauth" or claims.get("uid") != tid:
            raise HTTPException(status_code=403, detail="Invalid reauth token")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=403, detail="Invalid or expired reauth token")

    # Email must be verified — otherwise the user could enable 2FA
    # against an address they don't control and lock themselves out.
    row = await _atable("teachers").select("email_verified_at,email_2fa_enabled_at").eq("id", tid).limit(1).execute()
    if not row.data:
        raise HTTPException(status_code=404, detail="Teacher not found")
    user = row.data[0]
    if not user.get("email_verified_at"):
        raise HTTPException(status_code=400, detail="Verify your email before enabling 2FA")
    if user.get("email_2fa_enabled_at"):
        return {"ok": True, "already_enabled": True}

    await _atable("teachers").update({
        "email_2fa_enabled_at": now_ist().isoformat(),
    }).eq("id", tid).execute()
    await record_auth_event("2fa_enabled", request, "teacher", tid, teacher.get("email", ""))
    return {"ok": True, "message": "Two-factor authentication enabled. You'll receive a code by email on your next sign-in."}


@router.post("/api/v1/auth/2fa/disable")
@limiter.limit("5/minute")
async def email_2fa_disable(body: dict, request: Request):
    """Turn off email-OTP 2FA (requires a fresh reauth token)."""
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    reauth_token = (body.get("reauth_token") or "").strip()
    if not reauth_token:
        raise HTTPException(status_code=400, detail="reauth_token required")
    from ..constants import REAUTH_SIGNING_KEYS
    try:
        claims = _decode_token(reauth_token, REAUTH_SIGNING_KEYS)
        if claims.get("scope") != "reauth" or claims.get("uid") != tid:
            raise HTTPException(status_code=403, detail="Invalid reauth token")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=403, detail="Invalid or expired reauth token")

    await _atable("teachers").update({
        "email_2fa_enabled_at": None,
    }).eq("id", tid).execute()
    await record_auth_event("2fa_disabled", request, "teacher", tid, teacher.get("email", ""))
    return {"ok": True}


@router.get("/api/v1/auth/2fa/status")
@limiter.limit("30/minute")
async def email_2fa_status(request: Request):
    """Return current 2FA enrollment status for the calling teacher."""
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    row = await _atable("teachers").select("email_2fa_enabled_at,email_verified_at").eq("id", tid).limit(1).execute()
    if not row.data:
        return {"enabled": False, "email_verified": False, "method": "email_otp"}
    return {
        "enabled": row.data[0].get("email_2fa_enabled_at") is not None,
        "email_verified": row.data[0].get("email_verified_at") is not None,
        "method": "email_otp",
    }


# ─── SESSION REVOCATION ──────────────────────────────────────────

@router.get("/api/v1/auth/sessions")
@limiter.limit("30/minute")
async def list_sessions(request: Request):
    """List active sessions for the current user."""
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    rows = await _atable("auth_sessions").select("jti,ip,user_agent,issued_at,last_seen_at")\
        .eq("user_kind", "teacher").eq("user_id", tid).is_("revoked_at", "null")\
        .order("last_seen_at", desc=True).limit(20).execute()
    return {"sessions": rows.data or []}


@router.post("/api/v1/auth/sessions/{jti}/revoke")
@limiter.limit("20/minute")
async def revoke_session(jti: str, request: Request):
    """Revoke a specific session by JTI."""
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    await _atable("auth_sessions").update({"revoked_at": now_ist().isoformat()})\
        .eq("jti", jti).eq("user_kind", "teacher").eq("user_id", tid).execute()
    # Invalidate Redis cache
    try:
        from .. import cache as _cache
        from ..constants import ADMIN_TOKEN_TTL_MINUTES
        if _cache:
            _cache.set(f"session:{jti}", {"revoked": True}, ttl=ADMIN_TOKEN_TTL_MINUTES * 60)
    except Exception:
        _auth_log.debug("auth: teacher session revocation cache write failed", exc_info=True)
    await record_auth_event("session_revoked", request, "teacher", tid)
    return {"ok": True}


@router.post("/api/v1/auth/logout")
@limiter.limit("30/minute")
async def logout(request: Request):
    """Revoke the current access-session JTI and local refresh tokens."""
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    access_token = _access_token_from_request(request, "procta_access")
    current_jti = ""
    if access_token:
        try:
            from ..constants import ADMIN_SIGNING_KEYS
            claims = _decode_token(access_token, ADMIN_SIGNING_KEYS)
            current_jti = claims.get("jti", "")
        except Exception:
            current_jti = ""
    if current_jti:
        clear_csrf_token({"role": "teacher", "jti": current_jti})
        await _atable("auth_sessions").update({"revoked_at": now_ist().isoformat()})\
            .eq("jti", current_jti).eq("user_kind", "teacher").eq("user_id", tid).execute()
        try:
            from .. import cache as _cache
            from ..constants import ADMIN_TOKEN_TTL_MINUTES
            if _cache:
                _cache.set(f"session:{current_jti}", {"revoked": True}, ttl=ADMIN_TOKEN_TTL_MINUTES * 60)
        except Exception:
            _auth_log.debug("auth: teacher session revocation cache write failed", exc_info=True)
    await _revoke_refresh_tokens_for_user(tid, "teacher")
    # Best-effort: invalidate the Supabase session so the user can't
    # re-authenticate via Supabase even if our local tokens are revoked
    supabase_uid = teacher.get("supabase_uid", "")
    try:
        if supabase_uid:
            supabase.auth.admin.sign_out(supabase_uid)
    except (AttributeError, Exception):
        pass
    await record_auth_event("logout", request, "teacher", tid)
    response = JSONResponse({"ok": True})
    _clear_teacher_cookies(response)
    return response


@router.post("/api/v1/auth/sessions/revoke-others")
@limiter.limit("10/minute")
async def revoke_other_sessions(request: Request):
    """Revoke all sessions except the current one.

    Kills both access-session jtis AND every active refresh token for
    this user — without the refresh sweep, a leaked refresh token would
    just mint a new access token a few seconds later and undo the
    revoke. The current device's access token survives until its 12 h
    expiry; the user will need to log in fresh on this device after
    that. That's the security/UX tradeoff: "panic button" beats
    "convenience".
    """
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    # Decode current token to get its JTI
    from ..constants import ADMIN_SIGNING_KEYS
    auth = request.headers.get("Authorization", "")
    current_jti = ""
    if auth.startswith("Bearer "):
        try:
            claims = _decode_token(auth[7:], ADMIN_SIGNING_KEYS)
            current_jti = claims.get("jti", "")
        except Exception:
            _auth_log.debug("auth: logout JWT decode for jti lookup failed", exc_info=True)
    q = _atable("auth_sessions").update({"revoked_at": now_ist().isoformat()})\
        .eq("user_kind", "teacher").eq("user_id", tid).is_("revoked_at", "null")
    if current_jti:
        q = q.neq("jti", current_jti)
    await q.execute()
    if local_password_auth_enabled():
        # No FK from auth_sessions.jti to refresh_tokens.jti, so we
        # can't preserve the "current device's" refresh. Safer to nuke
        # all — see docstring.
        await _revoke_refresh_tokens_for_user(tid, "teacher")
    await record_auth_event("session_revoked", request, "teacher", tid, meta={"scope": "others"})
    return {"ok": True}


@router.post("/api/v1/student-auth/password-reset")
@limiter.limit("3/minute")
async def student_password_reset(body: dict, request: Request):
    """Send a password reset email for student accounts."""
    started = time.monotonic()
    await verify_or_403(request, (body or {}).get("captcha_token"))
    email = (body.get("email") or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")
    try:
        if local_password_auth_enabled():
            user = await _get_student_by_email_for_auth(email)
            if user:
                from ..emailer import send_password_reset_email
                from ..invites import _get_invite_base_url
                token = issue_password_reset_token(
                    user["id"], email, "student",
                    password_changed_at=_stringify_pwc(user.get("password_changed_at")),
                )
                base = _get_invite_base_url()
                send_password_reset_email(
                    email,
                    user.get("full_name", ""),
                    f"{base}/reset-password?token={token}",
                )
            return {"status": "sent"}
        try:
            supabase.auth.reset_password_for_email(email)
            return {"status": "sent"}
        except Exception:
            _auth_log.warning("[StudentPasswordReset] Supabase reset email failed")
            return {"status": "sent"}  # Don't reveal whether account exists
    finally:
        await asyncio.sleep(max(0.0, 0.35 - (time.monotonic() - started)))


RESET_PASSWORD_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Reset password — Procta</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{margin:0;padding:40px 20px;background:#0f172a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
.card{max-width:420px;margin:0 auto;background:#fff;border-radius:16px;padding:32px;color:#0f172a}
h1{font-size:22px;margin:0 0 8px}p{color:#64748b;font-size:14px;line-height:1.5}
label{display:block;font-size:13px;font-weight:600;margin:16px 0 6px}
input{width:100%;box-sizing:border-box;padding:11px;border:1px solid #cbd5e1;border-radius:8px;font-size:15px}
button{width:100%;margin-top:18px;padding:12px;border:0;border-radius:8px;background:#3b82f6;color:#fff;font-weight:700;cursor:pointer}
.err{display:none;margin-top:12px;color:#991b1b;background:#fef2f2;padding:10px;border-radius:8px;font-size:13px}
.ok{display:none;margin-top:12px;color:#065f46;background:#ecfdf5;padding:10px;border-radius:8px;font-size:13px}</style></head>
<body><div class="card">
<h1>Reset your password</h1>
<p>Choose a new password for your Procta account.</p>
<form id="f">
<label>New password</label>
<input id="password" type="password" minlength="10" autocomplete="new-password" required>
<button id="btn" type="submit">Update password</button>
</form>
<div class="err" id="err"></div><div class="ok" id="ok">Password updated. You can close this page and log in.</div>
</div>
<script>
document.getElementById('f').addEventListener('submit', async (e) => {
  e.preventDefault();
  const err = document.getElementById('err');
  const ok = document.getElementById('ok');
  const btn = document.getElementById('btn');
  err.style.display = 'none'; ok.style.display = 'none'; btn.disabled = true;
  try {
    const r = await fetch('/api/v1/auth/password-reset/confirm', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({token: '%(token)s', password: document.getElementById('password').value})
    });
    if (!r.ok) throw new Error((await r.json()).detail || 'Could not update password');
    ok.style.display = 'block';
  } catch (e) {
    err.textContent = e.message || 'Could not update password';
    err.style.display = 'block';
  } finally {
    btn.disabled = false;
  }
});
</script></body></html>"""


@router.get("/reset-password")
async def reset_password_page(token: str = ""):
    if not local_password_auth_enabled():
        return HTMLResponse("<h1>Password reset is handled by the auth provider.</h1>", status_code=404)
    if not verify_password_reset_token(token):
        return HTMLResponse("<h1>Reset link expired or invalid</h1>", status_code=400)
    return HTMLResponse(RESET_PASSWORD_HTML % {"token": _esc(token)})


@router.post("/api/v1/auth/password-reset/confirm")
@limiter.limit("5/minute")
async def confirm_password_reset(body: dict, request: Request):
    if not local_password_auth_enabled():
        raise HTTPException(status_code=404, detail="Password reset is handled by the auth provider")
    token = (body.get("token") or "").strip()
    password = body.get("password") or ""
    claims = verify_password_reset_token(token)
    if not claims:
        raise HTTPException(status_code=400, detail="Reset link expired or invalid")
    try:
        validate_password(password)
    except PasswordError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Explicit kind allow-list — defence in depth in case a future
    # issuer accepts a bad kind. Without this, an unexpected value
    # would silently default to student_accounts.
    kind = claims.get("kind")
    if kind not in ("teacher", "student"):
        raise HTTPException(status_code=400, detail="Reset link expired or invalid")
    table = "teachers" if kind == "teacher" else "student_accounts"
    user_id = str(claims.get("uid") or "")
    if not user_id:
        raise HTTPException(status_code=400, detail="Reset link expired or invalid")

    # Single-use enforcement: when the token was minted, it embedded the
    # user's current `password_changed_at` (or None for legacy tokens
    # minted before this column was wired). Fetch the live value; if it
    # doesn't match, the token has already been used (the column moved
    # forward) OR the user changed their password through another flow.
    # Either way, reject.
    pwc_claim = claims.get("pwc")
    live = await _atable(table).select("password_changed_at").eq("id", user_id).limit(1).execute()
    if not live.data:
        raise HTTPException(status_code=400, detail="Reset link expired or invalid")
    live_pwc = _stringify_pwc(live.data[0].get("password_changed_at"))
    if pwc_claim is not None:
        if live_pwc != pwc_claim:
            raise HTTPException(status_code=400, detail="Reset link has already been used")
    elif live_pwc is not None:
        raise HTTPException(status_code=400, detail="Reset link expired or invalid")

    await _atable(table).update({
        "password_hash": await hash_password(password),
        "auth_provider": "local",
        "password_changed_at": now_ist().isoformat(),
    }).eq("id", user_id).execute()
    await record_auth_event("password_reset_completed", request, kind, user_id, claims.get("email"))
    return {"ok": True}


# ─── OAUTH SIGN-IN — REMOVED 2026-05-23 ──────────────────────────
# Google + Microsoft OAuth (formerly /api/v1/auth/oauth/start and
# /api/v1/auth/oauth/callback) were removed in favour of email +
# password as the sole sign-in method. Operational complexity
# (provider app reviews, scope changes, token rotation) outweighed
# the conversion benefit at our stage. To re-enable, restore from
# git history before this commit AND re-add the Google + Microsoft
# providers in the Supabase Auth dashboard.
