"""Tests for the coding-question scoring branch (P1-T5).

Verifies that coding questions score via coding_submissions (passed/total ×
marks) while MCQ questions continue to score via answers_match — and that the
existing MCQ path is byte-for-byte unchanged.
"""
import base64
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.scoring import recalculate_score  # noqa: E402
from app.services import secrets_crypto  # noqa: E402
from app.repositories.questions import load_questions  # noqa: E402

_TEST_SECRETS_KEY = base64.b64encode(b"\x09" * 32).decode()


@pytest.fixture(autouse=True)
def _reset_secrets_key_cache():
    """secrets_crypto caches the parsed CODING_SECRETS_KEY for process
    lifetime; reset around every test so monkeypatch.setenv takes effect."""
    secrets_crypto.reset_key_cache()
    yield
    secrets_crypto.reset_key_cache()


def _mcq(qid="mcq-1", correct="B", marks=1):
    return {
        "id": qid, "question_type": "mcq_single",
        "options": {"A": "alpha", "B": "beta", "C": "gamma", "D": "delta"},
        "correct": correct,
        "options": None,  # not needed for scoring; load_questions may return it
    }


def _coding_q(qid="coding-1", marks_policy="partial", marks=10):
    return {
        "id": qid, "question_type": "coding",
        "options": {
            "marks_policy": marks_policy,
            "marks": marks,
            "allowed_languages": ["javascript"],
        },
    }


def _submission(question_id, passed, total):
    return {
        "question_id": question_id,
        "test_cases_passed": passed,
        "test_cases_total": total,
    }


def _patch_questions(questions):
    """Patch load_questions inside scoring.py."""
    return patch("app.services.scoring.load_questions", return_value=questions)


def _setup_db_mock(answers_rows, submissions_rows, questions_rows=None):
    """Configure the shared supabase mock to return given rows for answers,
    coding_submissions, and (optionally, for tests exercising the real
    load_questions->decrypt path) questions queries."""
    from tests.conftest import _mock_supabase, _install_default_supabase_chain
    _install_default_supabase_chain()

    def _table_side_effect(name):
        chain = MagicMock()
        for attr in ("select", "eq", "is_", "in_", "order", "limit",
                     "single", "range", "gte", "lte", "update", "delete"):
            getattr(chain, attr).return_value = chain

        if name == "answers":
            chain.execute.return_value = MagicMock(data=answers_rows)
        elif name == "coding_submissions":
            chain.execute.return_value = MagicMock(data=submissions_rows)
        elif name == "questions" and questions_rows is not None:
            chain.execute.return_value = MagicMock(data=questions_rows)
        else:
            chain.execute.return_value = MagicMock(data=[])
        return chain

    _mock_supabase.table.side_effect = _table_side_effect
    return _mock_supabase


class TestCodingScoring:
    """P1-T5: scoring must fold coding question results into the total score
    while keeping the MCQ path unchanged."""

    def test_mcq_only_unaffected(self):
        """Pure MCQ exam scores identically before and after the coding branch."""
        _setup_db_mock(
            answers_rows=[{"question_id": "mcq-1", "answer": "B"}],
            submissions_rows=[],
        )
        with _patch_questions([_mcq(correct="B")]):
            score, total = asyncio_run(recalculate_score("sess_1", {}, "tid", "eid"))
        assert score == 1 and total == 1

    def test_mcq_wrong_still_wrong(self):
        _setup_db_mock(
            answers_rows=[{"question_id": "mcq-1", "answer": "A"}],
            submissions_rows=[],
        )
        with _patch_questions([_mcq(correct="B")]):
            score, total = asyncio_run(recalculate_score("sess_1", {}, "tid", "eid"))
        assert score == 0 and total == 1

    def test_coding_partial_policy(self):
        """Coding question with partial marks: passed/total * question_marks."""
        _setup_db_mock(
            answers_rows=[],
            submissions_rows=[_submission("coding-1", passed=7, total=10)],
        )
        with _patch_questions([_coding_q(marks_policy="partial", marks=10)]):
            score, total = asyncio_run(recalculate_score("sess_1", {}, "tid", "eid"))
        assert score == 7 and total == 10

    def test_coding_partial_fractional(self):
        """Partial marks yields fractional score rounded to int."""
        _setup_db_mock(
            answers_rows=[],
            submissions_rows=[_submission("coding-1", passed=3, total=4)],
        )
        with _patch_questions([_coding_q(marks_policy="partial", marks=10)]):
            score, total = asyncio_run(recalculate_score("sess_1", {}, "tid", "eid"))
        # 3/4 * 10 = 7.5 → rounds to 8
        assert score == 8 and total == 10

    def test_coding_all_or_nothing_passes(self):
        """All-or-nothing: fully solved gets full marks."""
        _setup_db_mock(
            answers_rows=[],
            submissions_rows=[_submission("coding-1", passed=10, total=10)],
        )
        with _patch_questions([_coding_q(marks_policy="all_or_nothing", marks=10)]):
            score, total = asyncio_run(recalculate_score("sess_1", {}, "tid", "eid"))
        assert score == 10 and total == 10

    def test_coding_all_or_nothing_fails(self):
        """All-or-nothing: not fully solved gets 0."""
        _setup_db_mock(
            answers_rows=[],
            submissions_rows=[_submission("coding-1", passed=9, total=10)],
        )
        with _patch_questions([_coding_q(marks_policy="all_or_nothing", marks=10)]):
            score, total = asyncio_run(recalculate_score("sess_1", {}, "tid", "eid"))
        assert score == 0 and total == 10

    def test_coding_no_submission_scores_zero(self):
        """No coding_submissions row → 0 marks for that question."""
        _setup_db_mock(
            answers_rows=[],
            submissions_rows=[],
        )
        with _patch_questions([_coding_q(marks_policy="partial", marks=10)]):
            score, total = asyncio_run(recalculate_score("sess_1", {}, "tid", "eid"))
        assert score == 0 and total == 10

    def test_mcq_and_coding_combined(self):
        """1 correct MCQ + coding 7/10 partial = 1 + 7 marks out of 1 + 10 total."""
        _setup_db_mock(
            answers_rows=[{"question_id": "mcq-1", "answer": "B"}],
            submissions_rows=[_submission("coding-1", passed=7, total=10)],
        )
        qs = [_mcq(correct="B"), _coding_q(marks_policy="partial", marks=10)]
        with _patch_questions(qs):
            score, total = asyncio_run(recalculate_score("sess_1", {}, "tid", "eid"))
        assert score == 8 and total == 11

    def test_mcq_correct_and_coding_all_or_nothing(self):
        """1 correct MCQ + coding 7/10 all-or-nothing = 1 + 0 marks."""
        _setup_db_mock(
            answers_rows=[{"question_id": "mcq-1", "answer": "B"}],
            submissions_rows=[_submission("coding-1", passed=7, total=10)],
        )
        qs = [_mcq(correct="B"), _coding_q(marks_policy="all_or_nothing", marks=10)]
        with _patch_questions(qs):
            score, total = asyncio_run(recalculate_score("sess_1", {}, "tid", "eid"))
        assert score == 1 and total == 11

    def test_coding_uses_latest_submission(self):
        """When multiple coding_submissions exist, the latest (highest
        submitted_at) is used. We simulate this by returning two rows."""
        _setup_db_mock(
            answers_rows=[],
            submissions_rows=[
                _submission("coding-1", passed=3, total=10),
                _submission("coding-1", passed=8, total=10),
            ],
        )
        with _patch_questions([_coding_q(marks_policy="partial", marks=10)]):
            score, total = asyncio_run(recalculate_score("sess_1", {}, "tid", "eid"))
        # Should use the latest row (last in list = highest index)
        assert score == 8 and total == 10

    def test_coding_default_marks_policy_is_partial(self):
        """When marks_policy is absent from options, default to partial."""
        q = _coding_q()
        del q["options"]["marks_policy"]
        _setup_db_mock(
            answers_rows=[],
            submissions_rows=[_submission("coding-1", passed=5, total=10)],
        )
        with _patch_questions([q]):
            score, total = asyncio_run(recalculate_score("sess_1", {}, "tid", "eid"))
        assert score == 5 and total == 10

    def test_coding_default_marks_is_1(self):
        """When marks is absent from options, default to 1."""
        q = _coding_q()
        del q["options"]["marks"]
        _setup_db_mock(
            answers_rows=[],
            submissions_rows=[_submission("coding-1", passed=1, total=1)],
        )
        with _patch_questions([q]):
            score, total = asyncio_run(recalculate_score("sess_1", {}, "tid", "eid"))
        assert score == 1 and total == 1


class TestEncryptedCorrectAnswerScoring:
    """The MCQ `correct` answer key may now be stored as an enc:v1: token
    (app/services/secrets_crypto.py). recalculate_score's MCQ path reads
    `correct` via load_questions (app/repositories/questions.py), which
    decrypts transparently — these tests exercise the REAL load_questions
    function (not the patched-in dict) against an encrypted DB row to prove
    the grading read path actually decrypts."""

    def test_mcq_scores_correctly_with_encrypted_correct(self, monkeypatch):
        monkeypatch.setenv("CODING_SECRETS_KEY", _TEST_SECRETS_KEY)
        secrets_crypto.reset_key_cache()
        encrypted_correct = secrets_crypto.encrypt("B")
        assert secrets_crypto.is_encrypted(encrypted_correct)

        questions_rows = [{
            "question_id": "mcq-1", "question": "q", "options": '{"A":"a","B":"b"}',
            "correct": encrypted_correct, "question_type": "mcq_single",
        }]
        _setup_db_mock(
            answers_rows=[{"question_id": "mcq-1", "answer": "B"}],
            submissions_rows=[],
            questions_rows=questions_rows,
        )
        # Use the REAL load_questions (no patch) so the DB row's enc:v1:
        # token actually flows through the decrypt path under test.
        score, total = asyncio_run(recalculate_score("sess_1", {}, "tid", "eid"))
        assert score == 1 and total == 1

    def test_mcq_wrong_answer_still_wrong_with_encrypted_correct(self, monkeypatch):
        monkeypatch.setenv("CODING_SECRETS_KEY", _TEST_SECRETS_KEY)
        secrets_crypto.reset_key_cache()
        encrypted_correct = secrets_crypto.encrypt("B")

        questions_rows = [{
            "question_id": "mcq-1", "question": "q", "options": '{"A":"a","B":"b"}',
            "correct": encrypted_correct, "question_type": "mcq_single",
        }]
        _setup_db_mock(
            answers_rows=[{"question_id": "mcq-1", "answer": "A"}],
            submissions_rows=[],
            questions_rows=questions_rows,
        )
        score, total = asyncio_run(recalculate_score("sess_1", {}, "tid", "eid"))
        assert score == 0 and total == 1

    def test_load_questions_decrypts_legacy_plaintext_correct_unchanged(self):
        """Legacy rows (no enc:v1: prefix, written before this feature existed)
        keep working with no key configured — backward compatibility."""
        questions_rows = [{
            "question_id": "mcq-1", "question": "q", "options": '{"A":"a","B":"b"}',
            "correct": "B", "question_type": "mcq_single",
        }]
        _setup_db_mock(answers_rows=[], submissions_rows=[], questions_rows=questions_rows)
        result = asyncio_run(load_questions("tid", exam_id="eid"))
        assert result[0]["correct"] == "B"


def asyncio_run(coro):
    """Run a coroutine synchronously, scoping the event loop."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Already running — use a fresh loop in a new thread
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor() as pool:
        fut = pool.submit(asyncio.run, coro)
        return fut.result()
