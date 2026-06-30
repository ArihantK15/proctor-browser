"""Tests for question bank CRUD endpoints.

Covers the full set of endpoints in ``question_bank.py`` beyond the
``bank_to_exam`` and ``generate`` flows already tested in
``test_question_bank.py``:

  1. GET  /api/v1/admin/question-bank        — list (empty, with data, tag filter)
  2. POST /api/v1/admin/question-bank        — add (single, multiple, empty → 400)
  3. PUT  /api/v1/admin/question-bank/{qid}  — update (success, no fields → 400, not found → 404)
  4. DELETE /api/v1/admin/question-bank/{qid} — delete
  5. POST /api/v1/admin/question-bank/import — bulk import (success, empty → 400, too many → 413)
  6. GET  /api/v1/admin/question-bank/export — export
"""

import os
import sys
from unittest.mock import MagicMock, patch


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")

from tests.conftest import make_admin_token, shared_supabase_mock


TEACHER = {"id": "teacher-1", "email": "prof@test.com", "full_name": "Prof T"}


def _admin_headers():
    return {"Authorization": f"Bearer {make_admin_token(teacher_id='teacher-1', email='prof@test.com')}"}


def _table_side_effect(mapping):
    def _build_chain(data):
        m = MagicMock()
        for attr in ("select", "eq", "neq", "is_", "in_", "order",
                     "limit", "single", "range", "insert", "upsert",
                     "update", "delete", "gte", "lte", "gt", "lt",
                     "like", "contains", "count"):
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
     "question": "What is 2+2?", "question_type": "mcq_single",
     "options": {"A": "1", "B": "2", "C": "3", "D": "4"},
     "correct": "D", "tags": ["math"], "image_url": None,
     "created_at": "2025-01-01T00:00:00Z"},
    {"id": "bq2", "teacher_id": "teacher-1",
     "question": "Capital of France?", "question_type": "mcq_single",
     "options": {"A": "London", "B": "Paris", "C": "Berlin", "D": "Madrid"},
     "correct": "B", "tags": ["geography"], "image_url": None,
     "created_at": "2025-01-02T00:00:00Z"},
]


# ═══════════════════════════════════════════════════════════════════
#  GET /api/v1/admin/question-bank  —  list
# ═══════════════════════════════════════════════════════════════════

class TestListBankQuestions:

    def test_empty_list(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "question_bank": [],
        })):
            resp = client.get("/api/v1/admin/question-bank", headers=_admin_headers())
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_bank_questions(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "question_bank": BANK_QUESTIONS,
        })):
            resp = client.get("/api/v1/admin/question-bank", headers=_admin_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["id"] == "bq1"

    def test_filter_by_tag(self, client):
        # Tag filtering now happens at the DB (.contains("tags", [tag]) — see
        # question_bank.list_bank_questions), so the stub returns the already-
        # filtered set the real query would. The builder's @> SQL is covered in
        # test_postgres_table_contains.
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "question_bank": [BANK_QUESTIONS[0]],   # math row only
        })):
            resp = client.get("/api/v1/admin/question-bank?tag=math", headers=_admin_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["tags"] == ["math"]


# ═══════════════════════════════════════════════════════════════════
#  POST /api/v1/admin/question-bank  —  add
# ═══════════════════════════════════════════════════════════════════

class TestAddBankQuestions:

    def test_empty_body_400(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
        })):
            resp = client.post("/api/v1/admin/question-bank",
                               json={"questions": []}, headers=_admin_headers())
        assert resp.status_code == 400
        assert "no questions" in resp.json().get("detail", "").lower()

    def test_add_single_question(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "question_bank": [{"id": "new1", "question": "Q?", "question_type": "mcq_single",
                               "options": {"A": "1"}, "correct": "A"}],
        })):
            resp = client.post("/api/v1/admin/question-bank",
                               json={"questions": [{
                                   "question": "Q?", "options": {"A": "1"}, "correct": "A",
                               }]}, headers=_admin_headers())
        assert resp.status_code == 200

    def test_add_multiple_questions(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "question_bank": [{"id": "n1", "question": "Q1"}, {"id": "n2", "question": "Q2"}],
        })):
            resp = client.post("/api/v1/admin/question-bank",
                               json={"questions": [
                                   {"question": "Q1", "correct": "A", "options": {}},
                                   {"question": "Q2", "correct": "B", "options": {}},
                               ]}, headers=_admin_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2


# ═══════════════════════════════════════════════════════════════════
#  PUT /api/v1/admin/question-bank/{qid}  —  update
# ═══════════════════════════════════════════════════════════════════

class TestUpdateBankQuestion:

    def test_no_fields_400(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
        })):
            resp = client.put("/api/v1/admin/question-bank/bq1",
                              json={}, headers=_admin_headers())
        assert resp.status_code == 400
        assert "no fields" in resp.json().get("detail", "").lower()

    def test_not_found_404(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "question_bank": [],
        })):
            resp = client.put("/api/v1/admin/question-bank/unknown",
                              json={"question": "Updated?"}, headers=_admin_headers())
        assert resp.status_code == 404

    def test_successful_update(self, client):
        sm = shared_supabase_mock()
        updated = dict(BANK_QUESTIONS[0], question="Updated question?")
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "question_bank": [updated],
        })):
            resp = client.put("/api/v1/admin/question-bank/bq1",
                              json={"question": "Updated question?"}, headers=_admin_headers())
        assert resp.status_code == 200
        assert resp.json()["question"] == "Updated question?"


# ═══════════════════════════════════════════════════════════════════
#  DELETE /api/v1/admin/question-bank/{qid}
# ═══════════════════════════════════════════════════════════════════

class TestDeleteBankQuestion:

    def test_delete_success(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
        })):
            resp = client.delete("/api/v1/admin/question-bank/bq1", headers=_admin_headers())
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


# ═══════════════════════════════════════════════════════════════════
#  POST /api/v1/admin/question-bank/import
# ═══════════════════════════════════════════════════════════════════

class TestImportBankQuestions:

    def test_empty_import_400(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
        })):
            resp = client.post("/api/v1/admin/question-bank/import",
                               json={"questions": []}, headers=_admin_headers())
        assert resp.status_code == 400

    def test_too_many_questions_413(self, client):
        sm = shared_supabase_mock()
        many = [{"question": f"Q{i}", "correct": "A", "option_A": "x"} for i in range(2001)]
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
        })):
            resp = client.post("/api/v1/admin/question-bank/import",
                               json={"questions": many}, headers=_admin_headers())
        assert resp.status_code == 413
        assert "too many" in resp.json().get("detail", "").lower()

    def test_successful_import(self, client):
        sm = shared_supabase_mock()
        imported = [{"id": "imp1"}, {"id": "imp2"}]
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "question_bank": imported,
        })):
            resp = client.post("/api/v1/admin/question-bank/import",
                               json={"questions": [
                                   {"question": "Q1", "correct": "A", "option_A": "x"},
                                   {"question": "Q2", "correct": "B", "option_A": "y"},
                               ]}, headers=_admin_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported"] == 2

    def test_import_parses_tags(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "question_bank": [{"id": "imp1"}],
        })):
            resp = client.post("/api/v1/admin/question-bank/import",
                               json={"questions": [{
                                   "question": "Q1", "correct": "A", "option_A": "x",
                                   "tags": ["math", "algebra"],
                               }]}, headers=_admin_headers())
        assert resp.status_code == 200
        assert resp.json()["imported"] == 1


# ═══════════════════════════════════════════════════════════════════
#  GET /api/v1/admin/question-bank/export
# ═══════════════════════════════════════════════════════════════════

class TestExportBankQuestions:

    def test_export_empty(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "question_bank": [],
        })):
            resp = client.get("/api/v1/admin/question-bank/export", headers=_admin_headers())
        assert resp.status_code == 200
        assert resp.json() == []

    def test_export_with_data(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "question_bank": BANK_QUESTIONS,
        })):
            resp = client.get("/api/v1/admin/question-bank/export", headers=_admin_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["question"] == "What is 2+2?"
