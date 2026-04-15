"""Redis-backed cache for hot data (exam config, questions, access codes, etc.).

Falls back to a no-op when Redis is unavailable so the app still works
without Redis (just slower).
"""
import json
import os

import redis

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")

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
        return json.loads(raw)
    except (redis.ConnectionError, redis.TimeoutError, ConnectionError, OSError):
        _r_healthy = False  # trigger reconnect on next call
        return None
    except Exception:
        return None


def set(key: str, value, ttl: int = 300) -> None:
    """Cache a JSON-serialisable value with TTL (seconds)."""
    global _r_healthy
    if ttl <= 0:
        return  # Redis setex requires TTL > 0
    try:
        r = _client()
        if r is None:
            return
        r.setex(key, ttl, json.dumps(value, default=str))
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
