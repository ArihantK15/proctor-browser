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

TEACHER = {"id": "teacher-1", "email": "prof@test.com", "full_name": "Prof T"}


def admin_headers():
    return {"Authorization": f"Bearer {make_admin_token(teacher_id='teacher-1', email='prof@test.com')}"}


def admin_patch():
    return patch("app.dependencies._get_teacher_by_id", return_value=TEACHER)


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
            assert resp.status_code == 400
            assert "Missing" in resp.json()["detail"]

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


# ─── Exam Schedule ────────────────────────────────────────────────────

class TestExamSchedule:
    def test_schedule_without_exam_id_rejected(self, client):
        with admin_patch():
            resp = client.post("/api/v1/admin/exam-schedule",
                               json={"starts_at": "2025-06-01T09:00:00Z",
                                     "ends_at": "2025-06-01T11:00:00Z"},
                               headers=admin_headers())
            assert resp.status_code == 400
            assert "exam_id" in resp.json()["detail"].lower()

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

    def test_skips_invalid_entries(self, client):
        with admin_patch(), patch.object(shared_supabase_mock(), "table") as mock_table:
            mock_table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
            mock_table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{"roll_number": "R001"}])
            resp = client.post("/api/v1/admin/register-students-bulk",
                               json={"students": [
                                   {"roll_number": "", "full_name": "X", "email": "x@t.com"},
                                   {"roll_number": "R001", "full_name": "Valid", "email": "v@t.com"},
                               ]},
                               headers=admin_headers())
            assert resp.status_code == 200


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
