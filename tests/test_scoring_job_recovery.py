"""The async scoring job must write the session_recovered audit row when a
submission reopened a reaper-closed (ABANDONED) session — matching the inline
submit path. Regression: the async path silently dropped the recovery audit
because the handler had already flipped ABANDONED→SUBMITTED before the job ran.
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock, MagicMock

import pytest


def _recent_iso() -> str:
    # A few seconds ago, so the time-exceeded gate stays closed.
    return (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()


class _FakeChain:
    def __init__(self, name, session_row, recorder, answers_rows):
        self.name, self.session_row, self.recorder = name, session_row, recorder
        self.answers_rows = answers_rows
        self.op = "select"
        self.payload = None

    def select(self, *a, **k): self.op = "select"; return self
    def insert(self, p): self.op = "insert"; self.payload = p; return self
    def update(self, p): self.op = "update"; self.payload = p; return self
    def eq(self, *a): return self
    def neq(self, *a): return self
    def limit(self, *a): return self

    async def execute(self):
        if self.op == "insert":
            self.recorder.append((self.name, self.payload))
            return MagicMock(data=[self.payload])
        if self.op == "update":
            return MagicMock(data=[{"session_key": "S1"}])  # update succeeded
        if self.name == "exam_sessions":
            return MagicMock(data=[self.session_row])
        if self.name == "answers":
            return MagicMock(data=list(self.answers_rows))
        return MagicMock(data=[])  # violation-existence checks → empty


class _FakeTable:
    def __init__(self, session_row, recorder, answers_rows=()):
        self.session_row, self.recorder = session_row, recorder
        self.answers_rows = answers_rows

    def __call__(self, name):
        return _FakeChain(name, self.session_row, self.recorder, self.answers_rows)


def _run_job(recovered: bool):
    """Drive _score_submission_async with all heavy deps mocked; return the
    list of (table, payload) inserts the job performed."""
    from app.jobs.scoring_jobs import _score_submission_async

    session_row = {
        "status": "submitted", "score": None, "total": None, "percentage": None,
        "risk_score": None, "started_at": _recent_iso(), "full_name": "Alice",
        "email": "alice@test.com", "paused_secs_total": 0,
    }
    inserts = []
    fake = _FakeTable(session_row, inserts)

    with patch("app.database.async_table", side_effect=fake), \
         patch("app.services.scoring.recalculate_score", new=AsyncMock(return_value=(8, 10))), \
         patch("app.services.autosave.load_autosave_snapshot", new=MagicMock(return_value=None)), \
         patch("app.repositories.questions.load_exam_config", new=AsyncMock(return_value={"duration_minutes": 60})), \
         patch("app.services.risk.compute_risk_score", new=AsyncMock(return_value={"risk_score": 0, "label": "Low Risk"})), \
         patch("app.services.time_extension.get_time_extension", new=AsyncMock(return_value=0)), \
         patch("app.event_bus.async_publish", new=AsyncMock()):
        import asyncio
        asyncio.run(
            _score_submission_async(
                session_key="S1", teacher_id="teacher-1", exam_id="exam-1",
                roll_number="", time_taken_secs=100, recovered=recovered,
            )
        )
    return inserts


def test_scoring_merges_redis_snapshot_over_stale_db_answers():
    """When the final-answer flush is still in flight, the scoring job must
    score the FRESH Redis snapshot, not the stale DB rows. The snapshot wins."""
    from app.jobs.scoring_jobs import _score_submission_async

    session_row = {
        "status": "submitted", "score": None, "total": None, "percentage": None,
        "risk_score": None, "started_at": _recent_iso(), "full_name": "Alice",
        "email": "alice@test.com", "paused_secs_total": 0,
    }
    # DB still holds the stale autosave; the fresh final answers are only in Redis.
    db_answers = [{"question_id": "q1", "answer": "A"}]
    snapshot = {"answers": {"q1": "B", "q2": "C"}}
    fake = _FakeTable(session_row, [], answers_rows=db_answers)
    captured = {}

    async def _capture_recalc(session_key, payload, **kw):
        captured["payload"] = dict(payload)
        return (2, 2)

    with patch("app.database.async_table", side_effect=fake), \
         patch("app.services.scoring.recalculate_score", new=_capture_recalc), \
         patch("app.services.autosave.load_autosave_snapshot", new=MagicMock(return_value=snapshot)), \
         patch("app.repositories.questions.load_exam_config", new=AsyncMock(return_value={"duration_minutes": 60})), \
         patch("app.services.risk.compute_risk_score", new=AsyncMock(return_value={"risk_score": 0, "label": "Low Risk"})), \
         patch("app.services.time_extension.get_time_extension", new=AsyncMock(return_value=0)), \
         patch("app.event_bus.async_publish", new=AsyncMock()):
        import asyncio
        asyncio.run(_score_submission_async(
            session_key="S1", teacher_id="teacher-1", exam_id="exam-1",
            roll_number="", time_taken_secs=100,
        ))

    # Fresh snapshot value wins over the stale DB row; new question is included.
    assert captured["payload"].get("q1") == "B", captured
    assert captured["payload"].get("q2") == "C", captured


def _types(inserts):
    return [p.get("violation_type") for (tbl, p) in inserts if tbl == "violations"]


def test_recovered_submission_writes_session_recovered_audit():
    types = _types(_run_job(recovered=True))
    assert "session_recovered" in types, types
    assert "exam_submitted" in types, types  # normal audit still written


def test_non_recovered_submission_has_no_recovery_audit():
    types = _types(_run_job(recovered=False))
    assert "session_recovered" not in types, types
    assert "exam_submitted" in types, types
