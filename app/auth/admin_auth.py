"""Admin and student-dashboard auth with DB-backed lookups."""
import logging
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone, timedelta

from fastapi import Request, HTTPException
import jwt
from jwt.exceptions import InvalidTokenError as JWTError

from ..constants import (
    ADMIN_SIGNING_KEYS,
    ADMIN_TOKEN_TTL_MINUTES,
    STUDENT_SIGNING_KEYS,
    SUPER_ADMIN_EMAIL,
    _TEACHER_CACHE_MAX,
    _STUDENT_ACCT_CACHE_MAX,
)
from ..database import async_table as _atable
from ..db_context import set_context as _set_db_context

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
    result = (await _atable("teachers").select(
        "id,email,full_name,org_id,org_role,supabase_uid,status,email_verified_at,email_2fa_enabled_at"
    ).eq("id", str(teacher_id)).execute()).data
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


async def _load_org_auth_settings(org_id: str) -> dict:
    """Return (timeout_minutes, max_concurrent) for an org, cached."""
    if not org_id:
        return {"auth_session_timeout_minutes": None, "max_concurrent_auth_sessions": None}
    cache_key = f"org_auth_settings:{org_id}"
    if _cache:
        try:
            cached = _cache.get(cache_key)
            if cached and isinstance(cached, dict):
                return cached
        except Exception:
            pass
    row = (await _atable("organizations")
           .select("auth_session_timeout_minutes,max_concurrent_auth_sessions")
           .eq("id", str(org_id)).limit(1).execute()).data or []
    settings = row[0] if row else {}
    if _cache:
        try:
            _cache.set(cache_key, settings, ttl=300)
        except Exception:
            pass
    return settings


async def _touch_and_check_idle(jti: str, org_timeout_min: int | None,
                                user_kind: str, user_id: str) -> None:
    """Update last_seen_at (throttled) and revoke if idle beyond timeout."""
    if not jti or not org_timeout_min:
        return
    if _cache:
        try:
            throttle_key = f"session_seen:{jti}"
            if _cache.get(throttle_key):
                return
            _cache.set(throttle_key, "1", ttl=60)
        except Exception:
            pass
    try:
        now = datetime.now(timezone.utc).isoformat()
        await _atable("auth_sessions").update({"last_seen_at": now})\
            .eq("jti", str(jti)).eq("user_kind", user_kind)\
            .eq("user_id", str(user_id)).execute()
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=org_timeout_min)).isoformat()
        stale = await _atable("auth_sessions").select("jti")\
            .eq("jti", str(jti)).eq("user_kind", user_kind)\
            .eq("user_id", str(user_id)).is_("revoked_at", "null")\
            .lt("last_seen_at", cutoff).limit(1).execute()
        if stale.data:
            await _atable("auth_sessions").update({"revoked_at": now})\
                .eq("jti", str(jti)).execute()
            if _cache:
                try:
                    _cache.set(f"session:{jti}", {"revoked": True},
                               ttl=ADMIN_TOKEN_TTL_MINUTES * 60)
                except Exception:
                    pass
    except Exception:
        logger.debug("admin_auth: session touch/check failed", exc_info=True)


async def verify_admin_token(token: str) -> dict:
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        payload = _decode_with_keys(token, ADMIN_SIGNING_KEYS,
                                    options={"require": ["exp", "tid"]})
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    if payload.get("role") != "teacher":
        raise HTTPException(status_code=403, detail="Not a teacher token")

    jti = payload.get("jti", "")
    if jti:
        try:
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
    status = (teacher.get("status") or "").lower()
    if status in {"suspended", "deleted"}:
        raise HTTPException(status_code=401, detail="Session has been revoked")
    if jti:
        revoked = await _atable("auth_sessions").select("jti")\
            .eq("jti", str(jti)).eq("user_kind", "teacher").eq("user_id", str(tid))\
            .not_.is_("revoked_at", "null").limit(1).execute()
        if revoked.data:
            raise HTTPException(status_code=401, detail="Session has been revoked")

    org_id = teacher.get("org_id")
    if org_id:
        org_settings = await _load_org_auth_settings(str(org_id))
        timeout_mins = org_settings.get("auth_session_timeout_minutes")
        await _touch_and_check_idle(jti, timeout_mins, "teacher", str(tid))

    return teacher


def clear_teacher_cache(teacher_id: str) -> None:
    """Invalidate cached teacher auth metadata after role/status changes."""
    if not teacher_id:
        return
    if _cache:
        try:
            _cache.delete(f"teacher:{teacher_id}")
        except Exception:
            logger.debug("admin_auth: teacher cache delete failed", exc_info=True)
    with _teacher_cache_lock:
        _teacher_cache.pop(teacher_id, None)
        _teacher_cache_ttl.pop(teacher_id, None)


# Path prefixes a superadmin (org_role) IS allowed to mutate — its own
# cross-org operational tooling, never tenant product data. str.startswith
# accepts this tuple directly. Keep additions narrow and admin-namespaced.
_SUPERADMIN_WRITE_ALLOW = (
    "/api/v1/auth/",
    "/api/v1/admin/issues",
    "/api/v1/admin/coupons",
)


async def require_admin(request: Request) -> dict:
    token = _bearer_or_cookie(request, "procta_access")
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    teacher = await verify_admin_token(token)
    # RLS session context (phase124): scope this request's DB queries to the
    # caller. Inert unless RLS_SESSION_CONTEXT is on. org_role is the app.role
    # (teacher/admin/owner/superadmin).
    _set_db_context(role=teacher.get("org_role"), teacher_id=teacher.get("id"),
                    org_id=teacher.get("org_id"))
    # Superadmin is a cross-org, READ-ONLY monitor for TENANT product data
    # (exams, students, grades, org settings, billing). It may VIEW anything
    # (GET) but must not MUTATE a tenant's data. This single guard covers every
    # admin endpoint, since they all flow through here.
    #
    # Narrow exceptions — paths that are the superadmin's OWN cross-org
    # operational tools, not tenant product data, so writing them is exactly the
    # superadmin's job:
    #   /api/v1/auth/*          sign in / refresh / logout / reauth
    #   /api/v1/admin/issues*   triage the cross-org support inbox (resolve/note)
    #   /api/v1/admin/coupons*  manage global discount codes
    # Anything outside this allowlist (a tenant's exams, students, scores, org
    # settings, billing) stays blocked.
    if (request.method in ("POST", "PUT", "PATCH", "DELETE")
            and str(teacher.get("org_role") or "").lower() == "superadmin"
            and not request.url.path.startswith(_SUPERADMIN_WRITE_ALLOW)):
        raise HTTPException(status_code=403,
            detail="Superadmin is monitor-only and cannot modify product data.")
    return teacher


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


def clear_student_account_cache(account_id: str) -> None:
    """Invalidate cached student account metadata after email/profile changes."""
    if not account_id:
        return
    with _student_acct_cache_lock:
        _student_acct_cache.pop(account_id, None)
        _student_acct_cache_ttl.pop(account_id, None)


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
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    if payload.get("role") != "student_account":
        raise HTTPException(status_code=403, detail="Not a student token")
    jti = payload.get("jti", "")
    if jti:
        try:
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
    account = await verify_student_auth_token(token)
    # RLS session context (phase124) — inert unless RLS_SESSION_CONTEXT is on.
    _set_db_context(role="student", account_id=account.get("id"))
    return account
