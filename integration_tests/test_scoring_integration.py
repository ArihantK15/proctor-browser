"""Scoring engine — against REAL Postgres.

recalculate_score reads the exam's questions and the student's saved answers from
the DB and grades them. This exercises the real grading path end-to-end on real
rows: set-equality for MCQ, numeric-range tolerance, and the short_answer
exclusion from auto-grading — the logic behind every exam score. The mocked unit
suite tests answers_match in isolation but never against rows actually read back
out of Postgres (TEXT options json-roundtrip, question_id↔answers join, etc.).
"""
import json

import pytest

from app.database import async_table
from app.services.scoring import recalculate_score

pytestmark = pytest.mark.asyncio

TID = "33333333-3333-3333-3333-333333333333"


async def _q(exam: str, qid: str, qtype: str, options: dict, correct: str) -> None:
    await async_table("questions").insert({
        "teacher_id": TID, "exam_id": exam, "question_id": qid,
        "question": f"Question {qid}", "options": json.dumps(options),
        "correct": correct, "question_type": qtype,
    }).execute()


async def _a(sid: str, qid: str, ans: str) -> None:
    await async_table("answers").insert({
        "session_key": sid, "question_id": qid, "teacher_id": TID, "answer": ans,
    }).execute()


async def test_mixed_question_types_scored_against_real_rows():
    exam, sid = "exam-mixed", "S_mixed"
    await _q(exam, "q1", "mcq_single", {"A": "a", "B": "b"}, "A")
    await _q(exam, "q2", "mcq_multi", {"A": "a", "B": "b", "C": "c"}, "A,C")
    await _q(exam, "q3", "true_false", {"True": "True", "False": "False"}, "True")
    await _q(exam, "q4", "numeric", {}, "range:9.5:10.5")
    await _q(exam, "q5", "short_answer", {}, "")     # excluded from auto-grading

    await _a(sid, "q1", "A")        # correct
    await _a(sid, "q2", "C,A")      # correct — set-equality, order-insensitive
    await _a(sid, "q3", "False")    # WRONG
    await _a(sid, "q4", "10")       # correct — within tolerance band
    await _a(sid, "q5", "an essay") # excluded

    score, total = await recalculate_score(sid, {}, teacher_id=TID, exam_id=exam)
    assert total == 4               # short_answer not counted
    assert score == 3               # q1, q2, q4 correct; q3 wrong


async def test_numeric_out_of_range_is_wrong():
    exam, sid = "exam-num-oor", "S_oor"
    await _q(exam, "n1", "numeric", {}, "range:5:5")   # exact value 5
    await _a(sid, "n1", "6")
    assert await recalculate_score(sid, {}, teacher_id=TID, exam_id=exam) == (0, 1)


async def test_numeric_on_boundary_is_correct():
    exam, sid = "exam-num-edge", "S_edge"
    await _q(exam, "n2", "numeric", {}, "range:9.5:10.5")
    await _a(sid, "n2", "9.5")       # inclusive lower bound
    assert await recalculate_score(sid, {}, teacher_id=TID, exam_id=exam) == (1, 1)


async def test_unanswered_questions_score_zero_but_count_in_total():
    exam, sid = "exam-blank", "S_blank"
    await _q(exam, "b1", "mcq_single", {"A": "a", "B": "b"}, "A")
    await _q(exam, "b2", "mcq_single", {"A": "a", "B": "b"}, "B")
    # no answers saved at all
    assert await recalculate_score(sid, {}, teacher_id=TID, exam_id=exam) == (0, 2)
