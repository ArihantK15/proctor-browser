"""save-answer / save-answers-bulk persistence — against REAL Postgres.

These are the in-exam autosave endpoints. The thing only a real DB proves is the
upsert keyed on the answers UNIQUE (session_key, question_id): re-saving a
question must OVERWRITE, never duplicate — a MagicMock has no unique constraint,
so it can't catch a regression that double-inserts. The bulk path also flushes
through the shared flush_answers_to_db (Redis is down in the harness, so it
takes the durable DB branch — the production-realistic degraded path).

Shuffle is turned off on the seeded exam_config so answers persist literally;
the shuffle-translation layer is a separate concern with its own coverage.
"""
import json
import uuid

import pytest
import pytest_asyncio
from unittest.mock import MagicMock, patch

from app.database import async_table
from app.models.student import AnswerIn, BulkAnswerIn
from app.routers import exam as exam_mod
from app.routers.exam import save_answer, save_answers_bulk
from app.limiter import limiter

pytestmark = pytest.mark.asyncio

TID = "66666666-6666-6666-6666-666666666666"
SID = "77777777-7777-7777-7777-777777777777"
EID = "exam-save-1"
ROLL = "SAVE01"
SESSION = f"{ROLL}_sess1"
CLAIMS = {"roll": ROLL, "tid": TID, "eid": EID, "sid": SID}


@pytest_asyncio.fixture(autouse=True)
async def _small_pool(monkeypatch):
    from app.postgres_table import close_pool
    monkeypatch.setenv("POSTGRES_POOL_MIN", "1")
    monkeypatch.setenv("POSTGRES_POOL_MAX", "10")
    await close_pool()
    yield
    await close_pool()


async def _seed(status="in_progress"):
    await async_table("exam_config").insert({
        "teacher_id": TID, "exam_id": EID, "exam_title": "T", "duration_minutes": 60,
        "shuffle_questions": False, "shuffle_options": False,
    }).execute()
    await async_table("exam_sessions").insert({
        "session_key": SESSION, "teacher_id": TID, "exam_id": EID, "student_id": SID,
        "roll_number": ROLL, "full_name": "S", "email": "s@x.test", "status": status,
    }).execute()


async def _answers() -> dict:
    rows = (await async_table("answers").select("question_id,answer")
            .eq("session_key", SESSION).execute()).data or []
    return {r["question_id"]: r["answer"] for r in rows}


async def _count() -> int:
    from app.postgres_table import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM answers WHERE session_key = $1", SESSION)


async def _save_one(qid, ans):
    prev = limiter.enabled
    limiter.enabled = False
    try:
        with patch.object(exam_mod, "require_auth", lambda req: CLAIMS):
            return await save_answer(AnswerIn(session_id=SESSION, question_id=qid, answer=ans),
                                     MagicMock())
    finally:
        limiter.enabled = prev


async def _save_bulk(answers: dict):
    prev = limiter.enabled
    limiter.enabled = False
    try:
        with patch.object(exam_mod, "require_auth", lambda req: CLAIMS):
            return await save_answers_bulk(BulkAnswerIn(session_id=SESSION, answers=answers),
                                           MagicMock())
    finally:
        limiter.enabled = prev


async def test_bulk_save_persists_all_answers_via_db_flush():
    await _seed()
    resp = await _save_bulk({"q1": "A", "q2": "B", "q3": "C"})
    # Redis is down in the harness → durable DB branch, not the queued branch.
    assert resp["queued"] is False
    assert resp["saved"] == 3
    assert await _answers() == {"q1": "A", "q2": "B", "q3": "C"}


async def test_single_save_upsert_is_idempotent_and_overwrites():
    await _seed()
    await _save_one("q1", "A")
    await _save_one("q2", "B")
    # Re-save q1 with a different value: the student changed their mind.
    await _save_one("q1", "C")

    # Exactly two rows — the q1 re-save UPDATED in place (UNIQUE constraint),
    # it did not insert a duplicate — and q1 holds the latest value.
    assert await _count() == 2
    assert await _answers() == {"q1": "C", "q2": "B"}


async def test_bulk_save_overwrites_existing_answers():
    await _seed()
    await _save_one("q1", "A")
    await _save_one("q2", "B")
    # A later bulk save with q1 changed + q3 added.
    await _save_bulk({"q1": "X", "q2": "B", "q3": "Z"})
    assert await _count() == 3
    assert await _answers() == {"q1": "X", "q2": "B", "q3": "Z"}


async def test_save_rejected_after_session_is_terminal():
    await _seed(status="completed")
    # A submitted/completed exam must not accept further answer writes.
    with pytest.raises(Exception) as exc:
        await _save_one("q1", "A")
    assert getattr(exc.value, "status_code", None) == 409
    assert await _count() == 0  # nothing written


async def test_bulk_rejected_after_session_is_terminal():
    await _seed(status="force_submitted")
    with pytest.raises(Exception) as exc:
        await _save_bulk({"q1": "A", "q2": "B"})
    assert getattr(exc.value, "status_code", None) == 409
    assert await _count() == 0
