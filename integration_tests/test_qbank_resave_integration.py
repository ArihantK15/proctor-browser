"""Exam question re-save — against REAL Postgres.

Locks in the High-severity fix: re-saving an exam that already has questions
used to INSERT into a table with UNIQUE(teacher_id,exam_id,question_id) → 500 +
rollback, and the follow-up delete keyed on (teacher_id,exam_id) also matched
the just-written rows, wiping the exam. The fix is UPSERT + stale-only
delete-by-PK. Only a real DB with the real UNIQUE constraint can prove it (a
MagicMock never enforces UNIQUE).
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.database import async_table
from app.routers import question_bank as qb
from app.routers.question_bank import update_questions, UpdateQuestionsIn
from app.limiter import limiter

pytestmark = pytest.mark.asyncio

TID = "11111111-1111-1111-1111-111111111111"
EXAM = "exam-resave-1"


def _q(qid: str, correct: str = "A") -> dict:
    return {"id": qid, "question": f"Question {qid}", "question_type": "mcq_single",
            "options": {"A": "alpha", "B": "beta"}, "correct": correct}


async def _save(qids: list[str]) -> None:
    """Invoke the real update_questions endpoint against real Postgres."""
    body = UpdateQuestionsIn(questions=[_q(q) for q in qids], exam_id=EXAM)
    prev = limiter.enabled
    limiter.enabled = False  # bypass slowapi for the direct call
    try:
        with patch.object(qb, "require_admin", AsyncMock(return_value={"id": TID})):
            await update_questions(MagicMock(), body)
    finally:
        limiter.enabled = prev


async def _qids() -> set[str]:
    rows = (await async_table("questions").select("question_id")
            .eq("teacher_id", TID).eq("exam_id", EXAM).execute()).data or []
    return {r["question_id"] for r in rows}


async def test_first_save_inserts_all():
    await _save(["q1", "q2", "q3"])
    assert await _qids() == {"q1", "q2", "q3"}


async def test_resave_upserts_in_place_no_500_no_wipe():
    # Initial save.
    await _save(["q1", "q2", "q3"])
    assert await _qids() == {"q1", "q2", "q3"}

    # Re-save the SAME exam with an edited set (keep q1/q2, drop q3, add q4).
    # Pre-fix this 500'd on the UNIQUE collision (q1/q2) and/or wiped the exam.
    await _save(["q1", "q2", "q4"])

    # q1/q2 updated in place, q4 inserted, q3 (stale) removed — exam intact.
    assert await _qids() == {"q1", "q2", "q4"}


async def test_resave_updates_existing_row_content():
    await _save(["q1"])
    before = (await async_table("questions").select("id,correct")
              .eq("teacher_id", TID).eq("exam_id", EXAM).eq("question_id", "q1").execute()).data[0]

    # Re-save q1 with a different correct answer — must UPDATE in place (same PK
    # row id), not duplicate.
    body = UpdateQuestionsIn(questions=[_q("q1", correct="B")], exam_id=EXAM)
    prev = limiter.enabled
    limiter.enabled = False
    try:
        with patch.object(qb, "require_admin", AsyncMock(return_value={"id": TID})):
            await update_questions(MagicMock(), body)
    finally:
        limiter.enabled = prev

    rows = (await async_table("questions").select("id,correct")
            .eq("teacher_id", TID).eq("exam_id", EXAM).eq("question_id", "q1").execute()).data
    assert len(rows) == 1                 # no duplicate row
    assert rows[0]["id"] == before["id"]  # same row, upserted in place
    assert rows[0]["correct"] == "B"      # content updated
