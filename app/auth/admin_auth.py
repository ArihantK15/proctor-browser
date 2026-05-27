"""Admin and student-dashboard auth with DB-backed lookups."""
import logging
import threading
import time
from collections import OrderedDict

from fastapi import Request, HTTPException
import jwt
from jwt.exceptions import InvalidTokenError as JWTError

from ..constants import (
    ADMIN_SIGNING_KEYS,
    STUDENT_SIGNING_KEYS,
    SUPER_ADMIN_EMAIL,
    _TEACHER_CACHE_MAX,
    _STUDENT_ACCT_CACHE_MAX,
)
from ..database import async_table as _atable

logger = logging.getLogger(__name__)


def _maybe_promote_super_admin(teacher: dict | None) -> dict | None:
    """Stamp org_role='superadmin' on the master account.

    SUPER_ADMIN_EMAIL is an env-controlled escape hatch — a teacher row
    whose email matches it is treated as superadmin regardless of the
    DB-side org_role. Centralised here so every teacher-loading path
    (require_admin, /auth/me, teacher_login, scope resolution) sees
    the same promoted role. Reverting the inline promotion in
    require_admin() that was removed in 04cd262.
    """
    if not teacher:
        return teacher
    if SUPER_ADMIN_EMAIL and str(teacher.get("email", "")).strip().lower() == SUPER_ADMIN_EMAIL:
        teacher["org_role"] = "superadmin"
    return teacher


try:
    from .. import cache as _cache
except Exception:
    _cache = None


def _decode_with_keys(token: str, keys: list[str], **kwargs) -> dict:
    last_err: Exception | None = None
    for key in keys:
        try:
            return jwt.decode(token, key, algorithms=["HS256"], **kwargs)
        except JWTError as e:
            last_err = e
    raise last_err or JWTError("Token could not be decoded with any key")


def _bearer_or_cookie(request: Request, cookie_name: str) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return request.cookies.get(cookie_name, "")

# ─── Teacher lookup cache ─────────────────────────────────────────
_teacher_cache: OrderedDict = OrderedDict()
_teacher_cache_ttl: dict[str, float] = {}
_teacher_cache_lock = threading.Lock()


async def _get_teacher_by_id(teacher_id: str) -> dict | None:
    if not teacher_id:
        return None
    if _cache:
        cached = _cache.get(f"teacher:{teacher_id}")
        if cached:
            return cached
    else:
        now = time.time()
        with _teacher_cache_lock:
            if teacher_id in _teacher_cache and _teacher_cache_ttl.get(teacher_id, 0) > now:
                _teacher_cache.move_to_end(teacher_id)
                return _teacher_cache[teacher_id]
    result = (await _atable("teachers").select("id,email,full_name,org_id,org_role,supabase_uid").eq("id", str(teacher_id)).execute()).data
    if not result:
        return None
    teacher = _maybe_promote_super_admin(result[0])
    if _cache:
        _cache.set(f"teacher:{teacher_id}", teacher, ttl=60)
    else:
        now = time.time()
        with _teacher_cache_lock:
            _teacher_cache[teacher_id] = teacher
            _teacher_cache.move_to_end(teacher_id)
            _teacher_cache_ttl[teacher_id] = now + 60
            while len(_teacher_cache) > _TEACHER_CACHE_MAX:
                oldest = next(iter(_teacher_cache))
                _teacher_cache.popitem(last=False)
                _teacher_cache_ttl.pop(oldest, None)
    return teacher


async def _get_teacher_by_uid(uid: str) -> dict | None:
    if not uid:
        return None
    result = (await _atable("teachers").select("id,email,full_name,org_id,org_role,email_verified_at,status,email_2fa_enabled_at").eq("supabase_uid", str(uid)).execute()).data
    if not result:
        return None
    return _maybe_promote_super_admin(result[0])


async def verify_admin_token(token: str) -> dict:
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        payload = _decode_with_keys(token, ADMIN_SIGNING_KEYS,
                                    options={"require": ["exp", "tid"]})
    except JWTError as e:
        msg = str(e).lower()
        if "expired" in msg:
            raise HTTPException(status_code=401, detail="Token expired")
        raise HTTPException(status_code=401, detail="Invalid token")
    if payload.get("role") != "teacher":
        raise HTTPException(status_code=403, detail="Not a teacher token")

    # Session revocation check. Redis is a fast negative cache, but the
    # Postgres auth_sessions row is the source of truth so a cache miss,
    # expiry, or Redis outage cannot resurrect a revoked access token.
    jti = payload.get("jti", "")
    if jti:
        try:
            from .. import cache as _cache
            cached = _cache.get(f"session:{jti}") if _cache else None
            if cached and isinstance(cached, dict) and cached.get("revoked"):
                raise HTTPException(status_code=401, detail="Session has been revoked")
        except HTTPException:
            raise
        except Exception:
            logger.debug("admin_auth: revocation cache lookup failed", exc_info=True)

    tid = payload.get("tid")
    teacher = await _get_teacher_by_id(tid)
    if not teacher:
        raise HTTPException(status_code=403, detail="Teacher account not found")
    if jti:
        revoked = await _atable("auth_sessions").select("jti")\
            .eq("jti", str(jti)).eq("user_kind", "teacher").eq("user_id", str(tid))\
            .not_.is_("revoked_at", "null").limit(1).execute()
        if revoked.data:
            raise HTTPException(status_code=401, detail="Session has been revoked")
    return teacher


async def require_admin(request: Request) -> dict:
    token = _bearer_or_cookie(request, "procta_access")
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    return await verify_admin_token(token)


def require_reauth_or_403(
    body: dict | None,
    user_id: str,
    *,
    request: Request | None = None,
) -> None:
    """Guard for destructive admin actions.

    The caller must already have a valid access token (via require_admin).
    On top of that we require a short-lived "I just re-typed my password"
    proof — a `reauth_token` either in the request body field of the
    same name, or in the ``X-Reauth-Token`` header. The body path keeps
    backwards compat with the existing email_2fa_enable + admin_submit
    callers; the header path lets DELETE handlers (where adding a body
    is awkward) wire up without changing their signature.

    Tokens are obtained by the client from POST /api/v1/auth/reauth
    (5-minute expiry, scope=reauth).

    Rationale: an attacker who briefly takes over a teacher's session
    (XSS, stolen access cookie before refresh-rotation kicks in, a
    forgotten unlocked laptop) shouldn't be able to delete a whole exam,
    kick a colleague out, or force-submit a student's in-progress exam
    without re-proving they know the password. Each of those four sinks
    requires a fresh reauth_token; a 5-minute window lets a real user
    chain several destructive actions without re-prompting, but a stolen
    session is locked out the moment the token expires.

    Raises HTTPException(403) on missing / wrong-uid / expired token.
    Pattern matches the inline check already in app/routers/auth.py's
    email_2fa_enable; consolidated here so new destructive endpoints
    can share one line of plumbing.
    """
    from .tokens import _decode_token
    from ..constants import REAUTH_SIGNING_KEYS
    body = body or {}
    token = (body.get("reauth_token") or "").strip()
    if not token and request is not None:
        token = (request.headers.get("X-Reauth-Token") or "").strip()
    if not token:
        raise HTTPException(
            status_code=403,
            detail="Re-authentication required for this action",
        )
    try:
        claims = _decode_token(token, REAUTH_SIGNING_KEYS)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=403,
            detail="Re-authentication token expired or invalid",
        ) from exc
    if claims.get("scope") != "reauth":
        raise HTTPException(status_code=403, detail="Wrong token scope")
    if str(claims.get("uid")) != str(user_id):
        raise HTTPException(status_code=403, detail="Re-authentication does not match caller")


# ─── Student-account (dashboard) auth ────────────────────────────
_student_acct_cache: OrderedDict = OrderedDict()
_student_acct_cache_ttl: dict[str, float] = {}
_student_acct_cache_lock = threading.Lock()


async def _get_student_account_by_id(account_id: str) -> dict | None:
    if not account_id:
        return None
    now = time.time()
    with _student_acct_cache_lock:
        if account_id in _student_acct_cache and _student_acct_cache_ttl.get(account_id, 0) > now:
            _student_acct_cache.move_to_end(account_id)
            return _student_acct_cache[account_id]
    result = (await _atable("student_accounts").select("id,email,full_name").eq("id", str(account_id)).execute()).data
    if not result:
        return None
    acct = result[0]
    with _student_acct_cache_lock:
        _student_acct_cache[account_id] = acct
        _student_acct_cache.move_to_end(account_id)
        _student_acct_cache_ttl[account_id] = now + 60
        while len(_student_acct_cache) > _STUDENT_ACCT_CACHE_MAX:
            oldest = next(iter(_student_acct_cache))
            _student_acct_cache.popitem(last=False)
            _student_acct_cache_ttl.pop(oldest, None)
    return acct


async def _get_student_account_by_uid(uid: str) -> dict | None:
    if not uid:
        return None
    result = (await _atable("student_accounts").select("id,email,full_name").eq("supabase_uid", str(uid)).execute()).data
    if not result:
        return None
    return result[0]


async def verify_student_auth_token(token: str) -> dict:
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        payload = _decode_with_keys(token, STUDENT_SIGNING_KEYS,
                                    options={"verify_aud": False, "require": ["exp", "sid"]})
    except JWTError as e:
        msg = str(e).lower()
        if "expired" in msg:
            raise HTTPException(status_code=401, detail="Token expired")
        raise HTTPException(status_code=401, detail="Invalid token")
    if payload.get("role") != "student_account":
        raise HTTPException(status_code=403, detail="Not a student token")
    jti = payload.get("jti", "")
    if jti:
        try:
            from .. import cache as _cache
            cached = _cache.get(f"session:{jti}") if _cache else None
            if cached and isinstance(cached, dict) and cached.get("revoked"):
                raise HTTPException(status_code=401, detail="Session has been revoked")
        except HTTPException:
            raise
        except Exception:
            logger.debug("admin_auth: revocation cache lookup failed", exc_info=True)
    sid = payload.get("sid")
    account = await _get_student_account_by_id(sid)
    if not account:
        raise HTTPException(status_code=403, detail="Student account not found")
    if jti:
        revoked = await _atable("auth_sessions").select("jti")\
            .eq("jti", str(jti)).eq("user_kind", "student_account").eq("user_id", str(sid))\
            .not_.is_("revoked_at", "null").limit(1).execute()
        if revoked.data:
            raise HTTPException(status_code=401, detail="Session has been revoked")
    return account


async def require_student_account(request: Request) -> dict:
    token = _bearer_or_cookie(request, "procta_student_access")
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    return await verify_student_auth_token(token)
