"""Tests for SSE utility functions (app/routers/sse.py).

Covers _realtime_available, _sessions_event_data, _store_connect_token
(Redis + fallback), and _recompress_jpeg.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import io
import json as _json
import time
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from app.routers import sse


@pytest.fixture(autouse=True)
def _clear_tokens():
    sse._connect_tokens.clear()
    yield
    sse._connect_tokens.clear()


# =============================================================================
#  _realtime_available
# =============================================================================

class TestRealtimeAvailable:
    def test_no_redis_returns_false(self):
        with patch.object(sse, "_HAS_REDIS", False):
            assert sse._realtime_available() is False

    def test_healthy_returns_true(self):
        with patch.object(sse, "_HAS_REDIS", True):
            sse._cache._r_healthy = True
            assert sse._realtime_available() is True

    def test_unhealthy_returns_false(self):
        with patch.object(sse, "_HAS_REDIS", True):
            sse._cache._r_healthy = False
            assert sse._realtime_available() is False

    def test_exception_returns_false(self, monkeypatch):
        with patch.object(sse, "_HAS_REDIS", True):
            class _Boomer:
                @property
                def _r_healthy(self):
                    raise RuntimeError("boom")
            monkeypatch.setattr(sse, "_cache", _Boomer())
            assert sse._realtime_available() is False

    def test_has_redis_missing_r_healthy(self):
        with patch.object(sse, "_HAS_REDIS", True):
            sse._cache._r_healthy = None
            assert sse._realtime_available() is False


# =============================================================================
#  _sessions_event_data
# =============================================================================

class TestSessionsEventData:
    def test_basic_shape(self):
        snap = {"sessions": {"s1": {}}, "all_sessions": [{"id": "s1"}]}
        data = sse._sessions_event_data(snap)
        assert data["sessions"] == {"s1": {}}
        assert data["all_sessions"] == [{"id": "s1"}]
        assert "realtime" in data
        assert "ts" not in data

    def test_with_timestamp(self):
        data = sse._sessions_event_data({}, with_ts=True)
        assert "ts" in data
        assert isinstance(data["ts"], float)

    def test_empty_defaults(self):
        data = sse._sessions_event_data({})
        assert data["sessions"] == {}
        assert data["all_sessions"] == []

    def test_missing_keys_default_safely(self):
        data = sse._sessions_event_data({"sessions": {"s1": {}}})
        assert data["sessions"] == {"s1": {}}
        assert data["all_sessions"] == []


# =============================================================================
#  _store_connect_token
# =============================================================================

class TestStoreConnectToken:
    @pytest.mark.asyncio
    async def test_stores_in_cache_when_available(self, monkeypatch):
        mock_set = MagicMock()
        monkeypatch.setattr(sse._cache, "set", mock_set)
        await sse._store_connect_token("tok-1", "teacher-1")
        mock_set.assert_called_once_with(
            "sse_ct:tok-1", "teacher-1", ttl=sse._CONNECT_TOKEN_TTL_SECONDS,
        )

    @pytest.mark.asyncio
    async def test_falls_back_to_inprocess_dict_on_cache_none(self, monkeypatch):
        monkeypatch.setattr(sse, "_cache", None)
        await sse._store_connect_token("tok-fb", "teacher-fb")
        assert sse._connect_tokens.get("tok-fb") == "teacher-fb"

    @pytest.mark.asyncio
    async def test_falls_back_on_cache_set_exception(self, monkeypatch):
        mock_set = MagicMock(side_effect=RuntimeError("redis down"))
        monkeypatch.setattr(sse._cache, "set", mock_set)
        mock_logger = MagicMock()
        monkeypatch.setattr(sse, "logger", mock_logger)
        await sse._store_connect_token("tok-ex", "teacher-ex")
        assert sse._connect_tokens.get("tok-ex") == "teacher-ex"
        mock_logger.warning.assert_called_once()


# =============================================================================
#  _consume_connect_token
# =============================================================================

class TestConsumeConnectToken:
    @pytest.mark.asyncio
    async def test_consume_from_cache_via_getdel(self, monkeypatch):
        key = sse._ct_key("tok-gd")
        data = {key: _json.dumps("teacher-gd")}
        redis_mock = MagicMock()
        redis_mock.getdel = lambda k: data.pop(k, None)
        monkeypatch.setattr(sse._cache, "_client", lambda: redis_mock)

        tid = await sse._consume_connect_token("tok-gd")
        assert tid == "teacher-gd"

    @pytest.mark.asyncio
    async def test_consume_returns_none_for_missing(self, monkeypatch):
        redis_mock = MagicMock()
        redis_mock.getdel = lambda k: None
        monkeypatch.setattr(sse._cache, "_client", lambda: redis_mock)

        assert await sse._consume_connect_token("tok-none") is None

    @pytest.mark.asyncio
    async def test_falls_back_to_inprocess_when_cache_none(self, monkeypatch):
        monkeypatch.setattr(sse, "_cache", None)
        sse._connect_tokens["tok-ip"] = "teacher-ip"
        assert await sse._consume_connect_token("tok-ip") == "teacher-ip"
        assert "tok-ip" not in sse._connect_tokens

    @pytest.mark.asyncio
    async def test_falls_back_to_get_delete_when_getdel_fails(self, monkeypatch):
        key = sse._ct_key("tok-gd-fallback")
        data = {key: "teacher-gdf"}
        redis_mock = MagicMock()
        redis_mock.getdel = MagicMock(side_effect=RuntimeError("no getdel"))
        monkeypatch.setattr(sse._cache, "_client", lambda: redis_mock)
        monkeypatch.setattr(sse._cache, "get", lambda k: data.get(k, None))
        monkeypatch.setattr(sse._cache, "delete", lambda k: data.pop(k, None))

        tid = await sse._consume_connect_token("tok-gd-fallback")
        assert tid == "teacher-gdf"

    @pytest.mark.asyncio
    async def test_client_exception_falls_to_inprocess(self, monkeypatch):
        monkeypatch.setattr(sse._cache, "_client", MagicMock(side_effect=RuntimeError("no client")))
        sse._connect_tokens["tok-nc"] = "teacher-nc"
        assert await sse._consume_connect_token("tok-nc") == "teacher-nc"


# =============================================================================
#  _recompress_jpeg
# =============================================================================

class TestRecompressJpeg:
    def test_reduces_quality(self):
        from PIL import Image as PILImage
        original = io.BytesIO()
        img = PILImage.new("RGB", (100, 100), color="red")
        img.save(original, "JPEG", quality=95)
        original_bytes = original.getvalue()

        recompressed = sse._recompress_jpeg(original_bytes)
        assert isinstance(recompressed, bytes)
        assert len(recompressed) > 0
        PILImage.open(io.BytesIO(recompressed)).verify()
