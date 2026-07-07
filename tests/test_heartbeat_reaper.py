from __future__ import annotations

import asyncio
from unittest.mock import MagicMock


class _FakeTable:
    def __init__(self, name: str, db: "_FakeDb"):
        self.name = name
        self.db = db
        self.op = ""
        self.payload = None
        self.filters = []
        self.not_ = self

    def select(self, *args, **kwargs):
        self.op = "select"
        self.payload = args
        return self

    def update(self, payload):
        self.op = "update"
        self.payload = payload
        return self

    def insert(self, payload):
        self.op = "insert"
        self.payload = payload
        return self

    def eq(self, *args):
        self.filters.append(("eq", args))
        return self

    def in_(self, *args):
        self.filters.append(("in", args))
        return self

    def is_(self, *args):
        self.filters.append(("is", args))
        return self

    def lt(self, *args):
        self.filters.append(("lt", args))
        return self

    def limit(self, *args):
        self.filters.append(("limit", args))
        return self

    async def execute(self):
        self.db.calls.append((self.name, self.op, self.payload, list(self.filters)))
        result = MagicMock()
        if self.name == "exam_config" and self.op == "select":
            result.data = self.db.exam_config_rows
        elif self.name == "exam_sessions" and self.op == "select":
            # The status re-check selects exactly "status"; the stale-scan
            # selects the full row set. Re-check rows carry a status so the
            # reaper's terminal-skip guard sees IN_PROGRESS (not terminal).
            if self.payload == ("status",):
                result.data = [{"status": "in_progress"}]
            else:
                result.data = self.db.stale_rows
        elif self.op == "update":
            # Mirror the real builder: UPDATE ... RETURNING * yields the
            # matched rows. The reaper's compare-and-set checks this to
            # know whether the abandon transition actually applied.
            result.data = [{"status": "abandoned"}]
        else:
            result.data = []
        return result


class _FakeDb:
    def __init__(self, stale_rows, exam_config_rows=None):
        self.stale_rows = stale_rows
        self.exam_config_rows = exam_config_rows or []
        self.calls = []

    def table(self, name: str):
        return _FakeTable(name, self)


def _stale_row(sid="ROLL001_abc"):
    return {
        "session_key": sid, "roll_number": "ROLL001", "teacher_id": "teacher-1",
        "exam_id": "exam-1", "student_id": "student-1",
        "last_heartbeat": "2026-05-18T00:00:00+00:00",
    }


def _patch_scoring(monkeypatch):
    from app.services import autosave, scoring
    flushed = {}

    async def fake_flush(session_id, answers, **kwargs):
        flushed.update(session_id=session_id, answers=answers, kwargs=kwargs)

    async def fake_score(session_id, answers, **kwargs):
        return 1, 2

    monkeypatch.setattr(autosave, "load_autosave_snapshot", lambda _sid: {"answers": {"q1": "A"}})
    monkeypatch.setattr(autosave, "flush_answers_to_db", fake_flush)
    monkeypatch.setattr(scoring, "recalculate_score", fake_score)
    return flushed


def test_reaper_provisionally_scores_but_stays_abandoned_while_window_open(monkeypatch):
    """Window still open (no ends_at): reaper marks ABANDONED and persists a
    PROVISIONAL score (so answers aren't lost) but does NOT finalize to
    FORCE_SUBMITTED — the student can still recover-on-submit/-save."""
    from app.services import heartbeat_reaper
    from app import database

    fake_db = _FakeDb([_stale_row()])  # no exam_config_rows → window open
    flushed = _patch_scoring(monkeypatch)
    monkeypatch.setattr(database, "async_table", fake_db.table)

    asyncio.run(heartbeat_reaper._reap_once())

    exam_updates = [c for c in fake_db.calls if c[0] == "exam_sessions" and c[1] == "update"]
    assert exam_updates[0][2]["status"] == "abandoned"
    # Provisional score written, but NOT finalized.
    assert exam_updates[-1][2]["score"] == 1
    assert exam_updates[-1][2]["total"] == 2
    assert "status" not in exam_updates[-1][2], "must stay ABANDONED while window open"
    assert "submitted_at" not in exam_updates[-1][2]
    assert any(call[0] == "violations" and call[1] == "insert" for call in fake_db.calls)
    assert flushed["session_id"] == "ROLL001_abc"
    assert flushed["answers"] == {"q1": "A"}
    assert flushed["kwargs"]["delete_after"] is True


def test_reaper_finalizes_force_submit_when_window_closed(monkeypatch):
    """Window closed (ends_at in the past): no recovery is possible, so the
    reaper finalizes the abandoned session to FORCE_SUBMITTED with its score."""
    from app.services import heartbeat_reaper
    from app import database

    fake_db = _FakeDb([_stale_row()],
                      exam_config_rows=[{"ends_at": "2000-01-01T00:00:00+00:00"}])
    _patch_scoring(monkeypatch)
    monkeypatch.setattr(database, "async_table", fake_db.table)

    asyncio.run(heartbeat_reaper._reap_once())

    exam_updates = [c for c in fake_db.calls if c[0] == "exam_sessions" and c[1] == "update"]
    assert exam_updates[0][2]["status"] == "abandoned"
    assert exam_updates[-1][2]["status"] == "force_submitted"
    assert exam_updates[-1][2]["score"] == 1
    assert "submitted_at" in exam_updates[-1][2]


def test_exam_window_closed_helper(monkeypatch):
    from app.services import heartbeat_reaper

    def _db(rows):
        return _FakeDb([], exam_config_rows=rows).table

    # ends_at in the past → closed
    assert asyncio.run(heartbeat_reaper._exam_window_closed(
        "e1", _db([{"ends_at": "2000-01-01T00:00:00+00:00"}]))) is True
    # ends_at in the future → open
    assert asyncio.run(heartbeat_reaper._exam_window_closed(
        "e1", _db([{"ends_at": "2999-01-01T00:00:00+00:00"}]))) is False
    # no ends_at → open
    assert asyncio.run(heartbeat_reaper._exam_window_closed(
        "e1", _db([{"ends_at": None}]))) is False
    # no exam_id → open
    assert asyncio.run(heartbeat_reaper._exam_window_closed("", _db([]))) is False


def test_reaper_does_not_force_submit_when_no_autosave_snapshot(monkeypatch):
    from app.services import heartbeat_reaper, autosave
    from app import database

    fake_db = _FakeDb([
        {
            "session_key": "ROLL002_def",
            "roll_number": "ROLL002",
            "teacher_id": "teacher-1",
            "exam_id": "exam-1",
            "student_id": "student-1",
            "last_heartbeat": "2026-05-18T00:00:00+00:00",
        }
    ])

    monkeypatch.setattr(database, "async_table", fake_db.table)
    monkeypatch.setattr(autosave, "load_autosave_snapshot", lambda _sid: None)

    asyncio.run(heartbeat_reaper._reap_once())

    exam_updates = [
        call for call in fake_db.calls
        if call[0] == "exam_sessions" and call[1] == "update"
    ]
    assert len(exam_updates) == 1
    assert exam_updates[0][2]["status"] == "abandoned"

def test_reaper_cas_miss_skips_flush_and_violation(monkeypatch):
    """If the abandon UPDATE matches no row (the session was submitted
    between the SELECT and the UPDATE), the reaper must not write a
    violation, flush stale autosave answers, or force-submit — any of
    those would clobber the student's real submission."""
    from app.services import heartbeat_reaper, autosave
    from app import database

    fake_db = _FakeDb([
        {
            "session_key": "ROLL002_xyz",
            "roll_number": "ROLL002",
            "teacher_id": "teacher-1",
            "exam_id": "exam-1",
            "student_id": "student-2",
            "last_heartbeat": "2026-05-18T00:00:00+00:00",
        }
    ])

    # Make every UPDATE return no matched rows (CAS miss).
    orig_execute = _FakeTable.execute

    async def execute_cas_miss(self):
        result = await orig_execute(self)
        if self.op == "update":
            result.data = []
        return result

    monkeypatch.setattr(_FakeTable, "execute", execute_cas_miss)

    flushed = {}

    async def fake_flush(session_id, answers, **kwargs):
        flushed["session_id"] = session_id

    monkeypatch.setattr(database, "async_table", fake_db.table)
    monkeypatch.setattr(autosave, "load_autosave_snapshot", lambda _sid: {"answers": {"q1": "A"}})
    monkeypatch.setattr(autosave, "flush_answers_to_db", fake_flush)

    asyncio.run(heartbeat_reaper._reap_once())

    exam_updates = [c for c in fake_db.calls if c[0] == "exam_sessions" and c[1] == "update"]
    assert len(exam_updates) == 1  # only the abandon attempt, no force_submit
    assert not any(c[0] == "violations" for c in fake_db.calls)
    assert flushed == {}  # autosave flush never ran
