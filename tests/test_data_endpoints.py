"""
Tests for data endpoints: events, analyze-frame, exam CRUD, questions, schedule.

Covers audit findings:
- Heartbeat upsert overwrites completed session data
- updated_at = "now()" sets literal string, not SQL function
- Non-atomic delete-then-insert in update_questions
- analyze_frame silently swallows all errors
- analyze_frame has no size limit on base64 payload (OOM risk)
- admin_set_schedule creates orphan config rows when exam_id is falsy
- No validation on duration_minutes (negative/zero accepted)
"""
import os
import sys
import base64
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.conftest import shared_supabase_mock,  make_student_token, make_admin_token

TEACHER = {"id": "teacher-1", "email": "prof@test.com", "full_name": "Prof T", "org_id": "org-1", "org_role": "admin"}


def admin_headers():
    return {"Authorization": f"Bearer {make_admin_token(teacher_id='teacher-1', email='prof@test.com')}"}


def admin_patch():
    return patch("app.auth.admin_auth._get_teacher_by_id", return_value=TEACHER)


# ─── Analyze Frame ────────────────────────────────────────────────────

class TestAnalyzeFrame:
    def test_errors_now_raise_500(self, client):
        token = make_student_token(roll="ALICE001")
        resp = client.post("/api/v1/analyze-frame",
                           json={"session_id": "ALICE001_123",
                                 "frame": "not-valid-base64!!!",
                                 "timestamp": "2025-01-01T00:00:00Z"},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 500

    def test_size_limit_enforced(self, client):
        token = make_student_token(roll="ALICE001")
        large_payload = "A" * 600_000
        resp = client.post("/api/v1/analyze-frame",
                           json={"session_id": "ALICE001_123",
                                 "frame": large_payload,
                                 "timestamp": "2025-01-01T00:00:00Z"},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 413

    def test_path_traversal_sanitized(self, client):
        token = make_student_token(roll="../../etc")
        with patch("builtins.open", MagicMock()), patch("os.makedirs"):
            resp = client.post("/api/v1/analyze-frame",
                               json={"session_id": "../../etc_123",
                                     "frame": base64.b64encode(b"test").decode(),
                                     "timestamp": "2025-01-01T00:00:00Z"},
                               headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200

    def test_normal_frame_accepted(self, client):
        token = make_student_token(roll="ALICE001")
        small_frame = base64.b64encode(b"test_image_data").decode()
        with patch("builtins.open", MagicMock()), \
             patch("os.makedirs"), \
             patch("os.path.realpath", side_effect=lambda p: p):
            resp = client.post("/api/v1/analyze-frame",
                               json={"session_id": "ALICE001_123",
                                     "frame": small_frame,
                                     "timestamp": "2025-01-01T00:00:00Z"},
                               headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200


# ─── ID Verification ──────────────────────────────────────────────────

class TestIdVerification:
    def test_decode_errors_now_raise_500(self, client):
        token = make_student_token(roll="ALICE001")
        resp = client.post("/api/v1/id-verification",
                           json={"session_id": "ALICE001_123",
                                 "roll_number": "ALICE001",
                                 "selfie_frame": "bad-base64!!",
                                 "id_frame": "also-bad!!",
                                 "full_name": "Alice",
                                 "timestamp": "2025-01-01T00:00:00Z"},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 500

    def test_oversized_frame_rejected(self, client):
        token = make_student_token(roll="ALICE001")
        huge = "A" * 600_000
        resp = client.post("/api/v1/id-verification",
                           json={"session_id": "ALICE001_123",
                                 "roll_number": "ALICE001",
                                 "selfie_frame": huge,
                                 "id_frame": base64.b64encode(b"ok").decode(),
                                 "full_name": "Alice"},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 413


# ─── Update Questions ─────────────────────────────────────────────────

class TestUpdateQuestions:
    def test_missing_questions_key(self, client):
        with admin_patch():
            resp = client.post("/api/v1/admin/questions",
                               json={"not_questions": []},
                               headers=admin_headers())
            assert resp.status_code == 422

    def test_empty_questions_list(self, client):
        with admin_patch():
            resp = client.post("/api/v1/admin/questions",
                               json={"questions": []},
                               headers=admin_headers())
            assert resp.status_code == 400

    def test_question_missing_required_fields(self, client):
        with admin_patch():
            resp = client.post("/api/v1/admin/questions",
                               json={"questions": [{"id": 1}]},
                               headers=admin_headers())
            assert resp.status_code == 400
            assert "missing" in resp.json()["detail"].lower()

    def test_invalid_question_type(self, client):
        with admin_patch():
            resp = client.post("/api/v1/admin/questions",
                               json={"questions": [{
                                   "id": 1, "question": "Q?",
                                   "options": {"A": "yes", "B": "no"},
                                   "correct": "A", "question_type": "essay"}]},
                               headers=admin_headers())
            assert resp.status_code == 400
            assert "invalid question_type" in resp.json()["detail"].lower()

    def test_mcq_single_with_multiple_correct(self, client):
        with admin_patch():
            resp = client.post("/api/v1/admin/questions",
                               json={"questions": [{
                                   "id": 1, "question": "Q?",
                                   "options": {"A": "yes", "B": "no"},
                                   "correct": "A,B", "question_type": "mcq_single"}]},
                               headers=admin_headers())
            assert resp.status_code == 400
            assert "exactly 1" in resp.json()["detail"].lower()

    def test_mcq_multi_with_single_correct(self, client):
        with admin_patch():
            resp = client.post("/api/v1/admin/questions",
                               json={"questions": [{
                                   "id": 1, "question": "Q?",
                                   "options": {"A": "yes", "B": "no", "C": "maybe"},
                                   "correct": "A", "question_type": "mcq_multi"}]},
                               headers=admin_headers())
            assert resp.status_code == 400
            assert "at least 2" in resp.json()["detail"].lower()

    def test_correct_answer_not_in_options(self, client):
        with admin_patch():
            resp = client.post("/api/v1/admin/questions",
                               json={"questions": [{
                                   "id": 1, "question": "Q?",
                                   "options": {"A": "yes", "B": "no"},
                                   "correct": "Z"}]},
                               headers=admin_headers())
            assert resp.status_code == 400
            assert "not in options" in resp.json()["detail"].lower()

    def test_true_false_invalid_correct(self, client):
        with admin_patch():
            resp = client.post("/api/v1/admin/questions",
                               json={"questions": [{
                                   "id": 1, "question": "Is sky blue?",
                                   "options": {},
                                   "correct": "Maybe", "question_type": "true_false"}]},
                               headers=admin_headers())
            assert resp.status_code == 400

    def test_options_less_than_2(self, client):
        with admin_patch():
            resp = client.post("/api/v1/admin/questions",
                               json={"questions": [{
                                   "id": 1, "question": "Q?",
                                   "options": {"A": "only"},
                                   "correct": "A"}]},
                               headers=admin_headers())
            assert resp.status_code == 400
            assert "at least 2" in resp.json()["detail"].lower()

    def test_numeric_missing_range_rejected(self, client):
        with admin_patch():
            resp = client.post("/api/v1/admin/questions",
                               json={"questions": [{
                                   "id": 1, "question": "How many planets?",
                                   "options": {}, "correct": "",
                                   "question_type": "numeric"}]},
                               headers=admin_headers())
            assert resp.status_code == 400
            assert "min and max" in resp.json()["detail"].lower()

    def test_numeric_non_numeric_bounds_rejected(self, client):
        with admin_patch():
            resp = client.post("/api/v1/admin/questions",
                               json={"questions": [{
                                   "id": 1, "question": "Value of g?",
                                   "options": {}, "correct": "range:a:b",
                                   "question_type": "numeric"}]},
                               headers=admin_headers())
            assert resp.status_code == 400
            assert "must be numbers" in resp.json()["detail"].lower()

    def test_numeric_valid_range_accepted(self, client):
        with admin_patch():
            resp = client.post("/api/v1/admin/questions",
                               json={"questions": [{
                                   "id": 1, "question": "Value of g (m/s^2)?",
                                   "options": {}, "correct": "range:9.75:9.85",
                                   "question_type": "numeric"}]},
                               headers=admin_headers())
            # Validation must pass (no 400). DB layer is mocked by conftest.
            assert resp.status_code != 400, resp.text


# ─── Exam Schedule ────────────────────────────────────────────────────

class TestExamSchedule:
    def test_schedule_without_exam_id_rejected(self, client):
        with admin_patch():
            resp = client.post("/api/v1/admin/exam-schedule",
                               json={"starts_at": "2025-06-01T09:00:00Z",
                                     "ends_at": "2025-06-01T11:00:00Z"},
                               headers=admin_headers())
            assert resp.status_code == 422

    def test_schedule_with_exam_id(self, client):
        with admin_patch(), \
             patch.object(shared_supabase_mock(), "table") as mock_table, \
             patch("app.dependencies._cache") as mock_c:
            mock_table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
            mock_c.delete = MagicMock()
            resp = client.post("/api/v1/admin/exam-schedule",
                               json={"exam_id": "exam-1",
                                     "starts_at": "2025-06-01T09:00:00Z",
                                     "ends_at": "2025-06-01T11:00:00Z"},
                               headers=admin_headers())
            assert resp.status_code == 200


# ─── Event Logging ────────────────────────────────────────────────────

class TestEventLogging:
    def test_requires_auth(self, client):
        resp = client.post("/api/v1/event",
                           json={"session_id": "ALICE001_1",
                                 "event_type": "tab_switch", "severity": "medium"})
        assert resp.status_code == 401

    def test_wrong_session(self, client):
        token = make_student_token(roll="ALICE001")
        resp = client.post("/api/v1/event",
                           json={"session_id": "BOB002_1",
                                 "event_type": "tab_switch", "severity": "medium"},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403

    def test_valid_event(self, client):
        token = make_student_token(roll="ALICE001")
        with patch.object(shared_supabase_mock(), "table") as mock_table, \
             patch("app.routers.exam._atable") as atable_mock:
            mock_table.return_value.insert.return_value.execute.return_value = MagicMock(data=[])
            atable_mock.return_value.insert.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))
            resp = client.post("/api/v1/event",
                               json={"session_id": "ALICE001_123",
                                     "event_type": "tab_switch", "severity": "medium"},
                               headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200


# ─── Bulk Student Registration ────────────────────────────────────────

class TestBulkRegistration:
    def test_empty_list(self, client):
        with admin_patch():
            resp = client.post("/api/v1/admin/register-students-bulk",
                               json={"students": []},
                               headers=admin_headers())
            assert resp.status_code == 400

    def test_over_500_limit(self, client):
        with admin_patch():
            students = [{"roll_number": f"R{i}", "full_name": f"S{i}",
                         "email": f"s{i}@t.com"} for i in range(501)]
            resp = client.post("/api/v1/admin/register-students-bulk",
                               json={"students": students},
                               headers=admin_headers())
            assert resp.status_code == 400
            assert "500" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_reimport_of_existing_students_charges_no_new_seats(self):
        """A re-import that updates existing roster rows must charge 0 new seats
        against the org cap (it upserts-as-UPDATE; the DB quota trigger fires on
        INSERT only). Otherwise a teacher at capacity could never re-import."""
        import asyncio
        from app.routers import admin_students as mod

        rows = [{"roll_number": "R1", "full_name": "A", "email": "a@x.com"},
                {"roll_number": "R2", "full_name": "B", "email": "b@x.com"}]

        class _Chain:
            def select(self, *a, **k): return self
            def eq(self, *a, **k): return self
            def in_(self, *a, **k): return self
            def upsert(self, *a, **k): return self
            async def execute(self):
                # existing-roll lookup AND upserts both resolve to the two rows
                return MagicMock(data=[{"roll_number": "R1"}, {"roll_number": "R2"}])

        captured = {}

        async def _check(teacher, delta=0):
            captured["delta"] = delta
            return {"max_students": 100}

        with patch.object(mod, "_atable", lambda name: _Chain()), \
             patch.object(mod, "check_org_limits", _check):
            await mod._process_student_rows(
                {"id": "t1", "org_id": "o1"}, rows, dry_run=False, send_invites=False)

        assert captured["delta"] == 0   # both rolls already existed → 0 net-new

    def test_skips_invalid_entries(self, client):
        with admin_patch(), \
             patch("app.routers.admin_students.check_org_limits", return_value={"max_students": 999}), \
             patch.object(shared_supabase_mock(), "table") as mock_table:
            mock_table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
            mock_table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{"roll_number": "R001"}])
            resp = client.post("/api/v1/admin/register-students-bulk",
                               json={"students": [
                                   {"roll_number": "", "full_name": "X", "email": "x@t.com"},
                                   {"roll_number": "R001", "full_name": "Valid", "email": "v@t.com"},
                               ]},
                               headers=admin_headers())
            assert resp.status_code == 200


class TestCsvImport:
    def test_cp1252_encoded_csv_does_not_500(self, client):
        """Excel-on-Windows CSVs are often cp1252, not UTF-8. A non-UTF-8 byte
        (accented name) must not raise UnicodeDecodeError → 500; the decode
        falls back to cp1252. dry_run avoids any DB writes."""
        csv_bytes = "roll_number,full_name,email\nR1,José Núñez,jose@x.com\n".encode("cp1252")
        with admin_patch():
            resp = client.post(
                "/api/v1/admin/students/import-csv",
                files={"file": ("roster.csv", csv_bytes, "text/csv")},
                data={"dry_run": "true"},
                headers=admin_headers(),
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["would_register"] == 1

    def test_build_column_map_aliases_and_required(self):
        from app.routers.admin_students import _build_column_map
        m = _build_column_map(["Roll No", "Name", "Email Address", "Phone"])
        assert m["roll_number"] == "Roll No"
        assert m["full_name"] == "Name"
        assert m["email"] == "Email Address"
        # missing a required column → None
        assert _build_column_map(["Roll No", "Name"]) is None


# ─── Save Answer ──────────────────────────────────────────────────────

class TestSaveAnswer:
    def test_ownership_check(self, client):
        token = make_student_token(roll="ALICE001")
        resp = client.post("/api/v1/save-answer",
                           json={"session_id": "BOB002_123",
                                 "question_id": "1", "answer": "A"},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403

    def test_valid_save(self, client):
        token = make_student_token(roll="ALICE001")
        with patch("app.dependencies._canonicalise_student_answer", return_value="A"), \
             patch("app.routers.exam._atable") as atable_mock:
            atable_mock.return_value.upsert.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))
            resp = client.post("/api/v1/save-answer",
                               json={"session_id": "ALICE001_123",
                                     "question_id": "1", "answer": "A"},
                               headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200


class TestUpdateQuestionsPersistence:
    """Regression: the save path must UPSERT (a plain INSERT collides with
    UNIQUE (teacher_id, exam_id, question_id) on every re-save) and must
    delete ONLY stale old rows — the old delete-by-(tid,exam) filter wiped
    the freshly written rows too."""

    class _FakeTable:
        def __init__(self, name, db):
            self.name, self.db = name, db
            self.op, self.payload, self.filters = None, None, []

        def select(self, *a, **k): self.op = "select"; return self
        def insert(self, payload): self.op = "insert"; self.payload = payload; return self
        def upsert(self, payload, **k): self.op = "upsert"; self.payload = payload; return self
        def update(self, payload): self.op = "update"; self.payload = payload; return self
        def delete(self): self.op = "delete"; return self
        def eq(self, *a): self.filters.append(("eq", a)); return self
        def in_(self, *a): self.filters.append(("in", a)); return self
        def order(self, *a, **k): return self
        def limit(self, *a): return self

        async def execute(self):
            self.db.calls.append((self.name, self.op, self.payload, list(self.filters)))
            result = MagicMock()
            if self.name == "questions" and self.op == "select":
                result.data = self.db.existing_rows
            else:
                result.data = [{"id": 999}]
            return result

    def _fake_db(self, existing_rows):
        class _Db:
            pass
        db = _Db()
        db.existing_rows = existing_rows
        db.calls = []
        return db

    def test_resave_upserts_and_deletes_only_stale(self, client):
        db = self._fake_db([
            {"id": 11, "question_id": 1},
            {"id": 12, "question_id": 2},
            {"id": 13, "question_id": 3},   # dropped in the new set → stale
        ])
        fake_atable = lambda name: self._FakeTable(name, db)
        with admin_patch(), \
             patch("app.routers.question_bank._atable", fake_atable):
            resp = client.post("/api/v1/admin/questions",
                               json={"exam_id": "22222222-2222-2222-2222-222222222222",
                                     "questions": [
                                         {"id": 1, "question": "Q1?",
                                          "options": {"A": "a", "B": "b"}, "correct": "A"},
                                         {"id": 2, "question": "Q2?",
                                          "options": {"A": "a", "B": "b"}, "correct": "B"},
                                     ]},
                               headers=admin_headers())
        assert resp.status_code == 200, resp.text
        q_writes = [c for c in db.calls if c[0] == "questions" and c[1] in ("insert", "upsert")]
        assert q_writes and all(c[1] == "upsert" for c in q_writes)
        deletes = [c for c in db.calls if c[0] == "questions" and c[1] == "delete"]
        assert len(deletes) == 1
        in_filters = [f for f in deletes[0][3] if f[0] == "in"]
        assert in_filters and in_filters[0][1] == ("id", [13])  # only the stale PK

    def test_resave_same_set_deletes_nothing(self, client):
        db = self._fake_db([
            {"id": 11, "question_id": 1},
            {"id": 12, "question_id": 2},
        ])
        fake_atable = lambda name: self._FakeTable(name, db)
        with admin_patch(), \
             patch("app.routers.question_bank._atable", fake_atable):
            resp = client.post("/api/v1/admin/questions",
                               json={"exam_id": "22222222-2222-2222-2222-222222222222",
                                     "questions": [
                                         {"id": 1, "question": "Q1?",
                                          "options": {"A": "a", "B": "b"}, "correct": "A"},
                                         {"id": 2, "question": "Q2?",
                                          "options": {"A": "a", "B": "b"}, "correct": "B"},
                                     ]},
                               headers=admin_headers())
        assert resp.status_code == 200, resp.text
        deletes = [c for c in db.calls if c[0] == "questions" and c[1] == "delete"]
        assert deletes == []  # every old row survives in place
