"""Idempotency key helper — prevents duplicate processing of the same request."""

import logging
from typing import Optional

log = logging.getLogger("idempotency")

_IDEM_TTL = 300  # 5 minutes — key lives long enough for any retry window


async def check_idempotency(key: str) -> Optional[dict]:
    """Check if an idempotency key has already been processed.
    Returns the cached response dict if found, None otherwise.
    """
    try:
        from .. import cache as _cache
        if not _cache:
            return None
        cached = _cache.get(key)
        if cached and isinstance(cached, dict):
            return cached
    except Exception:
        log.debug("idempotency: cache get failed", exc_info=True)
    return None


async def mark_idempotent(key: str, response: dict) -> None:
    """Store the response for an idempotency key so future requests
    with the same key return the cached result."""
    try:
        from .. import cache as _cache
        if _cache:
            _cache.set(key, response, ttl=_IDEM_TTL)
    except Exception:
        log.debug("idempotency: cache set failed", exc_info=True)


def idempotency_key(prefix: str, teacher_id: str, *parts: str) -> str:
    """Build a namespaced idempotency key."""
    return f"idem:{prefix}:{teacher_id}:" + ":".join(parts)
