"""End-to-end Edge Compiler integration — against REAL Postgres.

Exercises the full submit→judge→score pipeline on real DB rows:
  - Create teacher + exam + coding question + test cases
  - Insert a coding_submission row (simulating the judge endpoint)
  - Assert the row is correctly stored and is_fully_solved is computed
  - Recalculate score with coding + MCQ and verify the fold-in
  - Cross-tenant RLS: second teacher's query cannot see the first's data

NOTE on RLS: the integration suite connects as the table owner (RLS is inert),
so we test tenancy via explicit WHERE teacher_id scoping — the same constraint
the RLS policies encode. The full RLS enforcement test requires a restricted
DB role (see docs/TENANCY_RLS_HARDENING.md).

NOTE on offboarding-guard: coding_submissions.teacher_id needs a MOVE/KEEP entry
in test_all_teacher_id_tables_are_categorized (tests/test_teacher_transfer.py).
That file is another session's WIP — we stub/skip that assertion here and flag
it for sequencing.
"""
import json
import uuid

import pytest

from app.database import async_table
from app.services.coding_judge import judge_outputs
from app.services.scoring import recalculate_score

pytestmark = pytest.mark.asyncio

TID_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
TID_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
STUDENT_ID = "11111111-1111-1111-1111-111111111111"


async def _teacher(tid: str, org_id: str = None) -> None:
    await async_table("teachers").insert({
        "id": tid, "email": f"{tid[:8]}@test.com",
        "full_name": f"Teacher {tid[:8]}",
        "org_id": org_id,
    }).execute()


async def _org(oid: str) -> None:
    await async_table("organizations").insert({
        "id": oid, "name": f"Org {oid[:8]}", "slug": oid[:8],
    }).execute()


async def _exam(teacher_id: str, exam_id: str) -> None:
    await async_table("exam_config").insert({
        "teacher_id": teacher_id,
        "exam_id": exam_id,
        "exam_title": "Coding Integration Test",
        "duration_minutes": 30,
        "coding_max_submit_attempts": 5,
    }).execute()


async def _question(teacher_id: str, exam_id: str, qid: str,
                    qtype: str = "coding", options: dict = None,
                    correct: str = "") -> str:
    """Insert a question with an EXPLICIT UUID PK and return it. The whole coding
    chain keys on questions.id (scoring matches q['id'], the judge stores
    coding_submissions.question_id from the renderer's q.id, testcases are queried
    by q.id) — so callers that score MUST key coding_test_cases / coding_submissions
    on this returned PK, not on the human `qid` label."""
    opts = options or {}
    pk = str(uuid.uuid4())
    await async_table("questions").insert({
        "id": pk,
        "teacher_id": teacher_id,
        "exam_id": exam_id,
        "question_id": qid,
        "question": f"Question {qid}",
        "question_type": qtype,
        "options": json.dumps(opts),
        "correct": correct,
    }).execute()
    return pk


async def _tc(teacher_id: str, question_id: str, idx: int,
              input_: str, expected: str, visibility: str = "hidden") -> None:
    await async_table("coding_test_cases").insert({
        "teacher_id": teacher_id,
        "question_id": question_id,
        "idx": idx,
        "input": input_,
        "expected_output": expected,
        "visibility": visibility,
    }).execute()


async def _score(sid: str, exam_id: str, teacher_id: str) -> tuple:
    return await recalculate_score(sid, {}, teacher_id=teacher_id, exam_id=exam_id)


class TestCodingE2E:
    """End-to-end: seed → submit → judge → score → RLS scoping."""

    async def test_judge_and_score_with_real_db(self):
        """Full pipeline: seed data, call judge (via function), assert the
        coding_submissions row, verify scoring folds in the result."""
        exam_id = "coding-e2e-exam-1"
        qid_coding = "coding-e2e-q1"
        qid_mcq = "coding-e2e-q2"
        session = "S_coding_e2e"

        await _org("org-e2e-1")
        await _teacher(TID_A)
        await _exam(TID_A, exam_id)

        # Coding question: sum of 3 numbers (partial marks, worth 10)
        q_coding = await _question(TID_A, exam_id, qid_coding, "coding", {
            "marks": 10, "marks_policy": "partial",
            "allowed_languages": ["javascript"],
        })
        # 1 sample + 2 hidden — keyed on the question PK
        await _tc(TID_A, q_coding, 0, "1 2 3\n", "6", "sample")
        await _tc(TID_A, q_coding, 1, "10 20 30\n", "60", "hidden")
        await _tc(TID_A, q_coding, 2, "0 0 0\n", "0", "hidden")

        # MCQ question (simple, worth 1). The MCQ path also keys on q['id'], so the
        # answer row's question_id must be the PK too.
        q_mcq = await _question(TID_A, exam_id, qid_mcq, "mcq_single",
                                {"A": "alpha", "B": "beta"}, "B")
        await async_table("answers").insert({
            "session_key": session, "question_id": q_mcq,
            "teacher_id": TID_A, "answer": "B",
        }).execute()

        # ── Simulate a judge call ────────────────────────────────────
        actual_outputs = ["6", "60", "0"]
        expected = ["6", "60", "0"]
        tolerances = [None, None, None]
        result = judge_outputs(actual_outputs, expected, tolerances)
        assert result["passed"] == 3
        assert result["total"] == 3

        # Insert the submission row (exactly as the judge endpoint does)
        await async_table("coding_submissions").insert({
            "exam_id": exam_id,
            "teacher_id": TID_A,
            "session_id": session,
            "student_id": STUDENT_ID,
            "question_id": q_coding,
            "language": "javascript",
            "test_cases_total": result["total"],
            "test_cases_passed": result["passed"],
            "average_execution_ms": 15,
            "memory_consumed_kb": 4096,
            "source_code": "// student solution",
            "keystroke_rhythm_variance": 0.03,
            "paste_attempts": 0,
            "focus_loss_count": 0,
        }).execute()

        # ── Assert coding_submissions row ────────────────────────────
        rows = (await async_table("coding_submissions")
                .select("*")
                .eq("session_id", session)
                .execute()).data
        assert len(rows) == 1
        sub = rows[0]
        assert sub["exam_id"] == exam_id
        assert sub["teacher_id"] == TID_A
        assert sub["question_id"] == q_coding
        assert sub["test_cases_passed"] == 3
        assert sub["test_cases_total"] == 3
        # is_fully_solved is GENERATED
        assert sub["is_fully_solved"] is True
        assert sub["average_execution_ms"] == 15
        assert sub["keystroke_rhythm_variance"] == 0.03

        # ── Score fold-in ────────────────────────────────────────────
        # MCQ (correct) = 1 mark + coding (3/3 * 10 = 10 marks) = 11 out of 11
        score, total = await _score(session, exam_id, TID_A)
        assert total == 11, f"Expected total 11, got {total}"
        assert score == 11, f"Expected score 11, got {score}"

    async def test_partial_score_with_wrong_outputs(self):
        """A coding submission with some wrong answers gets partial marks."""
        exam_id = "coding-e2e-exam-2"
        qid = "coding-e2e-q3"
        session = "S_coding_partial"

        await _teacher(TID_A)
        await _exam(TID_A, exam_id)
        q_pk = await _question(TID_A, exam_id, qid, "coding", {
            "marks": 10, "marks_policy": "partial",
        })
        await _tc(TID_A, q_pk, 0, "1\n", "1", "hidden")
        await _tc(TID_A, q_pk, 1, "2\n", "2", "hidden")
        await _tc(TID_A, q_pk, 2, "3\n", "3", "hidden")

        result = judge_outputs(["1", "WRONG", "3"], ["1", "2", "3"],
                               [None, None, None])
        assert result == {"passed": 2, "total": 3,
                          "per_case": [True, False, True]}

        await async_table("coding_submissions").insert({
            "exam_id": exam_id,
            "teacher_id": TID_A,
            "session_id": session,
            "question_id": q_pk,
            "language": "javascript",
            "test_cases_total": result["total"],
            "test_cases_passed": result["passed"],
        }).execute()

        score, total = await _score(session, exam_id, TID_A)
        # 2/3 * 10 = 6.67 → rounds to 7
        assert total == 10
        assert score == 7, f"Expected 7, got {score}"

    async def test_all_or_nothing_scoring(self):
        """All-or-nothing policy: not fully solved → 0 marks."""
        exam_id = "coding-e2e-exam-3"
        qid = "coding-e2e-q4"
        session = "S_coding_aon"

        await _teacher(TID_A)
        await _exam(TID_A, exam_id)
        q_pk = await _question(TID_A, exam_id, qid, "coding", {
            "marks": 5, "marks_policy": "all_or_nothing",
        })
        await _tc(TID_A, q_pk, 0, "a\n", "a", "hidden")
        await _tc(TID_A, q_pk, 1, "b\n", "b", "hidden")

        # Only 1/2 correct
        result = judge_outputs(["a", "WRONG"], ["a", "b"], [None, None])
        await async_table("coding_submissions").insert({
            "exam_id": exam_id,
            "teacher_id": TID_A,
            "session_id": session,
            "question_id": q_pk,
            "language": "javascript",
            "test_cases_total": result["total"],
            "test_cases_passed": result["passed"],
        }).execute()

        score, total = await _score(session, exam_id, TID_A)
        assert total == 5
        assert score == 0  # not fully solved → 0

    async def test_cross_tenant_rls_scoping(self):
        """Teacher A's coding_submissions are invisible when queried as
        Teacher B (simulated via WHERE teacher_id scoping)."""
        exam_a = "coding-e2e-exam-4a"
        exam_b = "coding-e2e-exam-4b"
        qid = "coding-e2e-q5"
        session_a = "S_coding_rls_a"
        session_b = "S_coding_rls_b"

        await _org("org-e2e-2")
        await _teacher(TID_A, "org-e2e-2")
        await _teacher(TID_B, "org-e2e-2")
        await _exam(TID_A, exam_a)
        await _exam(TID_B, exam_b)
        await _question(TID_A, exam_a, qid, "coding", {"marks": 1})
        await _question(TID_B, exam_b, qid, "coding", {"marks": 1})

        # Insert submission for teacher A
        await async_table("coding_submissions").insert({
            "exam_id": exam_a, "teacher_id": TID_A,
            "session_id": session_a, "question_id": qid,
            "language": "javascript",
            "test_cases_total": 2, "test_cases_passed": 2,
        }).execute()

        # Insert submission for teacher B
        await async_table("coding_submissions").insert({
            "exam_id": exam_b, "teacher_id": TID_B,
            "session_id": session_b, "question_id": qid,
            "language": "python",
            "test_cases_total": 2, "test_cases_passed": 1,
        }).execute()

        # Teacher A's scope → sees only A's submission
        a_rows = (await async_table("coding_submissions")
                  .select("*")
                  .eq("teacher_id", TID_A)
                  .execute()).data or []
        a_sessions = {r["session_id"] for r in a_rows}
        assert session_a in a_sessions
        assert session_b not in a_sessions  # B's data invisible

        # Teacher B's scope → sees only B's submission
        b_rows = (await async_table("coding_submissions")
                  .select("*")
                  .eq("teacher_id", TID_B)
                  .execute()).data or []
        b_sessions = {r["session_id"] for r in b_rows}
        assert session_b in b_sessions
        assert session_a not in b_sessions

    async def test_teacher_id_stored_in_submission(self):
        """Verify teacher_id is correctly stored in the coding_submissions row
        (the value the judge endpoint stamps from the JWT)."""
        exam_id = "coding-e2e-exam-5"
        qid = "coding-e2e-q6"
        session = "S_coding_tid"

        await _teacher(TID_A)
        await _exam(TID_A, exam_id)
        await _question(TID_A, exam_id, qid, "coding", {"marks": 1})
        await _tc(TID_A, qid, 0, "x\n", "x", "hidden")

        await async_table("coding_submissions").insert({
            "exam_id": exam_id,
            "teacher_id": TID_A,  # ← would be stamped from JWT in real endpoint
            "session_id": session,
            "question_id": qid,
            "language": "javascript",
            "test_cases_total": 1,
            "test_cases_passed": 1,
        }).execute()

        row = (await async_table("coding_submissions")
               .select("teacher_id")
               .eq("session_id", session)
               .single()
               .execute()).data
        assert row["teacher_id"] == TID_A

    async def test_coding_submissions_teacher_id_column_exists(self):
        """Schema guard: the coding_submissions table has a teacher_id column
        for RLS and offboarding categorization.

        NOTE: The offboarding guard
        (test_all_teacher_id_tables_are_categorized in
        tests/test_teacher_transfer.py) needs an entry for coding_submissions.
        That file is another session's WIP — we skip the assertion here and
        flag for sequencing."""
        cols = (await async_table("coding_submissions")
                .select("teacher_id")
                .limit(1)
                .execute()).data
        # If the column doesn't exist, this query would fail with
        # UndefinedColumnError — so reaching here IS the assertion.
        assert cols is not None
        # TODO: add coding_submissions to test_teacher_transfer.py's
        # test_all_teacher_id_tables_are_categorized (sequence with that session)
