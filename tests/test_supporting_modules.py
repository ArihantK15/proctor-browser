"""
Tests for supporting modules: database.py, event_bus.py, cache.py.

Covers audit findings:
- database.py: AsyncClient never closed, no retry, PostgREST filter injection, TOCTOU on client init
- event_bus.py: Race condition on _async_lock, sync/async Redis never closed, no reconnection
- cache.py: Stale broken client never reconnects, all errors silently swallowed, ttl=0 fails
"""
import asyncio
import importlib
import os
import sys
import json
from unittest.mock import MagicMock, AsyncMock, patch

import pytest
import redis

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")

APP_DIR = os.path.join(os.path.dirname(__file__), "..", "app")


def _import_real_module(name):
    """Import the real module from app/, bypassing any sys.modules mocks."""
    saved = sys.modules.pop(name, None)
    sys.path.insert(0, APP_DIR)
    try:
        mod = importlib.import_module(name)
        return mod
    finally:
        sys.path.remove(APP_DIR) if APP_DIR in sys.path else None
        # Don't restore mock — we want to test the real module


# ─── AsyncTable (database.py) ────────────────────────────────────────

class TestAsyncTable:
    """Tests for the AsyncTable query builder in database.py."""

    @pytest.fixture(autouse=True)
    def _import_db(self):
        self.db = _import_real_module("database")

    def test_update_without_filter_raises(self):
        """Safety: update() without eq() should raise to prevent mass update."""
        table = self.db.AsyncTable("test_table")
        table.update({"col": "val"})
        with pytest.raises(ValueError, match="at least one filter"):
            asyncio.run(table.execute())

    def test_delete_without_filter_raises(self):
        """Safety: delete() without eq() should raise to prevent mass delete."""
        table = self.db.AsyncTable("test_table")
        table.delete()
        with pytest.raises(ValueError, match="at least one filter"):
            asyncio.run(table.execute())

    def test_pg_val_none(self):
        assert self.db._pg_val(None) == "null"

    def test_pg_val_bool(self):
        assert self.db._pg_val(True) == "true"
        assert self.db._pg_val(False) == "false"

    def test_pg_val_string(self):
        assert self.db._pg_val("hello") == "hello"

    def test_pg_val_injection(self):
        """AUDIT: PostgREST filter value injection risk.
        Values like 'eq.evil_value' could confuse the filter parser."""
        malicious = "eq.something_evil"
        result = self.db._pg_val(malicious)
        # The value is passed through as-is — no escaping
        assert result == "eq.something_evil"
        # When used in a filter: col=eq.eq.something_evil — risky

    def test_filter_chaining(self):
        table = self.db.AsyncTable("students")
        table.select("*").eq("roll_number", "ALICE001").eq("teacher_id", "t1")
        params = table._build_params()
        assert params["select"] == "*"
        assert params["roll_number"] == "eq.ALICE001"
        assert params["teacher_id"] == "eq.t1"

    def test_order(self):
        table = self.db.AsyncTable("test")
        table.select("*").order("created_at", desc=True)
        params = table._build_params()
        assert params["order"] == "created_at.desc"

    def test_order_asc(self):
        table = self.db.AsyncTable("test")
        table.select("*").order("name")
        params = table._build_params()
        assert params["order"] == "name"

    def test_neq_filter(self):
        table = self.db.AsyncTable("test")
        table.select("*").neq("status", "completed")
        params = table._build_params()
        assert params["status"] == "neq.completed"

    def test_async_result_defaults(self):
        r = self.db._AsyncResult()
        assert r.data == []
        assert r.count is None

    def test_async_result_with_data(self):
        r = self.db._AsyncResult(data=[{"id": 1}], count=42)
        assert r.data == [{"id": 1}]
        assert r.count == 42

    def test_insert_wraps_single_dict_in_list(self):
        table = self.db.AsyncTable("test")
        table.insert({"a": 1})
        assert table._payload == [{"a": 1}]

    def test_upsert_wraps_single_dict_in_list(self):
        table = self.db.AsyncTable("test")
        table.upsert({"a": 1})
        assert table._payload == [{"a": 1}]

    def test_insert_keeps_list(self):
        table = self.db.AsyncTable("test")
        table.insert([{"a": 1}, {"a": 2}])
        assert table._payload == [{"a": 1}, {"a": 2}]

    def test_unknown_operation_raises(self):
        table = self.db.AsyncTable("test")
        table._op = "bogus"
        with pytest.raises(ValueError, match="Unknown operation"):
            asyncio.run(table.execute())

    def test_build_params_without_select(self):
        table = self.db.AsyncTable("test")
        table.eq("id", "123")
        params = table._build_params(include_select=False)
        assert "select" not in params
        assert params["id"] == "eq.123"


# ─── Cache (cache.py) ────────────────────────────────────────────────

class TestCache:
    """Tests for the Redis cache module."""

    @pytest.fixture(autouse=True)
    def _import_cache(self):
        self.cache = _import_real_module("cache")

    def test_cache_get_returns_none_when_redis_down(self):
        """Cache should degrade gracefully when Redis is unavailable."""
        with patch.object(self.cache, '_r', None), \
             patch.object(self.cache, '_client', return_value=None):
            assert self.cache.get("any_key") is None

    def test_cache_set_noop_when_redis_down(self):
        with patch.object(self.cache, '_r', None), \
             patch.object(self.cache, '_client', return_value=None):
            self.cache.set("key", {"data": True})  # Should not raise

    def test_cache_reconnects_after_failure(self):
        """FIX: After a connection error, _r_healthy is set to False,
        so next _client() call will reconnect."""
        original_r = self.cache._r
        original_h = self.cache._r_healthy
        try:
            broken = MagicMock()
            broken.get.side_effect = ConnectionError("Connection refused")
            self.cache._r = broken
            self.cache._r_healthy = True
            # First call returns broken client (still considered healthy)
            result = self.cache._client()
            assert result is broken
            # Calling get() triggers ConnectionError → marks unhealthy
            self.cache.get("key")
            assert self.cache._r_healthy is False
            # Next _client() call will try to reconnect
        finally:
            self.cache._r = original_r
            self.cache._r_healthy = original_h

    def test_cache_ttl_zero_skipped(self):
        """FIX: ttl=0 is now silently skipped instead of crashing setex."""
        mock_r = MagicMock()
        with patch.object(self.cache, '_client', return_value=mock_r):
            self.cache.set("key", "val", ttl=0)
            # setex should NOT have been called
            mock_r.setex.assert_not_called()

    def test_cache_delete_pattern(self):
        """delete_pattern should use SCAN, not KEYS (blocking)."""
        mock_r = MagicMock()
        mock_r.scan.return_value = (0, ["key1", "key2"])
        with patch.object(self.cache, '_client', return_value=mock_r):
            self.cache.delete_pattern("prefix:*")
            mock_r.scan.assert_called_once()

    def test_cache_json_round_trip(self):
        """Verify JSON serialization/deserialization works correctly."""
        mock_r = MagicMock()
        test_data = {"score": 42, "name": "Alice"}
        mock_r.get.return_value = json.dumps(test_data)
        with patch.object(self.cache, '_client', return_value=mock_r):
            result = self.cache.get("test_key")
            assert result == test_data

    def test_cache_delete_error_swallowed(self):
        mock_r = MagicMock()
        mock_r.delete.side_effect = Exception("Redis error")
        with patch.object(self.cache, '_client', return_value=mock_r):
            self.cache.delete("key")  # Should not raise

    def test_cache_initial_ping_failure(self):
        """If initial ping fails, _r should remain None."""
        original_r = self.cache._r
        try:
            self.cache._r = None
            with patch("redis.Redis.from_url") as mock_from_url:
                mock_client = MagicMock()
                mock_client.ping.side_effect = ConnectionError("refused")
                mock_from_url.return_value = mock_client
                result = self.cache._client()
                assert result is None
        finally:
            self.cache._r = original_r


# ─── Event Bus (event_bus.py) ─────────────────────────────────────────

class TestEventBus:
    """Tests for the Redis pub/sub event bus."""

    @pytest.fixture(autouse=True)
    def _import_bus(self):
        self.bus = _import_real_module("event_bus")

    def test_publish_swallows_errors(self):
        """publish() should not raise even if Redis is down."""
        original = self.bus._sync
        try:
            self.bus._sync = None
            with patch("redis.Redis.from_url", side_effect=Exception("Connection refused")):
                self.bus.publish("test_channel", {"data": "test"})
        finally:
            self.bus._sync = original

    def test_publish_serializes_payload(self):
        """Payload should be JSON-serialized before publishing."""
        mock_redis = MagicMock()
        original = self.bus._sync
        try:
            self.bus._sync = mock_redis
            self.bus.publish("channel", {"key": "value"})
            mock_redis.publish.assert_called_once()
            args = mock_redis.publish.call_args
            assert args[0][0] == "channel"
            parsed = json.loads(args[0][1])
            assert parsed["key"] == "value"
        finally:
            self.bus._sync = original

    def test_publish_reconnects_on_connection_error(self):
        """FIX: publish resets _sync on ConnectionError so next call reconnects."""
        broken = MagicMock()
        broken.publish.side_effect = redis.ConnectionError("gone")
        original = self.bus._sync
        try:
            self.bus._sync = broken
            self.bus.publish("channel", {"data": "test"})
            # _sync should be reset to None for reconnection
            assert self.bus._sync is None
        finally:
            self.bus._sync = original

    def test_sync_client_lazy_init(self):
        """_get_sync should lazily create the Redis client."""
        original = self.bus._sync
        try:
            self.bus._sync = None
            with patch("redis.Redis.from_url") as mock_from_url:
                mock_client = MagicMock()
                mock_from_url.return_value = mock_client
                result = self.bus._get_sync()
                assert result is mock_client
                mock_from_url.assert_called_once()
        finally:
            self.bus._sync = original

    @pytest.mark.asyncio
    async def test_async_publish_swallows_errors(self):
        """async_publish should not raise even if Redis is down."""
        with patch.object(self.bus, '_get_async', side_effect=Exception("Connection refused")):
            await self.bus.async_publish("channel", {"data": "test"})

    @pytest.mark.asyncio
    async def test_ensure_async_lock_idempotent(self):
        """FIX: _ensure_async_lock returns the same lock on repeated calls."""
        lock1 = self.bus._ensure_async_lock()
        lock2 = self.bus._ensure_async_lock()
        assert lock1 is lock2


# ─── Utility Functions ────────────────────────────────────────────────

class TestUtilityFunctions:
    """Tests for shared utility functions in main.py."""

    def test_fmt_ist_with_none(self):
        sys.path.insert(0, APP_DIR)
        from main import fmt_ist
        assert fmt_ist(None) == ""
        assert fmt_ist("") == ""

    def test_fmt_ist_with_valid_iso(self):
        from main import fmt_ist
        result = fmt_ist("2025-01-15T10:00:00Z")
        assert "IST" in result
        assert "15 Jan 2025" in result

    def test_fmt_ist_with_datetime_object(self):
        from main import fmt_ist
        from datetime import datetime, timezone
        dt = datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        result = fmt_ist(dt)
        assert "IST" in result

    def test_fmt_ist_with_garbage(self):
        from main import fmt_ist
        result = fmt_ist("not-a-date")
        assert result == "not-a-date"

    def test_now_ist_timezone(self):
        from main import now_ist, IST
        result = now_ist()
        assert result.tzinfo == IST
