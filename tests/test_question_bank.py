"""Tests for question bank endpoints.

Covers:
  1. ``bank_to_exam`` (POST .../question-bank/to-exam)
  2. ``generate_bank_questions`` (POST .../question-bank/generate) — LLM path
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")

from tests.conftest import make_admin_token, shared_supabase_mock  # noqa: E402


# ─── Helpers ─────────────────────────────────────────────────────────

TEACHER = {"id": "teacher-1", "email": "prof@test.com", "full_name": "Prof T"}


def _admin_headers():
    return {"Authorization": f"Bearer {make_admin_token(teacher_id='teacher-1', email='prof@test.com')}"}


def _table_side_effect(mapping):
    def _build_chain(data):
        m = MagicMock()
        for attr in ("select", "eq", "neq", "is_", "in_", "order",
                     "limit", "single", "range", "insert", "upsert",
                     "update", "delete", "gte", "lte", "gt", "lt",
                     "like", "count"):
            getattr(m, attr).return_value = m

        async def _execute():
            return MagicMock(data=data)

        m.execute = _execute
        return m

    def _side_effect(name):
        return _build_chain(mapping.get(name, []))

    return _side_effect


BANK_QUESTIONS = [
    {"id": "bq1", "teacher_id": "teacher-1",
     "question": "What is 2+2?", "options": ["1", "2", "3", "4"],
     "correct": "D", "question_type": "mcq_single",
     "tags": ["math"], "image_url": None},
    {"id": "bq2", "teacher_id": "teacher-1",
     "question": "What is the capital of France?",
     "options": ["London", "Paris", "Berlin", "Madrid"],
     "correct": "B", "question_type": "mcq_single",
     "tags": ["geography"], "image_url": None},
]


# ═══════════════════════════════════════════════════════════════════
#  POST /api/v1/admin/question-bank/to-exam
# ═══════════════════════════════════════════════════════════════════


class TestBankToExam:
    """Copy questions from the question bank into an exam."""

    def test_missing_question_ids_400(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
        })):
            resp = client.post(
                "/api/v1/admin/question-bank/to-exam",
                json={"question_ids": [], "exam_id": "exam-1"},
                headers=_admin_headers(),
            )
        assert resp.status_code == 400
        assert "question_ids and exam_id" in resp.json().get("detail", "").lower()

    def test_too_many_questions_413(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
        })):
            resp = client.post(
                "/api/v1/admin/question-bank/to-exam",
                json={"question_ids": [f"q{i}" for i in range(501)], "exam_id": "exam-1"},
                headers=_admin_headers(),
            )
        assert resp.status_code == 413
        assert "too many" in resp.json().get("detail", "").lower()

    def test_no_matching_bank_questions_404(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_config": [{"exam_id": "exam-1", "teacher_id": "teacher-1"}],
            "question_bank": [],  # bank is empty
            "questions": [],      # no existing exam questions
        })):
            resp = client.post(
                "/api/v1/admin/question-bank/to-exam",
                json={"question_ids": ["bq1"], "exam_id": "exam-1"},
                headers=_admin_headers(),
            )
        assert resp.status_code == 404
        assert "no matching" in resp.json().get("detail", "").lower()

    def test_successful_copy_to_exam(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_config": [{"exam_id": "exam-1", "teacher_id": "teacher-1"}],
            "question_bank": BANK_QUESTIONS,
            "questions": [],  # no existing questions
        })):
            resp = client.post(
                "/api/v1/admin/question-bank/to-exam",
                json={"question_ids": ["bq1", "bq2"], "exam_id": "exam-1"},
                headers=_admin_headers(),
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["added"] == 2
        assert body["skipped"] == 0

    def test_skips_incomplete_bank_questions(self, client):
        """Bank questions missing required fields should be skipped."""
        bad_bank = [
            {"id": "bad1", "teacher_id": "teacher-1",
             "question": "", "options": [], "correct": "",
             "question_type": "mcq_single", "tags": [], "image_url": None},
            BANK_QUESTIONS[1],  # valid
        ]
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_config": [{"exam_id": "exam-1", "teacher_id": "teacher-1"}],
            "question_bank": bad_bank,
            "questions": [],
        })):
            resp = client.post(
                "/api/v1/admin/question-bank/to-exam",
                json={"question_ids": ["bad1", "bq2"], "exam_id": "exam-1"},
                headers=_admin_headers(),
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["added"] == 1
        assert body["skipped"] == 1


# ═══════════════════════════════════════════════════════════════════
#  POST /api/v1/admin/question-bank/generate
# ═══════════════════════════════════════════════════════════════════


class TestGenerateBankQuestions:
    """AI-powered question generation via LLM."""

    LLM_RESPONSE = [
        {"question": "What is 2+2?", "question_type": "mcq_single",
         "option_A": "3", "option_B": "4", "option_C": "5", "option_D": "6",
         "correct": "B", "tags": ["math"], "image_url": None},
        {"question": "What is the capital of France?",
         "question_type": "mcq_single",
         "option_A": "London", "option_B": "Paris", "option_C": "Berlin",
         "option_D": "Madrid", "correct": "B", "tags": ["geography"],
         "image_url": None},
    ]

    def test_llm_not_configured_503(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
        })), \
            patch("app.llm.is_configured", return_value=False):
            resp = client.post(
                "/api/v1/admin/question-bank/generate",
                json={"topic": "math", "count": 5},
                headers=_admin_headers(),
            )
        assert resp.status_code == 503
        assert "unavailable" in resp.json().get("detail", "").lower()

    def test_generate_success(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
        })), \
            patch("app.llm.is_configured", return_value=True), \
            patch("app.llm.generate_questions",
                  return_value=self.LLM_RESPONSE):
            resp = client.post(
                "/api/v1/admin/question-bank/generate",
                json={"topic": "math", "count": 2},
                headers=_admin_headers(),
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2
        assert len(body["questions"]) == 2
        assert body["questions"][0]["correct"] == "B"

    def test_generate_empty_result_502(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
        })), \
            patch("app.llm.is_configured", return_value=True), \
            patch("app.llm.generate_questions",
                  return_value=[]):  # LLM returned nothing usable
            resp = client.post(
                "/api/v1/admin/question-bank/generate",
                json={"topic": "math", "count": 2},
                headers=_admin_headers(),
            )
        assert resp.status_code == 502
        assert "no usable" in resp.json().get("detail", "").lower()

    def test_source_text_too_long_400(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
        })), \
            patch("app.llm.is_configured", return_value=True):
            resp = client.post(
                "/api/v1/admin/question-bank/generate",
                json={"topic": "math", "count": 2,
                       "source_text": "x" * 20001},
                headers=_admin_headers(),
            )
        assert resp.status_code == 400
        assert "too long" in resp.json().get("detail", "").lower()
