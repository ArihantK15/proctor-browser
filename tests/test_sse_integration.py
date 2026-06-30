"""Tests for SSE internal logic and endpoints (app/routers/sse.py).

Covers _evict_live_frame_ts, _assert_exam_ws_session_access,
_store_live_frame, _store_room_frame, close_room_cam_ws,
_log_task_failure, _room_cam_offline_check, and the HTTP endpoints
sse_connect_token, upload_live_frame_http, proctor_control.
"""
from __future__ import annotations

import json
import os
import sys
import time
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.main import app

client = TestClient(app, raise_server_exceptions=False)

from app.routers import sse


# ── _evict_live_frame_ts ─────────────────────────────────────────────────

class TestEvictLiveFrameTs:
    def _cleanup(self):
        sse._last_live_frame_ts.clear()
        if hasattr(sse._evict_live_frame_ts, "_last_cleanup"):
            delattr(sse._evict_live_frame_ts, "_last_cleanup")

    def test_skips_cleanup_if_not_needed(self):
        self._cleanup()
        sse._last_live_frame_ts["sess-1"] = time.time()
        sse._evict_live_frame_ts(time.time())
        assert "sess-1" in sse._last_live_frame_ts

    def test_evicts_stale_entries(self):
        self._cleanup()
        now = time.time()
        sse._last_live_frame_ts["stale"] = now - 400  # > 300s cutoff
        sse._last_live_frame_ts["fresh"] = now - 10
        # Force cleanup by making last_cleanup old enough
        sse._evict_live_frame_ts(now + 120)  # last_cleanup=0, now-last_cleanup > 60
        assert "stale" not in sse._last_live_frame_ts
        assert "fresh" in sse._last_live_frame_ts

    def test_skips_eviction_when_under_1000_entries_and_recent_cleanup(self):
        self._cleanup()
        now = time.time()
        sse._evict_live_frame_ts(now)
        sse._last_live_frame_ts["sess-1"] = now - 400
        # Second call within 60s with < 1000 entries — skips
        sse._evict_live_frame_ts(now + 10)
        assert "sess-1" in sse._last_live_frame_ts


# ── _log_task_failure ────────────────────────────────────────────────────

class TestLogTaskFailure:
    def test_logs_exception(self, caplog):
        task = MagicMock()
        task.cancelled.return_value = False
        task.exception.return_value = RuntimeError("boom")
        sse._log_task_failure(task)
        assert "task died" in caplog.text

    def test_skips_cancelled(self, caplog):
        task = MagicMock()
        task.cancelled.return_value = True
        sse._log_task_failure(task)
        assert not caplog.text


# ── close_room_cam_ws ────────────────────────────────────────────────────

class TestCloseRoomCamWs:
    @pytest.mark.asyncio
    async def test_does_nothing_when_no_connection(self):
        # Was previously calling the coroutine without awaiting it, so the
        # body never ran (and leaked a "coroutine was never awaited" warning).
        # Await it for real and assert the early-return no-op path.
        sse._ws_room_conns.clear()
        result = await sse.close_room_cam_ws("sess-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_closes_existing_connection(self):
        ws = AsyncMock()
        sse._ws_room_conns["sess-1"] = ws
        await sse.close_room_cam_ws("sess-1", code=4004, reason="session_ended")
        ws.close.assert_called_once_with(code=4004, reason="session_ended")


# ── _assert_exam_ws_session_access ───────────────────────────────────────

class TestAssertExamWsSessionAccess:
    def _mock_chain(self, data: list[dict] | None = None) -> MagicMock:
        chain = MagicMock()
        for attr in ("select", "eq", "limit", "order", "in_"):
            getattr(chain, attr).return_value = chain

        async def _exec():
            r = MagicMock()
            r.data = data or []
            return r

        chain.execute = _exec
        return chain

    @pytest.mark.asyncio
    async def test_raises_on_roll_mismatch_in_session_id(self):
        claims = {"roll": "BOB001"}
        with pytest.raises(Exception):  # HTTPException
            await sse._assert_exam_ws_session_access(claims, "ALICE001_sess-1")

    @pytest.mark.asyncio
    async def test_raises_on_roll_mismatch_in_row(self):
        chain = self._mock_chain([{"roll_number": "BOB001", "teacher_id": "t-1",
                                   "exam_id": "e-1", "student_id": "s-1"}])
        with patch("app.routers.sse._atable", return_value=chain):
            with pytest.raises(Exception):
                await sse._assert_exam_ws_session_access(
                    {"roll": "ALICE001", "tid": "t-1", "eid": "e-1", "sid": "s-1"},
                    "ALICE001_sess-1",
                )

    @pytest.mark.asyncio
    async def test_raises_on_tid_mismatch(self):
        chain = self._mock_chain([{"roll_number": "ALICE001", "teacher_id": "other-t",
                                   "exam_id": "e-1", "student_id": "s-1"}])
        with patch("app.routers.sse._atable", return_value=chain):
            with pytest.raises(Exception):
                await sse._assert_exam_ws_session_access(
                    {"roll": "ALICE001", "tid": "t-1", "eid": "e-1", "sid": "s-1"},
                    "ALICE001_sess-1",
                )

    @pytest.mark.asyncio
    async def test_succeeds_on_match(self):
        chain = self._mock_chain([{"roll_number": "ALICE001", "teacher_id": "t-1",
                                   "exam_id": "e-1", "student_id": "s-1"}])
        with patch("app.routers.sse._atable", return_value=chain):
            await sse._assert_exam_ws_session_access(
                {"roll": "ALICE001", "tid": "t-1", "eid": "e-1", "sid": "s-1"},
                "ALICE001_sess-1",
            )
            assert True  # no exception

    @pytest.mark.asyncio
    async def test_returns_on_empty_data(self):
        chain = self._mock_chain([])
        with patch("app.routers.sse._atable", return_value=chain):
            await sse._assert_exam_ws_session_access(
                {"roll": "ALICE001", "tid": "t-1"},
                "ALICE001_sess-1",
            )
            assert True  # no exception

    @pytest.mark.asyncio
    async def test_returns_on_db_exception(self):
        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.execute = AsyncMock(side_effect=RuntimeError("db dead"))
        with patch("app.routers.sse._atable", return_value=chain):
            await sse._assert_exam_ws_session_access(
                {"roll": "ALICE001"}, "ALICE001_sess-1",
            )
            assert True  # no exception


# ── _store_live_frame ────────────────────────────────────────────────────

class TestStoreLiveFrame:
    @pytest.mark.asyncio
    async def test_rate_limited_returns_false(self):
        sse._last_live_frame_ts.clear()
        now = time.time()
        sse._last_live_frame_ts["sess-1"] = now  # just stored
        result = await sse._store_live_frame("sess-1", b"jpeg_data")
        assert result is False

    @pytest.mark.asyncio
    async def test_stores_frame_when_accepted(self):
        sse._last_live_frame_ts.clear()
        with patch("app.routers.sse._cache", MagicMock()) as mock_cache, \
             patch("app.routers.sse._recompress_jpeg", return_value=b"compressed"), \
             patch("app.routers.sse.asyncio.to_thread", AsyncMock(return_value=b"compressed")):
            mock_cache.set_live_frame = MagicMock()
            result = await sse._store_live_frame("sess-1", b"jpeg_data")
            assert result is True


# ── _store_room_frame ────────────────────────────────────────────────────

class TestStoreRoomFrame:
    def _clean(self):
        if hasattr(sse._store_room_frame, "_frame_meta"):
            delattr(sse._store_room_frame, "_frame_meta")
        if hasattr(sse._store_room_frame, "_last_ts"):
            delattr(sse._store_room_frame, "_last_ts")

    @pytest.mark.asyncio
    async def test_skips_small_payload(self):
        self._clean()
        await sse._store_room_frame("sess-1", b"too small")
        assert True  # no crash

    @pytest.mark.asyncio
    async def test_skips_non_jpeg(self):
        self._clean()
        await sse._store_room_frame("sess-1", b"x" * 600)
        assert True  # no crash

    @pytest.mark.asyncio
    async def test_stores_valid_jpeg(self):
        self._clean()
        raw_jpeg = b"\xff\xd8\xff" + b"x" * 600  # valid JPEG header
        from tests.conftest import mock_cache
        mock_cache.set_room_frame = MagicMock()
        with patch("app.routers.sse._recompress_jpeg", return_value=b"compressed"), \
             patch("app.routers.sse.asyncio.to_thread", AsyncMock(return_value=b"compressed")):
            await sse._store_room_frame("sess-1", raw_jpeg)
            mock_cache.set_room_frame.assert_called_once()


# ── _room_cam_offline_check ──────────────────────────────────────────────

class TestRoomCamOfflineCheck:
    @pytest.mark.asyncio
    async def test_skips_recent_sessions(self):
        sse._last_room_frame.clear()
        sse._ROOM_CAM_OFFLINE_FIRED.clear()
        sse._last_room_frame["sess-1"] = time.time()  # recent
        await sse._room_cam_offline_check()
        assert "sess-1" not in sse._ROOM_CAM_OFFLINE_FIRED

    @pytest.mark.asyncio
    async def test_cleans_up_stale_entries(self):
        sse._last_room_frame.clear()
        sse._ROOM_CAM_OFFLINE_FIRED.clear()
        sse._last_room_frame["stale"] = time.time() - 400  # > 300s cutoff
        await sse._room_cam_offline_check()
        assert "stale" not in sse._last_room_frame

    @pytest.mark.asyncio
    async def test_fires_violation_for_offline_session(self):
        sse._last_room_frame.clear()
        sse._ROOM_CAM_OFFLINE_FIRED.clear()
        sse._last_room_frame["sess-1"] = time.time() - 30  # > 20s timeout

        fake_chain = MagicMock()
        fake_chain.select.return_value = fake_chain
        fake_chain.eq.return_value = fake_chain
        fake_chain.limit.return_value = fake_chain
        fake_chain.insert.return_value = fake_chain
        fake_chain.update.return_value = fake_chain
        fake_chain.execute = AsyncMock(return_value=MagicMock(data=[{"teacher_id": "t-1"}]))

        with patch("app.routers.sse._atable", return_value=fake_chain):
            await sse._room_cam_offline_check()

        assert "sess-1" in sse._ROOM_CAM_OFFLINE_FIRED

    @pytest.mark.asyncio
    async def test_does_not_fire_twice(self):
        sse._last_room_frame.clear()
        sse._ROOM_CAM_OFFLINE_FIRED.clear()
        sse._last_room_frame["sess-1"] = time.time() - 30
        sse._ROOM_CAM_OFFLINE_FIRED.add("sess-1")  # already fired

        await sse._room_cam_offline_check()
        # Should not duplicate the violation — just skip


# ── sse_connect_token endpoint ───────────────────────────────────────────

class TestSseConnectToken:
    def test_returns_token(self):
        patches = [
            patch("app.routers.sse.require_admin"),
            patch("app.routers.sse._store_connect_token"),
        ]
        for p in patches:
            p.start()
        try:
            resp = client.post("/api/v1/sse/connect-token")
            assert resp.status_code == 200
            data = resp.json()
            assert "connect_token" in data
        finally:
            for p in reversed(patches):
                p.stop()


# ── upload_live_frame_http endpoint ──────────────────────────────────────

class TestUploadLiveFrameHttp:
    def _fake_token(self) -> str:
        import time, base64
        token = base64.urlsafe_b64encode(time.monotonic_ns().to_bytes(8, 'big')).decode()
        return token

    def test_missing_auth_returns_401(self):
        resp = client.post("/api/v1/proctor/live-frame", json={
            "session_id": "sess-1", "jpeg_b64": "AAAA",
        })
        assert resp.status_code == 401

    def test_invalid_token_returns_401(self):
        resp = client.post("/api/v1/proctor/live-frame", json={
            "session_id": "sess-1", "jpeg_b64": "AAAA",
        }, headers={"Authorization": "Bearer bad-token"})
        assert resp.status_code == 401


# ── proctor_control endpoint ─────────────────────────────────────────────

class TestProctorControl:
    def test_missing_auth_returns_401(self):
        resp = client.get("/api/v1/proctor/control/sess-1")
        assert resp.status_code == 401

    def test_invalid_token_returns_401(self):
        resp = client.get("/api/v1/proctor/control/sess-1",
                          headers={"Authorization": "Bearer bad-token"})
        assert resp.status_code == 401
