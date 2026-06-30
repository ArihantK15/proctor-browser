"""Tests for the Redis pub/sub event bus (app/event_bus.py).

publish/async_publish/subscribe are the core SSE notification path for
violations, heartbeats, and submissions. These tests verify message
routing, reconnection behaviour, and keepalive sentinels.
"""
import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


# The conftest replaces sys.modules["app.event_bus"] with a MagicMock;
# we import the real module under a separate name.

_saved_event_bus = sys.modules.pop("app.event_bus", None)

import importlib

# Mock redis modules so the real event_bus imports cleanly with _HAS_REDIS=True
_redis_mod = MagicMock()
_redis_mod.ConnectionError = ConnectionError
_redis_mod.TimeoutError = TimeoutError
_aioredis_mod = MagicMock()
sys.modules["redis"] = _redis_mod
sys.modules["redis.asyncio"] = _aioredis_mod

_app_event_bus_spec = importlib.util.find_spec("app.event_bus")
_event_bus = importlib.util.module_from_spec(_app_event_bus_spec)
_app_event_bus_spec.loader.exec_module(_event_bus)

sys.modules["app.event_bus"] = _saved_event_bus


def _reset_globals():
    _event_bus._sync = None
    _event_bus._async_pool = None
    _event_bus._async_lock = None


# ── publish (sync) ──────────────────────────────────────────────────


class TestPublish:
    def setup_method(self):
        _reset_globals()
        _redis_mod.Redis.from_url.reset_mock()

    def test_publishes_json_to_channel(self):
        client = MagicMock()
        _redis_mod.Redis.from_url.return_value = client

        _event_bus.publish("sessions:t1", {"type": "violation", "count": 3})

        client.publish.assert_called_once()
        channel, payload = client.publish.call_args[0]
        assert channel == "sessions:t1"
        assert json.loads(payload) == {"type": "violation", "count": 3}

    def test_resets_sync_on_connection_error(self):
        client = MagicMock()
        client.publish.side_effect = ConnectionError("refused")
        _redis_mod.Redis.from_url.return_value = client

        _event_bus.publish("ch", {"msg": "x"})

        assert _event_bus._sync is None

    def test_swallows_unexpected_error(self):
        client = MagicMock()
        client.publish.side_effect = ValueError("weird")
        _redis_mod.Redis.from_url.return_value = client

        _event_bus.publish("ch", {"msg": "x"})

    def test_reuses_client(self):
        client = MagicMock()
        _redis_mod.Redis.from_url.return_value = client

        assert _event_bus._sync is None
        _event_bus.publish("c1", {"a": 1})
        sync_client = _event_bus._sync
        assert sync_client is not None
        _event_bus.publish("c2", {"b": 2})

        assert _event_bus._sync is sync_client
        assert client.publish.call_count == 2


# ── async_publish ───────────────────────────────────────────────────


class TestAsyncPublish:
    def setup_method(self):
        _reset_globals()

    @pytest.mark.asyncio
    async def test_publishes_json_to_channel(self):
        client = AsyncMock()
        client.publish = AsyncMock()
        _event_bus._get_async = AsyncMock(return_value=client)

        await _event_bus.async_publish("events:t1:s1", {"type": "heartbeat"})

        client.publish.assert_awaited_once()
        channel, payload = client.publish.call_args[0]
        assert channel == "events:t1:s1"
        assert json.loads(payload) == {"type": "heartbeat"}

    @pytest.mark.asyncio
    async def test_reconnects_on_connection_error(self):
        failing = AsyncMock()
        failing.publish = AsyncMock(side_effect=ConnectionError("lost"))
        _event_bus._get_async = AsyncMock(return_value=failing)
        reconnect_mock = AsyncMock()
        _event_bus._reconnect_async = reconnect_mock

        await _event_bus.async_publish("ch", {"k": "v"})

        reconnect_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_swallows_unexpected_error(self):
        client = AsyncMock()
        client.publish = AsyncMock(side_effect=ValueError("weird"))
        _event_bus._get_async = AsyncMock(return_value=client)

        await _event_bus.async_publish("ch", {"k": "v"})


# ── subscribe (async generator) ─────────────────────────────────────


class TestSubscribe:
    def setup_method(self):
        _reset_globals()

    @staticmethod
    def _make_pubsub():
        """Build a mock Redis client + pubsub with all methods async."""
        client = MagicMock()
        pubsub = MagicMock()
        client.pubsub.return_value = pubsub

        # Make every coroutine-method on pubsub return an awaitable coroutine.
        for name in ("subscribe", "unsubscribe", "close", "get_message"):
            m = AsyncMock()
            setattr(pubsub, name, m)
        return client, pubsub

    async def _run(self, gen, timeout=1.0):
        """Advance the generator to the first yield; fail on timeout."""
        try:
            return await asyncio.wait_for(gen.__anext__(), timeout=timeout)
        except asyncio.TimeoutError:
            pytest.fail("generator timed out without yielding")
        finally:
            await gen.aclose()

    @pytest.mark.asyncio
    async def test_yields_messages(self):
        client, pubsub = self._make_pubsub()
        pubsub.get_message.return_value = {
            "type": "message", "data": '{"event": "violation"}',
        }
        _event_bus._get_async = AsyncMock(return_value=client)

        msg = await self._run(
            _event_bus.subscribe("sessions:t1", keepalive_sec=999)
        )
        assert msg == {"event": "violation"}
        pubsub.subscribe.assert_called_once_with("sessions:t1")

    @pytest.mark.asyncio
    async def test_skips_corrupt_json_then_keepalive(self):
        client, pubsub = self._make_pubsub()
        pubsub.get_message.side_effect = [
            {"type": "message", "data": "not-json"},
            None,
        ]
        _event_bus._get_async = AsyncMock(return_value=client)

        msg = await self._run(
            _event_bus.subscribe("c", keepalive_sec=0)
        )
        assert msg == {"_keepalive": True}

    @pytest.mark.asyncio
    async def test_keepalive_sentinel(self):
        client, pubsub = self._make_pubsub()
        pubsub.get_message.return_value = None
        _event_bus._get_async = AsyncMock(return_value=client)

        msg = await self._run(
            _event_bus.subscribe("c", keepalive_sec=0)
        )
        assert msg == {"_keepalive": True}

    @pytest.mark.asyncio
    async def test_reconnects_on_error(self):
        client, pubsub = self._make_pubsub()
        _event_bus._get_async = AsyncMock(return_value=client)

        call_count = 0
        async def _side(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("broken pipe")
            return None
        pubsub.get_message.side_effect = _side

        msg = await self._run(
            _event_bus.subscribe("c", keepalive_sec=0),
            timeout=2.0,  # long enough for 0.5s reconnect backoff + yield
        )
        assert msg == {"_keepalive": True}
        assert call_count >= 2
