"""Tests for LTI grade-passback RQ job (app/jobs/lti_jobs.py).

Verifies that ags_grade_passback_job calls _try_ags_grade_passback with
the correct arguments and returns {"ok": True} on success.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import AsyncMock

from app.jobs import lti_jobs
import app.routers.exam as _exam_mod


def _run_coro_sync(coro):
    """Simple helper to run an async coroutine synchronously."""
    import asyncio
    return asyncio.run(coro)


class TestAgsGradePassbackJob:
    def test_calls_try_ags_with_correct_args(self, monkeypatch):
        mock_ags = AsyncMock()
        monkeypatch.setattr(_exam_mod, "_try_ags_grade_passback", mock_ags)
        monkeypatch.setattr(lti_jobs, "_run_coro_in_sync", _run_coro_sync)

        result = lti_jobs.ags_grade_passback_job(
            roll_number="A001",
            score=8,
            total=10,
            percentage=80.0,
            teacher_id="t1",
        )

        assert result == {"ok": True}
        mock_ags.assert_awaited_once_with(
            "A001", 8, 10, 80.0,
            teacher_id="t1",
            raise_on_failure=True,
        )

    def test_teacher_id_none_when_empty(self, monkeypatch):
        mock_ags = AsyncMock()
        monkeypatch.setattr(_exam_mod, "_try_ags_grade_passback", mock_ags)
        monkeypatch.setattr(lti_jobs, "_run_coro_in_sync", _run_coro_sync)

        lti_jobs.ags_grade_passback_job(
            roll_number="B002",
            score=5,
            total=10,
            percentage=50.0,
        )

        # teacher_id defaults to "" and should be passed as None
        _, kwargs = mock_ags.await_args
        assert kwargs.get("teacher_id") is None

    def test_returns_ok_on_success(self, monkeypatch):
        mock_ags = AsyncMock()
        monkeypatch.setattr(_exam_mod, "_try_ags_grade_passback", mock_ags)
        monkeypatch.setattr(lti_jobs, "_run_coro_in_sync", _run_coro_sync)

        result = lti_jobs.ags_grade_passback_job(
            roll_number="C003", score=10, total=10, percentage=100.0,
        )
        assert result == {"ok": True}
