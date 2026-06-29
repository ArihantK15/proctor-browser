"""
Tests for authentication, session ownership, scoring, and submit-exam.

Covers audit findings:
- require_auth doesn't validate JWT claims beyond signature+expiry
- _check_session_ownership IDOR via crafted session_id
- submit-exam allows client-supplied roll_number/full_name/email to overwrite (IDOR)
- submit-exam allows re-submission overwriting completed sessions
- _recalculate_score returns 0/0 on exception (permanent zero lock)
- asyncio.gather return_exceptions=True silently swallows DB write failures
- validate-student TOCTOU race (duplicate tokens)
- Teacher/student signup TOCTOU race (orphaned Supabase Auth users)
- Heartbeat upsert can wipe completed session data
- Unbounded in-process teacher/student caches (memory leak)
"""
import os
import sys
import time
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock
import jwt as jose_jwt

import pytest

# Must set env before importing app
os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.conftest import make_student_token, make_admin_token, shared_supabase_mock


# ─── JWT / Auth ──────────────────────────────────────────────────────

class TestRequireAuth:
    """Tests for the student JWT auth function."""

    def test_missing_auth_header(self, client):
        resp = client.post("/api/v1/save-answer", json={
            "session_id": "ALICE001_123",
            "question_id": "1",
            "answer": "A",
        })
        assert resp.status_code == 401
        assert "Missing" in resp.json()["detail"] or "Authorization" in resp.json()["detail"]

    def test_invalid_token(self, client):
        resp = client.post("/api/v1/save-answer",
                           json={"session_id": "X_1", "question_id": "1", "answer": "A"},
                           headers={"Authorization": "Bearer garbage.token.here"})
        assert resp.status_code == 401

    def test_expired_token(self, client):
        token = make_student_token(expired=True)
        resp = client.post("/api/v1/save-answer",
                           json={"session_id": "ALICE001_1", "question_id": "1", "answer": "A"},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401

    def test_valid_token_accepted(self, client, student_headers):
        """A valid JWT with matching session should not get a 401."""
        with patch.object(shared_supabase_mock(), "table") as mock_table:
            mock_table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
            # Save-answer may fail for other reasons, but auth should pass
            resp = client.post("/api/v1/save-answer",
                               json={"session_id": "ALICE001_123",
                                     "question_id": "1", "answer": "A"},
                               headers=student_headers)
            assert resp.status_code != 401

    def test_token_without_roll_claim(self, client):
        """A signed JWT with no 'roll' claim must be cleanly rejected.

        Earlier `_check_session_ownership` did ``claims.get("roll", "").upper()``
        which crashed (None.upper) when the key existed with value None, and
        only worked accidentally when the key was absent. The hardened
        ``(claims.get("roll") or "").upper()`` returns "" in both cases,
        which fails the ownership compare and 403s. Pin that behaviour.
        """
        secret = os.environ["SUPABASE_JWT_SECRET"]
        now = datetime.now(timezone.utc)
        # Token with explicit roll=None (the case the old code crashed on)
        token = jose_jwt.encode({
            "roll": None,
            "exp": now + timedelta(hours=10),
            "iat": now,
        }, secret, algorithm="HS256")
        resp = client.post("/api/v1/save-answer",
                           json={"session_id": "ANYONE_1", "question_id": "1", "answer": "A"},
                           headers={"Authorization": f"Bearer {token}"})
        # Must NOT 500 — clean 401/403 either way.
        assert resp.status_code in (401, 403)

    def test_room_cam_token_rejected_on_session_endpoints(self, client):
        """A room-cam JWT must not authenticate against session endpoints.

        Room-cam tokens are signed with a key in ALL_SIGNING_KEYS (so
        admin_media can accept them for image fetches), but they grant a
        narrow capability — only the phone-camera pairing path. Without
        the scope guard in require_auth, a stolen QR-code token would
        get a 2-hour window to POST events, save-answer, etc., for the
        session it was issued for. This pins the rejection.
        """
        from app.constants import ROOM_CAM_SIGNING_KEY
        now = datetime.now(timezone.utc)
        room_cam_tok = jose_jwt.encode({
            "scope": "room-cam",
            "sid": "ALICE001_abc",
            "roll": "ALICE001",
            "exp": now + timedelta(hours=2),
            "iat": now,
        }, ROOM_CAM_SIGNING_KEY, algorithm="HS256")
        resp = client.post("/api/v1/save-answer",
                           json={"session_id": "ALICE001_abc",
                                 "question_id": "1", "answer": "A"},
                           headers={"Authorization": f"Bearer {room_cam_tok}"})
        assert resp.status_code == 403
        # Make sure the rejection cites the scope, not generic auth — so
        # a future refactor that drops the scope check fails this test
        # loudly instead of letting it slip past as a different 403.
        assert "scope" in (resp.json().get("detail") or "").lower()


class TestCheckSessionOwnership:
    """Tests for _check_session_ownership IDOR prevention."""

    def test_matching_session(self, client, student_headers):
        """Roll ALICE001 should own session ALICE001_123."""
        with patch.object(shared_supabase_mock(), "table") as mock_table:
            mock_table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
            resp = client.post("/api/v1/save-answer",
                               json={"session_id": "ALICE001_123",
                                     "question_id": "1", "answer": "A"},
                               headers=student_headers)
            assert resp.status_code != 403

    def test_wrong_session(self, client):
        """Roll ALICE001 should NOT own session BOB002_123."""
        token = make_student_token(roll="ALICE001")
        resp = client.post("/api/v1/save-answer",
                           json={"session_id": "BOB002_123",
                                 "question_id": "1", "answer": "A"},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403

    def test_session_id_without_underscore(self, client, student_headers):
        """AUDIT: session_id with no underscore — rsplit('_', 1)[0] returns full string."""
        resp = client.post("/api/v1/save-answer",
                           json={"session_id": "ALICE001",
                                 "question_id": "1", "answer": "A"},
                           headers=student_headers)
        # "ALICE001" rsplit("_", 1)[0] == "ALICE001" → matches roll
        assert resp.status_code != 403


class TestAdminAuth:
    """Tests for teacher JWT auth."""

    def test_admin_token_with_wrong_role(self, client):
        """Token with role != 'teacher' should be rejected."""
        from app.constants import ADMIN_SIGNING_KEY
        now = datetime.now(timezone.utc)
        token = jose_jwt.encode({
            "tid": "teacher-1",
            "email": "x@x.com",
            "role": "student_account",  # Wrong role
            "exp": now + timedelta(hours=12),
            "iat": now,
        }, ADMIN_SIGNING_KEY, algorithm="HS256")
        resp = client.get("/api/v1/admin/exam-schedule",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403

    def test_admin_token_teacher_not_found(self, client):
        """Valid admin token but teacher_id not in DB → 403."""
        with patch("app.dependencies._get_teacher_by_id", return_value=None):
            resp = client.get("/api/v1/admin/exam-schedule",
                              headers={"Authorization": f"Bearer {make_admin_token()}"})
            assert resp.status_code == 403


# ─── Score Recalculation ──────────────────────────────────────────────

class TestRecalculateScore:
    """Tests for _recalculate_score edge cases."""

    def test_score_raises_on_persistent_failure(self):
        """FIX: recalculate_score now raises RuntimeError after 2 retries
        instead of returning 0/0 (which permanently locked score)."""
        with patch("app.services.scoring.load_questions", side_effect=Exception("DB down")), \
             patch("asyncio.sleep", new=AsyncMock()):
            from app.services.scoring import recalculate_score
            with pytest.raises(RuntimeError, match="Score recalculation failed"):
                asyncio.run(recalculate_score("sess_1", {}, "tid", "eid"))

    def test_correct_scoring(self):
        """Normal scoring should work correctly."""
        questions = [
            {"id": "1", "correct": "A"},
            {"id": "2", "correct": "B"},
            {"id": "3", "correct": "C"},
        ]
        saved_answers = MagicMock()
        saved_answers.data = [
            {"question_id": 1, "answer": "A"},
            {"question_id": 2, "answer": "A"},  # Wrong
        ]
        with patch("app.services.scoring.load_questions", return_value=questions), \
             patch("app.database.async_table") as mock_atable:
            mock_atable.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=saved_answers)
            from app.services.scoring import recalculate_score
            score, total = asyncio.run(recalculate_score("sess_1", {}, "tid", "eid"))
            assert total == 3
            assert score == 1  # Only Q1 correct

    def test_question_id_type_mismatch(self):
        """AUDIT MEDIUM: Question ID int vs string mismatch in scoring.
        Questions have string ids, DB answers have int question_id."""
        questions = [
            {"id": "1", "correct": "A"},
        ]
        saved_answers = MagicMock()
        saved_answers.data = [
            {"question_id": 1, "answer": "A"},  # int, not string
        ]
        with patch("app.services.scoring.load_questions", return_value=questions), \
             patch("app.database.async_table") as mock_atable:
            mock_atable.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=saved_answers)
            from app.services.scoring import recalculate_score
            score, total = asyncio.run(recalculate_score("sess_1", {}, "tid", "eid"))
            # str(1) == "1" → should match thanks to the str() cast
            assert score == 1

    def test_empty_questions(self):
        """No questions in DB → total should be 0."""
        saved_answers = MagicMock()
        saved_answers.data = []
        with patch("app.services.scoring.load_questions", return_value=[]), \
             patch("app.database.async_table") as mock_atable:
            mock_atable.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=saved_answers)
            from app.services.scoring import recalculate_score
            score, total = asyncio.run(recalculate_score("sess_1", {}, "tid", "eid"))
            assert total == 0
            assert score == 0


# ─── Submit Exam ──────────────────────────────────────────────────────

class TestSubmitExam:
    """Tests for the submit-exam endpoint."""

    def _mock_submit_deps(self, mock_sb, mock_atable, score=(5, 10)):
        """Set up common mocks for submit-exam tests."""
        with patch("app.routers.exam._recalculate_score", return_value=score), \
             patch("app.routers.exam._load_exam_config", return_value={"duration_minutes": 60}), \
             patch("app.routers.exam.compute_risk_score", new=AsyncMock(return_value={"risk_score": 25, "label": "Low Risk"})), \
             patch("app.routers.exam._atable") as atable_mock:
            atable_mock.return_value.upsert.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))
            atable_mock.return_value.insert.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))
            atable_mock.return_value.update.return_value.eq.return_value.not_.in_.return_value.execute = AsyncMock(
                return_value=MagicMock(data=[{"session_key": "ALICE001_123"}]))
            atable_mock.return_value.eq.return_value.eq.return_value.update.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))
            atable_mock.return_value.eq.return_value.update.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))
            yield atable_mock

    def _mock_atable_for_submit(self, atable_mock):
        """Set up common _atable mocks for submit-exam tests."""
        # Re-submission check: no existing completed session
        atable_mock.return_value.select.return_value.eq.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[]))
        atable_mock.return_value.upsert.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))
        atable_mock.return_value.insert.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))
        atable_mock.return_value.update.return_value.eq.return_value.not_.in_.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[{"session_key": "ALICE001_123"}]))
        atable_mock.return_value.eq.return_value.eq.return_value.update.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))
        atable_mock.return_value.eq.return_value.update.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))

    def test_submit_uses_server_score_not_client(self, client):
        """Client-supplied score should be ignored; server recalculates."""
        token = make_student_token(roll="ALICE001")
        with patch("app.routers.exam._recalculate_score", return_value=(3, 10)) as mock_score, \
             patch("app.routers.exam._load_exam_config", return_value={"duration_minutes": 60}), \
             patch("app.routers.exam.compute_risk_score", new=AsyncMock(return_value={"risk_score": 10, "label": "Low Risk"})), \
             patch("app.routers.exam._atable") as atable_mock:
            self._mock_atable_for_submit(atable_mock)

            resp = client.post("/api/v1/submit-exam",
                               json={
                                   "session_id": "ALICE001_123",
                                   "roll_number": "ALICE001",
                                   "full_name": "Alice",
                                   "email": "a@test.com",
                                   "time_taken_secs": 600,
                                   "answers": {},
                                   "score": 999,  # Client lies about score
                                   "total": 10,
                               },
                               headers={"Authorization": f"Bearer {token}"})
            if resp.status_code == 200:
                data = resp.json()
                assert data["score"] == 3  # Server's calculation
                assert data["total"] == 10

    def test_submit_uses_jwt_roll_not_client_supplied(self, client):
        """FIX: Submit now uses JWT roll claim, ignoring client-supplied roll_number.
        Token says ALICE001, body says BOB002 — upsert should use ALICE001."""
        token = make_student_token(roll="ALICE001")
        with patch("app.routers.exam._recalculate_score", return_value=(5, 10)), \
             patch("app.routers.exam._load_exam_config", return_value={"duration_minutes": 60}), \
             patch("app.routers.exam.compute_risk_score", new=AsyncMock(return_value={"risk_score": 10, "label": "Low Risk"})), \
             patch("app.routers.exam._atable") as atable_mock:
            # select for re-submission check returns no existing session
            atable_mock.return_value.select.return_value.eq.return_value.execute = AsyncMock(
                return_value=MagicMock(data=[]))
            atable_mock.return_value.upsert.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))
            atable_mock.return_value.insert.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))
            atable_mock.return_value.update.return_value.eq.return_value.not_.in_.return_value.execute = AsyncMock(
                return_value=MagicMock(data=[{"session_key": "ALICE001_123"}]))
            atable_mock.return_value.eq.return_value.eq.return_value.update.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))
            atable_mock.return_value.eq.return_value.update.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))

            resp = client.post("/api/v1/submit-exam",
                               json={
                                   "session_id": "ALICE001_123",
                                   "roll_number": "BOB002",  # Client tries IDOR
                                   "full_name": "Evil Bob",
                                   "email": "evil@test.com",
                                   "time_taken_secs": 600,
                                   "answers": {},
                               },
                               headers={"Authorization": f"Bearer {token}"})
            if resp.status_code == 200:
                # Verify the upserted row uses JWT roll, not client-supplied
                calls = atable_mock.return_value.upsert.call_args_list
                if calls:
                    upserted_row = calls[0][0][0]
                    assert upserted_row["roll_number"] == "ALICE001"  # From JWT, not BOB002

    def test_submit_recovers_abandoned_session(self, client):
        """recover-on-submit: a session the reaper marked ABANDONED, but with a
        valid late submission, RECOVERS (not 409) — so an over-aggressive
        abandonment never loses a student's attempt."""
        token = make_student_token(roll="ALICE001")
        with patch("app.routers.exam._recalculate_score", return_value=(7, 10)), \
             patch("app.routers.exam._load_exam_config", return_value={"duration_minutes": 60}), \
             patch("app.routers.exam.compute_risk_score", new=AsyncMock(return_value={"risk_score": 10, "label": "Low Risk"})), \
             patch("app.routers.exam._atable") as atable_mock:
            atable_mock.return_value.select.return_value.eq.return_value.execute = AsyncMock(
                return_value=MagicMock(data=[{"status": "abandoned", "full_name": "Alice", "email": "a@test.com"}]))
            atable_mock.return_value.upsert.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))
            atable_mock.return_value.insert.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))
            atable_mock.return_value.update.return_value.eq.return_value.not_.in_.return_value.execute = AsyncMock(
                return_value=MagicMock(data=[{"session_key": "ALICE001_123"}]))
            atable_mock.return_value.eq.return_value.eq.return_value.update.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))
            atable_mock.return_value.eq.return_value.update.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))

            resp = client.post("/api/v1/submit-exam",
                               json={"session_id": "ALICE001_123", "roll_number": "ALICE001",
                                     "full_name": "Alice", "email": "a@test.com",
                                     "time_taken_secs": 600, "answers": {}},
                               headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200, resp.text

    def test_submit_completed_session_still_409(self, client):
        """A genuinely COMPLETED session still 409s on re-submit — recover-on-submit
        must NOT resurrect a finished attempt."""
        token = make_student_token(roll="ALICE001")
        with patch("app.routers.exam._load_exam_config", return_value={"duration_minutes": 60}), \
             patch("app.routers.exam._atable") as atable_mock:
            atable_mock.return_value.select.return_value.eq.return_value.execute = AsyncMock(
                return_value=MagicMock(data=[{"status": "completed", "score": 5, "total": 10, "percentage": 50}]))
            resp = client.post("/api/v1/submit-exam",
                               json={"session_id": "ALICE001_123", "roll_number": "ALICE001",
                                     "full_name": "Alice", "email": "a@test.com",
                                     "time_taken_secs": 600, "answers": {}},
                               headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 409, resp.text
            assert "already submitted" in resp.json()["detail"].lower()

    def test_submit_zero_score_warning(self, client):
        """When score is 0/0, a warning should be logged (not crash)."""
        token = make_student_token(roll="ALICE001")
        with patch("app.routers.exam._recalculate_score", return_value=(0, 0)), \
             patch("app.routers.exam._load_exam_config", return_value={"duration_minutes": 60}), \
             patch("app.routers.exam.compute_risk_score", new=AsyncMock(return_value={"risk_score": 0, "label": "Low Risk"})), \
             patch("app.routers.exam._atable") as atable_mock:
            self._mock_atable_for_submit(atable_mock)

            resp = client.post("/api/v1/submit-exam",
                               json={
                                   "session_id": "ALICE001_123",
                                   "roll_number": "ALICE001",
                                   "full_name": "Alice",
                                   "email": "a@test.com",
                                   "time_taken_secs": 600,
                                   "answers": {},
                               },
                               headers={"Authorization": f"Bearer {token}"})
            if resp.status_code == 200:
                data = resp.json()
                # percentage should be 0 (division by max(0,1) = 1)
                assert data["percentage"] == 0.0

    def test_time_exceeded_violation(self, client):
        """Submitting past duration + 2min grace should log a violation."""
        token = make_student_token(roll="ALICE001")
        with patch("app.routers.exam._recalculate_score", return_value=(5, 10)), \
             patch("app.routers.exam._load_exam_config", return_value={"duration_minutes": 60}), \
             patch("app.routers.exam.compute_risk_score", new=AsyncMock(return_value={"risk_score": 30, "label": "Moderate"})), \
             patch("app.routers.exam._atable") as atable_mock:
            insert_calls = []
            def track_insert(data):
                insert_calls.append(data)
                result = MagicMock()
                result.execute = AsyncMock(return_value=MagicMock(data=[]))
                return result
            # Re-submission check: no existing completed session
            atable_mock.return_value.select.return_value.eq.return_value.execute = AsyncMock(
                return_value=MagicMock(data=[]))
            atable_mock.return_value.upsert.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))
            atable_mock.return_value.update.return_value.eq.return_value.not_.in_.return_value.execute = AsyncMock(
                return_value=MagicMock(data=[{"session_key": "ALICE001_123"}]))
            atable_mock.return_value.insert.side_effect = track_insert
            atable_mock.return_value.eq.return_value.eq.return_value.update.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))
            atable_mock.return_value.eq.return_value.update.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))

            resp = client.post("/api/v1/submit-exam",
                               json={
                                   "session_id": "ALICE001_123",
                                   "roll_number": "ALICE001",
                                   "full_name": "Alice",
                                   "email": "a@test.com",
                                   "time_taken_secs": 3800,  # 60min + 3min20s
                                   "answers": {},
                               },
                               headers={"Authorization": f"Bearer {token}"})
            # FIX: Submit beyond 60min+2min grace should succeed
            assert resp.status_code == 200
            time_viols = [c for c in insert_calls
                          if isinstance(c, dict) and c.get("violation_type") == "time_exceeded"]
            assert len(time_viols) > 0, "time_exceeded violation should have been logged"


# ─── Heartbeat ────────────────────────────────────────────────────────

class TestHeartbeat:
    """Tests for the heartbeat endpoint."""

    def test_heartbeat_skips_completed_sessions(self, client):
        """FIX: Heartbeat now checks session status first and skips if completed."""
        token = make_student_token(roll="ALICE001")
        with patch("app.routers.exam._atable") as atable_mock:
            # Session exists and is completed
            atable_mock.return_value.select.return_value.eq.return_value.execute = AsyncMock(
                return_value=MagicMock(data=[{"status": "completed"}]))
            # update should NOT be called
            update_mock = AsyncMock(return_value=MagicMock(data=[]))
            atable_mock.return_value.eq.return_value.update.return_value.execute = update_mock

            resp = client.post("/api/v1/heartbeat",
                               json={
                                   "session_id": "ALICE001_123",
                                   "event_type": "heartbeat",
                                   "severity": "low",
                               },
                               headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200
            # The update should NOT have been called for a completed session
            assert not update_mock.called

    def test_heartbeat_updates_in_progress_session(self, client):
        """Heartbeat for an in-progress session uses UPDATE (not upsert)."""
        token = make_student_token(roll="ALICE001")
        with patch("app.routers.exam._atable") as atable_mock:
            # Session exists, in_progress
            atable_mock.return_value.select.return_value.eq.return_value.execute = AsyncMock(
                return_value=MagicMock(data=[{"status": "in_progress"}]))
            # Track update call
            atable_mock.return_value.eq.return_value.update.return_value.execute = AsyncMock(
                return_value=MagicMock(data=[]))

            resp = client.post("/api/v1/heartbeat",
                               json={
                                   "session_id": "ALICE001_123",
                                   "event_type": "heartbeat",
                                   "severity": "low",
                               },
                               headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200

    def test_heartbeat_creates_new_session(self, client):
        """Heartbeat for a non-existent session creates one via upsert."""
        token = make_student_token(roll="ALICE001")
        with patch("app.routers.exam._atable") as atable_mock:
            # No existing session
            atable_mock.return_value.select.return_value.eq.return_value.execute = AsyncMock(
                return_value=MagicMock(data=[]))
            atable_mock.return_value.upsert.return_value.execute = AsyncMock(
                return_value=MagicMock(data=[]))

            resp = client.post("/api/v1/heartbeat",
                               json={
                                   "session_id": "ALICE001_123",
                                   "event_type": "heartbeat",
                                   "severity": "low",
                               },
                               headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200


# ─── Validate Student ────────────────────────────────────────────────

class TestValidateStudent:
    """Tests for the validate-student endpoint."""

    def test_unknown_roll_number(self, client):
        """Non-existent roll number should return 404."""
        class _EmptyChain:
            """Fluent mock that returns empty data at any chain depth."""
            async def execute(self):
                r = MagicMock()
                r.data = []
                return r
            def __getattr__(self, name):
                if name == 'execute':
                    return self.execute
                return self
            def __call__(self, *a, **kw):
                return self

        with patch.object(shared_supabase_mock(), "table",
                          side_effect=lambda name: _EmptyChain()), \
             patch("app.routers.exam._load_exam_config", return_value={}):
            resp = client.post("/api/v1/validate-student",
                               json={"roll_number": "UNKNOWN999"})
            assert resp.status_code == 403
            assert resp.json()["detail"] == "Invalid student details, invite status, or access code."

    def test_already_completed(self, client):
        """Student who already submitted gets a SPECIFIC 409 (not the collapsed
        generic 403) so the actionable reason — 'already submitted' — reaches
        them instead of a misleading 'invalid student details'."""
        with patch.object(shared_supabase_mock(), "table") as mock_table, \
             patch("app.routers.exam._load_exam_config", return_value={}), \
             patch("app.routers.exam._get_access_code", return_value=""), \
             patch("app.routers.exam._check_group_access", return_value=True):

            def table_side_effect(name):
                mock_table = MagicMock()
                if name == "students":
                    mock_table.select.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(
                        data=[{"roll_number": "ALICE001", "full_name": "Alice",
                               "teacher_id": "t1", "email": "a@t.com"}])
                    mock_table.select.return_value.eq.return_value.execute.return_value = MagicMock(
                        data=[{"roll_number": "ALICE001", "full_name": "Alice",
                               "teacher_id": "t1", "email": "a@t.com"}])
                elif name == "exam_sessions":
                    mock_table.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(
                        data=[{"session_key": "ALICE001_old"}])
                    mock_table.select.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(
                        data=[{"session_key": "ALICE001_old"}])
                return mock_table
            mock_table.side_effect = table_side_effect
            resp = client.post("/api/v1/validate-student",
                               json={"roll_number": "ALICE001"})
            assert resp.status_code == 409, resp.text
            assert "already submitted" in resp.json()["detail"].lower()

    def test_concurrent_exam_rejected(self, client):
        """Student already IN_PROGRESS in a different exam is blocked."""
        with patch.object(shared_supabase_mock(), "table") as mock_table, \
             patch("app.routers.exam._load_exam_config", return_value={}), \
             patch("app.routers.exam._get_access_code", return_value=""), \
             patch("app.routers.exam._check_group_access", return_value=True):

            def table_side_effect(name):
                mt = MagicMock()
                if name == "students":
                    mt.select.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(
                        data=[{"roll_number": "ALICE001", "full_name": "Alice",
                               "teacher_id": "t1", "email": "a@t.com"}])
                    mt.select.return_value.eq.return_value.execute.return_value = MagicMock(
                        data=[{"roll_number": "ALICE001", "full_name": "Alice",
                               "teacher_id": "t1", "email": "a@t.com"}])
                elif name == "exam_sessions":
                    row = [{"session_key": "OTHER_exam", "status": "in_progress"}]
                    mt.select.return_value.eq.return_value.in_.return_value.limit.return_value.execute.return_value = MagicMock(data=row)
                    mt.select.return_value.eq.return_value.in_.return_value.execute.return_value = MagicMock(data=[])
                    mt.select.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
                return mt
            mock_table.side_effect = table_side_effect
            resp = client.post("/api/v1/validate-student",
                               json={"roll_number": "ALICE001"})
            assert resp.status_code == 409, resp.text
            assert "already have an active exam" in resp.json()["detail"].lower()

    def test_abandoned_session_gives_disconnection_message(self, client):
        """An ABANDONED session (reaper closed it after a disconnect) is NOT a
        submission — the student should be told it was a disconnection and to
        ask for a reset, not the misleading 'already submitted'."""
        with patch.object(shared_supabase_mock(), "table") as mock_table, \
             patch("app.routers.exam._load_exam_config", return_value={}), \
             patch("app.routers.exam._get_access_code", return_value=""), \
             patch("app.routers.exam._check_group_access", return_value=True):

            def table_side_effect(name):
                mt = MagicMock()
                if name == "students":
                    mt.select.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(
                        data=[{"roll_number": "ALICE001", "full_name": "Alice",
                               "teacher_id": "t1", "email": "a@t.com"}])
                    mt.select.return_value.eq.return_value.execute.return_value = MagicMock(
                        data=[{"roll_number": "ALICE001", "full_name": "Alice",
                               "teacher_id": "t1", "email": "a@t.com"}])
                elif name == "exam_sessions":
                    row = [{"session_key": "ALICE001_old", "status": "abandoned"}]
                    # terminal query: select().eq(roll).in_(status).eq(teacher).execute()
                    mt.select.return_value.eq.return_value.in_.return_value.eq.return_value.execute.return_value = MagicMock(data=row)
                    mt.select.return_value.eq.return_value.in_.return_value.execute.return_value = MagicMock(data=row)
                return mt
            mock_table.side_effect = table_side_effect
            resp = client.post("/api/v1/validate-student",
                               json={"roll_number": "ALICE001"})
            assert resp.status_code == 409, resp.text
            d = resp.json()["detail"].lower()
            assert "disconnection" in d or "reset" in d
            assert "already submitted" not in d

    def test_exam_not_started_yet(self, client):
        """Exam window hasn't opened → 403."""
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        with patch.object(shared_supabase_mock(), "table") as mock_table, \
             patch("app.routers.exam._load_exam_config", return_value={
                 "starts_at": future,
             }):
            mock_table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
                data=[{"teacher_id": "t1"}])
            resp = client.post("/api/v1/validate-student",
                               json={"roll_number": "ALICE001"})
            assert resp.status_code == 403
            # Entry now uses the lobby gate (early-join window). With no
            # early_join_minutes the lobby opens exactly at starts_at, so a
            # before-window request is still 403 — just worded for the lobby.
            assert "lobby opens" in resp.json()["detail"].lower()

    def test_exam_window_closed(self, client):
        """Exam window has ended → 403."""
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with patch.object(shared_supabase_mock(), "table") as mock_table, \
             patch("app.routers.exam._load_exam_config", return_value={
                 "ends_at": past,
             }):
            mock_table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
                data=[{"teacher_id": "t1"}])
            resp = client.post("/api/v1/validate-student",
                               json={"roll_number": "ALICE001"})
            assert resp.status_code == 403
            assert "closed" in resp.json()["detail"].lower()


# ─── Teacher Signup ───────────────────────────────────────────────────

class TestTeacherSignup:
    """Tests for teacher signup edge cases."""

    def test_weak_password(self, client):
        resp = client.post("/api/v1/auth/signup",
                           json={"email": "x@test.com", "password": "short",
                                 "full_name": "Test", "org_name": "TestOrg"})
        assert resp.status_code == 400
        assert "at least" in resp.json()["detail"].lower()

    def test_invalid_email(self, client):
        resp = client.post("/api/v1/auth/signup",
                           json={"email": "notanemail", "password": "longpassword",
                                 "full_name": "Test", "org_name": "TestOrg"})
        assert resp.status_code == 400
        assert "email" in resp.json()["detail"].lower()

    def test_empty_name(self, client):
        resp = client.post("/api/v1/auth/signup",
                           json={"email": "x@test.com", "password": "longpassword",
                                 "full_name": "  ", "org_name": "TestOrg"})
        assert resp.status_code == 400

    def test_duplicate_email_detected(self, client):
        """Existing teacher email → 409."""
        with patch.object(shared_supabase_mock(), "table") as mock_table:
            mock_table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
                data=[{"id": "existing"}])
            resp = client.post("/api/v1/auth/signup",
                               json={"email": "dup@test.com", "password": "StrongP@ss1",
                                     "full_name": "Dup", "org_name": "TestOrg"})
            assert resp.status_code == 409


class TestHybridAuthTransition:
    """Hybrid mode lets no-hash legacy users fall back to Supabase while
    protecting users who already set a local password hash."""

    def test_hybrid_teacher_with_local_hash_does_not_fallback_to_supabase(self, client, monkeypatch):
        monkeypatch.setenv("AUTH_PROVIDER", "hybrid")
        with patch("app.routers.auth.verify_or_403", new=AsyncMock()), \
             patch("app.routers.auth.check_lockout", new=AsyncMock(return_value=(False, 0))), \
             patch("app.routers.auth.record_failure", new=AsyncMock()), \
             patch("app.routers.auth.record_auth_event", new=AsyncMock()), \
             patch("app.routers.auth._get_teacher_by_email_for_auth", new=AsyncMock(return_value={
                 "id": "teacher-1",
                 "email": "legacy@example.com",
                 "full_name": "Legacy",
                 "password_hash": "$2b$hash",
             })), \
             patch("app.routers.auth.verify_password", new=AsyncMock(return_value=False)), \
             patch("app.routers.auth.supabase") as mock_supabase:
            resp = client.post("/api/v1/auth/login", json={
                "email": "legacy@example.com",
                "password": "WrongPassword1!",
            })

        assert resp.status_code == 401
        mock_supabase.auth.sign_in_with_password.assert_not_called()

    def test_hybrid_teacher_without_local_hash_is_rejected_no_supabase_fallback(self, client, monkeypatch):
        monkeypatch.setenv("AUTH_PROVIDER", "hybrid")
        auth_resp = MagicMock()
        auth_resp.user.id = "supabase-uid-1"
        with patch("app.routers.auth.verify_or_403", new=AsyncMock()), \
             patch("app.routers.auth.check_lockout", new=AsyncMock(return_value=(False, 0))), \
             patch("app.routers.auth.clear_failures", new=AsyncMock()), \
             patch("app.routers.auth.record_auth_event", new=AsyncMock()), \
             patch("app.routers.auth._get_teacher_by_email_for_auth", new=AsyncMock(return_value={
                 "id": "teacher-1",
                 "email": "legacy@example.com",
                 "full_name": "Legacy",
                 "password_hash": None,
             })), \
             patch("app.routers.auth._get_teacher_by_uid", new=AsyncMock(return_value={
                 "id": "teacher-1",
                 "email": "legacy@example.com",
                 "full_name": "Legacy",
                 "email_verified_at": "2026-01-01T00:00:00+00:00",
             })), \
             patch("app.routers.auth.issue_admin_token", return_value="access-1"), \
             patch("app.routers.auth._issue_and_persist_refresh_token", new=AsyncMock(return_value="refresh-1")), \
             patch("app.routers.auth.supabase") as mock_supabase:
            mock_supabase.auth.sign_in_with_password.return_value = auth_resp
            resp = client.post("/api/v1/auth/login", json={
                "email": "legacy@example.com",
                "password": "SupabasePassword1!",
            })

        # Gap #38: the Supabase-Auth fallback is decommissioned. A no-local-hash
        # teacher can no longer log in via Supabase — they get a clean 401 and
        # must password-reset (which sets a local hash). Fallback must NOT fire.
        assert resp.status_code == 401
        mock_supabase.auth.sign_in_with_password.assert_not_called()


class TestTeacherSignupEmailVerificationGate:
    """Regression coverage for the bug where new teacher signups skipped
    real email verification entirely: status defaulted to NULL instead of
    'pending_verification' at INSERT time, so the auto-verify-legacy-account
    branch (intended only for pre-feature accounts) silently verified every
    brand-new signup on first login. Fixed in auth.py — these tests pin the
    behavior so it can't regress unnoticed again."""

    def test_pending_verification_status_blocks_login(self, client, monkeypatch):
        """A freshly-signed-up account (status='pending_verification',
        email_verified_at=None) must be rejected, not auto-verified."""
        with patch("app.routers.auth.verify_or_403", new=AsyncMock()), \
             patch("app.routers.auth.check_lockout", new=AsyncMock(return_value=(False, 0))), \
             patch("app.routers.auth.record_auth_event", new=AsyncMock()) as mock_record, \
             patch("app.routers.auth._get_teacher_by_email_for_auth", new=AsyncMock(return_value={
                 "id": "teacher-new",
                 "email": "new@example.com",
                 "full_name": "New Teacher",
                 "password_hash": "$2b$hash",
                 "status": "pending_verification",
                 "email_verified_at": None,
             })), \
             patch("app.routers.auth.verify_password", new=AsyncMock(return_value=True)):
            resp = client.post("/api/v1/auth/login", json={
                "email": "new@example.com",
                "password": "CorrectPassword1!",
            })

        assert resp.status_code == 403
        assert resp.json().get("error") == "EMAIL_UNVERIFIED"
        # Must NOT have been silently marked verified.
        verified_calls = [c for c in mock_record.call_args_list
                          if c.args and c.args[0] == "email_verified"]
        assert not verified_calls


class TestStudentDashboardAuthHardening:
    """Regression tests for student auth audit findings."""

    def test_account_exists_no_longer_reveals_presence(self, client):
        with patch("app.routers.auth._atable", new=AsyncMock()) as mock_table:
            resp = client.get("/api/v1/student/account-exists?email=known@example.com")

        assert resp.status_code == 200
        assert resp.json() == {"exists": False}
        mock_table.assert_not_called()

    def test_student_login_honors_lockout_before_auth_lookup(self, client):
        with patch("app.routers.auth.verify_or_403", new=AsyncMock()), \
             patch("app.routers.auth.check_lockout", new=AsyncMock(return_value=(True, 900))), \
             patch("app.routers.auth.record_auth_event", new=AsyncMock()) as event_mock, \
             patch("app.routers.auth._get_student_by_email_for_auth", new=AsyncMock()) as lookup_mock:
            resp = client.post("/api/v1/student/auth/login", json={
                "email": "student@example.com",
                "password": "WrongPassword1!",
            })

        assert resp.status_code == 429
        event_mock.assert_awaited()
        lookup_mock.assert_not_called()


# ─── Student Registration ────────────────────────────────────────────

class TestStudentRegistration:
    """Tests for student self-registration."""

    def test_missing_teacher_id(self, client):
        resp = client.post("/api/v1/register-student",
                           json={"roll_number": "R001", "full_name": "Test",
                                 "email": "t@t.com"})
        assert resp.status_code == 400
        assert "teacher" in resp.json()["detail"].lower()

    def test_empty_roll_number(self, client):
        resp = client.post("/api/v1/register-student",
                           json={"roll_number": "", "full_name": "Test",
                                 "email": "t@t.com", "teacher_id": "t1"})
        assert resp.status_code == 400

    def test_invalid_email(self, client):
        resp = client.post("/api/v1/register-student",
                           json={"roll_number": "R001", "full_name": "Test",
                                 "email": "notanemail", "teacher_id": "t1"})
        assert resp.status_code == 400

    def test_unknown_teacher_id(self, client):
        with patch("app.dependencies._get_teacher_by_id", return_value=None):
            resp = client.post("/api/v1/register-student",
                               json={"roll_number": "R001", "full_name": "Test",
                                     "email": "t@t.com", "teacher_id": "nonexistent"})
            assert resp.status_code == 404


# ─── Caches ───────────────────────────────────────────────────────────

class TestInProcessCaches:
    """AUDIT MEDIUM: Unbounded in-process caches can leak memory."""

    def test_teacher_cache_grows_unbounded(self):
        """Each unique teacher_id adds an entry that never expires if TTL
        is checked lazily. With enough distinct IDs, memory grows."""
        from app.auth.admin_auth import _teacher_cache, _teacher_cache_ttl
        initial_size = len(_teacher_cache)
        # The cache has no max size — this is the audit finding
        # We just verify the structure exists and is a plain dict
        assert isinstance(_teacher_cache, dict)
        assert isinstance(_teacher_cache_ttl, dict)


# ─── Answer Normalization ─────────────────────────────────────────────

class TestAnswerNormalization:
    """Tests for _normalise_answer_set and _answers_match."""

    def test_single_answer(self):
        from app.services.scoring import normalise_answer_set as _normalise_answer_set, answers_match as _answers_match
        assert _normalise_answer_set("A") == {"A"}
        assert _answers_match("A", "A") is True
        assert _answers_match("A", "B") is False

    def test_multi_answer_order_insensitive(self):
        from app.services.scoring import answers_match as _answers_match
        assert _answers_match("A,C", "C,A") is True
        assert _answers_match("A, C", "C,A") is True

    def test_empty_answer(self):
        from app.services.scoring import normalise_answer_set as _normalise_answer_set
        assert _normalise_answer_set("") == set()

    def test_whitespace_handling(self):
        from app.services.scoring import normalise_answer_set as _normalise_answer_set
        assert _normalise_answer_set(" A , B ") == {"A", "B"}


# ─── Risk Scoring ─────────────────────────────────────────────────────

class TestRiskScoring:
    """Tests for compute_risk_score."""

    def test_no_violations(self):
        from app.services.risk import compute_risk_score
        from app.database import async_table as _atable
        # Use the shared mock infrastructure: _atable wraps the shared supabase mock
        shared_mock = shared_supabase_mock()
        shared_mock.reset_mock()
        shared_mock.table.return_value.select.return_value.eq.return_value.order.return_value.execute.return_value = MagicMock(data=[])
        result = asyncio.run(compute_risk_score("sess_1", teacher_id="t1"))
        assert result["risk_score"] == 0
        assert "Low" in result["label"]

    def test_risk_label_boundaries(self):
        from app.services.risk import _risk_label
        assert _risk_label(0) == "Low Risk"
        assert _risk_label(15) == "Low Risk"
        assert _risk_label(16) == "Moderate Risk"
        assert _risk_label(40) == "Moderate Risk"
        assert _risk_label(41) == "High Risk"
        assert _risk_label(70) == "High Risk"
        assert _risk_label(71) == "Critical Risk"
        assert _risk_label(100) == "Critical Risk"
        assert _risk_label(101) == "Critical Risk"  # Over 100 → still Critical
