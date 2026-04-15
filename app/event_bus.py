"""Redis pub/sub event bus for SSE streaming.

Channels:
  sessions:{teacher_id}  — dashboard live updates (violations, heartbeats, submissions)
  events:{teacher_id}:{session_id} — per-student violation/force-submit feed
"""
import asyncio
import json
import os
import time
from typing import AsyncGenerator

import redis
import redis.asyncio as aioredis

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")

# Sync client for publishing from sync endpoints
_sync: redis.Redis | None = None

# Async client pool for SSE subscribers
_async_pool: aioredis.Redis | None = None


def _get_sync() -> redis.Redis:
    global _sync
    if _sync is None:
        _sync = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    return _sync


_async_lock = asyncio.Lock()

async def _get_async() -> aioredis.Redis:
    global _async_pool
    if _async_pool is None:
        async with _async_lock:
            if _async_pool is None:
                _async_pool = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _async_pool


def publish(channel: str, payload: dict) -> None:
    """Publish a JSON message to a Redis channel (sync, safe from sync endpoints)."""
    try:
        _get_sync().publish(channel, json.dumps(payload, default=str))
    except Exception as e:
        print(f"[EventBus] publish error on {channel}: {e}")


async def async_publish(channel: str, payload: dict) -> None:
    """Publish a JSON message to a Redis channel (async, for async endpoints)."""
    try:
        r = await _get_async()
        await r.publish(channel, json.dumps(payload, default=str))
    except Exception as e:
        print(f"[EventBus] async publish error on {channel}: {e}")


async def subscribe(channel: str, keepalive_sec: int = 15) -> AsyncGenerator[dict, None]:
    """Async generator that yields messages from a Redis pub/sub channel.

    Yields a keepalive sentinel ``{"_keepalive": True}`` every *keepalive_sec*
    seconds so the SSE connection doesn't time out behind proxies.
    """
    r = await _get_async()
    pubsub = r.pubsub()
    await pubsub.subscribe(channel)
    last_msg_time = time.monotonic()
    try:
        while True:
            msg = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0)
            if msg and msg["type"] == "message":
                last_msg_time = time.monotonic()
                try:
                    yield json.loads(msg["data"])
                except (json.JSONDecodeError, TypeError):
                    pass
            else:
                # No message this cycle — check if keepalive is due
                if time.monotonic() - last_msg_time >= keepalive_sec:
                    last_msg_time = time.monotonic()
                    yield {"_keepalive": True}
            # Yield control so other coroutines run
            await asyncio.sleep(0)
    except asyncio.CancelledError:
        pass
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
