"""Redis-backed cache for hot data (exam config, questions, access codes, etc.).

Falls back to a no-op when Redis is unavailable so the app still works
without Redis (just slower).
"""
import json
import os
import time
import logging

import redis

_log = logging.getLogger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
_LIVEFRAME_MAX = int(os.environ.get("LIVEFRAME_MAX_SESSIONS", "50"))
LIVEFRAME_PREFIX = "liveframe:"
ROOMFRAME_PREFIX = "roomframe:"
ROOMCAM_PREFIX = "roomcam:"
LIVEFRAME_INDEX_KEY = f"{LIVEFRAME_PREFIX}_index"

_r: redis.Redis | None = None
_r_healthy: bool = False  # tracks whether _r has been successfully pinged

_br: redis.Redis | None = None  # binary client (decode_responses=False) for JPEG frames
_br_healthy: bool = False


def _client() -> redis.Redis | None:
    global _r, _r_healthy
    if _r is not None and _r_healthy:
        return _r
    # Either no client yet, or previous client was broken — (re)connect
    try:
        _r = redis.Redis.from_url(REDIS_URL, decode_responses=True,
                                   socket_connect_timeout=2,
                                   socket_timeout=2)
        _r.ping()
        _r_healthy = True
    except (redis.ConnectionError, redis.TimeoutError, ConnectionError, OSError):
        _log.warning("Redis connection failed", exc_info=True)
        _r = None
        _r_healthy = False
    except Exception:
        _log.exception("Unexpected Redis client initialisation failure")
        _r = None
        _r_healthy = False
    return _r


def _binary_client() -> redis.Redis | None:
    """Redis client without decode_responses for raw binary JPEG storage."""
    global _br, _br_healthy
    if _br is not None and _br_healthy:
        return _br
    try:
        _br = redis.Redis.from_url(REDIS_URL, decode_responses=False,
                                    socket_connect_timeout=2,
                                    socket_timeout=2)
        _br.ping()
        _br_healthy = True
    except (redis.ConnectionError, redis.TimeoutError, ConnectionError, OSError):
        _log.warning("Redis binary connection failed", exc_info=True)
        _br = None
        _br_healthy = False
    except Exception:
        _log.exception("Unexpected Redis binary client initialisation failure")
        _br = None
        _br_healthy = False
    return _br


def get(key: str) -> dict | list | None:
    """Return cached value or None on miss / error."""
    global _r_healthy
    try:
        # liveframe/roomframe keys use raw binary storage (no base64 overhead)
        if key.startswith(LIVEFRAME_PREFIX) or key.startswith(ROOMFRAME_PREFIX):
            br = _binary_client()
            if br is None:
                return None
            raw_bytes = br.get(key)
            if raw_bytes is None:
                return None
            # Timestamp lives in the sorted-set index (text client)
            session_id = key.split(":", 1)[1]
            ts = time.time()
            try:
                r = _client()
                if r:
                    score = r.zscore(LIVEFRAME_INDEX_KEY, session_id)
                    if score is not None:
                        ts = float(score)
            except (redis.ConnectionError, redis.TimeoutError, ConnectionError, OSError):
                _r_healthy = False
            except Exception:
                _log.exception("Cache liveframe timestamp lookup failed for key=%s", key)
            return {"jpeg_bytes": raw_bytes, "at": ts}
        r = _client()
        if r is None:
            return None
        raw = r.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            _log.warning("Cache value is corrupt JSON for key=%s; deleting", key)
            try:
                r.delete(key)
            except (redis.ConnectionError, redis.TimeoutError, ConnectionError, OSError):
                _r_healthy = False
            except Exception:
                _log.exception("Failed to delete corrupt cache key=%s", key)
            return None
    except (redis.ConnectionError, redis.TimeoutError, ConnectionError, OSError):
        _r_healthy = False
        return None
    except Exception:
        _log.exception("Unexpected cache get failure for key=%s", key)
        return None


def set(key: str, value, ttl: int = 300) -> None:
    """Cache a JSON-serialisable value with TTL (seconds)."""
    global _r_healthy
    if ttl <= 0:
        return
    try:
        r = _client()
        if r is None:
            return
        r.setex(key, ttl, json.dumps(value, default=str))
    except (redis.ConnectionError, redis.TimeoutError, ConnectionError, OSError):
        _r_healthy = False
    except Exception:
        _log.exception("Unexpected cache set failure for key=%s", key)


def set_live_frame(session_id: str, jpeg_bytes: bytes, ttl: int = 10) -> None:
    """Store a live camera frame with enforced LRU cap.

    JPEG bytes are stored as raw binary (no base64 overhead) via a
    separate Redis client with decode_responses=False. The timestamp
    is tracked in a sorted-set index (text client) for LRU eviction.
    """
    global _r_healthy
    if ttl <= 0:
        return
    try:
        br = _binary_client()
        if br is None:
            return
        r = _client()
        if r is None:
            return

        key = f"{LIVEFRAME_PREFIX}{session_id}"
        now = time.time()
        br.setex(key, ttl, jpeg_bytes)

        # Track in sorted set for LRU eviction (text client)
        r.zadd(LIVEFRAME_INDEX_KEY, {session_id: now})
        r.expire(LIVEFRAME_INDEX_KEY, ttl + 5)

        # Evict oldest if over cap
        total = r.zcard(LIVEFRAME_INDEX_KEY)
        if total > _LIVEFRAME_MAX:
            to_remove = total - _LIVEFRAME_MAX
            oldest = r.zrange(LIVEFRAME_INDEX_KEY, 0, to_remove - 1)
            if oldest:
                oldest_keys = [f"{LIVEFRAME_PREFIX}{s.decode()}" if isinstance(s, bytes) else f"{LIVEFRAME_PREFIX}{s}" for s in oldest]
                br.delete(*oldest_keys)
                r.zrem(LIVEFRAME_INDEX_KEY, *oldest)
    except (redis.ConnectionError, redis.TimeoutError, ConnectionError, OSError):
        _r_healthy = False
    except Exception:
        _log.debug("Liveframe LRU eviction failed", exc_info=True)


def set_room_frame(session_id: str, jpeg_bytes: bytes, ttl: int = 10) -> None:
    """Store a room camera frame (from student's phone) in Redis.

    Raw binary storage (no base64 overhead) via binary Redis client.
    Room frames are per-session, not aggregated per-teacher.
    """
    global _r_healthy
    if ttl <= 0:
        return
    try:
        br = _binary_client()
        if br is None:
            return
        br.setex(f"{ROOMFRAME_PREFIX}{session_id}", ttl, jpeg_bytes)
    except (redis.ConnectionError, redis.TimeoutError, ConnectionError, OSError):
        _r_healthy = False
    except Exception:
        _log.debug("Cache set_room_frame failed for session=%s", session_id, exc_info=True)


def delete(key: str) -> None:
    """Remove a single cache key."""
    global _r_healthy
    try:
        r = _client()
        if r is None:
            return
        r.delete(key)
    except (redis.ConnectionError, redis.TimeoutError, ConnectionError, OSError):
        _r_healthy = False
    except Exception:
        _log.debug("Cache delete failed for key=%s", key, exc_info=True)


def delete_pattern(pattern: str) -> None:
    """Remove all keys matching a glob pattern (e.g. 'exam_config:tid:*')."""
    global _r_healthy
    try:
        r = _client()
        if r is None:
            return
        cursor = 0
        while True:
            cursor, keys = r.scan(cursor, match=pattern, count=100)
            if keys:
                r.delete(*keys)
            if cursor == 0:
                break
    except (redis.ConnectionError, redis.TimeoutError, ConnectionError, OSError):
        _r_healthy = False
    except Exception:
        _log.debug("Cache pattern delete failed for pattern=%s", pattern, exc_info=True)


# ── Async wrappers ────────────────────────────────────────────────
# The sync Redis client blocks the event loop for the duration of each
# call (typically < 1 ms, but up to socket_timeout on failures).
# These thin wrappers push the blocking call to the default thread pool
# executor so async route handlers stay non-blocking.

import asyncio as _asyncio


async def aget(key: str):
    """Async-safe version of get()."""
    return await _asyncio.get_event_loop().run_in_executor(None, get, key)


async def aset(key: str, value, ttl: int = 300) -> None:
    """Async-safe version of set()."""
    await _asyncio.get_event_loop().run_in_executor(None, lambda: set(key, value, ttl))


async def adelete(key: str) -> None:
    """Async-safe version of delete()."""
    await _asyncio.get_event_loop().run_in_executor(None, delete, key)


async def adelete_pattern(pattern: str) -> None:
    """Async-safe version of delete_pattern()."""
    await _asyncio.get_event_loop().run_in_executor(None, delete_pattern, pattern)


def cleanup_room_frames() -> None:
    """Delete all roomframe:* keys (belt-and-suspenders — Redis TTL handles daily expiry).
    
    Intended to be called as a daily cron or on startup to ensure no
    room camera frames persist beyond their intended lifetime.
    """
    try:
        delete_pattern(f"{ROOMFRAME_PREFIX}*")
        _log.info("Room frame cache cleaned")
    except Exception:
        _log.warning("Room frame cleanup failed", exc_info=True)


async def acleanup_room_frames() -> None:
    """Async-safe version of cleanup_room_frames()."""
    await _asyncio.get_event_loop().run_in_executor(None, cleanup_room_frames)
