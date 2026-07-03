"""Tests for exam scheduling endpoints.

Covers:
  1. GET  /api/v1/admin/exam-schedule  — read schedule (with/without exam_id)
  2. POST /api/v1/admin/exam-schedule  — set schedule window (starts_at, ends_at)
  3. GET  /api/v1/admin/shuffle-config — read shuffle flags
  4. POST /api/v1/admin/shuffle-config — set shuffle flags
  5. GET/POST /api/v1/admin/proctoring-sensitivity — false-positive sensitivity
  6. GET  /api/v1/exam-schedule        — public schedule endpoint
  7. Window status logic via /api/student/exams (upcoming / open / closed)
"""

import os
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")

from tests.conftest import make_admin_token, shared_supabase_mock


TEACHER = {"id": "teacher-1", "email": "prof@test.com", "full_name": "Prof T"}


def _admin_headers():
    return {"Authorization": f"Bearer {make_admin_token(teacher_id='teacher-1', email='prof@test.com')}"}


def _student_account_token(account_id="student-1", email="alice@test.com"):
    """Create a student account JWT (role=student_account, requires sid)."""
    import jwt as jose_jwt
    from app.constants import STUDENT_SIGNING_KEY
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    payload = {
        "sid": account_id,
        "email": email,
        "role": "student_account",
        "exp": now + timedelta(hours=10),
        "iat": now,
    }
    return jose_jwt.encode(payload, STUDENT_SIGNING_KEY, algorithm="HS256")


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
#  GET/POST /api/v1/admin/proctoring-sensitivity
# ═══════════════════════════════════════════════════════════════════

class TestAdminProctoringConfig:

    def test_get_proctoring_config_defaults_to_balanced(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_config": [{**EXAM_CONFIG, "proctoring_sensitivity": None}],
        })):
            resp = client.get("/api/v1/admin/proctoring-sensitivity?exam_id=exam-1",
                              headers=_admin_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["proctoring_sensitivity"] == "balanced"
        assert "strict" in data["presets"]
        assert "lenient" in data["presets"]

    def test_set_proctoring_config_accepts_lenient(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_config": [{**EXAM_CONFIG, "proctoring_sensitivity": "lenient"}],
        })):
            resp = client.post("/api/v1/admin/proctoring-sensitivity",
                               json={"exam_id": "exam-1", "proctoring_sensitivity": "lenient"},
                               headers=_admin_headers())
        assert resp.status_code == 200
        assert resp.json()["proctoring_sensitivity"] == "lenient"

    def test_set_proctoring_config_rejects_unknown_value(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_config": [EXAM_CONFIG],
        })):
            resp = client.post("/api/v1/admin/proctoring-sensitivity",
                               json={"exam_id": "exam-1", "proctoring_sensitivity": "maximum"},
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

    def test_null_access_code_does_not_crash_lobby(self, client):
        """Exam configs may store NULL access_code (legacy exams predating
        the mandatory-access-code requirement); the lobby must still render,
        and access_code_required is now always True — the server
        auto-generates and persists a code for exams that don't have one
        rather than treating a null code as "none required"."""
        config = {**EXAM_CONFIG, "access_code": None}
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
        exams = resp.json().get("exams", [])
        assert exams
        assert exams[0]["access_code_required"] is True

    def test_account_id_linked_roster_rows_are_visible(self, client):
        """A linked account should see exams even if email lookup misses."""
        sm = shared_supabase_mock()
        calls = {"students": 0}

        def _side_effect(name):
            if name == "students":
                calls["students"] += 1
                if calls["students"] == 1:
                    return _table_side_effect({"students": []})("students")
                return _table_side_effect({"students": [{
                    "roll_number": "ALICE001",
                    "teacher_id": "teacher-1",
                    "account_id": "student-1",
                    "email": "",
                }]})("students")
            return _table_side_effect({
                "teachers": [TEACHER],
                "student_accounts": [STUDENT_ACCOUNT],
                "exam_config": [EXAM_CONFIG],
                "exam_sessions": [],
            })(name)

        with patch.object(sm, "table", side_effect=_side_effect):
            resp = client.get("/api/student/exams", headers=_student_headers())
        assert resp.status_code == 200, resp.text
        assert resp.json().get("exams")

    def test_session_status_is_scoped_to_enrolled_exam_id(self, client):
        """A completed attempt in Exam A must not mark Exam B submitted."""

        class _Chain:
            def __init__(self, rows):
                self.rows = list(rows)
                self.eqs = {}
                self.ins = {}
                self._limit = None
                self._order_col = None
                self._order_desc = False

            def select(self, *a, **kw): return self
            def eq(self, col, val):
                self.eqs[col] = val
                return self
            def in_(self, col, vals):
                self.ins[col] = set(vals or [])
                return self
            def order(self, col, desc=False, **kw):
                self._order_col = col
                self._order_desc = bool(desc)
                return self
            def limit(self, n):
                self._limit = n
                return self
            async def execute(self):
                rows = []
                for row in self.rows:
                    if all(str(row.get(k) or "") == str(v or "") for k, v in self.eqs.items()) \
                       and all(row.get(k) in vals for k, vals in self.ins.items()):
                        rows.append(row)
                if self._order_col:
                    rows.sort(key=lambda r: str(r.get(self._order_col) or ""), reverse=self._order_desc)
                if self._limit is not None:
                    rows = rows[:self._limit]
                return MagicMock(data=rows)

        rows = {
            "student_accounts": [STUDENT_ACCOUNT],
            # Teacher-wide roster row (students has no exam_id column).
            "students": [{
                "roll_number": "ALICE001",
                "teacher_id": "teacher-1",
                "email": "alice@test.com",
                "account_id": "student-1",
            }],
            # Per-exam membership lives here: registered for exam-2 only.
            # This is the canonical source the lobby now reads (replacing
            # the old students.exam_id).
            "student_invites": [{
                "teacher_id": "teacher-1",
                "roll_number": "ALICE001",
                "exam_id": "exam-2",
                "status": "accepted",
            }],
            "teachers": [TEACHER],
            "exam_config": [
                {**EXAM_CONFIG, "exam_id": "exam-1", "exam_title": "Old Exam"},
                {**EXAM_CONFIG, "exam_id": "exam-2", "exam_title": "Demo Exam"},
            ],
            "exam_sessions": [{
                "status": "completed",
                "submitted_at": _PAST,
                "teacher_id": "teacher-1",
                "roll_number": "ALICE001",
                "exam_id": "exam-1",
            }],
        }

        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=lambda name: _Chain(rows.get(name, []))):
            resp = client.get("/api/student/exams", headers=_student_headers())

        assert resp.status_code == 200, resp.text
        exams = resp.json().get("exams", [])
        assert len(exams) == 1
        assert exams[0]["exam_id"] == "exam-2"
        assert exams[0]["exam_title"] == "Demo Exam"
        assert exams[0]["status"] == "open"
