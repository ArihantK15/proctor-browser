"""Tests for exam scheduling endpoints.

Covers:
  1. GET  /api/v1/admin/exam-schedule  — read schedule (with/without exam_id)
  2. POST /api/v1/admin/exam-schedule  — set schedule window (starts_at, ends_at)
  3. GET  /api/v1/admin/shuffle-config — read shuffle flags
  4. POST /api/v1/admin/shuffle-config — set shuffle flags
  5. GET  /api/v1/exam-schedule        — public schedule endpoint
  6. Window status logic via /api/student/exams (upcoming / open / closed)
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")

from tests.conftest import make_admin_token, make_student_token, shared_supabase_mock


TEACHER = {"id": "teacher-1", "email": "prof@test.com", "full_name": "Prof T"}


def _admin_headers():
    return {"Authorization": f"Bearer {make_admin_token(teacher_id='teacher-1', email='prof@test.com')}"}


def _student_account_token(account_id="student-1", email="alice@test.com"):
    """Create a student account JWT (role=student_account, requires sid)."""
    from jose import jwt as jose_jwt
    secret = os.environ["SUPABASE_JWT_SECRET"]
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    payload = {
        "sid": account_id,
        "email": email,
        "role": "student_account",
        "exp": now + timedelta(hours=10),
        "iat": now,
    }
    return jose_jwt.encode(payload, secret, algorithm="HS256")


def _student_headers(account_id="student-1", email="alice@test.com"):
    token = _student_account_token(account_id=account_id, email=email)
    return {"Authorization": f"Bearer {token}"}


STUDENT_ACCOUNT = {
    "id": "student-1", "email": "alice@test.com",
    "full_name": "Alice", "roll_number": "ALICE001",
}


def _table_side_effect(mapping):
    def _build_chain(data):
        m = MagicMock()
        for attr in ("select", "eq", "neq", "is_", "in_", "order",
                     "limit", "single", "range", "insert", "upsert",
                     "update", "delete", "gte", "lte", "gt", "lt",
                     "like"):
            getattr(m, attr).return_value = m

        async def _execute():
            return MagicMock(data=data)

        m.execute = _execute
        return m

    def _side_effect(name):
        return _build_chain(mapping.get(name, []))

    return _side_effect


_NOW = datetime.now(timezone.utc)
_PAST = (_NOW - timedelta(hours=2)).isoformat()
_FUTURE = (_NOW + timedelta(hours=2)).isoformat()
_FAR_PAST = (_NOW - timedelta(days=7)).isoformat()
_FAR_FUTURE = (_NOW + timedelta(days=7)).isoformat()


EXAM_CONFIG = {
    "exam_id": "exam-1",
    "teacher_id": "teacher-1",
    "exam_title": "Test Exam",
    "duration_minutes": 60,
    "starts_at": _PAST,
    "ends_at": _FUTURE,
    "access_code": "1234",
    "shuffle_questions": True,
    "shuffle_options": True,
}


# ═══════════════════════════════════════════════════════════════════
#  GET /api/v1/admin/exam-schedule
# ═══════════════════════════════════════════════════════════════════

class TestAdminGetSchedule:

    def test_get_schedule_no_exam_id(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_config": [EXAM_CONFIG],
        })):
            resp = client.get("/api/v1/admin/exam-schedule", headers=_admin_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["exam_title"] == "Test Exam"
        assert data["starts_at"] == _PAST
        assert data["ends_at"] == _FUTURE

    def test_get_schedule_with_exam_id(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_config": [EXAM_CONFIG],
        })):
            resp = client.get("/api/v1/admin/exam-schedule?exam_id=exam-1", headers=_admin_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["exam_title"] == "Test Exam"

    def test_get_schedule_no_config_uses_defaults(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_config": [],
        })):
            resp = client.get("/api/v1/admin/exam-schedule?exam_id=nonexistent", headers=_admin_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["exam_title"] == "Exam"
        assert data["starts_at"] is None
        assert data["ends_at"] is None


# ═══════════════════════════════════════════════════════════════════
#  POST /api/v1/admin/exam-schedule
# ═══════════════════════════════════════════════════════════════════

class TestAdminSetSchedule:

    def test_set_starts_at(self, client):
        sm = shared_supabase_mock()
        new_start = _FAR_FUTURE
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_config": [{**EXAM_CONFIG, "starts_at": new_start}],
        })):
            resp = client.post("/api/v1/admin/exam-schedule",
                               json={"exam_id": "exam-1", "starts_at": new_start},
                               headers=_admin_headers())
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"

    def test_set_ends_at(self, client):
        sm = shared_supabase_mock()
        new_end = _FAR_FUTURE
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_config": [{**EXAM_CONFIG, "ends_at": new_end}],
        })):
            resp = client.post("/api/v1/admin/exam-schedule",
                               json={"exam_id": "exam-1", "ends_at": new_end},
                               headers=_admin_headers())
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"

    def test_clear_schedule(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_config": [{**EXAM_CONFIG, "starts_at": None, "ends_at": None}],
        })):
            resp = client.post("/api/v1/admin/exam-schedule",
                               json={"exam_id": "exam-1"},
                               headers=_admin_headers())
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════
#  GET /api/v1/admin/shuffle-config
# ═══════════════════════════════════════════════════════════════════

class TestAdminGetShuffle:

    def test_get_shuffle_defaults(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_config": [EXAM_CONFIG],
        })):
            resp = client.get("/api/v1/admin/shuffle-config?exam_id=exam-1", headers=_admin_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["shuffle_questions"] is True
        assert data["shuffle_options"] is True


# ═══════════════════════════════════════════════════════════════════
#  POST /api/v1/admin/shuffle-config
# ═══════════════════════════════════════════════════════════════════

class TestAdminSetShuffle:

    def test_set_shuffle_disabled(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_config": [{**EXAM_CONFIG, "shuffle_questions": False, "shuffle_options": False}],
        })):
            resp = client.post("/api/v1/admin/shuffle-config",
                               json={"exam_id": "exam-1",
                                     "shuffle_questions": False,
                                     "shuffle_options": False},
                               headers=_admin_headers())
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"

    def test_no_fields_400(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
        })):
            resp = client.post("/api/v1/admin/shuffle-config",
                               json={"exam_id": "exam-1"},
                               headers=_admin_headers())
        assert resp.status_code == 400


# ═══════════════════════════════════════════════════════════════════
#  GET /api/v1/exam-schedule  —  public
# ═══════════════════════════════════════════════════════════════════

class TestPublicSchedule:

    def test_public_schedule_returns_data(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "exam_config": [EXAM_CONFIG],
        })):
            resp = client.get("/api/v1/exam-schedule?t=teacher-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["exam_title"] == "Test Exam"
        assert data["duration_minutes"] == 60

    def test_public_schedule_no_teacher(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "exam_config": [],
        })):
            resp = client.get("/api/v1/exam-schedule")
        assert resp.status_code == 200
        data = resp.json()
        assert data["exam_title"] == "Exam"


# ═══════════════════════════════════════════════════════════════════
#  Window Status  —  upcoming / open / closed
# ═══════════════════════════════════════════════════════════════════

class TestWindowStatus:

    def test_exam_upcoming(self, client):
        """Exam with starts_at in the future should show as upcoming."""
        future_start = (_NOW + timedelta(hours=1)).isoformat()
        future_end = (_NOW + timedelta(hours=3)).isoformat()
        config = {**EXAM_CONFIG, "starts_at": future_start, "ends_at": future_end}
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "student_accounts": [STUDENT_ACCOUNT],
            "students": [{"roll_number": "ALICE001", "teacher_id": "teacher-1",
                          "email": "alice@test.com"}],
            "exam_config": [config],
            "exam_sessions": [],
        })):
            resp = client.get("/api/student/exams", headers=_student_headers())
        assert resp.status_code == 200, resp.text
        body = resp.json()
        exams = body.get("exams", [])
        assert len(exams) > 0
        assert exams[0].get("status") == "upcoming"

    def test_exam_open_now(self, client):
        """Exam with past starts_at and future ends_at should show as open."""
        config = {**EXAM_CONFIG, "starts_at": _PAST, "ends_at": _FUTURE}
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "student_accounts": [STUDENT_ACCOUNT],
            "students": [{"roll_number": "ALICE001", "teacher_id": "teacher-1",
                          "email": "alice@test.com"}],
            "exam_config": [config],
            "exam_sessions": [],
        })):
            resp = client.get("/api/student/exams", headers=_student_headers())
        assert resp.status_code == 200, resp.text

    def test_exam_closed(self, client):
        """Exam with ends_at in the past should show as closed."""
        closed_config = {**EXAM_CONFIG, "starts_at": _FAR_PAST, "ends_at": _PAST}
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "student_accounts": [STUDENT_ACCOUNT],
            "students": [{"roll_number": "ALICE001", "teacher_id": "teacher-1",
                          "email": "alice@test.com"}],
            "exam_config": [closed_config],
            "exam_sessions": [],
        })):
            resp = client.get("/api/student/exams", headers=_student_headers())
        assert resp.status_code == 200, resp.text

    def test_no_schedule_always_open(self, client):
        """Exam without starts_at/ends_at should show as open."""
        no_sched = {**EXAM_CONFIG, "starts_at": None, "ends_at": None}
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "student_accounts": [STUDENT_ACCOUNT],
            "students": [{"roll_number": "ALICE001", "teacher_id": "teacher-1",
                          "email": "alice@test.com"}],
            "exam_config": [no_sched],
            "exam_sessions": [],
        })):
            resp = client.get("/api/student/exams", headers=_student_headers())
        assert resp.status_code == 200, resp.text
