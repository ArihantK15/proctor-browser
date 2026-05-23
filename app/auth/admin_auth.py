"""Admin and student-dashboard auth with DB-backed lookups."""
import threading
import time
from collections import OrderedDict

from fastapi import Request, HTTPException
import jwt
from jwt.exceptions import InvalidTokenError as JWTError

from ..constants import SECRET_KEY, SUPER_ADMIN_EMAIL, _TEACHER_CACHE_MAX, _STUDENT_ACCT_CACHE_MAX
from ..database import async_table as _atable
try:
    from .. import cache as _cache
except Exception:
    _cache = None

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
    teacher = result[0]
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
    result = (await _atable("teachers").select("id,email,full_name,org_id,org_role,email_verified_at,status,totp_enabled_at").eq("supabase_uid", str(uid)).execute()).data
    if not result:
        return None
    return result[0]


async def verify_admin_token(token: str) -> dict:
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"],
                             options={"require": ["exp", "tid"]})
    except JWTError as e:
        msg = str(e).lower()
        if "expired" in msg:
            raise HTTPException(status_code=401, detail="Token expired")
        raise HTTPException(status_code=401, detail="Invalid token")
    if payload.get("role") != "teacher":
        raise HTTPException(status_code=403, detail="Not a teacher token")

    # Session revocation check (via Redis cache)
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
            pass

    tid = payload.get("tid")
    teacher = await _get_teacher_by_id(tid)
    if not teacher:
        raise HTTPException(status_code=403, detail="Teacher account not found")
    return teacher


async def require_admin(request: Request) -> dict:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    teacher = await verify_admin_token(auth[7:])
    if teacher.get("email", "").lower() == SUPER_ADMIN_EMAIL:
        teacher["org_role"] = "superadmin"
    return teacher


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
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"],
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
            pass
    sid = payload.get("sid")
    account = await _get_student_account_by_id(sid)
    if not account:
        raise HTTPException(status_code=403, detail="Student account not found")
    return account


async def require_student_account(request: Request) -> dict:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    return await verify_student_auth_token(auth[7:])
