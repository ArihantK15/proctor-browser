"""Redis-backed account lockout after failed login attempts.

Falls back to an in-process dict when Redis is unavailable so lockout
protection is never entirely absent — even without Redis, brute-force
attempts recorded in this process are tracked.  The fallback is per-process
only (won't cross workers) but is better than no protection at all.
"""
import threading
import time
import logging

logger = logging.getLogger(__name__)

_MAX_FAILURES = 5
_LOCKOUT_WINDOW = 900  # 15 minutes
_LOCKOUT_DURATION = 900  # 15 minutes

_REDIS_PREFIX = "auth:fail:"
_USER_TTL = _LOCKOUT_WINDOW + 60  # extra 60s buffer

# In-memory fallback: { key: {"count": int, "first_at": float} }
_FALLBACK: dict[str, dict] = {}
_FALLBACK_LOCK = threading.Lock()


def _key(kind: str, identifier: str) -> str:
    return f"{_REDIS_PREFIX}{kind}:{identifier}"


def _redis():
    """Get the shared cache module (lazy import to avoid circular deps)."""
    from .. import cache as _c
    return _c


def _fallback_get(key: str) -> int:
    """Return current failure count from in-memory fallback."""
    with _FALLBACK_LOCK:
        entry = _FALLBACK.get(key)
        if not entry:
            return 0
        # Expire old entries
        if time.monotonic() - entry["first_at"] > _LOCKOUT_WINDOW:
            del _FALLBACK[key]
            return 0
        return entry["count"]


def _fallback_incr(key: str) -> int:
    """Increment failure count in in-memory fallback, return new count."""
    now = time.monotonic()
    with _FALLBACK_LOCK:
        entry = _FALLBACK.get(key)
        if not entry or now - entry["first_at"] > _LOCKOUT_WINDOW:
            _FALLBACK[key] = {"count": 1, "first_at": now}
            return 1
        entry["count"] += 1
        return entry["count"]


def _fallback_delete(key: str) -> None:
    with _FALLBACK_LOCK:
        _FALLBACK.pop(key, None)


async def check_lockout(kind: str, identifier: str) -> tuple[bool, int]:
    """Check if an identifier is currently locked out.
    Returns (is_locked, retry_after_seconds).

    When Redis is unavailable the in-process fallback is used, so lockout
    protection degrades gracefully instead of failing open.
    """
    key = _key(kind, identifier)
    try:
        r = _redis()
        val = r.get(key)
        if val is None:
            return False, 0
        count = int(val) if isinstance(val, (int, str)) else 0
        if count >= _MAX_FAILURES:
            return True, _LOCKOUT_DURATION  # approximate
        return False, 0
    except Exception as e:
        logger.warning("[lockout] Redis check failed, using in-memory fallback: %s", e)
        count = _fallback_get(key)
        if count >= _MAX_FAILURES:
            return True, _LOCKOUT_DURATION
        return False, 0


async def record_failure(kind: str, identifier: str) -> int:
    """Record a failed login attempt. Returns current failure count.

    Always re-arms the TTL after INCR — previously EXPIRE was set only
    on count==1, which meant a failed Redis EXPIRE after the first INCR
    (network blip, reconfigure, etc.) left the counter without a TTL.
    A keyless-expiry counter would have permanently locked the user
    out after 5 failures. Re-arming is idempotent on Redis and gives a
    sliding 15-minute window — stricter than the original "15 min
    from first fail" semantics, which is the right trade for an
    auth-rate-limit gate.
    """
    key = _key(kind, identifier)
    try:
        from ..cache import _client
        r = _client()
        if r is None:
            raise RuntimeError("Redis client unavailable")
        count = r.incr(key)
        r.expire(key, _LOCKOUT_WINDOW)
        return count
    except Exception as e:
        logger.warning("[lockout] Redis record failed, using in-memory fallback: %s", e)
        return _fallback_incr(key)


async def clear_failures(kind: str, identifier: str) -> None:
    """Clear lockout counter after successful login."""
    key = _key(kind, identifier)
    try:
        from ..cache import _client
        r = _client()
        if r:
            r.delete(key)
    except Exception as e:
        logger.warning("[lockout] Redis clear failed: %s", e)
    # Always clear fallback too so a Redis recovery doesn't leave stale state
    _fallback_delete(key)
