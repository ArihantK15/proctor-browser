"""Redis-backed account lockout after failed login attempts."""
import time
import logging

logger = logging.getLogger(__name__)

_MAX_FAILURES = 5
_LOCKOUT_WINDOW = 900  # 15 minutes
_LOCKOUT_DURATION = 900  # 15 minutes

_REDIS_PREFIX = "auth:fail:"
_USER_TTL = _LOCKOUT_WINDOW + 60  # extra 60s buffer


def _key(kind: str, identifier: str) -> str:
    return f"{_REDIS_PREFIX}{kind}:{identifier}"


def _redis():
    """Get the shared cache module (lazy import to avoid circular deps)."""
    from .. import cache as _c
    return _c


async def check_lockout(kind: str, identifier: str) -> tuple[bool, int]:
    """Check if an identifier is currently locked out.
    Returns (is_locked, retry_after_seconds)."""
    try:
        r = _redis()
        val = r.get(_key(kind, identifier))
        if val is None:
            return False, 0
        count = int(val) if isinstance(val, (int, str)) else 0
        if count >= _MAX_FAILURES:
            # Get remaining TTL from Redis
            return True, _LOCKOUT_DURATION  # approximate
        return False, 0
    except Exception as e:
        logger.warning("[lockout] check failed: %s", e)
        return False, 0


async def record_failure(kind: str, identifier: str) -> int:
    """Record a failed login attempt. Returns current failure count."""
    try:
        from ..cache import _client, _r_healthy as _rh
        r = _client()
        if r is None:
            return 1
        key = _key(kind, identifier)
        count = r.incr(key)
        if count == 1:
            r.expire(key, _LOCKOUT_WINDOW)
        return count
    except Exception as e:
        logger.warning("[lockout] record failed: %s", e)
        return 1


async def clear_failures(kind: str, identifier: str) -> None:
    """Clear lockout counter after successful login."""
    try:
        from ..cache import _client
        r = _client()
        if r:
            r.delete(_key(kind, identifier))
    except Exception as e:
        logger.warning("[lockout] clear failed: %s", e)
