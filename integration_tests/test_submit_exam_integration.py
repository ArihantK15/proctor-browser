"""/submit-exam — against REAL Postgres.

This is the endpoint a student hits when they finish an exam, and the row it
writes is the authoritative grade. The unit suite mocks the DB, so it can't
prove the thing that actually matters here: that the server RE-SCORES against
real question rows and persists ITS number — never the client-supplied score —
and that the integrity guards (double-submit, identity spoofing, abandoned
recovery) behave against real rows with real constraints.

Runs the inline-scoring path (ASYNC_SCORING_ENABLED unset). Redis is pointed at
a closed port by the harness, so autosave/SSE degrade to no-ops and every write
lands in Postgres — exactly the path we want to exercise.
"""
import json
import uuid

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.database import async_table
from app.models.student import ResultIn
from app.routers import exam as exam_mod
from app.routers.exam import submit_exam
from app.limiter import limiter

pytestmark = pytest.mark.asyncio

TID = "44444444-4444-4444-4444-444444444444"
SID = "55555555-5555-5555-5555-555555555555"
EID = "exam-submit-1"
ROLL = "STU001"
SESSION = f"{ROLL}_sess1"   # suffix is NOT a uuid → ownership tid cross-check skipped

CLAIMS = {"roll": ROLL, "tid": TID, "eid": EID, "sid": SID}


@pytest_asyncio.fixture(autouse=True)
async def _small_pool(monkeypatch):
    """Tiny asyncpg pool so the suite can't trip a small dev Postgres."""
    from app.postgres_table import close_pool
    monkeypatch.setenv("POSTGRES_POOL_MIN", "1")
    monkeypatch.setenv("POSTGRES_POOL_MAX", "10")
    await close_pool()
    yield
    await close_pool()


async def _seed_questions():
    """Three MCQ-single questions; correct answers are A / B / C.

    Also seed a shuffle-OFF exam_config so the grader takes the submitted
    labels literally. With the default shuffle-on config the server
    de-translates each label through a per-session shuffle view — correct
    behaviour, but a separate concern from the scoring/integrity guarantees
    under test here.
    """
    await async_table("exam_config").insert({
        "teacher_id": TID, "exam_id": EID, "exam_title": "T", "duration_minutes": 60,
        "shuffle_questions": False, "shuffle_options": False,
    }).execute()
    for qid, correct in (("q1", "A"), ("q2", "B"), ("q3", "C")):
        await async_table("questions").insert({
            "teacher_id": TID, "exam_id": EID, "question_id": qid,
            "question": f"Question {qid}", "question_type": "mcq_single",
            "options": json.dumps({"A": "a", "B": "b", "C": "c"}),
            "correct": correct,
        }).execute()


async def _seed_session(status="in_progress", full_name="Real Student",
                        email="real@school.edu"):
    await async_table("exam_sessions").insert({
        "session_key": SESSION, "teacher_id": TID, "exam_id": EID,
        "student_id": SID, "roll_number": ROLL, "full_name": full_name,
        "email": email, "status": status,
    }).execute()


async def _session_row():
    rows = (await async_table("exam_sessions").select("*")
            .eq("session_key", SESSION).execute()).data
    return rows[0] if rows else None


def _result(answers, *, score=0, total=0, full_name="Real Student",
            email="real@school.edu"):
    return ResultIn(session_id=SESSION, roll_number=ROLL, full_name=full_name,
                    email=email, time_taken_secs=120, answers=answers,
                    score=score, total=total)


async def _submit(result):
    """Call the real endpoint with auth patched, SSE stubbed, limiter off."""
    prev = limiter.enabled
    limiter.enabled = False
    try:
        with patch.object(exam_mod, "require_auth", lambda req: CLAIMS), \
             patch.object(exam_mod, "_bus_async_publish", AsyncMock()):
            return await submit_exam(result, MagicMock())
    finally:
        limiter.enabled = prev


async def test_server_recomputes_score_and_ignores_client_score():
    await _seed_questions()
    await _seed_session()

    # Student got q1 + q3 right, q2 wrong (sent "A" instead of "B").
    # Client ALSO sends a forged score=999/total=999 — must be ignored.
    resp = await _submit(_result({"q1": "A", "q2": "A", "q3": "C"},
                                 score=999, total=999))

    # Response carries the SERVER score, not the client's 999. (The inline
    # path returns status "submitted" as an ack; "completed" is the
    # authoritative state on the DB row, asserted below.)
    assert resp["score"] == 2
    assert resp["total"] == 3

    # And the persisted authoritative row matches — not 999.
    row = await _session_row()
    assert int(row["score"]) == 2
    assert int(row["total"]) == 3
    assert row["status"] == "completed"
    assert row["submitted_at"] is not None


async def test_double_submit_is_rejected():
    await _seed_questions()
    await _seed_session()
    first = await _submit(_result({"q1": "A", "q2": "B", "q3": "C"}))
    assert int(first["score"]) == 3
    assert (await _session_row())["status"] == "completed"

    # A second submit on a COMPLETED session must 409, not re-grade/overwrite.
    with pytest.raises(Exception) as exc:
        await _submit(_result({"q1": "A", "q2": "A", "q3": "A"}))  # would score 1
    assert getattr(exc.value, "status_code", None) == 409

    # The original perfect score must be untouched.
    row = await _session_row()
    assert int(row["score"]) == 3


async def test_identity_comes_from_session_not_client():
    await _seed_questions()
    await _seed_session(full_name="Real Student", email="real@school.edu")

    # Client tries to spoof a different name/email on the result payload.
    await _submit(_result({"q1": "A", "q2": "B", "q3": "C"},
                          full_name="Spoofed Hacker", email="evil@attacker.com"))

    row = await _session_row()
    assert row["full_name"] == "Real Student"     # server kept the enrolled identity
    assert row["email"] == "real@school.edu"


async def test_abandoned_session_is_recovered_on_valid_submit():
    await _seed_questions()
    # The heartbeat reaper closed this session, but the student submits valid
    # JWT-authed answers — the attempt must be recovered, not lost.
    await _seed_session(status="abandoned")

    resp = await _submit(_result({"q1": "A", "q2": "B", "q3": "C"}))
    assert int(resp["score"]) == 3

    row = await _session_row()
    assert row["status"] == "completed"   # ABANDONED → COMPLETED, attempt not lost
    assert int(row["score"]) == 3

    # A session_recovered audit violation must be written.
    viols = (await async_table("violations").select("violation_type")
             .eq("session_key", SESSION).execute()).data or []
    types = {v["violation_type"] for v in viols}
    assert "session_recovered" in types
    assert "exam_submitted" in types
