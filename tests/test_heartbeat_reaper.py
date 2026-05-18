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

    async def execute(self):
        self.db.calls.append((self.name, self.op, self.payload, list(self.filters)))
        result = MagicMock()
        if self.name == "exam_sessions" and self.op == "select":
            result.data = self.db.stale_rows
        else:
            result.data = []
        return result


class _FakeDb:
    def __init__(self, stale_rows):
        self.stale_rows = stale_rows
        self.calls = []

    def table(self, name: str):
        return _FakeTable(name, self)


def test_reaper_marks_stale_exam_session_abandoned_and_force_submits_autosave(monkeypatch):
    from app.services import heartbeat_reaper, autosave, scoring
    from app import database

    fake_db = _FakeDb([
        {
            "session_key": "ROLL001_abc",
            "roll_number": "ROLL001",
            "teacher_id": "teacher-1",
            "exam_id": "exam-1",
            "student_id": "student-1",
            "last_heartbeat": "2026-05-18T00:00:00+00:00",
        }
    ])
    flushed = {}

    async def fake_flush(session_id, answers, **kwargs):
        flushed["session_id"] = session_id
        flushed["answers"] = answers
        flushed["kwargs"] = kwargs

    async def fake_score(session_id, answers, **kwargs):
        return 1, 2

    monkeypatch.setattr(database, "async_table", fake_db.table)
    monkeypatch.setattr(autosave, "load_autosave_snapshot", lambda _sid: {"q1": "A"})
    monkeypatch.setattr(autosave, "flush_answers_to_db", fake_flush)
    monkeypatch.setattr(scoring, "recalculate_score", fake_score)

    asyncio.run(heartbeat_reaper._reap_once())

    exam_updates = [
        call for call in fake_db.calls
        if call[0] == "exam_sessions" and call[1] == "update"
    ]
    assert exam_updates[0][2]["status"] == "abandoned"
    assert exam_updates[-1][2]["status"] == "force_submitted"
    assert exam_updates[-1][2]["score"] == 1
    assert exam_updates[-1][2]["total"] == 2
    assert any(call[0] == "violations" and call[1] == "insert" for call in fake_db.calls)
    assert flushed["session_id"] == "ROLL001_abc"
    assert flushed["answers"] == {"q1": "A"}
    assert flushed["kwargs"]["delete_after"] is True


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
