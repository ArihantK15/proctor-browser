"""Tests for Gap #42: Question version history (audit trail).

Covers:
  1. GET  /api/v1/admin/question-bank/{qid}/versions        — list (newest first)
  2. POST /api/v1/admin/question-bank/{qid}/versions/{v}/restore — restore snapshot
  3. Ownership — other teacher's question → 404 on both
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")

from tests.conftest import make_admin_token, shared_supabase_mock

TEACHER = {"id": "teacher-1", "email": "prof@test.com", "full_name": "Prof T"}


def _admin_headers(tid="teacher-1", email="prof@test.com"):
    return {"Authorization": f"Bearer {make_admin_token(teacher_id=tid, email=email)}"}


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


QUESTION_ID = "bq-vers-1"

SNAPSHOT_V1 = {
    "question": "Original question?",
    "question_type": "mcq_single",
    "options": {"A": "1", "B": "2"},
    "correct": "A",
    "tags": ["test"],
    "image_url": None,
}

SNAPSHOT_V2 = {
    "question": "Updated question?",
    "question_type": "mcq_single",
    "options": {"A": "1", "B": "2"},
    "correct": "A",
    "tags": ["test"],
    "image_url": None,
}

VERSIONS = [
    {
        "id": "ver-2",
        "question_id": QUESTION_ID,
        "teacher_id": "teacher-1",
        "version_number": 2,
        "change_type": "update",
        "snapshot": json.dumps(SNAPSHOT_V2),
        "changed_by": "teacher-1",
        "changed_at": "2025-01-02T00:00:00Z",
    },
    {
        "id": "ver-1",
        "question_id": QUESTION_ID,
        "teacher_id": "teacher-1",
        "version_number": 1,
        "change_type": "create",
        "snapshot": json.dumps(SNAPSHOT_V1),
        "changed_by": "teacher-1",
        "changed_at": "2025-01-01T00:00:00Z",
    },
]


# ═══════════════════════════════════════════════════════════════════
#  GET /api/v1/admin/question-bank/{qid}/versions
# ═══════════════════════════════════════════════════════════════════

class TestListQuestionVersions:

    def test_list_returns_newest_first(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "question_bank": [{"id": QUESTION_ID, "teacher_id": "teacher-1"}],
            "question_versions": VERSIONS,
        })):
            resp = client.get(
                f"/api/v1/admin/question-bank/{QUESTION_ID}/versions",
                headers=_admin_headers(),
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["version_number"] == 2  # newest first
        assert data[1]["version_number"] == 1

    def test_list_404_other_teacher(self, client):
        """Other teacher's question → 404."""
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [{"id": "teacher-2", "email": "other@test.com"}],
            "question_bank": [],  # teacher-2 doesn't own this question
        })):
            resp = client.get(
                f"/api/v1/admin/question-bank/{QUESTION_ID}/versions",
                headers=_admin_headers(tid="teacher-2", email="other@test.com"),
            )
        assert resp.status_code == 404

    def test_list_404_nonexistent_question(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "question_bank": [],
        })):
            resp = client.get(
                "/api/v1/admin/question-bank/nonexistent/versions",
                headers=_admin_headers(),
            )
        assert resp.status_code == 404

    def test_list_empty_200(self, client):
        """Question exists but has no versions → 200 with []."""
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "question_bank": [{"id": QUESTION_ID, "teacher_id": "teacher-1"}],
            "question_versions": [],
        })):
            resp = client.get(
                f"/api/v1/admin/question-bank/{QUESTION_ID}/versions",
                headers=_admin_headers(),
            )
        assert resp.status_code == 200
        assert resp.json() == []


# ═══════════════════════════════════════════════════════════════════
#  POST /api/v1/admin/question-bank/{qid}/versions/{version}/restore
# ═══════════════════════════════════════════════════════════════════

class TestRestoreQuestionVersion:

    def test_restore_creates_new_version(self, client):
        """Restore applies the snapshot and records a new update version."""
        sm = shared_supabase_mock()
        updated_row = dict(SNAPSHOT_V1, id=QUESTION_ID, teacher_id="teacher-1")
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "question_bank": [
                {"id": QUESTION_ID, "teacher_id": "teacher-1"},  # ownership check
                updated_row,                                       # after update
            ],
            "question_versions": [VERSIONS[1]],  # version 1 snapshot
        })):
            resp = client.post(
                f"/api/v1/admin/question-bank/{QUESTION_ID}/versions/1/restore",
                headers=_admin_headers(),
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["version"] == 1

    def test_restore_404_other_teacher(self, client):
        """Other teacher's question → 404."""
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [{"id": "teacher-2", "email": "other@test.com"}],
            "question_bank": [],
        })):
            resp = client.post(
                f"/api/v1/admin/question-bank/{QUESTION_ID}/versions/1/restore",
                headers=_admin_headers(tid="teacher-2", email="other@test.com"),
            )
        assert resp.status_code == 404

    def test_restore_404_nonexistent_version(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "question_bank": [{"id": QUESTION_ID, "teacher_id": "teacher-1"}],
            "question_versions": [],
        })):
            resp = client.post(
                f"/api/v1/admin/question-bank/{QUESTION_ID}/versions/99/restore",
                headers=_admin_headers(),
            )
        assert resp.status_code == 404

    def test_restore_404_nonexistent_question(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "question_bank": [],
        })):
            resp = client.post(
                f"/api/v1/admin/question-bank/{QUESTION_ID}/versions/1/restore",
                headers=_admin_headers(),
            )
        assert resp.status_code == 404
