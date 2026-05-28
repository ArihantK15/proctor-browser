"""Redis-backed cache for hot data (exam config, questions, access codes, etc.).

Falls back to a no-op when Redis is unavailable so the app still works
without Redis (just slower).
"""
from .log_safe import safe
import json
import os
import time
import logging

import redis

_log = logging.getLogger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
# 3500-student coaching-institute scale + ~85% headroom: default
# lifted from 50 -> 6500 concurrent sessions in the live-frame cache.
# JPEGs are recompressed server-side to quality=60
# (sse._recompress_jpeg), so a single frame is ~30-80 KB.
# 6500 sessions × 60 KB ≈ 390 MB Redis memory — comfortable inside a
# 1 GB Redis maxmemory budget. Override via env when the box has more
# memory; consider moving to Redis Cluster + key sharding once this
# rises past ~10k concurrent sessions or the cache exceeds ~500 MB.
_LIVEFRAME_MAX = int(os.environ.get("LIVEFRAME_MAX_SESSIONS", "6500"))
# Per-frame upper bound — silently drops oversized frames instead of
# letting one misbehaving client thrash the LRU. 1 MB is generous given
# the recompress target; legitimate frames are 30-80 KB.
_LIVEFRAME_MAX_FRAME_BYTES = int(os.environ.get("LIVEFRAME_MAX_FRAME_BYTES", str(1024 * 1024)))
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
                _log.exception("Cache liveframe timestamp lookup failed for key=%s", safe(key))
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
            _log.warning("Cache value is corrupt JSON for key=%s; deleting", safe(key))
            try:
                r.delete(key)
            except (redis.ConnectionError, redis.TimeoutError, ConnectionError, OSError):
                _r_healthy = False
            except Exception:
                _log.exception("Failed to delete corrupt cache key=%s", safe(key))
            return None
    except (redis.ConnectionError, redis.TimeoutError, ConnectionError, OSError):
        _r_healthy = False
        return None
    except Exception:
        _log.exception("Unexpected cache get failure for key=%s", safe(key))
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
        _log.exception("Unexpected cache set failure for key=%s", safe(key))


def set_live_frame(session_id: str, jpeg_bytes: bytes, ttl: int = 10) -> None:
    """Store a live camera frame with enforced LRU cap + size cap.

    JPEG bytes are stored as raw binary (no base64 overhead) via a
    separate Redis client with decode_responses=False. The timestamp
    is tracked in a sorted-set index (text client) for LRU eviction.

    Oversized frames (> LIVEFRAME_MAX_FRAME_BYTES, default 1 MB) are
    silently dropped — protects against a misbehaving client thrashing
    the LRU at 3500-concurrent-session scale. Real frames are 30-80 KB.
    """
    global _r_healthy
    if ttl <= 0:
        return
    if not jpeg_bytes:
        return
    if len(jpeg_bytes) > _LIVEFRAME_MAX_FRAME_BYTES:
        _log.warning(
            "live_frame oversize drop: session=%s bytes=%d max=%d",
            safe(session_id), len(jpeg_bytes), _LIVEFRAME_MAX_FRAME_BYTES,
        )
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
        _log.debug("Cache set_room_frame failed for session=%s", safe(session_id), exc_info=True)


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
        _log.debug("Cache delete failed for key=%s", safe(key), exc_info=True)


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


def live_frame_stats() -> dict:
    """Snapshot of the live-frame cache for observability.

    Returns a dict shaped for the admin /api/v1/admin/live-stats endpoint.
    Used by ops to monitor the cache under load. Fields:
      - cached_sessions: count of sessions currently in the LRU sorted set
      - cap: configured LRU cap (LIVEFRAME_MAX_SESSIONS)
      - utilisation_pct: cached_sessions / cap × 100
      - redis_used_bytes: total memory used by the Redis instance
      - redis_max_bytes: configured Redis maxmemory (0 = unbounded)
      - healthy: Redis ping ok
    All fields are best-effort; any failure returns the partial dict.
    """
    out = {
        "cached_sessions": 0,
        "cap": _LIVEFRAME_MAX,
        "utilisation_pct": 0.0,
        "redis_used_bytes": None,
        "redis_max_bytes": None,
        "healthy": False,
    }
    try:
        r = _client()
        if r is None:
            return out
        out["healthy"] = bool(_r_healthy)
        cached = int(r.zcard(LIVEFRAME_INDEX_KEY) or 0)
        out["cached_sessions"] = cached
        if _LIVEFRAME_MAX > 0:
            out["utilisation_pct"] = round(cached / _LIVEFRAME_MAX * 100, 2)
        try:
            info = r.info(section="memory") or {}
            out["redis_used_bytes"] = int(info.get("used_memory") or 0)
            out["redis_max_bytes"] = int(info.get("maxmemory") or 0)
        except Exception:
            _log.debug("live_frame_stats: redis INFO memory failed", exc_info=True)
    except Exception:
        _log.debug("live_frame_stats: snapshot failed", exc_info=True)
    return out
