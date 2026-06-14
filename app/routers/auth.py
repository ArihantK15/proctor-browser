from ..log_safe import mask_email, safe
from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
import asyncio
import logging
import os
import re
import time
_auth_log = logging.getLogger("auth")
import uuid as _uuid
from datetime import datetime, timezone, timedelta

from ..database import supabase, async_table as _atable, is_postgres_backend
from ..limiter import limiter
from ..models import TeacherSignupIn, TeacherLoginIn, RefreshIn, StudentSignupIn, StudentLoginIn, PasswordResetIn
from ..auth import (
    issue_admin_token, _get_teacher_by_id, _get_teacher_by_uid,
    issue_student_auth_token, _get_student_account_by_id, _get_student_account_by_uid,
    require_admin, require_student_account, clear_teacher_cache, clear_student_account_cache,
)
from ..auth.scope import org_is_solo
from ..utils import fmt_ist, now_ist
from ..models import SessionStatus
from ..models.invites import InviteStatus
from ..constants import ALL_SIGNING_KEYS, PLANS, TRIAL_DAYS
from ..services.passwords import validate_password, validate_password_async, PasswordError
from ..services.local_auth import (
    burn_password_verify,
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


def _fill_template(template: str, values: dict) -> str:
    # Plain text substitution. We deliberately avoid `template % values` and
    # `template.format(...)` because the embedded HTML/CSS contains literal
    # `%` (e.g. `width:100%;`, `border-radius:50%;`) and `{` characters that
    # both formatters would mis-interpret and raise on.
    out = template
    for key, value in values.items():
        out = out.replace(f"%({key})s", value)
    return out


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


def _is_electron_origin(request: Request | None) -> bool:
    """True if the request looks like it came from the desktop Electron
    app (any version) rather than a regular web browser hitting
    app.procta.net.

    The desktop lobby loads with origin `procta-lobby://lobby` since
    v2.3.14; older versions loaded via file:// which Chromium sends as
    Origin: null. Either signal means we need cross-site-friendly
    cookie attributes — SameSite=Strict/Lax would block the cookie
    from coming back on the next request, so the user would appear to
    log in successfully and then 401 on every subsequent call.
    """
    if request is None:
        return False
    origin = (request.headers.get("origin") or "").lower()
    if origin.startswith("procta-lobby://") or origin == "null":
        return True
    # Electron user-agent fallback — covers cases where Origin header
    # was stripped by an intermediate proxy.
    ua = (request.headers.get("user-agent") or "").lower()
    return "electron" in ua and "procta" in ua


def _set_auth_cookie(
    response: JSONResponse, name: str, value: str, max_age_seconds: int,
    *, request: Request | None = None,
) -> None:
    is_access = name in {"procta_access", "procta_student_access"}
    if _is_electron_origin(request):
        # Cross-site request from the Electron desktop app — the cookie
        # must be SameSite=None;Secure for the browser to attach it on
        # subsequent fetches. None requires Secure; we force Secure on
        # regardless of _secure_cookie_enabled() because modern browsers
        # reject None-without-Secure cookies.
        same_site = "none"
        secure = True
    else:
        # Web browser hitting app.procta.net same-origin — keep the
        # strict defaults so CSRF defence-in-depth stays in place even
        # if the CSRF token mechanism ever has a gap.
        same_site = "strict" if is_access else "lax"
        secure = _secure_cookie_enabled()
    response.set_cookie(
        name,
        value,
        max_age=max_age_seconds,
        httponly=True,
        secure=secure,
        samesite=same_site,
        path="/",
    )


def _set_teacher_cookies(
    response: JSONResponse, access_token: str, refresh_token: str,
    *, request: Request | None = None,
) -> None:
    from ..constants import ADMIN_TOKEN_TTL_MINUTES
    _set_auth_cookie(response, "procta_access", access_token,
                     ADMIN_TOKEN_TTL_MINUTES * 60, request=request)
    _set_auth_cookie(response, "procta_refresh", refresh_token,
                     30 * 24 * 60 * 60, request=request)


def _set_student_cookies(
    response: JSONResponse, access_token: str, refresh_token: str,
    *, request: Request | None = None,
) -> None:
    from ..constants import STUDENT_AUTH_TTL_MINUTES
    _set_auth_cookie(response, "procta_student_access", access_token,
                     STUDENT_AUTH_TTL_MINUTES * 60, request=request)
    _set_auth_cookie(response, "procta_student_refresh", refresh_token,
                     30 * 24 * 60 * 60, request=request)


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


def _pwc_equal(a: str | None, b: str | None) -> bool:
    """Compare two ``password_changed_at`` representations as the SAME instant.

    The column is written as ``now_ist().isoformat()`` (an IST ``+05:30``
    offset) but is read back through different code paths/backends that may
    render the same instant with a different offset (``+05:30`` vs ``+00:00``)
    or different microsecond precision. A raw string compare therefore produces
    false mismatches that wrongly reject a *legitimate first-use* reset token
    with "already used"/"invalid" — the reset-always-fails bug. Compare the
    parsed instants; fall back to exact-string only when either side cannot be
    parsed (so behaviour never regresses for already-matching values)."""
    if a == b:
        return True
    if not a or not b:
        return False
    try:
        da = datetime.fromisoformat(a)
        db = datetime.fromisoformat(b)
    except (TypeError, ValueError):
        return False
    if da.tzinfo is None or db.tzinfo is None:
        # Can't compare a naive against an aware instant safely — require the
        # exact-string match already checked above.
        return False
    return da == db


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
    if str(rec.get("user_id")) != str(user_id) or rec.get("kind") != expected_kind:
        # Claim/DB mismatch — should not happen unless someone is
        # tampering. Reject without leaking which check failed.
        raise HTTPException(status_code=401, detail="Refresh token invalid")
    if rec.get("revoked_at"):
        # Replay attempt — token was previously rotated. Compromise the
        # whole refresh family + access sessions for this user.
        # _revoke_auth_sessions_for_user handles the teacher→teacher /
        # student→student_account user_kind mapping; calling the inline
        # UPDATE with user_kind='student' would have matched 0 rows and
        # silently failed to revoke active access tokens for students.
        await _revoke_refresh_tokens_for_user(user_id, expected_kind)
        await _revoke_auth_sessions_for_user(str(user_id), expected_kind)
        raise HTTPException(status_code=401, detail="Refresh token has been revoked")

    # Mint the replacement first so we have its jti for the rotation
    # pointer, then perform a CAS-style UPDATE: only revoke the old
    # row if it's still NOT revoked. If someone else rotated in the
    # gap between our SELECT and this UPDATE, the rowcount is 0 — we
    # treat that as a replay and burn the family the same way as the
    # explicit replay branch above. Closes the TOCTOU window where
    # two parallel refresh calls with the same token could both
    # succeed and mint two children off one parent.
    now_iso = now_ist().isoformat()
    new_token, new_jti, new_exp = issue_refresh_token(user_id, expected_kind)
    rotate_result = await _atable("refresh_tokens").update({
        "revoked_at": now_iso,
        "replaced_by_jti": new_jti,
    }).eq("jti", jti).is_("revoked_at", "null").execute()
    if not (rotate_result.data or []):
        # Concurrent rotation — same shape as replay. Burn the family.
        await _revoke_refresh_tokens_for_user(user_id, expected_kind)
        await _revoke_auth_sessions_for_user(str(user_id), expected_kind)
        raise HTTPException(status_code=401, detail="Refresh token has been revoked")

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


async def _revoke_auth_sessions_for_user(user_id: str, kind: str) -> None:
    """Bulk-revoke active access-token sessions for a user.

    Access tokens are validated against auth_sessions.revoked_at on every
    request (see app/auth/admin_auth.py), so marking the rows revoked
    immediately invalidates any still-live access token. Pair this with
    _revoke_refresh_tokens_for_user wherever we need to fully sign a user
    out — e.g. password reset, which must actually evict an intruder
    rather than leaving their current access token valid until it expires.

    `kind` is the auth domain ("teacher" | "student"); auth_sessions keys
    students under the "student_account" user_kind.
    """
    user_kind = "teacher" if kind == "teacher" else "student_account"
    try:
        await _atable("auth_sessions").update({"revoked_at": now_ist().isoformat()})\
            .eq("user_kind", user_kind).eq("user_id", str(user_id))\
            .is_("revoked_at", "null").execute()
    except Exception:
        _auth_log.warning("[auth] auth_sessions revoke failed for %s/%s",
                          kind, safe(str(user_id)), exc_info=True)


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
        await validate_password_async(body.password)
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
            auth_resp = await asyncio.to_thread(
                supabase.auth.admin.create_user, {
                    "email": email,
                    "password": body.password,
                    "email_confirm": False,
                },
            )
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

        _auth_log.info("[TeacherSignup] %s <%s> created (org=%s)", safe(name), mask_email(email), safe(org_name))
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
                await asyncio.to_thread(supabase.auth.admin.delete_user, str(supabase_uid))
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

    _auth_log.info("[TeacherSignup] %s <%s> created (org=%s)", safe(name), mask_email(email), safe(org_name))

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
            # Burn a bcrypt cycle so this no-account path takes the same
            # wall time as a real verify_password — otherwise the timing
            # difference (≈100ms vs ≈1ms) leaks account existence.
            await burn_password_verify()
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

    # Block terminal account states even when credentials are valid.
    # phase62 reserved 'suspended' for admin-disable; 'deleted' is set
    # by the privacy/SAR erasure flow alongside an email anonymisation
    # (so 'deleted' is rarely reachable from this path, but if the
    # anonymisation step failed we still bounce). Generic 401 keeps
    # the response indistinguishable from a wrong-password attempt —
    # we don't want to confirm to an attacker that an email is known
    # but just suspended. Failures intentionally NOT cleared so a
    # suspended user can't burn down their own lockout counter.
    status = (teacher.get("status") or "").lower()
    if status in ("suspended", "deleted"):
        await record_auth_event(
            "login_failed", request, "teacher", str(teacher.get("id", "")), email,
            {"reason": f"account_{status}"},
        )
        raise HTTPException(status_code=401, detail="Invalid email or password")

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
        clear_teacher_cache(str(teacher["id"]))
        _auth_log.info("[TeacherLogin] Auto-verified existing account %s <%s>", safe(teacher.get("full_name", "")), mask_email(email))
        await record_auth_event("email_verified", request, "teacher", teacher["id"], email)

    # Gap #20 — per-org MFA enforcement. If the teacher's org has
    # `require_2fa` set, run the email-OTP 2FA step even when the user
    # never opted in individually. Procta's 2FA is email-OTP (no enrolled
    # secret), so org-wide enforcement is simply "always challenge". A
    # missing column / lookup error falls back to opt-in (fail-open on the
    # policy lookup, never the auth check itself) so a schema lag can't
    # lock an entire org out of login.
    org_requires_2fa = False
    if not teacher.get("email_2fa_enabled_at") and teacher.get("org_id"):
        try:
            _orow = (await _atable("organizations")
                     .select("require_2fa")
                     .eq("id", str(teacher["org_id"]))
                     .limit(1).execute()).data
            org_requires_2fa = bool(_orow and _orow[0].get("require_2fa"))
        except Exception as e:
            _auth_log.warning("[TeacherLogin] require_2fa lookup failed for org=%s: %s",
                              safe(str(teacher.get("org_id"))), safe(e))

    if teacher.get("email_2fa_enabled_at") or org_requires_2fa:
        # Email-OTP 2FA (replaced TOTP/Google Authenticator on 2026-05-23).
        # First call (no code yet): generate a fresh OTP, email it, return
        # 401 EMAIL_2FA_REQUIRED. The browser shows a code-input UI and
        # re-POSTs with the same email/password + the email_otp_code.
        from ..services.email_otp import issue as otp_issue, verify as otp_verify, OtpRateLimitError
        from ..emailer import send_2fa_otp_email

        if not body.email_otp_code:
            try:
                code = await otp_issue("teacher", str(teacher["id"]), "2fa_login")
            except OtpRateLimitError:
                # Per-(user, purpose) cap. Surface a 429 so the client UI
                # shows "wait a few minutes" instead of leaving the user
                # in the dark — same shape used everywhere else in this
                # router for OTP rate-limits.
                await record_auth_event("login_failed", request, "teacher", teacher["id"], email, {"reason": "email_2fa_rate_limited"})
                raise HTTPException(
                    status_code=429,
                    detail="Too many 2FA codes requested. Please wait a few minutes before trying again.",
                )
            try:
                send_2fa_otp_email(email, teacher.get("full_name", ""), code, purpose="login")
            except Exception as e:
                # Sending failed — surface a clean error so the user doesn't
                # sit forever waiting for an email that never comes.
                _auth_log.error("[TeacherLogin] 2FA email send failed for %s: %s", mask_email(email), safe(e))
                raise HTTPException(status_code=502, detail="Could not send 2FA code. Please try again.")
            await record_auth_event("login_failed", request, "teacher", teacher["id"], email, {"reason": "email_2fa_required"})
            return JSONResponse(
                status_code=401,
                content={
                    "error": "EMAIL_2FA_REQUIRED",
                    "message": "We sent a 6-digit code to your email. Enter it to finish signing in.",
                },
            )
        locked, _ = await check_lockout("otp_verify", str(teacher["id"]))
        if locked:
            await record_auth_event("login_failed", request, "teacher", teacher["id"], email, {"reason": "otp_locked_out"})
            raise HTTPException(status_code=429, detail="Too many failed 2FA attempts. Please wait and try again.")
        if not await otp_verify("teacher", str(teacher["id"]), "2fa_login", body.email_otp_code.strip()):
            await record_failure("otp_verify", str(teacher["id"]))
            await record_auth_event("login_failed", request, "teacher", teacher["id"], email, {"reason": "email_2fa_invalid"})
            raise HTTPException(status_code=401, detail="Invalid or expired 2FA code")
        await clear_failures("otp_verify", str(teacher["id"]))

    await record_auth_event("login_success", request, "teacher", teacher["id"], email)

    # Suspicious-login email — non-blocking heads-up if this looks like
    # a new device/location vs the user's last 30 days. Fire-and-forget
    # so we never delay the login response on auth_events lookup + SMTP.
    try:
        from ..services.suspicious_login import check_and_notify as _sus_check
        asyncio.create_task(_sus_check(
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

    # Evict oldest active auth sessions if org has a concurrent cap.
    try:
        org_id = teacher.get("org_id")
        if jti and org_id:
            org_row = (await _atable("organizations")
                       .select("max_concurrent_auth_sessions")
                       .eq("id", str(org_id)).limit(1).execute()).data or []
            cap = org_row[0].get("max_concurrent_auth_sessions") if org_row else None
            if cap and int(cap) > 0:
                active = await _atable("auth_sessions").select("jti,last_seen_at,issued_at")\
                    .eq("user_kind", "teacher").eq("user_id", str(teacher["id"]))\
                    .is_("revoked_at", "null")\
                    .order("last_seen_at", desc=True).execute()
                if len(active.data or []) > int(cap):
                    to_evict = (active.data or [])[int(cap):]
                    for row in to_evict:
                        evict_jti = row["jti"]
                        if evict_jti == jti:
                            continue
                        await _atable("auth_sessions").update({
                            "revoked_at": now_ist().isoformat(),
                        }).eq("jti", evict_jti).execute()
                        try:
                            from ..cache import _cache
                            if _cache:
                                from ..constants import ADMIN_TOKEN_TTL_MINUTES
                                _cache.set(f"session:{evict_jti}", {"revoked": True},
                                           ttl=ADMIN_TOKEN_TTL_MINUTES * 60)
                        except Exception:
                            pass
    except Exception:
        _auth_log.warning("auth: concurrent-session eviction failed", exc_info=True)

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
            "is_solo": await org_is_solo(teacher),
            "email_verified_at": teacher.get("email_verified_at"),
        },
    })
    _set_teacher_cookies(response, access_token, refresh_tok, request=request)
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
        "is_solo": await org_is_solo(teacher),
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
    # Mirror the login-time terminal-status block so an already-logged-in
    # user whose account got suspended/deleted after their session
    # started can't extend their access via refresh. Revoke the active
    # refresh + auth_sessions for them so the bad token is dead going
    # forward, then 401 the current call. Without this, a suspended
    # teacher could keep refreshing for as long as their refresh rotates
    # within its TTL.
    status = (teacher.get("status") or "").lower()
    if status in ("suspended", "deleted"):
        try:
            await _revoke_refresh_tokens_for_user(teacher_id, "teacher")
            await _revoke_auth_sessions_for_user(teacher_id, "teacher")
        except Exception:
            _auth_log.warning("[Refresh] session revoke on suspended/deleted account failed", exc_info=True)
        raise HTTPException(status_code=401, detail="Session expired. Please sign in again.")
    access_token = issue_admin_token(teacher)
    response = JSONResponse({
        "access_token": access_token,
        "refresh_token": new_refresh,
    })
    _set_teacher_cookies(response, access_token, new_refresh, request=request)
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
                await asyncio.to_thread(supabase.auth.reset_password_for_email, email)
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
  input:focus{ border-color:#5b8af0; box-shadow:0 0 0 3px rgba(91,138,240,.15); }
  button{ width:100%; padding:12px; background:#5b8af0; color:#fff; border:none;
          border-radius:8px; font-size:15px; font-weight:600; cursor:pointer; }
  button:hover{ background:#4a78dc; }
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
  <form id="acceptForm" data-token="{token}">
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
    Already have an account? <a href="https://app.procta.net/dashboard" style="color:#5b8af0;">Go to dashboard</a>
  </p>
</div>
<script src="/static/invite-accept.js" defer></script>
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
    # Token shape gate — invite tokens are issued by secrets.token_urlsafe
    # which produces [A-Za-z0-9_-]. Rejecting anything else here:
    #   (a) defeats reflective-XSS via the {token} substitution in the
    #       returned HTML (the rendered page embeds the token inside a
    #       single-quoted JS string literal — a stray quote would break
    #       out of the literal),
    #   (b) saves a pointless SHA-256 + DB roundtrip for obviously-bad
    #       URLs (scanners, malformed pastes).
    import re as _re
    if not _re.fullmatch(r"[A-Za-z0-9_-]{1,256}", token or ""):
        return HTMLResponse("<h1>Invalid or expired invitation link</h1>", status_code=404)
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
        except (ValueError, TypeError):
            # Fail closed — we can't prove the invite is still valid, so
            # treat it as expired. The earlier code logged + fell through,
            # which accepted malformed-expiry rows even if they were
            # long-expired in reality.
            _auth_log.warning("auth: invite_page expires parse failed (id=%s)", invite.get("id"))
            return HTMLResponse("<h1>This invitation has expired</h1>", status_code=410)
        if datetime.now(timezone.utc) > expires:
            return HTMLResponse("<h1>This invitation has expired</h1>", status_code=410)
    org_result = await _atable("organizations").select("name").eq("id", str(invite["org_id"])).limit(1).execute()
    org_name = org_result.data[0]["name"] if org_result.data else "an organization"
    page = _INVITE_PAGE.replace("{org_name}", _esc(org_name)).replace("{token}", _esc(token))
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
        await validate_password_async(password)
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
        except (ValueError, TypeError) as exc:
            # Fail closed: if we can't parse the expiry we can't prove the
            # invite is still valid, so refuse it. The earlier code logged
            # and fell through, which accepted a malformed-expiry row even
            # though it could have been long-expired.
            _auth_log.warning("auth: invite expires parse failed (id=%s): %s", invite.get("id"), exc)
            raise HTTPException(status_code=410, detail="Invitation has expired") from exc
        if datetime.now(timezone.utc) > expires:
            raise HTTPException(status_code=410, detail="Invitation has expired")

    email = (invite.get("email") or "").strip().lower()
    if not email:
        # Hard reject: every code path below assumes a usable email
        # (account lookup, Supabase Auth create, teacher insert). A row
        # missing email is corrupt invite data, not a user error.
        _auth_log.error("auth: invite %s has no email; rejecting", invite.get("id"))
        raise HTTPException(status_code=400, detail="Invitation is missing an email address. Ask your admin to resend it.")
    org_id = str(invite.get("org_id") or "")
    if not org_id:
        _auth_log.error("auth: invite %s has no org_id; rejecting", invite.get("id"))
        raise HTTPException(status_code=400, detail="Invitation is not linked to an organization. Ask your admin to resend it.")

    # Check if teacher already exists
    existing = await _atable("teachers").select("id,org_id,org_role,full_name,email,email_verified_at").eq("email", email).execute()
    if existing.data:
        teacher = existing.data[0]
        if teacher.get("org_id"):
            raise HTTPException(status_code=409, detail="This email is already part of an organization")
        # Trust boundary: only update profile fields when the existing
        # teacher hasn't verified their email yet. A verified account
        # owns its own profile; an org admin sending an invite to a
        # verified teacher's email must not silently rename that
        # teacher. We still flip status so the invite acceptance flow
        # can complete, but the verified user's full_name is preserved.
        update_fields: dict = {"status": "pending_verification"}
        if not teacher.get("email_verified_at"):
            update_fields["full_name"] = full_name
            if local_password_auth_enabled():
                update_fields.update({
                    "password_hash": await hash_password(password),
                    "auth_provider": "local",
                    "password_changed_at": now_ist().isoformat(),
                })
        await _atable("teachers").update(update_fields).eq("id", teacher["id"]).execute()
        clear_teacher_cache(str(teacher["id"]))
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
                auth_resp = await asyncio.to_thread(
                    supabase.auth.admin.create_user, {
                        "email": email,
                        "password": password,
                        "email_confirm": False,
                    },
                )
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
    _auth_log.info("[AcceptInvite] %s <%s> pending verification for org %s", safe(resolved_name), mask_email(email), safe(org_id))
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

    # Existence check runs BEFORE password validation on purpose: a returning
    # student must receive a clean 409 even when their existing password
    # predates the current strength policy. The registration page relies on
    # this 409 to auto-detect an existing account — gating it behind
    # validate_password would surface a misleading "password too weak" error
    # and block legacy-password students from enrolling for a new exam. This
    # leaks no new oracle: signup is captcha-gated + rate-limited (5/hour) and
    # the 409 message is deliberately non-committal.
    existing = await _atable("student_accounts").select("id").eq("email", email).execute()
    if existing.data:
        raise HTTPException(status_code=409, detail="If an account exists with this email, you can sign in or reset your password.")

    try:
        await validate_password_async(body.password)
    except PasswordError as e:
        raise HTTPException(status_code=400, detail=str(e))

    auth_resp = None
    password_hash = None
    auth_provider = "supabase"
    if local_password_auth_enabled():
        supabase_uid = new_auth_uid()
        password_hash = await hash_password(body.password)
        auth_provider = "local"
    else:
        try:
            auth_resp = await asyncio.to_thread(
                supabase.auth.admin.create_user, {
                    "email": email,
                    "password": body.password,
                    "email_confirm": False,
                },
            )
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
            "email_verified_at": None,
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
                await asyncio.to_thread(supabase.auth.admin.delete_user, str(supabase_uid))
                _auth_log.info("[StudentSignup] Rolled back Auth user %s", supabase_uid)
            except Exception as rollback_err:
                _auth_log.critical("[StudentSignup] Failed to rollback Auth user %s: %s", supabase_uid, rollback_err)
        raise HTTPException(status_code=500, detail="Failed to create student record")

    # NOTE: auto-link of pre-existing students rows used to run here, but
    # that opened a confused-deputy account hijack:
    #   1. Attacker signs up with victim@example.com (CAPTCHA + valid pw).
    #   2. Pre-enrolled students rows for victim@example.com get
    #      account_id pointed at the attacker's freshly-minted account.
    #   3. Verification email lands in the victim's inbox; victim clicks
    #      or supplies the OTP thinking it's their own signup.
    #   4. Attacker logs in (they know their own password) and now has
    #      access to the victim's pre-enrollments.
    # The fix is to defer auto-linking until email ownership is proven
    # via OTP / verify-email. See _auto_link_student_enrollments() and
    # the call sites in student_verify_signup_otp + verify_email.

    _auth_log.info("[StudentSignup] %s <%s> created", safe(name), mask_email(email))
    try:
        await _track_a_issue_signup_otp(account, email)
    except Exception:
        _auth_log.warning("[StudentSignup] signup OTP send failed", exc_info=True)
    return {
        "account_id": account["id"],
        "email":      email,
        "full_name":  name,
        "verify_required": True,
        "expires_in": 600,
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
            # Constant-time defense — see burn_password_verify docstring.
            # Without this the timing gap between an existing-account
            # bcrypt verify and a no-account early return enumerates
            # students.
            await burn_password_verify()
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

    account = await _track_a_hydrate_student_account(account)
    if "email_verified_at" in account and not account.get("email_verified_at"):
        await record_auth_event("login_failed", request, "student_account", str(account.get("id") or ""), email, {"reason": "email_unverified"})
        raise HTTPException(
            status_code=403,
            detail={
                "code": "EMAIL_VERIFICATION_REQUIRED",
                "message": "Check your email for a 6-digit verification code before logging in.",
            },
        )

    await clear_failures("student", email)

    # Opportunistic auto-link on every login in case the student was
    # registered by a teacher AFTER they created their account.
    # Case-insensitive to match the exams-page auto-link (_student_exams) and
    # catch legacy roster rows written before emails were lowercased on write —
    # otherwise the student sees zero exams until the exams endpoint relinks.
    try:
        await _atable("students")\
            .update({"account_id": account["id"]})\
            .ilike("email", email)\
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
    _set_student_cookies(response, access_token, refresh_tok, request=request)
    return response


@router.get("/api/v1/student/auth/me")
@limiter.limit("30/minute")
async def student_me(request: Request):
    account = await require_student_account(request)
    return {
        "id":        account["id"],
        "email":     account["email"],
        "full_name": account["full_name"],
        "email_reminders_enabled": await _get_student_email_reminders_enabled(str(account["id"])),
    }


async def _get_student_email_reminders_enabled(account_id: str) -> bool:
    """Read the student reminder preference, defaulting to enabled.

    The default-on fallback keeps legacy schemas and old accounts from losing
    reminders before the migration has run.
    """
    try:
        row = (await _atable("student_accounts")
               .select("email_reminders_enabled")
               .eq("id", account_id)
               .limit(1)
               .execute()).data or []
        if not row:
            return True
        val = row[0].get("email_reminders_enabled")
        return True if val is None else bool(val)
    except Exception as e:
        msg = str(e).lower()
        if "email_reminders_enabled" in msg and ("column" in msg or "schema cache" in msg):
            return True
        raise


@router.get("/api/v1/student/account/preferences")
@limiter.limit("30/minute")
async def student_account_preferences(request: Request):
    account = await require_student_account(request)
    enabled = await _get_student_email_reminders_enabled(str(account["id"]))
    return {"email_reminders_enabled": enabled}


@router.patch("/api/v1/student/account/preferences")
@limiter.limit("20/minute")
async def update_student_account_preferences(request: Request):
    account = await require_student_account(request)
    body = await request.json()
    if "email_reminders_enabled" not in body:
        raise HTTPException(status_code=400, detail="email_reminders_enabled is required")
    enabled = bool(body.get("email_reminders_enabled"))
    try:
        await (_atable("student_accounts")
               .update({"email_reminders_enabled": enabled})
               .eq("id", str(account["id"]))
               .execute())
    except Exception as e:
        msg = str(e).lower()
        if "email_reminders_enabled" in msg and ("column" in msg or "schema cache" in msg):
            raise HTTPException(status_code=503, detail="Reminder preferences are not available until the latest migration is applied.")
        raise
    await record_auth_event(
        "preference_updated",
        request,
        "student_account",
        str(account["id"]),
        account.get("email", ""),
        {"email_reminders_enabled": enabled},
    )
    return {"email_reminders_enabled": enabled}


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
    _set_student_cookies(response, access_token, new_refresh, request=request)
    return response


@router.post("/api/v1/student/auth/logout")
@limiter.limit("30/minute")
async def student_logout(request: Request):
    """Revoke the current student access-session and refresh tokens."""
    account = await require_student_account(request)
    account_id = str(account["id"])
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


async def _student_enrollments_for_account(account: dict, email: str, columns: str) -> list[dict]:
    """Return only enrollments bound to this authenticated student account.

    We still opportunistically claim unlinked rows with the account's verified
    email so teacher roster imports made after signup appear without a fresh
    login. After that, reads are account_id-scoped. This prevents stale rows
    whose email was later edited or bound to another account from leaking into
    this student's lobby/history.
    """
    account_id = str(account.get("id") or "")
    if not account_id:
        return []

    if email:
        try:
            await (_atable("students")
                   .update({"account_id": account_id})
                   .ilike("email", email)
                   .is_("account_id", "null")
                   .execute())
        except Exception:
            _auth_log.debug("student enrollment auto-link failed", exc_info=True)

    try:
        r = await (_atable("students")
                   .select(columns)
                   .eq("account_id", account_id)
                   .execute())
        return r.data or []
    except Exception as e:
        msg = str(e).lower()
        if "account_id" in msg and ("column" in msg or "schema cache" in msg):
            # Legacy schema fallback. Modern production schema has account_id,
            # so this should only apply to old dev/test databases.
            if not email:
                return []
            r = await (_atable("students")
                       .select(columns)
                       .eq("email", email)
                       .execute())
            return r.data or []
        raise


@router.get("/api/student/exams")
@limiter.limit("30/minute")
async def student_exams(request: Request):
    """Return all exams the authenticated student is enrolled in.

    Looks up the student account from the Bearer token, claims any
    still-unlinked rows for the account's email, then lists only rows
    bound to that account_id before enriching them with exam_config
    details and session status.

    Hardened post-2026-05-31: previously any unhandled exception
    bubbled up as an opaque 500 with no diagnostic; user-reported
    flow had a freshly-logged-in student seeing 500 here right after
    auto-update which masked whatever the actual cause was. Now we
    catch + log with the request_id so the next failure leaves a
    traceable breadcrumb in the server log.
    """
    account = await require_student_account(request)
    # Defensive: email can be None on accounts created via the older
    # Supabase-auth-only path. Empty enrollments query is preferred
    # over an AttributeError that becomes an opaque 500.
    email_raw = account.get("email") if isinstance(account, dict) else None
    email = (email_raw or "").strip().lower()

    try:
        # Read account-bound enrollment rows (teacher-wide identity). This
        # keeps lobby visibility tied to the authenticated account instead
        # of a raw email string that may be stale or reused elsewhere. The
        # students table has no exam_id column, so we ask only for
        # roll_number + teacher_id and resolve per-exam membership from
        # student_invites in the expansion step below.
        enrollments = await _student_enrollments_for_account(
            account, email, "roll_number, teacher_id",
        )
        if not enrollments:
            return {"exams": []}
    except Exception as e:
        rid = getattr(request.state, "request_id", "") or "-"
        _auth_log.error("[student/exams] enrollment lookup failed (rid=%s, email=%s): %s",
                        rid, mask_email(email) if email else "<none>", e, exc_info=True)
        raise HTTPException(status_code=500,
            detail=f"Failed to load enrollments ({type(e).__name__}). request_id: {rid}")

    # Expand each teacher-wide enrollment into per-exam entries. Per-exam
    # membership lives in student_invites — the canonical roster written by
    # the teacher's bulk-register / Email-Invites tools and by student
    # self-registration. Emit one lobby card per (teacher, exam) the
    # student is invited to (REVOKED excluded). An enrollment with NO
    # invite row falls back to a single teacher-scoped entry (exam_id=None
    # resolves the teacher's exam in the loop) so silent-rostered / legacy
    # students still see their exam rather than nothing. This replaces the
    # old behaviour that read students.exam_id (a non-existent column) and
    # therefore always surfaced the teacher's FIRST exam.
    active_inv_statuses = [s.value for s in InviteStatus if s != InviteStatus.REVOKED]
    expanded: list[dict] = []
    seen: set = set()
    for enr in enrollments:
        roll = enr.get("roll_number")
        enr_tid = enr.get("teacher_id")
        if not roll or not enr_tid:
            continue
        enr_tid = str(enr_tid)
        try:
            inv_rows = (await _atable("student_invites")
                        .select("exam_id")
                        .eq("teacher_id", enr_tid)
                        .eq("roll_number", roll)
                        .in_("status", active_inv_statuses)
                        .execute()).data or []
        except Exception as e:
            _auth_log.warning("[student/exams] invite lookup failed (roll=%s tid=%s): %s",
                              roll, enr_tid, e)
            inv_rows = []
        eids = [eid for eid in (str(r.get("exam_id") or "").strip() for r in inv_rows) if eid]
        # Standing access (gap #59): also surface exams assigned to the student's
        # batch/cohort, even with no per-exam invite row — so cohort students SEE
        # their exams in the lobby instead of needing the access code to discover
        # them. Best-effort: never break the lobby on a batch lookup hiccup.
        try:
            srow = (await _atable("students").select("batch")
                    .eq("roll_number", roll).eq("teacher_id", enr_tid)
                    .limit(1).execute()).data
            sbatch = ((srow[0].get("batch") if srow else "") or "").strip()
            if sbatch:
                bx = (await _atable("exam_batch_assignments").select("exam_id")
                      .eq("teacher_id", enr_tid).eq("batch", sbatch).execute()).data or []
                for r in bx:
                    e = str(r.get("exam_id") or "").strip()
                    if e and e not in eids:
                        eids.append(e)
        except Exception as e:
            _auth_log.warning("[student/exams] batch exam lookup failed (roll=%s tid=%s): %s",
                              roll, enr_tid, e)
        if not eids:
            eids = [None]  # fallback: resolve the teacher's exam in the loop
        for eid in eids:
            key = (enr_tid, eid)
            if key in seen:
                continue
            seen.add(key)
            expanded.append({"roll_number": roll, "teacher_id": enr_tid, "exam_id": eid})
    enrollments = expanded

    exams = []
    now = datetime.now(timezone.utc)

    for enr in enrollments:
      try:
        teacher_id = enr.get("teacher_id")
        if not teacher_id:
            continue
        teacher_id = str(teacher_id)

        # Get exam config. If the enrollment row carries an exam_id
        # (the student registered via a teacher_id+exam_id share link
        # or was added to a specific exam's roster), filter by THAT
        # exam_id so we surface the right exam — not whichever happens
        # to be first for the teacher. Falls back to teacher-only
        # lookup when the enrollment is teacher-scoped only.
        enrolled_exam_id = (enr.get("exam_id") or "").strip() if isinstance(enr, dict) else ""
        cfg_q = _atable("exam_config").select("*").eq("teacher_id", teacher_id)
        if enrolled_exam_id:
            cfg_q = cfg_q.eq("exam_id", enrolled_exam_id)
        config_result = await cfg_q.limit(1).execute()
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

        # Check for existing session. Scope by exam_id when available so a
        # completed/in-progress attempt in one exam cannot hide or relabel
        # another exam for the same teacher + roll number.
        session_q = _atable("exam_sessions").select(
            "status, submitted_at"
        ).eq("teacher_id", teacher_id).eq(
            "roll_number", enr["roll_number"]
        ).eq("status", SessionStatus.IN_PROGRESS)
        if exam_id:
            session_q = session_q.eq("exam_id", exam_id)
        session_result = await session_q.limit(1).execute()
        session = session_result.data[0] if session_result.data else None

        # If no in_progress session, check for a completed one
        if not session:
            done_q = _atable("exam_sessions").select(
                "status, submitted_at"
            ).eq("teacher_id", teacher_id).eq(
                "roll_number", enr["roll_number"]
            )
            if exam_id:
                done_q = done_q.eq("exam_id", exam_id)
            done_result = await done_q.order("submitted_at", desc=True).limit(1).execute()
            if done_result.data:
                session = done_result.data[0]

        # Compute status
        if session:
            st = (session.get("status") or "").lower()
            if st == SessionStatus.IN_PROGRESS:
                status = "in_progress"
            elif st in (SessionStatus.COMPLETED, SessionStatus.SUBMITTED,
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
            "access_code_required": bool(str(cfg.get("access_code") or "").strip()),
            "status": status,
            "submitted_at": session.get("submitted_at") if session else None,
        })
      except Exception as e:
        # One bad enrollment row shouldn't blank out the student's
        # whole lobby. Log it and skip — the rest of the loop still
        # populates other exams correctly.
        rid = getattr(request.state, "request_id", "") or "-"
        _auth_log.warning(
            "[student/exams] enrichment failed for enrollment (rid=%s, roll=%s, tid=%s): %s",
            rid, enr.get("roll_number"), enr.get("teacher_id"), e, exc_info=True)
        continue

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

    enrollments = await _student_enrollments_for_account(
        account, email, "roll_number,teacher_id,full_name",
    )
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
                    .in_("status", [SessionStatus.COMPLETED, SessionStatus.FORCE_SUBMITTED])
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
        # Expired or tampered link — we can't read its `kind` claim safely
        # (decoding without signature verification would be a security
        # finding), so route to the marketing root which links to both
        # the teacher and student dashboards. The user picks the right
        # one and requests a new verification from that login page.
        return HTMLResponse(_fill_template(EMAIL_VERIFY_HTML, {
            "title": "Link expired or invalid",
            "msg": "This verification link has expired or is invalid. Open Procta from your bookmarks (or procta.net) and request a new verification email from your login page.",
            "login_url": "https://procta.net",
            "btn": "Go to Procta",
        }), status_code=400)

    # Route the post-verify "Log In" button to the right dashboard. Without
    # this, a student-account verify link landed them on /dashboard which
    # is the TEACHER login — they couldn't sign in there at all.
    _login_url = "/student" if claims.get("kind") == "student_account" else "/dashboard"

    user_id = claims.get("uid", "")
    kind = claims.get("kind", "teacher")
    table = "teachers" if kind == "teacher" else "student_accounts"

    await _atable(table).update({"email_verified_at": now_ist().isoformat()}).eq("id", user_id).execute()

    # Mirror the OTP-verify path: link pre-existing students rows AFTER
    # ownership is proven. resend-verification can issue email-link
    # tokens for student_accounts (auth.py:2043), so the same hijack
    # vector applies on this code path too.
    if kind == "student_account":
        await _auto_link_student_enrollments(
            str(user_id), str(claims.get("email") or "").strip().lower(),
        )

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
                except (ValueError, TypeError):
                    # Fail closed — without a usable expiry we can't verify
                    # the invite is still valid, so refuse the org-linking.
                    # The email is still verified (above), the teacher just
                    # doesn't get auto-attached to the org via this path.
                    _auth_log.warning("auth: verify_email invite expires parse failed (id=%s)", invite.get("id"))
                    expired = True
            if not expired:
                teacher_res = await _atable("teachers").select("org_id").eq("id", user_id).limit(1).execute()
                teacher = teacher_res.data[0] if teacher_res.data else {}
                if not teacher.get("org_id"):
                    await _atable("teachers").update({
                        "org_id": str(invite["org_id"]),
                        "org_role": "teacher",
                        "status": "active",
                    }).eq("id", user_id).execute()
                    clear_teacher_cache(str(user_id))
                    await _atable("org_invites").update({
                        "status": "accepted",
                        "accepted_at": datetime.now(timezone.utc).isoformat(),
                    }).eq("id", invite["id"]).execute()
                else:
                    await _atable("teachers").update({"status": "active"}).eq("id", user_id).execute()
                    clear_teacher_cache(str(user_id))
                    # Teacher already belongs to an org — this invite can never
                    # be fulfilled, so retire it instead of leaving it stuck in
                    # pending_verification forever (pollutes org-invite listings
                    # and blocks a clean re-invite).
                    await _atable("org_invites").update({
                        "status": "expired",
                    }).eq("id", invite["id"]).execute()
            else:
                await _atable("org_invites").update({"status": "expired"}).eq("id", invite["id"]).execute()
        else:
            await _atable("teachers").update({"status": "active"}).eq("id", user_id).execute()
            clear_teacher_cache(str(user_id))

    await record_auth_event("email_verified", request, kind, user_id, claims.get("email"))

    return HTMLResponse(_fill_template(EMAIL_VERIFY_HTML, {
        "title": "Email verified!",
        "msg": "Your email has been verified. You can now log in to Procta.",
        "login_url": _login_url,
        "btn": "Log In",
    }))


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
    clear_teacher_cache(tid)
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
    clear_teacher_cache(tid)
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
    await _revoke_refresh_tokens_for_user(tid, "teacher")
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
            await asyncio.to_thread(supabase.auth.admin.sign_out, supabase_uid)
    except (AttributeError, Exception):
        pass
    await record_auth_event("logout", request, "teacher", tid)
    response = JSONResponse({"ok": True})
    _clear_teacher_cookies(response)
    return response


@router.post("/api/v1/auth/sessions/revoke-others")
@limiter.limit("10/minute")
async def revoke_other_sessions(request: Request, body: dict = Body(default_factory=dict)):
    """Revoke all sessions except the current one.

    Kills both access-session jtis AND every active refresh token for
    this user — without the refresh sweep, a leaked refresh token would
    just mint a new access token a few seconds later and undo the
    revoke. The current device's access token survives until its 12 h
    expiry; the user will need to log in fresh on this device after
    that. That's the security/UX tradeoff: "panic button" beats
    "convenience".

    Requires a fresh reauth_token (P1.2): a stolen access cookie
    shouldn't be able to "panic button" the legitimate user's other
    devices out of the way without re-entering the password.
    """
    from ..auth.admin_auth import require_reauth_or_403
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    require_reauth_or_403(body, tid, request=request)
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
                try:
                    send_password_reset_email(
                        email,
                        user.get("full_name", ""),
                        f"{base}/reset-password?token={token}",
                    )
                except Exception:
                    _auth_log.warning("[StudentPasswordReset] Email send failed")
        else:
            try:
                await asyncio.to_thread(supabase.auth.reset_password_for_email, email)
            except Exception:
                _auth_log.warning("[StudentPasswordReset] Supabase reset email failed")
    finally:
        await asyncio.sleep(max(0.0, 0.35 - (time.monotonic() - started)))
    return {"status": "sent"}


RESET_PASSWORD_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Reset password — Procta</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--accent:#5b8af0;--accent-dark:#4a78e0;--navy:#0f172a;--ink:#0f172a;--muted:#64748b}
*{box-sizing:border-box}
body{margin:0;min-height:100dvh;display:flex;align-items:center;justify-content:center;padding:24px;
  background:radial-gradient(1100px 520px at 50% -10%,#1c2742 0%,#0f172a 55%,#0a0d12 100%);
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:var(--ink)}
.wrap{width:100%;max-width:420px;animation:rise .5s cubic-bezier(.2,.7,.2,1) both}
.brand{display:flex;align-items:center;justify-content:center;gap:9px;margin-bottom:18px}
.brand .mark{width:34px;height:34px;border-radius:9px;background:var(--accent);display:flex;align-items:center;
  justify-content:center;box-shadow:0 6px 20px rgba(91,138,240,.45)}
.brand .name{color:#fff;font-size:18px;font-weight:700;letter-spacing:-.01em}
.card{background:#fff;border-radius:18px;padding:30px 30px 26px;
  box-shadow:0 24px 60px rgba(2,6,23,.55),0 0 0 1px rgba(255,255,255,.04)}
h1{font-size:21px;margin:0 0 6px;letter-spacing:-.01em}
.sub{color:var(--muted);font-size:13.5px;line-height:1.5;margin:0 0 18px}
label{display:block;font-size:12.5px;font-weight:600;margin:0 0 6px;color:#334155}
.field{position:relative}
input{width:100%;padding:12px 44px 12px 13px;border:1px solid #cbd5e1;border-radius:10px;font-size:15px;
  color:var(--ink);outline:none;transition:border-color .15s,box-shadow .15s;background:#fff}
input:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(91,138,240,.18)}
.toggle{position:absolute;right:6px;top:50%;transform:translateY(-50%);border:0;background:transparent;
  color:var(--muted);font-size:12px;font-weight:600;cursor:pointer;padding:6px 8px;border-radius:7px}
.toggle:hover{color:var(--accent);background:#f1f5f9}
.hint{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--muted);margin:8px 2px 0}
.hint .dot{width:7px;height:7px;border-radius:50%;background:#cbd5e1;transition:background .15s}
.hint.good .dot{background:#10b981}.hint.good{color:#047857}
button.submit{width:100%;margin-top:18px;padding:13px;border:0;border-radius:10px;background:var(--accent);
  color:#fff;font-weight:600;font-size:15px;cursor:pointer;transition:background .15s,transform .06s,box-shadow .15s;
  box-shadow:0 8px 20px rgba(91,138,240,.32)}
button.submit:hover:not(:disabled){background:var(--accent-dark);box-shadow:0 10px 26px rgba(91,138,240,.42)}
button.submit:active:not(:disabled){transform:translateY(1px)}
button.submit:disabled{opacity:.55;cursor:not-allowed;box-shadow:none}
.msg{display:none;margin-top:14px;padding:11px 13px;border-radius:10px;font-size:13px;line-height:1.45}
.err{color:#b91c1c;background:#fef2f2;border:1px solid #fecaca}
.ok{color:#065f46;background:#ecfdf5;border:1px solid #a7f3d0}
.login-btn{display:none;width:100%;margin-top:16px;padding:12px;border-radius:10px;text-align:center;
  text-decoration:none;font-weight:600;font-size:14px;color:#fff;background:var(--navy)}
.foot{text-align:center;color:#64748b;font-size:11.5px;margin-top:16px}
@keyframes rise{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
</style></head>
<body><div class="wrap">
<div class="brand">
  <span class="mark"><svg width="20" height="20" viewBox="0 0 16 16" fill="none" aria-hidden="true">
    <path d="M4 2 H12 Q13.5 2 13.5 3.5 V8 Q13.5 12 8 14 Q2.5 12 2.5 8 V3.5 Q2.5 2 4 2 Z" fill="none" stroke="#fff" stroke-width="1.2" stroke-linejoin="round"/>
    <circle cx="8" cy="8" r="1.5" fill="#fff"/></svg></span>
  <span class="name">Procta</span>
</div>
<div class="card">
  <h1>Reset your password</h1>
  <p class="sub">Choose a new password for your Procta account. Use at least 10 characters.</p>
  <form id="f" novalidate data-token="%(token)s">
    <label for="password">New password</label>
    <div class="field">
      <input id="password" type="password" minlength="10" autocomplete="new-password" required placeholder="Enter a new password">
      <button type="button" class="toggle" id="toggle" aria-label="Show password">Show</button>
    </div>
    <div class="hint" id="hint"><span class="dot"></span><span id="hint-text">At least 10 characters</span></div>
    <button class="submit" id="btn" type="submit" disabled>Update password</button>
  </form>
  <div class="msg err" id="err"></div>
  <div class="msg ok" id="ok">Password updated. You can now sign in with your new password.</div>
  <a class="login-btn" id="login-btn" href="/dashboard">Go to login</a>
</div>
<p class="foot">If you didn't request this, you can safely ignore the email.</p>
</div>
<script src="/static/reset-password.js" defer></script></body></html>"""


RESET_ERROR_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>%(title)s — Procta</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--accent:#5b8af0;--navy:#0f172a;--muted:#64748b}
*{box-sizing:border-box}
body{margin:0;min-height:100dvh;display:flex;align-items:center;justify-content:center;padding:24px;
  background:radial-gradient(1100px 520px at 50% -10%,#1c2742 0%,#0f172a 55%,#0a0d12 100%);
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#0f172a}
.wrap{width:100%;max-width:420px;animation:rise .5s cubic-bezier(.2,.7,.2,1) both}
.brand{display:flex;align-items:center;justify-content:center;gap:9px;margin-bottom:18px}
.brand .mark{width:34px;height:34px;border-radius:9px;background:var(--accent);display:flex;align-items:center;
  justify-content:center;box-shadow:0 6px 20px rgba(91,138,240,.45)}
.brand .name{color:#fff;font-size:18px;font-weight:700;letter-spacing:-.01em}
.card{background:#fff;border-radius:18px;padding:32px 30px;text-align:center;
  box-shadow:0 24px 60px rgba(2,6,23,.55),0 0 0 1px rgba(255,255,255,.04)}
.icon{width:48px;height:48px;border-radius:50%;background:#fef2f2;display:flex;align-items:center;
  justify-content:center;margin:0 auto 16px}
h1{font-size:20px;margin:0 0 8px;letter-spacing:-.01em}
p{color:var(--muted);font-size:14px;line-height:1.55;margin:0 0 22px}
.btn{display:block;width:100%;padding:12px;border-radius:10px;text-decoration:none;font-weight:600;
  font-size:14px;color:#fff;background:var(--accent);box-shadow:0 8px 20px rgba(91,138,240,.32)}
@keyframes rise{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
</style></head>
<body><div class="wrap">
<div class="brand">
  <span class="mark"><svg width="20" height="20" viewBox="0 0 16 16" fill="none" aria-hidden="true">
    <path d="M4 2 H12 Q13.5 2 13.5 3.5 V8 Q13.5 12 8 14 Q2.5 12 2.5 8 V3.5 Q2.5 2 4 2 Z" fill="none" stroke="#fff" stroke-width="1.2" stroke-linejoin="round"/>
    <circle cx="8" cy="8" r="1.5" fill="#fff"/></svg></span>
  <span class="name">Procta</span>
</div>
<div class="card">
  <div class="icon"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#dc2626" stroke-width="2" stroke-linecap="round">
    <circle cx="12" cy="12" r="9"/><path d="M12 7v6"/><circle cx="12" cy="16.5" r=".5" fill="#dc2626"/></svg></div>
  <h1>%(title)s</h1>
  <p>%(message)s</p>
  <a class="btn" href="/dashboard">Back to login</a>
</div>
</div>
</body></html>"""


def _reset_error_page(title: str, message: str, status_code: int) -> HTMLResponse:
    return HTMLResponse(
        _fill_template(RESET_ERROR_HTML, {"title": _esc(title), "message": _esc(message)}),
        status_code=status_code,
    )


@router.get("/reset-password")
async def reset_password_page(token: str = ""):
    if not local_password_auth_enabled():
        return _reset_error_page(
            "Password reset unavailable",
            "Password reset for this account is handled by your identity provider. "
            "Open Procta and sign in from there.",
            404,
        )
    if not verify_password_reset_token(token):
        return _reset_error_page(
            "Reset link expired",
            "This password reset link has expired or already been used. "
            "Request a new one from the login page and use it within 30 minutes.",
            400,
        )
    return HTMLResponse(_fill_template(RESET_PASSWORD_HTML, {"token": _esc(token)}))


@router.post("/api/v1/auth/password-reset/confirm")
@limiter.limit("5/minute")
async def confirm_password_reset(body: dict, request: Request):
    if not local_password_auth_enabled():
        raise HTTPException(status_code=404, detail="Password reset is handled by the auth provider")
    token = (body.get("token") or "").strip()
    password = body.get("password") or ""
    # Every reject path below is logged with a distinct reason. Previously all
    # six collapsed into one opaque "Reset link expired or invalid" with NO
    # server log, so a failing reset was undiagnosable. Keep the reasons
    # specific (server side) and the user message accurate (client side).
    claims = verify_password_reset_token(token)
    if not claims:
        _auth_log.info("[password_reset_confirm] reject=token_verify_failed (expired/signature/scope)")
        raise HTTPException(status_code=400,
            detail="This reset link has expired. Request a new one from the login page.")
    try:
        await validate_password_async(password)
    except PasswordError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Explicit kind allow-list — defence in depth in case a future
    # issuer accepts a bad kind. Without this, an unexpected value
    # would silently default to student_accounts.
    kind = claims.get("kind")
    if kind not in ("teacher", "student"):
        _auth_log.warning("[password_reset_confirm] reject=bad_kind kind=%s", safe(str(kind)))
        raise HTTPException(status_code=400,
            detail="This reset link is invalid. Request a new one from the login page.")
    table = "teachers" if kind == "teacher" else "student_accounts"
    user_id = str(claims.get("uid") or "")
    if not user_id:
        _auth_log.warning("[password_reset_confirm] reject=missing_uid kind=%s", kind)
        raise HTTPException(status_code=400,
            detail="This reset link is invalid. Request a new one from the login page.")

    # Single-use enforcement: when the token was minted, it embedded the
    # user's current `password_changed_at` (or None for legacy tokens
    # minted before this column was wired). Fetch the live value; if it
    # doesn't match, the token has already been used (the column moved
    # forward) OR the user changed their password through another flow.
    # The comparison is instant-based (_pwc_equal), not raw-string, so a
    # +05:30/+00:00 or precision difference between the mint-time read and
    # this confirm-time read does NOT falsely reject a genuine first use.
    pwc_claim = claims.get("pwc")
    live = await _atable(table).select("password_changed_at").eq("id", user_id).limit(1).execute()
    if not live.data:
        _auth_log.warning("[password_reset_confirm] reject=user_not_found kind=%s id=%s",
                          kind, safe(user_id))
        raise HTTPException(status_code=400,
            detail="This reset link is invalid. Request a new one from the login page.")
    live_pwc = _stringify_pwc(live.data[0].get("password_changed_at"))
    if pwc_claim is not None:
        if not _pwc_equal(live_pwc, pwc_claim):
            _auth_log.info("[password_reset_confirm] reject=pwc_mismatch_already_used kind=%s id=%s",
                           kind, safe(user_id))
            raise HTTPException(status_code=400,
                detail="This reset link has already been used. Request a new one from the login page.")
    elif live_pwc is not None:
        # Legacy token (no embedded pwc) but the account has since set a
        # password — the link predates the current credential, treat as stale.
        _auth_log.info("[password_reset_confirm] reject=legacy_token_stale kind=%s id=%s",
                       kind, safe(user_id))
        raise HTTPException(status_code=400,
            detail="This reset link has expired. Request a new one from the login page.")

    await _atable(table).update({
        "password_hash": await hash_password(password),
        "auth_provider": "local",
        "password_changed_at": now_ist().isoformat(),
    }).eq("id", user_id).execute()
    # Evict any session active before the reset — refresh + live access.
    await _revoke_refresh_tokens_for_user(user_id, kind)
    await _revoke_auth_sessions_for_user(user_id, kind)
    await record_auth_event("password_reset_completed", request, kind, user_id, claims.get("email"))
    _auth_log.info("[password_reset_confirm] success kind=%s id=%s", kind, safe(user_id))
    return {"ok": True}


# ─────── TRACK A: signup verify + account delete ───────

async def _track_a_hydrate_student_account(account: dict) -> dict:
    if account.get("email_verified_at") is not None and account.get("supabase_uid") is not None:
        return account
    account_id = str(account.get("id") or "")
    if not account_id:
        return account
    row = await _atable("student_accounts").select(
        "id,email,full_name,supabase_uid,email_verified_at"
    ).eq("id", account_id).limit(1).execute()
    return row.data[0] if row.data else account


async def _track_a_issue_signup_otp(account: dict, email: str | None = None) -> None:
    from ..services import email_otp
    from ..services.email_otp import OtpRateLimitError
    from ..emailer import send_2fa_otp_email

    account_id = str(account.get("id") or "")
    to_email = (email or account.get("email") or "").strip().lower()
    if not account_id or not to_email:
        return
    # Swallow the per-(user, purpose) hourly cap silently. The endpoint
    # surface already returns a uniform "sent: true" response to defeat
    # enumeration; surfacing rate-limit state to one caller but not
    # another would re-introduce that oracle.
    try:
        code = await email_otp.issue("student", account_id, "signup_verify")
    except OtpRateLimitError:
        _auth_log.info("[signup_otp] rate-limited account=%s", safe(account_id))
        return
    send_2fa_otp_email(to_email, account.get("full_name") or "", code, purpose="signup")


async def _auto_link_student_enrollments(account_id: str, email: str) -> None:
    """Link pre-existing students rows whose email matches this account.

    Called ONLY from verified-ownership paths (verify-signup-otp,
    verify-email). The student_signup endpoint used to call this
    immediately, which let an attacker who knew a victim's email seize
    the victim's pre-enrollments by signing up first — verification
    later by a confused victim then validated the takeover. Doing the
    link only after ownership is proven removes that path.
    """
    if not (account_id and email):
        return
    try:
        await _atable("students")\
            .update({"account_id": account_id})\
            .ilike("email", email)\
            .is_("account_id", "null")\
            .execute()
    except Exception as e:
        _auth_log.warning("[Verify] auto-link warning for %s: %s", mask_email(email), e)


@router.post("/api/v1/student/auth/verify-signup-otp")
@limiter.limit("10/hour")
async def student_verify_signup_otp(body: dict, request: Request):
    from ..services import email_otp

    email = ((body or {}).get("email") or "").strip().lower()
    code = re.sub(r"\D", "", str((body or {}).get("code") or ""))
    if not _looks_like_email(email) or len(code) != 6:
        raise HTTPException(status_code=403, detail="Invalid or expired code")
    account = await _get_student_by_email_for_auth(email)
    if not account:
        raise HTTPException(status_code=403, detail="Invalid or expired code")
    if account.get("email_verified_at"):
        return {"ok": True, "already_verified": True}
    ok = await email_otp.verify("student", str(account["id"]), "signup_verify", code)
    if not ok:
        raise HTTPException(status_code=403, detail="Invalid or expired code")
    await _atable("student_accounts").update({
        "email_verified_at": now_ist().isoformat(),
    }).eq("id", str(account["id"])).execute()
    # Auto-link AFTER verification — see _auto_link_student_enrollments
    # docstring for the hijack vector this defers against.
    await _auto_link_student_enrollments(str(account["id"]), email)
    await record_auth_event("email_verified", request, "student_account", str(account["id"]), email, {"method": "otp"})
    return {"ok": True}


@router.post("/api/v1/student/auth/resend-signup-otp")
@limiter.limit("5/hour")
async def student_resend_signup_otp(body: dict, request: Request):
    started = time.monotonic()
    email = ((body or {}).get("email") or "").strip().lower()
    if not _looks_like_email(email):
        raise HTTPException(status_code=400, detail="A valid email is required")
    try:
        account = await _get_student_by_email_for_auth(email)
        if account and not account.get("email_verified_at"):
            await _track_a_issue_signup_otp(account, email)
    finally:
        await asyncio.sleep(max(0.0, 0.35 - (time.monotonic() - started)))
    return {"sent": True, "expires_in": 600}


async def _track_a_recent_teacher_for_student(account: dict) -> tuple[dict | None, dict | None]:
    account_id = str(account.get("id") or "")
    email = (account.get("email") or "").strip().lower()
    candidates: list[dict] = []
    if account_id:
        rows = await _atable("students").select(
            "teacher_id,full_name,email,roll_number,created_at"
        ).eq("account_id", account_id).order("created_at", desc=True).limit(1).execute()
        candidates.extend(rows.data or [])
    if not candidates and email:
        rows = await _atable("students").select(
            "teacher_id,full_name,email,roll_number,created_at"
        ).eq("email", email).order("created_at", desc=True).limit(1).execute()
        candidates.extend(rows.data or [])
    if not candidates and account_id:
        rows = await _atable("exam_sessions").select(
            "teacher_id,full_name,email,roll_number,created_at"
        ).eq("student_id", account_id).order("created_at", desc=True).limit(1).execute()
        candidates.extend(rows.data or [])
    if not candidates and email:
        rows = await _atable("exam_sessions").select(
            "teacher_id,full_name,email,roll_number,created_at"
        ).eq("email", email).order("created_at", desc=True).limit(1).execute()
        candidates.extend(rows.data or [])
    ctx = candidates[0] if candidates else None
    teacher = await _get_teacher_by_id(str(ctx.get("teacher_id"))) if ctx and ctx.get("teacher_id") else None
    return teacher, ctx


async def _track_a_hybrid_delete_student_account(account: dict, request: Request) -> dict:
    account = await _track_a_hydrate_student_account(account)
    account_id = str(account.get("id") or "")
    email = (account.get("email") or "").strip().lower()
    anon_id = _uuid.uuid4().hex
    anon_email = f"deleted_user_{anon_id}@deleted.procta.net"
    anon_roll = f"DEL_{anon_id}"
    errors: list[str] = []

    teacher, teacher_ctx = await _track_a_recent_teacher_for_student(account)
    student_name = (teacher_ctx or {}).get("full_name") or account.get("full_name") or "Deleted User"
    student_roll = (teacher_ctx or {}).get("roll_number") or ""
    student_email = (teacher_ctx or {}).get("email") or email

    async def _best_effort(label: str, coro):
        try:
            await coro
        except Exception as exc:
            _auth_log.warning("[StudentDelete] %s failed: %s", label, exc, exc_info=True)
            errors.append(f"{label}: {type(exc).__name__}")

    await _best_effort("exam_sessions update by account", _atable("exam_sessions").update({
        "email": anon_email,
        "full_name": "Deleted User",
        "roll_number": anon_roll,
    }).eq("student_id", account_id).execute())
    if email:
        await _best_effort("exam_sessions update by email", _atable("exam_sessions").update({
            "email": anon_email,
            "full_name": "Deleted User",
            "roll_number": anon_roll,
        }).eq("email", email).execute())
    await _best_effort("appeals anonymise", _atable("appeals").update({
        "student_id": anon_id,
        "roll_number": anon_roll,
    }).eq("student_id", account_id).execute())
    await _best_effort("students delete by account", _atable("students").delete().eq("account_id", account_id).execute())
    if email:
        await _best_effort("students delete by email", _atable("students").delete().eq("email", email).execute())
        await _best_effort("student_invites delete by email", _atable("student_invites").delete().eq("email", email).execute())
    await _best_effort("student_invites delete by account", _atable("student_invites").delete().eq("student_id", account_id).execute())
    await _best_effort("consent_records delete", _atable("consent_records").delete().eq("user_id", account_id).execute())
    await _best_effort("auth_sessions revoke", _atable("auth_sessions").update({
        "revoked_at": now_ist().isoformat(),
    }).eq("user_kind", "student_account").eq("user_id", account_id).execute())
    await _best_effort("refresh_tokens revoke", _revoke_refresh_tokens_for_user(account_id, "student"))

    supabase_uid = str(account.get("supabase_uid") or "")
    if supabase_uid and not is_postgres_backend():
        try:
            await asyncio.to_thread(supabase.auth.admin.delete_user, supabase_uid)
        except Exception as exc:
            _auth_log.warning("[StudentDelete] Supabase user delete failed: %s", exc, exc_info=True)
            errors.append(f"supabase_user_delete: {type(exc).__name__}")
    await _best_effort("student_accounts delete", _atable("student_accounts").delete().eq("id", account_id).execute())

    if teacher and teacher.get("email"):
        try:
            from ..services.notification_prefs import teacher_wants
            tid = str(teacher.get("id") or "")
            if not tid or await teacher_wants(tid, "student_activity"):
                from ..emailer import send_student_account_deleted_to_teacher
                send_student_account_deleted_to_teacher(
                    to_email=teacher.get("email"),
                    to_name=teacher.get("full_name") or "",
                    student_name=student_name,
                    student_email=student_email,
                    student_roll=student_roll,
                    deleted_at_str=fmt_ist(now_ist()),
                )
        except Exception:
            _auth_log.warning("[StudentDelete] teacher notification failed", exc_info=True)

    # Mask the email before it lands in the retained auth_events table —
    # storing the deleted subject's full address there would re-persist the
    # PII this erasure just scrubbed everywhere else (parity with the SAR
    # teacher path's _mask_email; see admin_sar.py).
    masked_email = f"{email[0]}***@{email.split('@', 1)[1]}" if (email and "@" in email) else ""
    await record_auth_event("account_deleted", request, "student_account", account_id, masked_email, {"anon_id": anon_id})
    return {"errors": errors, "anon_id": anon_id}


@router.post("/api/v1/student/account/delete-request")
@limiter.limit("3/hour")
async def student_account_delete_request(request: Request):
    from ..services import email_otp
    from ..services.email_otp import OtpRateLimitError
    from ..emailer import send_2fa_otp_email

    account = await require_student_account(request)
    account_id = str(account["id"])
    email = (account.get("email") or "").strip().lower()
    try:
        code = await email_otp.issue("student", account_id, "account_delete")
    except OtpRateLimitError:
        raise HTTPException(
            status_code=429,
            detail="Too many delete codes requested. Please wait before trying again.",
        )
    send_2fa_otp_email(email, account.get("full_name") or "", code, purpose="delete")
    return {"sent": True, "expires_in": 600}


@router.post("/api/v1/student/account/delete-confirm")
@limiter.limit("6/hour")
async def student_account_delete_confirm(request: Request, body: dict = Body(default_factory=dict)):
    from ..services import email_otp

    account = await require_student_account(request)
    account_id = str(account["id"])
    code = re.sub(r"\D", "", str((body or {}).get("otp_code") or (body or {}).get("code") or ""))
    if len(code) != 6:
        raise HTTPException(status_code=403, detail="Invalid or expired code")
    ok = await email_otp.verify("student", account_id, "account_delete", code)
    if not ok:
        raise HTTPException(status_code=403, detail="Invalid or expired code")
    result = await _track_a_hybrid_delete_student_account(account, request)
    clear_student_account_cache(account_id)
    response = JSONResponse({
        "deleted": True,
        "status": "deleted" if not result["errors"] else "partial",
        "errors": result["errors"] or None,
    })
    _clear_student_cookies(response)
    return response


# ─────── TRACK B: OTP password reset + email change ───────

def _looks_like_email(value: str) -> bool:
    return bool(value and "@" in value and "." in value.rsplit("@", 1)[-1])


async def _track_b_find_user_for_reset(kind: str, email: str) -> dict | None:
    table = "teachers" if kind == "teacher" else "student_accounts"
    result = await _atable(table).select(
        "id,email,full_name,password_hash,password_changed_at,supabase_uid"
    ).eq("email", email).limit(1).execute()
    return result.data[0] if result.data else None


async def _track_b_set_password(kind: str, user: dict, password: str, request: Request) -> None:
    table = "teachers" if kind == "teacher" else "student_accounts"
    user_id = str(user.get("id") or "")
    if not user_id:
        raise HTTPException(status_code=400, detail="Invalid or expired code")
    payload = {
        "password_hash": await hash_password(password),
        "auth_provider": "local",
        "password_changed_at": now_ist().isoformat(),
    }
    await _atable(table).update(payload).eq("id", user_id).execute()
    supabase_uid = str(user.get("supabase_uid") or "")
    if supabase_uid and not local_password_auth_enabled():
        try:
            await asyncio.to_thread(
                supabase.auth.admin.update_user_by_id,
                supabase_uid,
                {"password": password},
            )
        except Exception:
            _auth_log.warning("[PasswordResetOtp] Supabase password update failed", exc_info=True)
    await _revoke_refresh_tokens_for_user(user_id, kind)
    # Also kill live access-token sessions — a password reset is the
    # "lock the intruder out" action, so it must evict any session that
    # was active before the reset, not just the refresh path.
    await _revoke_auth_sessions_for_user(user_id, kind)
    await record_auth_event("password_reset_completed", request, kind, user_id, user.get("email"))


async def _track_b_send_password_reset_otp(kind: str, email: str) -> None:
    from ..services import email_otp
    from ..services.email_otp import OtpRateLimitError
    from ..emailer import send_2fa_otp_email

    user = await _track_b_find_user_for_reset(kind, email)
    if not user:
        return
    purpose = "teacher_password_reset" if kind == "teacher" else "password_reset"
    # As with signup OTP: swallow rate-limit so the public reset-request
    # endpoint returns the same shape whether the user exists, the user
    # doesn't exist, or the user is just hitting the per-hour cap.
    try:
        code = await email_otp.issue(kind, str(user["id"]), purpose)
    except OtpRateLimitError:
        _auth_log.info("[password_reset_otp] rate-limited kind=%s user=%s", kind, safe(str(user.get("id"))))
        return
    send_2fa_otp_email(email, user.get("full_name") or "", code, purpose="password_reset")


async def _student_reset_request_is_authenticated_for_email(request: Request, email: str) -> bool:
    """Allow logged-in students to request their own reset OTP without CAPTCHA."""
    try:
        account = await require_student_account(request)
    except HTTPException:
        return False
    except Exception:
        _auth_log.debug("[password_reset_otp] student auth probe failed", exc_info=True)
        return False
    return (account.get("email") or "").strip().lower() == email


@router.post("/api/v1/student/auth/reset-request")
@limiter.limit("3/minute")
async def student_password_reset_otp_request(body: dict, request: Request):
    started = time.monotonic()
    email = ((body or {}).get("email") or "").strip().lower()
    if not _looks_like_email(email):
        raise HTTPException(status_code=400, detail="A valid email is required")
    if not await _student_reset_request_is_authenticated_for_email(request, email):
        await verify_or_403(request, (body or {}).get("captcha_token"))
    try:
        await _track_b_send_password_reset_otp("student", email)
    finally:
        await asyncio.sleep(max(0.0, 0.35 - (time.monotonic() - started)))
    return {"sent": True, "expires_in": 600}


@router.post("/api/v1/student/auth/reset-confirm")
@limiter.limit("5/minute")
async def student_password_reset_otp_confirm(body: dict, request: Request):
    from ..services import email_otp
    from ..emailer import send_student_password_changed_notification

    email = ((body or {}).get("email") or "").strip().lower()
    code = re.sub(r"\D", "", str((body or {}).get("code") or ""))
    password = (body or {}).get("new_password") or (body or {}).get("password") or ""
    if not _looks_like_email(email) or len(code) != 6:
        raise HTTPException(status_code=403, detail="Invalid or expired code")
    try:
        await validate_password_async(password)
    except PasswordError as e:
        raise HTTPException(status_code=400, detail=str(e))
    user = await _track_b_find_user_for_reset("student", email)
    if not user:
        raise HTTPException(status_code=403, detail="Invalid or expired code")
    ok = await email_otp.verify("student", str(user["id"]), "password_reset", code)
    if not ok:
        raise HTTPException(status_code=403, detail="Invalid or expired code")
    await _track_b_set_password("student", user, password, request)
    try:
        send_student_password_changed_notification(
            to_email=email,
            to_name=user.get("full_name") or "",
            changed_at_str=fmt_ist(now_ist()),
            ip=request.client.host if request.client else "",
        )
    except Exception:
        _auth_log.warning("[PasswordResetOtp] password-changed email failed", exc_info=True)
    return {"ok": True}


@router.post("/api/v1/teacher/auth/reset-request")
@router.post("/api/v1/auth/password-reset/otp-request")
@limiter.limit("3/minute")
async def teacher_password_reset_otp_request(body: dict, request: Request):
    started = time.monotonic()
    await verify_or_403(request, (body or {}).get("captcha_token"))
    email = ((body or {}).get("email") or "").strip().lower()
    if not _looks_like_email(email):
        raise HTTPException(status_code=400, detail="A valid email is required")
    try:
        await _track_b_send_password_reset_otp("teacher", email)
    finally:
        await asyncio.sleep(max(0.0, 0.35 - (time.monotonic() - started)))
    return {"sent": True, "expires_in": 600}


@router.post("/api/v1/teacher/auth/reset-confirm")
@router.post("/api/v1/auth/password-reset/otp-confirm")
@limiter.limit("5/minute")
async def teacher_password_reset_otp_confirm(body: dict, request: Request):
    from ..services import email_otp

    email = ((body or {}).get("email") or "").strip().lower()
    code = re.sub(r"\D", "", str((body or {}).get("code") or ""))
    password = (body or {}).get("new_password") or (body or {}).get("password") or ""
    if not _looks_like_email(email) or len(code) != 6:
        raise HTTPException(status_code=403, detail="Invalid or expired code")
    try:
        await validate_password_async(password)
    except PasswordError as e:
        raise HTTPException(status_code=400, detail=str(e))
    user = await _track_b_find_user_for_reset("teacher", email)
    if not user:
        raise HTTPException(status_code=403, detail="Invalid or expired code")
    ok = await email_otp.verify("teacher", str(user["id"]), "teacher_password_reset", code)
    if not ok:
        raise HTTPException(status_code=403, detail="Invalid or expired code")
    await _track_b_set_password("teacher", user, password, request)
    return {"ok": True}


@router.post("/api/v1/student/account/email-change-request")
@limiter.limit("5/hour")
async def student_email_change_request(request: Request, body: dict = Body(default_factory=dict)):
    from ..auth.admin_auth import require_reauth_or_403
    from ..services import email_otp
    from ..services.email_otp import OtpRateLimitError
    from ..emailer import send_2fa_otp_email, send_student_email_change_heads_up

    account = await require_student_account(request)
    account_id = str(account["id"])
    require_reauth_or_403(body, account_id, request=request)
    new_email = ((body or {}).get("new_email") or "").strip().lower()
    if not _looks_like_email(new_email):
        raise HTTPException(status_code=400, detail="A valid new email is required")
    old_email = (account.get("email") or "").strip().lower()
    if new_email == old_email:
        raise HTTPException(status_code=400, detail="New email must be different")
    existing = await _atable("student_accounts").select("id").eq("email", new_email).limit(1).execute()
    if existing.data:
        raise HTTPException(status_code=409, detail="That email is already in use")
    try:
        # Bind the OTP to the *target* address. Without this, a code issued
        # for new_email=A could be replayed on a confirm that names a
        # different new_email=B — moving the account to an address that never
        # received a code. Folding new_email into the purpose-tag makes the
        # code verify ONLY for the exact address it was mailed to.
        code = await email_otp.issue("student", account_id, f"email_change:{new_email}")
    except OtpRateLimitError:
        raise HTTPException(
            status_code=429,
            detail="Too many email-change codes requested. Please wait before trying again.",
        )
    send_2fa_otp_email(new_email, account.get("full_name") or "", code)
    if old_email:
        try:
            send_student_email_change_heads_up(
                to_email=old_email,
                to_name=account.get("full_name") or "",
                new_email=new_email,
                requested_at_str=fmt_ist(now_ist()),
                ip=request.client.host if request.client else "",
            )
        except Exception:
            _auth_log.warning("[EmailChange] heads-up email failed", exc_info=True)
    return {"sent": True, "expires_in": 600}


@router.post("/api/v1/student/account/email-change-confirm")
@limiter.limit("10/hour")
async def student_email_change_confirm(request: Request, body: dict = Body(default_factory=dict)):
    from ..services import email_otp

    account = await require_student_account(request)
    account_id = str(account["id"])
    new_email = ((body or {}).get("new_email") or "").strip().lower()
    code = re.sub(r"\D", "", str((body or {}).get("code") or ""))
    if not _looks_like_email(new_email) or len(code) != 6:
        raise HTTPException(status_code=403, detail="Invalid or expired code")
    existing = await _atable("student_accounts").select("id").eq("email", new_email).limit(1).execute()
    if existing.data and str(existing.data[0].get("id")) != account_id:
        raise HTTPException(status_code=409, detail="That email is already in use")
    ok = await email_otp.verify("student", account_id, f"email_change:{new_email}", code)
    if not ok:
        raise HTTPException(status_code=403, detail="Invalid or expired code")
    old_email = (account.get("email") or "").strip().lower()
    try:
        await _atable("student_accounts").update({
            "email": new_email,
        }).eq("id", account_id).execute()
    except Exception as exc:
        # A concurrent signup/change could have claimed new_email between the
        # pre-check above and this write; the DB unique constraint
        # (student_accounts_email_unique) is the real arbiter. Surface that
        # race as a clean 409 rather than a 500.
        err_lower = str(exc).lower()
        if "duplicate key" in err_lower or "unique constraint" in err_lower or "already exists" in err_lower:
            raise HTTPException(status_code=409, detail="That email is already in use")
        raise
    await _atable("students").update({"email": new_email}).eq("account_id", account_id).execute()
    if old_email:
        await _atable("students").update({"email": new_email}).eq("email", old_email).execute()
    row = await _atable("student_accounts").select("supabase_uid").eq("id", account_id).limit(1).execute()
    supabase_uid = str((row.data[0] if row.data else {}).get("supabase_uid") or "")
    if supabase_uid and not local_password_auth_enabled():
        try:
            await asyncio.to_thread(
                supabase.auth.admin.update_user_by_id,
                supabase_uid,
                {"email": new_email, "email_confirm": True},
            )
        except Exception:
            _auth_log.warning("[EmailChange] Supabase email update failed", exc_info=True)
    await record_auth_event("email_changed", request, "student_account", account_id, new_email, {"old_email": old_email})
    clear_student_account_cache(account_id)
    return {"ok": True, "email": new_email}


# ─── TEACHER NOTIFICATION PREFERENCES ──────────────────────────


@router.get("/api/v1/notification-preferences")
@limiter.limit("30/minute")
async def get_notification_preferences(request: Request):
    teacher = await require_admin(request)
    from ..services.notification_prefs import get_prefs
    try:
        prefs = await get_prefs(str(teacher["id"]))
    except Exception as e:
        msg = str(e).lower()
        if "notification_prefs" in msg and ("column" in msg or "schema cache" in msg):
            raise HTTPException(status_code=503, detail="Notification preferences are not available until the latest migration is applied.")
        raise
    return prefs


@router.patch("/api/v1/notification-preferences")
@limiter.limit("20/minute")
async def update_notification_preferences(request: Request):
    teacher = await require_admin(request)
    from ..services.notification_prefs import get_prefs, KNOWN_CATEGORIES
    body = await request.json()
    unknown = set(body.keys()) - KNOWN_CATEGORIES
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown categories: {', '.join(sorted(unknown))}")
    for k, v in body.items():
        if not isinstance(v, bool):
            raise HTTPException(status_code=400, detail=f"Value for '{k}' must be a boolean")
    tid = str(teacher["id"])
    try:
        current_raw = (await _atable("teachers").select("notification_prefs")
                       .eq("id", tid).limit(1).execute()).data or []
        current = {}
        if current_raw and current_raw[0].get("notification_prefs"):
            raw = current_raw[0]["notification_prefs"]
            if isinstance(raw, dict):
                current = dict(raw)
            elif isinstance(raw, str):
                import json
                current = json.loads(raw)
        current.update(body)
        import json as _json
        await (_atable("teachers")
               .update({"notification_prefs": _json.dumps(current)})
               .eq("id", tid)
               .execute())
    except Exception as e:
        msg = str(e).lower()
        if "notification_prefs" in msg and ("column" in msg or "schema cache" in msg):
            raise HTTPException(status_code=503, detail="Notification preferences are not available until the latest migration is applied.")
        raise
    await record_auth_event(
        "preference_updated",
        request,
        "teacher",
        tid,
        teacher.get("email", ""),
        {"notification_prefs": current},
    )
    merged = await get_prefs(tid)
    return merged


# ─── OAUTH SIGN-IN — REMOVED 2026-05-23 ──────────────────────────
# Google + Microsoft OAuth (formerly /api/v1/auth/oauth/start and
# /api/v1/auth/oauth/callback) were removed in favour of email +
# password as the sole sign-in method. Operational complexity
# (provider app reviews, scope changes, token rotation) outweighed
# the conversion benefit at our stage. To re-enable, restore from
# git history before this commit AND re-add the Google + Microsoft
# providers in the Supabase Auth dashboard.
