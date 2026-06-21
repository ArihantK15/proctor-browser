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


async def reserve_idempotency(key: str, ttl: int = _IDEM_TTL):
    """Atomically reserve an idempotency key — the TOCTOU-safe replacement for
    check_idempotency().

    check_idempotency()+mark_idempotent() is check-then-act: two concurrent
    requests with the same key both see "unseen" and both process (double-click /
    retry storm). This does a single atomic SET NX (cache.set_if_absent), so
    exactly one caller wins the reservation.

    Returns ``(acquired, cached)``:
      * ``(True,  None)`` — you won; PROCESS, then call mark_idempotent(key, resp)
        on success, or release_idempotency(key) on failure so a retry can proceed.
      * ``(False, dict)`` — the request already COMPLETED; return this cached resp.
      * ``(False, None)`` — a concurrent request is IN FLIGHT (reserved, not yet
        marked); treat as a duplicate (e.g. HTTP 409).

    Fails OPEN — returns ``(True, None)`` if the cache is unavailable, mirroring
    set_if_absent, so a Redis outage never wedges a billing endpoint.
    """
    try:
        from .. import cache as _cache
        if not _cache:
            return True, None
        if _cache.set_if_absent(key, ttl):
            return True, None  # we reserved it — first writer wins
        # Already present: either the in-flight marker ("1", not a dict) or the
        # completed response dict written by mark_idempotent.
        existing = _cache.get(key)
        if isinstance(existing, dict):
            return False, existing
        return False, None
    except Exception:
        log.debug("idempotency: reserve failed", exc_info=True)
        return True, None  # fail open — never block on a cache hiccup


async def release_idempotency(key: str) -> None:
    """Drop a reservation made by reserve_idempotency() so a FAILED request can
    be retried immediately (mark_idempotent was never called). No-op-safe."""
    try:
        from .. import cache as _cache
        if _cache:
            _cache.delete(key)
    except Exception:
        log.debug("idempotency: release failed", exc_info=True)


async def mark_idempotent(key: str, response: dict) -> None:
    """Store the response for an idempotency key so future requests with the same
    key return the cached result. Overwrites the in-flight reservation marker."""
    try:
        from .. import cache as _cache
        if _cache:
            _cache.set(key, response, ttl=_IDEM_TTL)
    except Exception:
        log.debug("idempotency: cache set failed", exc_info=True)


def idempotency_key(prefix: str, teacher_id: str, *parts: str) -> str:
    """Build a namespaced idempotency key."""
    return f"idem:{prefix}:{teacher_id}:" + ":".join(parts)
