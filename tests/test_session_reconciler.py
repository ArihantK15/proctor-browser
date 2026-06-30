"""Tests for session-state reconciler (app/services/session_reconciler.py).

Covers _report (log + Sentry), _reconcile_once (all 3 healing categories),
and the session_reconciler_loop lifecycle.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import session_reconciler as rec

import app.jobs as jobs


# =============================================================================
#  _enqueue_rescore  (roll-number fallback, error swallowing, queue routing)
# =============================================================================

@pytest.fixture
def captured(monkeypatch):
    calls = []

    def _fake_enqueue(job, **kwargs):
        calls.append(kwargs)
        return "job-id"

    monkeypatch.setattr(jobs, "enqueue_job", _fake_enqueue)
    return calls


def test_uses_explicit_roll_number(captured):
    row = {"session_key": "2102508447_abc123", "teacher_id": "t1",
           "exam_id": "e1", "roll_number": "EXPLICIT99"}
    assert rec._enqueue_rescore(row) is True
    assert captured[0]["roll_number"] == "EXPLICIT99"


def test_derives_roll_from_session_key_when_missing(captured):
    row = {"session_key": "2102508447_abc123", "teacher_id": "t1", "exam_id": "e1"}
    assert rec._enqueue_rescore(row) is True
    assert captured[0]["roll_number"] == "2102508447"


def test_roll_empty_when_session_key_has_no_separator(captured):
    row = {"session_key": "nounderscorehere", "teacher_id": "t1"}
    assert rec._enqueue_rescore(row) is True
    assert captured[0]["roll_number"] == ""


def test_returns_false_and_swallows_enqueue_error(monkeypatch):
    def _boom(job, **kwargs):
        raise RuntimeError("queue down")
    monkeypatch.setattr(jobs, "enqueue_job", _boom)
    row = {"session_key": "r_1", "teacher_id": "t1"}
    assert rec._enqueue_rescore(row) is False


def test_enqueue_targets_scoring_queue(captured):
    row = {"session_key": "r_1", "teacher_id": "t1", "exam_id": "e1"}
    rec._enqueue_rescore(row)
    assert captured[0]["queue_name"] == "scoring"


# =============================================================================
#  _report
# =============================================================================

class TestReport:
    def test_logs_warning(self, caplog):
        caplog.set_level("WARNING")
        with patch.object(rec, "sentry_sdk", None, create=True):
            rec._report("something drifted")
        assert "[reconciler] something drifted" in caplog.text

    def test_sends_to_sentry_when_available(self):
        sentry = MagicMock()
        with patch.dict("sys.modules", {"sentry_sdk": sentry}):
            rec._report("drift detected")
        sentry.capture_message.assert_called_once_with(
            "[reconciler] drift detected", level="warning"
        )

    def test_swallows_sentry_failure(self, caplog):
        caplog.set_level("WARNING")
        sentry = MagicMock()
        sentry.capture_message.side_effect = RuntimeError("sentry down")
        with patch.dict("sys.modules", {"sentry_sdk": sentry}):
            rec._report("sentry failed")
        assert "[reconciler] sentry failed" in caplog.text


# =============================================================================
#  _reconcile_once
# =============================================================================

def _make_chain(data=None):
    """Return a chain object that responds to .select().eq().lt().is_().limit()"""
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.lt.return_value = chain
    chain.is_.return_value = chain
    chain.limit.return_value = chain
    chain.execute = AsyncMock(return_value=MagicMock(data=data or []))
    return chain


def _make_update_chain(row_count=1):
    """Return a chain that captures the update payload and returns row_count."""
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.is_.return_value = chain
    chain.limit.return_value = chain
    chain.execute.return_value = MagicMock(data=[])
    # Separate chain for the update
    update_chain = MagicMock()
    update_chain.update.return_value = update_chain
    update_chain.eq.return_value = update_chain
    update_chain.is_.return_value = update_chain
    chain.update = lambda p, **kw: update_chain.update(p, **kw) or update_chain
    return chain, update_chain


class TestReconcileOnce:
    def test_stuck_submitted_enqueues_rescore(self, monkeypatch):
        monkeypatch.setattr(
            rec, "_enqueue_rescore",
            MagicMock(return_value=True),
        )
        monkeypatch.setattr(
            rec, "_atable",
            lambda name: _make_chain(data=[{
                "session_key": "sess_1", "status": "submitted",
                "teacher_id": "t1", "exam_id": "e1",
                "submitted_at": "2024-01-01T00:00:00",
            }]),
        )
        healed = asyncio.run(rec._reconcile_once())
        assert healed["stuck_submitted"] == 1

    def test_completed_no_score_enqueues_rescore(self, monkeypatch):
        monkeypatch.setattr(
            rec, "_enqueue_rescore",
            MagicMock(return_value=True),
        )
        monkeypatch.setattr(
            rec, "_atable",
            lambda name: _make_chain(data=[{
                "session_key": "sess_2", "status": "completed",
                "score": None, "teacher_id": "t1",
            }]),
        )
        healed = asyncio.run(rec._reconcile_once())
        assert healed["completed_no_score"] == 1

    def test_missing_submitted_at_backfills(self, monkeypatch):
        """RESULT-status rows missing submitted_at get backfilled."""
        captured = {}

        class _Chain:
            def select(self, *a, **kw):
                return self
            def eq(self, *a):
                return self
            def lt(self, *a):
                return self
            def is_(self, *a):
                return self
            def limit(self, *a):
                return self
            def update(self, payload):
                captured["update"] = payload
                return self
            async def execute(self):
                return MagicMock(data=[{"session_key": "s_1", "teacher_id": "t1"}])

        monkeypatch.setattr(rec, "_atable", lambda name: _Chain())
        monkeypatch.setattr(rec, "_report", MagicMock())

        healed = asyncio.run(rec._reconcile_once())

        assert healed["missing_submitted_at"] > 0
        assert "submitted_at" in captured["update"]

    def test_backfill_exception_swallowed(self, monkeypatch):
        """Exception during submitted_at backfill is logged, not raised."""

        class _FailingUpdateChain:
            def select(self, *a, **kw):
                return self
            def eq(self, *a):
                return self
            def is_(self, *a):
                return self
            def limit(self, *a):
                return self
            def lt(self, *a):
                return self
            async def execute(self):
                if hasattr(self, "_update_called") and self._update_called:
                    raise RuntimeError("update failed")
                self._update_called = True
                return MagicMock(data=[{"session_key": "s_1", "teacher_id": "t1"}])
            def update(self, payload):
                self._update_called = True
                return self

        monkeypatch.setattr(rec, "_atable", lambda name: _FailingUpdateChain())
        monkeypatch.setattr(rec, "_report", MagicMock())

        healed = asyncio.run(rec._reconcile_once())
        # Should not raise — exception was swallowed
        assert healed["missing_submitted_at"] == 0

    def test_enqueue_rescore_false_does_not_count(self, monkeypatch):
        monkeypatch.setattr(
            rec, "_enqueue_rescore",
            MagicMock(return_value=False),
        )
        monkeypatch.setattr(
            rec, "_atable",
            lambda name: _make_chain(data=[{
                "session_key": "sess_1", "status": "submitted",
                "teacher_id": "t1", "exam_id": "e1",
                "submitted_at": "2024-01-01T00:00:00",
            }]),
        )
        healed = asyncio.run(rec._reconcile_once())
        assert healed["stuck_submitted"] == 0

    def test_no_anomalies_returns_zeros(self, monkeypatch):
        monkeypatch.setattr(rec, "_enqueue_rescore", MagicMock())
        monkeypatch.setattr(rec, "_atable", lambda name: _make_chain(data=[]))
        monkeypatch.setattr(rec, "_report", MagicMock())

        healed = asyncio.run(rec._reconcile_once())

        assert healed == {"stuck_submitted": 0, "missing_submitted_at": 0, "completed_no_score": 0}
        rec._report.assert_not_called()

    def test_report_called_when_healed(self, monkeypatch):
        monkeypatch.setattr(
            rec, "_enqueue_rescore",
            MagicMock(return_value=True),
        )
        mock_report = MagicMock()
        monkeypatch.setattr(rec, "_report", mock_report)

        call_idx = [0]

        class _SelectiveChain:
            def select(self, *a, **kw):
                return self
            def eq(self, *a):
                return self
            def lt(self, *a):
                return self
            def is_(self, *a):
                return self
            def limit(self, *a):
                return self
            def update(self, p):
                return self
            async def execute(self):
                idx = call_idx[0]
                call_idx[0] += 1
                if idx == 0:
                    # First call — stuck_submitted query
                    return MagicMock(data=[{
                        "session_key": "s_1", "status": "submitted",
                        "teacher_id": "t1", "exam_id": "e1",
                        "submitted_at": "2024-01-01T00:00:00",
                    }])
                # All subsequent calls — empty
                return MagicMock(data=[])

        monkeypatch.setattr(rec, "_atable", lambda name: _SelectiveChain())

        healed = asyncio.run(rec._reconcile_once())
        assert healed["stuck_submitted"] == 1
        mock_report.assert_called_once()


# =============================================================================
#  session_reconciler_loop
# =============================================================================

class TestReconcilerLoop:
    @pytest.mark.asyncio
    async def test_calls_reconcile_once(self, monkeypatch):
        calls = []
        async def _fake_reconcile():
            calls.append(1)
            return {"stuck_submitted": 0, "missing_submitted_at": 0, "completed_no_score": 0}

        monkeypatch.setattr(rec, "RECONCILER_STARTUP_DELAY_SECS", 0.001)
        monkeypatch.setattr(rec, "RECONCILER_INTERVAL_SECS", 0.001)
        monkeypatch.setattr(rec, "_reconcile_once", _fake_reconcile)

        task = asyncio.create_task(rec.session_reconciler_loop())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, RuntimeError):
            pass

        assert len(calls) >= 2  # at least 2 iterations

    @pytest.mark.asyncio
    async def test_survives_reconcile_exception(self, monkeypatch):
        call_count = 0

        async def _flaky_reconcile():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("reconcile boom")
            return {"stuck_submitted": 0, "missing_submitted_at": 0, "completed_no_score": 0}

        monkeypatch.setattr(rec, "RECONCILER_STARTUP_DELAY_SECS", 0.001)
        monkeypatch.setattr(rec, "RECONCILER_INTERVAL_SECS", 0.001)
        monkeypatch.setattr(rec, "_reconcile_once", _flaky_reconcile)

        task = asyncio.create_task(rec.session_reconciler_loop())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, RuntimeError):
            pass

        assert call_count >= 2  # survived the exception and ran again
