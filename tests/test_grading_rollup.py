"""Fixtures for the gradebook-write rollup (_apply_short_answer_to_session).

When a teacher confirms a short-answer score, the session's stored
{score,total,percentage} must be recomputed from canonical state:
MCQ correctness (recalculate_score) PLUS the sum of confirmed
teacher_scores, over the max possible (MCQ count + short-answer
max_score sum). A bug here corrupts the gradebook silently. These
tests pin the math and the idempotent "rebuild from scratch" contract.

The router queries four tables in one call; the shared conftest chain
can't return per-table data, so we patch app.routers.grading._atable
with a per-table fake and stub recalculate_score.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from tests.conftest import _AsyncTableMock
import app.routers.grading as grading
import app.services.scoring as scoring


def _patch_tables(monkeypatch, *, session_rows, sa_questions, sa_answers,
                  mcq=(0, 0)):
    """Route _atable(name) to a fake returning that table's rows, and stub
    recalculate_score → (mcq_score, mcq_total). Returns the update-capture."""
    captured = {}

    class _CapTable(_AsyncTableMock):
        def update(self, payload, *a, **kw):
            captured["update"] = payload
            return self

    def fake_atable(name):
        if name == "exam_sessions":
            return _CapTable(data=session_rows)
        if name == "questions":
            return _AsyncTableMock(data=sa_questions)
        if name == "answers":
            return _AsyncTableMock(data=sa_answers)
        return _AsyncTableMock(data=[])

    monkeypatch.setattr(grading, "_atable", fake_atable)

    async def _fake_recalc(session_id, payload, teacher_id, eid):
        return mcq

    monkeypatch.setattr(scoring, "recalculate_score", _fake_recalc)
    return captured


@pytest.mark.asyncio
async def test_returns_none_when_session_missing(monkeypatch):
    _patch_tables(monkeypatch, session_rows=[], sa_questions=[], sa_answers=[])
    r = await grading._apply_short_answer_to_session("sk", "t1")
    assert r is None


@pytest.mark.asyncio
async def test_combines_mcq_and_short_answer(monkeypatch):
    cap = _patch_tables(
        monkeypatch,
        session_rows=[{"session_key": "sk", "exam_id": "e1", "teacher_id": "t1"}],
        sa_questions=[{"question_id": "q1", "max_score": 5}, {"question_id": "q2", "max_score": 3}],
        sa_answers=[{"teacher_score": 4}, {"teacher_score": 2}, {"teacher_score": None}],
        mcq=(6, 10),
    )
    r = await grading._apply_short_answer_to_session("sk", "t1")
    # score = 6 (mcq) + 4 + 2 = 12 ; total = 10 + (5+3) = 18 ; pct = 66.7
    assert r["score"] == 12
    assert r["total"] == 18
    assert r["percentage"] == 66.7
    assert r["mcq_score"] == 6 and r["mcq_total"] == 10
    assert r["short_answer_score"] == 6 and r["short_answer_max"] == 8
    # and it must have written those totals back to the session row
    assert cap["update"] == {"score": 12, "total": 18, "percentage": 66.7}


@pytest.mark.asyncio
async def test_none_teacher_scores_ignored(monkeypatch):
    _patch_tables(
        monkeypatch,
        session_rows=[{"session_key": "sk", "exam_id": "e1", "teacher_id": "t1"}],
        sa_questions=[{"question_id": "q1", "max_score": 2}],
        sa_answers=[{"teacher_score": None}, {"teacher_score": None}],
        mcq=(0, 0),
    )
    r = await grading._apply_short_answer_to_session("sk", "t1")
    assert r["short_answer_score"] == 0
    assert r["score"] == 0
    assert r["total"] == 2
    assert r["percentage"] == 0.0


@pytest.mark.asyncio
async def test_returns_none_when_mcq_recalc_fails(monkeypatch):
    _patch_tables(
        monkeypatch,
        session_rows=[{"session_key": "sk", "exam_id": "e1", "teacher_id": "t1"}],
        sa_questions=[], sa_answers=[],
    )

    async def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(scoring, "recalculate_score", _boom)
    r = await grading._apply_short_answer_to_session("sk", "t1")
    assert r is None


@pytest.mark.asyncio
async def test_missing_max_score_defaults_to_one(monkeypatch):
    _patch_tables(
        monkeypatch,
        session_rows=[{"session_key": "sk", "exam_id": "e1", "teacher_id": "t1"}],
        sa_questions=[{"question_id": "q1"}, {"question_id": "q2", "max_score": None}],
        sa_answers=[{"teacher_score": 1}],
        mcq=(0, 0),
    )
    r = await grading._apply_short_answer_to_session("sk", "t1")
    # both short-answer questions default to max_score 1.0 → total 2
    assert r["short_answer_max"] == 2
    assert r["total"] == 2
