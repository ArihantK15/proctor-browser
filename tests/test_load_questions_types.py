"""load_questions must preserve every valid question_type — a too-narrow
allowlist silently rewrites short_answer/numeric to mcq_single, breaking
student delivery (optionless MCQ) and the short-answer scoring exclusion.
"""
import asyncio
import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")

import app.repositories.questions as Q  # noqa: E402


class _Chain:
    def __init__(self, rows): self._rows = rows
    def select(self, *a): return self
    def eq(self, *a): return self
    def order(self, *a): return self
    async def execute(self):
        r = MagicMock(); r.data = self._rows; return r


def _run(rows):
    with patch.object(Q, "_atable", lambda t: _Chain(rows)), patch.object(Q, "_cache", None):
        return asyncio.new_event_loop().run_until_complete(Q.load_questions("t1", "e1"))


def test_preserves_numeric_and_short_answer_and_others():
    rows = [
        {"question_id": 1, "question": "m", "options": '{"A":"x","B":"y"}', "correct": "A", "question_type": "mcq_single"},
        {"question_id": 2, "question": "s", "options": "{}", "correct": "", "question_type": "short_answer"},
        {"question_id": 3, "question": "n", "options": "{}", "correct": "range:9.75:9.85", "question_type": "numeric"},
        {"question_id": 4, "question": "t", "options": '{"True":"True","False":"False"}', "correct": "True", "question_type": "true_false"},
    ]
    out = {o["id"]: o["question_type"] for o in _run(rows)}
    assert out["1"] == "mcq_single"
    assert out["2"] == "short_answer"   # not coerced
    assert out["3"] == "numeric"        # not coerced — the feature-breaking bug
    assert out["4"] == "true_false"


def test_unknown_type_still_falls_back():
    rows = [{"question_id": 9, "question": "x", "options": "{}", "correct": "A", "question_type": "essay"}]
    assert _run(rows)[0]["question_type"] == "mcq_single"
