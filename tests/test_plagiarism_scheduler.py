import asyncio
from unittest.mock import MagicMock

from app.services import plagiarism_scheduler as sched


class _Chain:
    """Same lightweight fake query-builder pattern as test_session_reconciler.py."""
    def __init__(self, table_name, data_by_table):
        self._table = table_name
        self._data_by_table = data_by_table

    def select(self, *a, **kw):
        return self

    def lt(self, *a):
        return self

    async def execute(self):
        return MagicMock(data=self._data_by_table.get(self._table, []))


def test_finds_recently_ended_unchecked_exams(monkeypatch):
    data_by_table = {
        "exam_config": [{"exam_id": "e1", "teacher_id": "t1"},
                         {"exam_id": "e2", "teacher_id": "t2"}],
        "coding_plagiarism_checks": [{"exam_id": "e2", "status": "ok"}],
    }
    monkeypatch.setattr(sched, "_atable", lambda name: _Chain(name, data_by_table))

    result = asyncio.run(sched._find_exams_to_check())
    assert result == [{"exam_id": "e1", "teacher_id": "t1"}]


def test_retries_previously_failed_exams(monkeypatch):
    data_by_table = {
        "exam_config": [{"exam_id": "e1", "teacher_id": "t1"}],
        "coding_plagiarism_checks": [{"exam_id": "e1", "status": "failed"}],
    }
    monkeypatch.setattr(sched, "_atable", lambda name: _Chain(name, data_by_table))

    result = asyncio.run(sched._find_exams_to_check())
    assert result == [{"exam_id": "e1", "teacher_id": "t1"}]


def test_skips_exams_with_no_ended_rows(monkeypatch):
    data_by_table = {"exam_config": [], "coding_plagiarism_checks": []}
    monkeypatch.setattr(sched, "_atable", lambda name: _Chain(name, data_by_table))

    result = asyncio.run(sched._find_exams_to_check())
    assert result == []
