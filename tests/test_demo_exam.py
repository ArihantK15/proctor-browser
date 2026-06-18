"""Fixtures for new-teacher demo-exam seeding (services/demo_exam.py).

seed_demo_exam inserts an exam_config row + the demo questions and returns
the exam_id. It accepts an injected _atable for testing. Contract: it
returns "" (and skips question insert) if the exam_config write fails, and
otherwise persists all demo questions with options JSON-encoded.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
import pytest

from app.services import demo_exam


class _FakeTable:
    def __init__(self, sink, fail_on=None):
        self._sink = sink
        self._fail_on = fail_on
        self._table = None
        self._payload = None

    def __call__(self, name):
        self._table = name
        return self

    def insert(self, payload, *a, **kw):
        self._payload = payload
        return self

    async def execute(self):
        if self._fail_on == self._table:
            raise RuntimeError("db down")
        self._sink.setdefault(self._table, []).append(self._payload)

        class _R:
            data = []
        return _R()


@pytest.mark.asyncio
async def test_seeds_config_and_questions():
    sink = {}
    fake = _FakeTable(sink)
    exam_id = await demo_exam.seed_demo_exam("teacher-1", _atable=fake)
    assert exam_id  # non-empty uuid
    # exam_config written with the teacher + DEMO access code
    cfg = sink["exam_config"][0]
    assert cfg["teacher_id"] == "teacher-1"
    assert cfg["access_code"] == "DEMO"
    assert cfg["exam_id"] == exam_id
    # all demo questions written, options JSON-encoded, scoped to the exam
    qs = sink["questions"][0]
    assert len(qs) == len(demo_exam.DEMO_QUESTIONS)
    assert all(r["exam_id"] == exam_id and r["teacher_id"] == "teacher-1" for r in qs)
    # the MCQ's options round-trip as JSON; the short-answer's options is "{}"
    mcq = next(r for r in qs if r["question_type"] == "mcq_single")
    assert json.loads(mcq["options"])["A"]
    sa = next(r for r in qs if r["question_type"] == "short_answer")
    assert sa["options"] == "{}"
    assert sa["max_score"] == 5


@pytest.mark.asyncio
async def test_returns_empty_and_skips_questions_when_config_fails():
    sink = {}
    fake = _FakeTable(sink, fail_on="exam_config")
    exam_id = await demo_exam.seed_demo_exam("teacher-1", _atable=fake)
    assert exam_id == ""
    assert "questions" not in sink  # never attempted the question insert


@pytest.mark.asyncio
async def test_question_insert_failure_still_returns_exam_id():
    """A failed question insert is best-effort — the exam_id is still
    returned so signup isn't blocked."""
    sink = {}
    fake = _FakeTable(sink, fail_on="questions")
    exam_id = await demo_exam.seed_demo_exam("teacher-1", _atable=fake)
    assert exam_id  # config succeeded → id returned despite question failure
