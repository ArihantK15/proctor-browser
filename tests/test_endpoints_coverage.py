"""Coverage tests for previously-uncovered endpoint paths.

Covers:
  1. ``email_webhook`` — invite bounce/complaint/opened/clicked
  2. ``id_decision`` — approve/retake/reject ID verification
  3. ``duplicate_exam`` — clone exam with/without questions
  4. ``admin_submit`` — admin submits on behalf of student
  5. ``bank_to_exam`` — copy bank questions into exam
  6. ``generate_bank_questions`` — AI question generation (LLM path)
  7. ``analyze_frame`` — proctoring frame ingestion
  8. Chat service — additional edge coverage
"""

import json
import os
import sys
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")

from tests.conftest import make_admin_token, make_student_token, shared_supabase_mock
from app.auth.tokens import issue_reauth_token


TEACHER = {"id": "teacher-1", "email": "prof@test.com", "full_name": "Prof T", "org_id": "org-1"}


def _admin_headers():
    return {"Authorization": f"Bearer {make_admin_token(teacher_id='teacher-1', email='prof@test.com')}"}


def _reauth_body():
    return {"reauth_token": issue_reauth_token("teacher-1")}


def _student_headers(roll="STU001"):
    return {"Authorization": f"Bearer {make_student_token(roll=roll, tid='teacher-1', eid='exam-1')}"}


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
#  1. POST /api/v1/webhooks/email  — invite-bounce flow
# ═══════════════════════════════════════════════════════════════════

class TestEmailWebhook:

    BOUNCE_PAYLOAD = {
        "type": "email.bounced",
        "data": {"email_id": "msg-1", "bounce": "550 5.1.1 user unknown"},
    }
    COMPLAINT_PAYLOAD = {
        "type": "email.complained",
        "data": {"email_id": "msg-2"},
    }
    OPENED_PAYLOAD = {
        "type": "email.opened",
        "data": {"email_id": "msg-3"},
    }
    CLICKED_PAYLOAD = {
        "type": "email.clicked",
        "data": {"email_id": "msg-4"},
    }
    DELIVERED_PAYLOAD = {
        "type": "email.delivered",
        "data": {"email_id": "msg-5"},
    }
    UNKNOWN_EVENT = {
        "type": "email.unknown",
        "data": {"email_id": "msg-6"},
    }

    def _post(self, client, payload, headers=None):
        hdrs = {"svix-signature": "v1,fakesig", "svix-id": "svix_test_1"}
        if headers:
            hdrs.update(headers)
        return client.post("/api/v1/webhooks/email", json=payload, headers=hdrs)

    def test_bounce_updates_invite(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "student_invites": [{"id": 1, "provider_msg_id": "msg-1"}],
        })):
            resp = self._post(client, self.BOUNCE_PAYLOAD)
        assert resp.status_code == 200
        assert resp.json().get("event") == "email.bounced"

    def test_complaint_updates_invite(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "student_invites": [{"id": 1, "provider_msg_id": "msg-2"}],
        })):
            resp = self._post(client, self.COMPLAINT_PAYLOAD)
        assert resp.status_code == 200
        assert resp.json().get("event") == "email.complained"

    def test_opened_updates_invite(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "student_invites": [{"id": 1, "provider_msg_id": "msg-3"}],
        })):
            resp = self._post(client, self.OPENED_PAYLOAD)
        assert resp.status_code == 200
        assert resp.json().get("event") == "email.opened"

    def test_clicked_increments_count(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "student_invites": [{"id": 1, "provider_msg_id": "msg-4",
                                 "click_count": 0, "clicked_at": None,
                                 "status": "sent"}],
        })):
            resp = self._post(client, self.CLICKED_PAYLOAD)
        assert resp.status_code == 200
        assert resp.json().get("event") == "email.clicked"

    def test_delivered_is_noop(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({})):
            resp = self._post(client, self.DELIVERED_PAYLOAD)
        assert resp.status_code == 200
        assert resp.json().get("event") == "email.delivered"

    def test_unknown_event_ignored(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({})):
            resp = self._post(client, self.UNKNOWN_EVENT)
        assert resp.status_code == 200

    def test_missing_svix_signature_returns_403(self, client):
        resp = client.post("/api/v1/webhooks/email",
                          json=self.BOUNCE_PAYLOAD,
                          headers={"svix-signature": ""})
        assert resp.status_code == 403

    def test_no_msg_id_returns_ok_ignored(self, client):
        payload = {"type": "email.bounced", "data": {}}
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({})):
            resp = self._post(client, payload)
        assert resp.status_code == 200
        assert resp.json().get("ignored") is not None


# ═══════════════════════════════════════════════════════════════════
#  2. POST /api/v1/admin/id-decision
# ═══════════════════════════════════════════════════════════════════

class TestIdDecision:

    VIOLATION = {
        "id": 42, "session_key": "sess-1",
        "violation_type": "id_verification",
        "severity": "low", "teacher_id": "teacher-1",
        "details": json.dumps({"status": "pending"}),
    }

    def test_approve(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "violations": [self.VIOLATION],
        })):
            resp = client.post(
                "/api/v1/admin/id-decision",
                json={"violation_id": 42, "session_key": "sess-1",
                      "decision": "approved"},
                headers=_admin_headers(),
            )
        assert resp.status_code == 200
        assert resp.json()["decision"] == "approved"

    def test_retake(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "violations": [self.VIOLATION],
        })):
            resp = client.post(
                "/api/v1/admin/id-decision",
                json={"violation_id": 42, "session_key": "sess-1",
                      "decision": "retake"},
                headers=_admin_headers(),
            )
        assert resp.status_code == 200
        assert resp.json()["decision"] == "retake"

    def test_rejected_marks_session_rejected(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "violations": [self.VIOLATION],
        })):
            resp = client.post(
                "/api/v1/admin/id-decision",
                json={"violation_id": 42, "session_key": "sess-1",
                      "decision": "rejected"},
                headers=_admin_headers(),
            )
        assert resp.status_code == 200
        assert resp.json()["decision"] == "rejected"

    def test_invalid_decision_returns_400(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
        })):
            resp = client.post(
                "/api/v1/admin/id-decision",
                json={"violation_id": 42, "session_key": "sess-1",
                      "decision": "invalid_choice"},
                headers=_admin_headers(),
            )
        assert resp.status_code == 400

    def test_missing_violation_returns_404(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "violations": [],
        })):
            resp = client.post(
                "/api/v1/admin/id-decision",
                json={"violation_id": 999, "session_key": "sess-1",
                      "decision": "approved"},
                headers=_admin_headers(),
            )
        assert resp.status_code == 404

    def test_unauthenticated_returns_401(self, client):
        resp = client.post(
            "/api/v1/admin/id-decision",
            json={"violation_id": 1, "session_key": "sess-1",
                  "decision": "approved"},
        )
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════
#  3. POST /api/v1/admin/exams/{exam_id}/duplicate
# ═══════════════════════════════════════════════════════════════════

class TestDuplicateExam:

    CONFIG = {
        "exam_id": "exam-1", "teacher_id": "teacher-1",
        "exam_title": "Midterm", "duration_minutes": 60,
        "shuffle_questions": True, "shuffle_options": False,
        "starts_at": None, "ends_at": None, "access_code": "",
    }
    QUESTIONS = [
        {"id": 1, "question_id": "q1", "exam_id": "exam-1",
         "teacher_id": "teacher-1", "question": "Q1?",
         "correct": "A", "options": {"A": "Ans A", "B": "Ans B"},
         "order_index": 0},
    ]

    def test_duplicate_with_questions(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_config": [self.CONFIG],
            "questions": self.QUESTIONS,
        })):
            resp = client.post(
                "/api/v1/admin/exams/exam-1/duplicate",
                json={},
                headers=_admin_headers(),
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "duplicated"
        assert body["questions_copied"] == 1

    def test_duplicate_with_custom_title(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_config": [self.CONFIG],
            "questions": [],
        })):
            resp = client.post(
                "/api/v1/admin/exams/exam-1/duplicate",
                json={"new_title": "Midterm Copy v2"},
                headers=_admin_headers(),
            )
        assert resp.status_code == 200
        assert resp.json()["questions_copied"] == 0

    def test_duplicate_nonexistent_exam_returns_404(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_config": [],
        })):
            resp = client.post(
                "/api/v1/admin/exams/exam-999/duplicate",
                json={},
                headers=_admin_headers(),
            )
        assert resp.status_code == 404

    def test_duplicate_requires_auth(self, client):
        resp = client.post(
            "/api/v1/admin/exams/exam-1/duplicate", json={})
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════
#  4. POST /api/v1/admin-submit/{session_id}
# ═══════════════════════════════════════════════════════════════════

class TestAdminSubmit:

    SESSION = {
        "session_key": "sess-1", "roll_number": "R001",
        "full_name": "Alice", "exam_id": "exam-1",
        "score": None, "total": None, "status": "in_progress",
    }

    VIOLATIONS = [
        {"violation_type": "enrollment_started", "severity": "low",
         "details": "Student: Alice (R001)", "created_at": "2025-06-01T00:00:00Z"},
        {"violation_type": "answer_selected", "severity": "low",
         "details": "q:1|a:A", "created_at": "2025-06-01T00:01:00Z"},
        {"violation_type": "answer_selected", "severity": "low",
         "details": "q:2|a:B", "created_at": "2025-06-01T00:02:00Z"},
        {"violation_type": "tab_hidden", "severity": "medium",
         "details": "Tab switch", "created_at": "2025-06-01T00:03:00Z"},
    ]

    def test_submit_completes_session(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_sessions": [self.SESSION],
            "violations": self.VIOLATIONS,
            "questions": [],
            "answers": [],
            "students": [{"roll_number": "R001", "full_name": "Alice",
                          "email": "alice@test.com", "teacher_id": "teacher-1"}],
        })), \
            patch("app.routers.admin_sessions.compute_risk_score") as mock_risk:
            mock_risk.return_value = {"risk_score": 15, "label": "Low", "risk_level": "low"}
            resp = client.post(
                "/api/v1/admin-submit/sess-1",
                headers=_admin_headers(),
                json=_reauth_body(),
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("status") == "force_submitted"

    def test_submit_already_completed(self, client):
        completed = dict(self.SESSION)
        completed["status"] = "completed"
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_sessions": [completed],
            "violations": [],
        })):
            resp = client.post(
                "/api/v1/admin-submit/sess-1",
                headers=_admin_headers(),
                json=_reauth_body(),
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "already_submitted"

    def test_submit_missing_session_returns_404(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_sessions": [],
        })):
            resp = client.post(
                "/api/v1/admin-submit/sess-999",
                headers=_admin_headers(),
                json=_reauth_body(),
            )
        assert resp.status_code in (403, 404)

    def test_submit_no_events_force_submits(self, client):
        # A real session with zero violations is still a real session —
        # force-submit must succeed (score=0, no events to parse). The
        # earlier "404 on no events" guard was removed in 291ceca because
        # it conflated "session missing" with "session has no violations
        # yet," which broke legitimate force-submits where answers were
        # persisted via the answers table and no violations had fired.
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_sessions": [self.SESSION],
            "violations": [],
            "questions": [],
            "answers": [],
            "students": [{"roll_number": "R001", "full_name": "Alice",
                          "email": "alice@test.com", "teacher_id": "teacher-1"}],
        })), \
            patch("app.routers.admin_sessions.compute_risk_score") as mock_risk:
            mock_risk.return_value = {"risk_score": 0, "label": "Low", "risk_level": "low"}
            resp = client.post(
                "/api/v1/admin-submit/sess-1",
                headers=_admin_headers(),
                json=_reauth_body(),
            )
        assert resp.status_code == 200
        assert resp.json().get("status") == "force_submitted"

    def test_submit_requires_auth(self, client):
        resp = client.post("/api/v1/admin-submit/sess-1")
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════
#  5. POST /api/v1/admin/question-bank/to-exam
# ═══════════════════════════════════════════════════════════════════

class TestBankToExam:

    BANK_QUESTIONS = [
        {"id": 10, "teacher_id": "teacher-1", "question": "What is 2+2?",
         "correct": "4", "options": {"3": "Three", "4": "Four", "5": "Five"}},
        {"id": 11, "teacher_id": "teacher-1", "question": "Capital of France?",
         "correct": "Paris", "options": {"London": "London", "Paris": "Paris", "Berlin": "Berlin"}},
    ]

    def test_copies_bank_to_exam(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_config": [{"exam_id": "exam-1", "teacher_id": "teacher-1"}],
            "question_bank": self.BANK_QUESTIONS,
            "questions": [],  # no existing questions
            "answers": [],
        })):
            resp = client.post(
                "/api/v1/admin/question-bank/to-exam",
                json={"question_ids": ["10", "11"], "exam_id": "exam-1"},
                headers=_admin_headers(),
            )
        assert resp.status_code == 200
        assert resp.json()["added"] == 2

    def test_missing_question_ids_returns_400(self, client):
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

    def test_no_matching_bank_questions_returns_404(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_config": [{"exam_id": "exam-1", "teacher_id": "teacher-1"}],
            "question_bank": [],
        })):
            resp = client.post(
                "/api/v1/admin/question-bank/to-exam",
                json={"question_ids": ["999"], "exam_id": "exam-1"},
                headers=_admin_headers(),
            )
        assert resp.status_code == 404

    def test_requires_auth(self, client):
        resp = client.post(
            "/api/v1/admin/question-bank/to-exam",
            json={"question_ids": ["1"], "exam_id": "exam-1"},
        )
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════
#  6. POST /api/v1/admin/question-bank/generate  — LLM path
# ═══════════════════════════════════════════════════════════════════

class TestGenerateBankQuestions:

    def test_generates_questions(self, client):
        sm = shared_supabase_mock()
        fake_questions = [
            {"question": "What is 2+2?", "options": {"3": "3", "4": "4"},
             "correct": "4", "type": "mcq", "difficulty": "easy", "explanation": "2+2=4"},
        ]
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
        })), \
            patch("app.llm.is_configured", return_value=True), \
            patch("app.llm.generate_questions",
                  return_value=fake_questions):
            resp = client.post(
                "/api/v1/admin/question-bank/generate",
                json={"topic": "math", "count": 1,
                      "difficulty": "easy", "question_type": "mcq"},
                headers=_admin_headers(),
            )
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_503_when_llm_not_configured(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
        })), \
            patch("app.llm.is_configured", return_value=False):
            resp = client.post(
                "/api/v1/admin/question-bank/generate",
                json={"topic": "math", "count": 1,
                      "difficulty": "easy", "question_type": "mcq"},
                headers=_admin_headers(),
            )
        assert resp.status_code == 503

    def test_400_when_source_text_too_long(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
        })), \
            patch("app.llm.is_configured", return_value=True):
            resp = client.post(
                "/api/v1/admin/question-bank/generate",
                json={"topic": "history", "count": 1,
                      "difficulty": "medium", "question_type": "mcq",
                      "source_text": "x" * 20001},
                headers=_admin_headers(),
            )
        assert resp.status_code == 400

    def test_502_when_llm_returns_empty(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
        })), \
            patch("app.llm.is_configured", return_value=True), \
            patch("app.llm.generate_questions", return_value=[]):
            resp = client.post(
                "/api/v1/admin/question-bank/generate",
                json={"topic": "math", "count": 1,
                      "difficulty": "easy", "question_type": "mcq"},
                headers=_admin_headers(),
            )
        assert resp.status_code == 502

    def test_requires_auth(self, client):
        resp = client.post(
            "/api/v1/admin/question-bank/generate",
            json={"topic": "math", "count": 1,
                  "difficulty": "easy", "question_type": "mcq"},
        )
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════
#  7. POST /api/v1/analyze-frame  — proctoring frame ingestion
# ═══════════════════════════════════════════════════════════════════

class TestAnalyzeFrame:

    SMALL_VALID_FRAME = "a" * 100
    VALID_PAYLOAD = {"session_id": "STU001_1234567890",
                     "frame": "a" * 100, "timestamp": "2025-06-01T00:00:00Z"}

    def test_accepts_valid_frame(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "exam_sessions": [{"session_key": "STU001_1234567890",
                               "teacher_id": "teacher-1"}],
            "violations": [],
        })), \
            patch("app.routers.exam.os.makedirs"), \
            patch("builtins.open", MagicMock()), \
            patch("app.routers.exam.os.path.realpath") as mock_realpath:
            mock_realpath.return_value = "/tmp/procta_test_screenshots/teacher-1/STU001"
            resp = client.post(
                "/api/v1/analyze-frame",
                json=self.VALID_PAYLOAD,
                headers=_student_headers(),
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "received"

    def test_practice_sandbox_returns_ok(self, client):
        payload = dict(self.VALID_PAYLOAD)
        payload["session_id"] = "PRACTICE_STU001_1234567890"
        resp = client.post(
            "/api/v1/analyze-frame", json=payload,
            headers=_student_headers(),
        )
        assert resp.status_code == 200
        assert resp.json()["practice"] is True

    def test_rejects_oversized_frame(self, client):
        payload = dict(self.VALID_PAYLOAD)
        payload["frame"] = "x" * 5_000_000
        resp = client.post(
            "/api/v1/analyze-frame", json=payload,
            headers=_student_headers(),
        )
        assert resp.status_code == 413

    def test_requires_auth(self, client):
        resp = client.post(
            "/api/v1/analyze-frame", json=self.VALID_PAYLOAD,
        )
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════
#  8. Chat service — additional edge coverage
# ═══════════════════════════════════════════════════════════════════

class TestChatServiceEdgeCases:

    @pytest.fixture(autouse=True)
    def _clean_chathub(self):
        from app.routers.chat import chat_hub
        chat_hub.threads.clear()
        chat_hub.student_conns.clear()
        chat_hub.teacher_conns.clear()
        chat_hub.student_meta.clear()
        yield

    def test_teacher_broadcast_delivers_to_connected(self):
        from app.routers.chat import chat_hub
        import asyncio
        ws1, ws2 = AsyncMock(), AsyncMock()
        ws1.send_json = AsyncMock()
        ws2.send_json = AsyncMock()
        asyncio.run(chat_hub.register_teacher("t1", ws1))
        asyncio.run(chat_hub.register_student(
            session_id="sess-1", teacher_id="t1",
            roll="ALICE", name="Alice", ws=AsyncMock(),
        ))
        delivered = asyncio.run(chat_hub.teacher_broadcast("t1", "Test"))
        assert delivered == 1

    def test_teacher_broadcast_delivers_zero_with_no_students(self):
        from app.routers.chat import chat_hub
        import asyncio
        delivered = asyncio.run(chat_hub.teacher_broadcast("t1", "Hello"))
        assert delivered == 0

    def test_fanout_removes_dead_sockets(self):
        from app.routers.chat import chat_hub
        import asyncio
        dead_ws = AsyncMock()
        dead_ws.send_json = AsyncMock(side_effect=Exception("disconnected"))
        asyncio.run(chat_hub.register_teacher("t1", dead_ws))
        asyncio.run(chat_hub._fanout_teachers("t1", {"type": "ping"}))
        assert dead_ws not in chat_hub.teacher_conns.get("t1", set())

    def test_global_cap_evicts_with_last_seen(self):
        from app.routers.chat import chat_hub
        from datetime import datetime, timezone, timedelta
        import asyncio
        saved_max = chat_hub.GLOBAL_MAX_CONNECTIONS
        saved_ttl = chat_hub.STUDENT_META_TTL_SECONDS
        chat_hub.GLOBAL_MAX_CONNECTIONS = 2
        chat_hub.STUDENT_META_TTL_SECONDS = 999999
        try:
            ws1 = AsyncMock()
            ws2 = AsyncMock()
            ws3 = AsyncMock()
            # Register first two and give them last_seen timestamps
            asyncio.run(chat_hub.register_student(
                session_id="sess-1", teacher_id="t1",
                roll="A", name="A", ws=ws1,
            ))
            chat_hub.student_meta["sess-1"]["last_seen"] = \
                (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
            asyncio.run(chat_hub.register_student(
                session_id="sess-2", teacher_id="t1",
                roll="B", name="B", ws=ws2,
            ))
            chat_hub.student_meta["sess-2"]["last_seen"] = \
                (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            # Third registration triggers eviction of oldest (sess-1)
            asyncio.run(chat_hub.register_student(
                session_id="sess-3", teacher_id="t1",
                roll="C", name="C", ws=ws3,
            ))
            assert "sess-1" not in chat_hub.student_conns
            assert "sess-2" in chat_hub.student_conns
            assert "sess-3" in chat_hub.student_conns
        finally:
            chat_hub.GLOBAL_MAX_CONNECTIONS = saved_max
            chat_hub.STUDENT_META_TTL_SECONDS = saved_ttl

    def test_student_meta_ttl_eviction(self):
        from app.routers.chat import chat_hub
        from datetime import datetime, timezone, timedelta
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        chat_hub.student_meta["stale-sess"] = {
            "roll": "X", "name": "Old", "teacher_id": "t1",
            "joined_at": old_ts,
        }
        chat_hub._evict_stale_meta()
        assert "stale-sess" not in chat_hub.student_meta
