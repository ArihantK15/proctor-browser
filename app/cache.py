"""Redis-backed cache for hot data (exam config, questions, access codes, etc.).

Falls back to a no-op when Redis is unavailable so the app still works
without Redis (just slower).
"""
import json
import os

import redis

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")

_r: redis.Redis | None = None


def _client() -> redis.Redis | None:
    global _r
    if _r is None:
        try:
            _r = redis.Redis.from_url(REDIS_URL, decode_responses=True,
                                       socket_connect_timeout=2)
            _r.ping()
        except Exception:
            _r = None
    return _r


def get(key: str) -> dict | list | None:
    """Return cached value or None on miss / error."""
    try:
        r = _client()
        if r is None:
            return None
        raw = r.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:
        return None


def set(key: str, value, ttl: int = 300) -> None:
    """Cache a JSON-serialisable value with TTL (seconds)."""
    try:
        r = _client()
        if r is None:
            return
        r.setex(key, ttl, json.dumps(value, default=str))
    except Exception:
        pass


def delete(key: str) -> None:
    """Remove a single cache key."""
    try:
        r = _client()
        if r is None:
            return
        r.delete(key)
    except Exception:
        pass


def delete_pattern(pattern: str) -> None:
    """Remove all keys matching a glob pattern (e.g. 'exam_config:tid:*')."""
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
    except Exception:
        pass
