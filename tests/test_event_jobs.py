"""Tests for event/violation persistence RQ job (app/jobs/event_jobs.py).

Covers cooldown suppression, DB insert success/failure, and the sync
wrapper contract.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import MagicMock

from app.jobs import event_jobs
from tests.conftest import mock_database, mock_cache


class _InsertCapture:
    def __init__(self):
        self.inserted: dict | None = None

    def insert(self, row, *a, **kw):
        self.inserted = row
        return self

    async def execute(self):
        pass


def _run_coro_sync(coro):
    import asyncio
    return asyncio.run(coro)


class TestRecordViolationAsync:
    @pytest.mark.asyncio
    async def test_records_violation_and_sets_cooldown(self, monkeypatch):
        cap = _InsertCapture()
        monkeypatch.setattr(mock_database, "async_table", lambda name: cap)
        monkeypatch.setattr(mock_cache, "get", MagicMock(return_value=None))
        monkeypatch.setattr(mock_cache, "set", MagicMock())

        result = await event_jobs._record_violation_async({
            "session_key": "sess-1",
            "violation_type": "gaze_away",
            "severity": "high",
        })

        assert result == {"status": "recorded"}
        assert cap.inserted is not None
        assert cap.inserted["session_key"] == "sess-1"
        assert cap.inserted["violation_type"] == "gaze_away"
        mock_cache.set.assert_called_once_with("vio_cd:sess-1:gaze_away", "1", ttl=30)

    @pytest.mark.asyncio
    async def test_cooldown_suppresses_duplicate(self, monkeypatch):
        monkeypatch.setattr(mock_cache, "get", MagicMock(return_value="1"))

        result = await event_jobs._record_violation_async({
            "session_key": "sess-1",
            "violation_type": "gaze_away",
        })

        assert result["status"] == "suppressed"

    @pytest.mark.asyncio
    async def test_no_cooldown_without_session_key(self, monkeypatch):
        cap = _InsertCapture()
        monkeypatch.setattr(mock_database, "async_table", lambda name: cap)
        monkeypatch.setattr(mock_cache, "get", MagicMock(return_value=None))
        monkeypatch.setattr(mock_cache, "set", MagicMock())

        result = await event_jobs._record_violation_async({
            "violation_type": "face_missing",
        })

        assert result == {"status": "recorded"}
        assert cap.inserted is not None
        assert cap.inserted["violation_type"] == "face_missing"
        mock_cache.set.assert_not_called()

    @pytest.mark.asyncio
    async def test_insert_failure_returns_failed(self, monkeypatch):
        class _FailTable:
            def insert(self, row, *a, **kw):
                return self
            async def execute(self):
                raise RuntimeError("db deadlock")
        monkeypatch.setattr(mock_database, "async_table", lambda name: _FailTable())
        monkeypatch.setattr(mock_cache, "get", MagicMock(return_value=None))

        result = await event_jobs._record_violation_async({
            "session_key": "sess-2",
            "violation_type": "tab_hidden",
        })

        assert result["status"] == "failed"

    @pytest.mark.asyncio
    async def test_cache_get_exception_does_not_block(self, monkeypatch):
        cap = _InsertCapture()
        monkeypatch.setattr(mock_database, "async_table", lambda name: cap)
        monkeypatch.setattr(mock_cache, "get", MagicMock(side_effect=RuntimeError("cache down")))
        monkeypatch.setattr(mock_cache, "set", MagicMock())

        result = await event_jobs._record_violation_async({
            "session_key": "sess-3",
            "violation_type": "vm_detected",
        })

        assert result == {"status": "recorded"}

    @pytest.mark.asyncio
    async def test_cache_set_exception_does_not_block(self, monkeypatch):
        cap = _InsertCapture()
        monkeypatch.setattr(mock_database, "async_table", lambda name: cap)
        monkeypatch.setattr(mock_cache, "get", MagicMock(return_value=None))
        monkeypatch.setattr(mock_cache, "set", MagicMock(side_effect=RuntimeError("cache set failed")))

        result = await event_jobs._record_violation_async({
            "session_key": "sess-4",
            "violation_type": "voice_detected",
        })

        assert result == {"status": "recorded"}


class TestRecordViolationJob:
    def test_sync_wrapper_calls_async(self, monkeypatch):
        captured = None

        async def _fake_async(row):
            nonlocal captured
            captured = row
            return {"status": "recorded"}

        monkeypatch.setattr(event_jobs, "_record_violation_async", _fake_async)
        monkeypatch.setattr(event_jobs, "_run_coro_in_sync", _run_coro_sync)

        result = event_jobs.record_violation_job({"session_key": "s-1", "violation_type": "test"})

        assert result == {"status": "recorded"}
        assert captured == {"session_key": "s-1", "violation_type": "test"}
