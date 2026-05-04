"""Redis-backed cache for hot data (exam config, questions, access codes, etc.).

Falls back to a no-op when Redis is unavailable so the app still works
without Redis (just slower).
"""
import json
import os
import base64
import pickle
import time

import redis

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
_LIVEFRAME_MAX = int(os.environ.get("LIVEFRAME_MAX_SESSIONS", "50"))

_r: redis.Redis | None = None
_r_healthy: bool = False  # tracks whether _r has been successfully pinged


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
    except Exception:
        _r = None
        _r_healthy = False
    return _r


def get(key: str) -> dict | list | None:
    """Return cached value or None on miss / error."""
    global _r_healthy
    try:
        r = _client()
        if r is None:
            return None
        raw = r.get(key)
        if raw is None:
            return None
        # liveframe keys use pickle (raw jpeg bytes can't be JSON-encoded)
        if key.startswith("liveframe:"):
            # Data is base64-encoded pickle (to survive decode_responses=True)
            return pickle.loads(base64.b64decode(raw))
        return json.loads(raw)
    except (redis.ConnectionError, redis.TimeoutError, ConnectionError, OSError):
        _r_healthy = False
        return None
    except Exception:
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
        pass


def set_live_frame(session_id: str, jpeg_bytes: bytes, ttl: int = 10) -> None:
    """Store a live camera frame with enforced LRU cap.

    Pickles the frame dict then base64-encodes it so it survives the
    redis-py decode_responses=True client. Maintains a sorted set of
    liveframe keys by timestamp for LRU eviction.
    """
    global _r_healthy
    if ttl <= 0:
        return
    try:
        r = _client()
        if r is None:
            return

        key = f"liveframe:{session_id}"
        now = time.time()
        # Pickle then base64 — survives decode_responses=True client
        payload = base64.b64encode(
            pickle.dumps({"jpeg_bytes": jpeg_bytes, "at": now})
        ).decode("ascii")
        r.setex(key, ttl, payload)

        # Track in sorted set for LRU eviction
        r.zadd("liveframe:_index", {session_id: now})
        r.expire("liveframe:_index", ttl + 5)

        # Evict oldest if over cap
        total = r.zcard("liveframe:_index")
        if total > _LIVEFRAME_MAX:
            to_remove = total - _LIVEFRAME_MAX
            oldest = r.zrange("liveframe:_index", 0, to_remove - 1)
            if oldest:
                oldest_keys = [f"liveframe:{s.decode()}" if isinstance(s, bytes) else f"liveframe:{s}" for s in oldest]
                r.delete(*oldest_keys)
                r.zrem("liveframe:_index", *oldest)
    except (redis.ConnectionError, redis.TimeoutError, ConnectionError, OSError):
        _r_healthy = False
    except Exception:
        pass


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
        pass


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
        pass
