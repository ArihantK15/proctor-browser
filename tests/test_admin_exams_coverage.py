"""Tests for exam admin endpoints that lacked coverage.

Covers:
  1. ``duplicate_exam`` (POST .../exams/{exam_id}/duplicate)
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
SRC_EXAM = {
    "exam_id": "exam-1", "exam_title": "Midterm",
    "duration_minutes": 60, "shuffle_questions": False,
    "shuffle_options": True, "starts_at": None, "ends_at": None,
    "access_code": "ABC", "teacher_id": "teacher-1",
}
SRC_QUESTIONS = [
    {"id": "q1", "question_id": 1, "exam_id": "exam-1", "teacher_id": "teacher-1",
     "question": "Q1?", "options": ["A", "B", "C", "D"], "correct": "A",
     "order_index": 1, "created_at": "...", "updated_at": "..."},
    {"id": "q2", "question_id": 2, "exam_id": "exam-1", "teacher_id": "teacher-1",
     "question": "Q2?", "options": ["A", "B", "C", "D"], "correct": "B",
     "order_index": 2, "created_at": "...", "updated_at": "..."},
]


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


# ═══════════════════════════════════════════════════════════════════
#  POST /api/v1/admin/exams/{exam_id}/duplicate
# ═══════════════════════════════════════════════════════════════════


class TestDuplicateExam:
    """Clone an exam together with its questions."""

    def test_source_exam_not_found_404(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_config": [],  # no source exam
        })):
            resp = client.post(
                "/api/v1/admin/exams/exam-1/duplicate",
                json={},
                headers=_admin_headers(),
            )
        assert resp.status_code == 404
        assert "not found" in resp.json().get("detail", "").lower()

    def test_duplicate_with_questions(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_config": [SRC_EXAM],
            "questions": SRC_QUESTIONS,
        })):
            resp = client.post(
                "/api/v1/admin/exams/exam-1/duplicate",
                json={},
                headers=_admin_headers(),
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "duplicated"
        assert body["source_exam_id"] == "exam-1"
        assert body["questions_copied"] == 2
        assert body["exam_id"] != "exam-1"
        # Default title appends " (copy)"
        assert "(copy)" in body["exam_title"]

    def test_duplicate_with_custom_title(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_config": [SRC_EXAM],
            "questions": SRC_QUESTIONS,
        })):
            resp = client.post(
                "/api/v1/admin/exams/exam-1/duplicate",
                json={"new_title": "Custom Clone"},
                headers=_admin_headers(),
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["exam_title"] == "Custom Clone"

    def test_duplicate_with_no_questions(self, client):
        """An exam with zero questions should still clone the config."""
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_config": [SRC_EXAM],
            "questions": [],
        })):
            resp = client.post(
                "/api/v1/admin/exams/exam-1/duplicate",
                json={},
                headers=_admin_headers(),
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["questions_copied"] == 0
