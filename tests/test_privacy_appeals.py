"""Tests for privacy center and student appeals."""

from unittest.mock import patch, AsyncMock, MagicMock, Mock

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.fixture
def mock_teacher():
    with patch("app.auth.admin_auth._get_teacher_by_id", new_callable=AsyncMock) as m:
        m.return_value = {"id": "teacher-1", "email": "prof@test.com", "full_name": "Prof Test", "org_id": "org-1"}
        yield m


@pytest.fixture
def mock_student_account():
    with patch("app.auth.admin_auth.verify_student_auth_token", new_callable=AsyncMock) as m:
        m.return_value = {"id": "student-1", "email": "alice@test.com", "full_name": "Alice Test"}
        yield m


def _make_session_row(session_key="test_session", student_id="student-1", roll_number="ALICE001",
                      teacher_id="teacher-1", email="alice@test.com"):
    return MagicMock(data=[{
        "session_key": session_key,
        "student_id": student_id,
        "roll_number": roll_number,
        "teacher_id": teacher_id,
        "email": email,
        "exam_id": "exam-1",
    }])


class TestPrivacyExport:
    def test_teacher_export_requires_auth(self):
        r = client.get("/api/v1/privacy/export")
        assert r.status_code == 401

    def test_teacher_export_returns_profile(self, admin_headers, mock_teacher):
        r = client.get("/api/v1/privacy/export", headers=admin_headers)
        assert r.status_code == 200, f"Expected 200 got {r.status_code}: {r.text[:200]}"
        d = r.json()
        assert d.get("user_type") == "teacher"
        assert d.get("user_id") == "teacher-1"
        assert "profile" in d
        assert "exams" in d
        assert "students" in d
        assert "consent_records" in d

    def test_student_export_returns_profile(self, student_headers, mock_student_account):
        r = client.get("/api/v1/privacy/export", headers=student_headers)
        assert r.status_code == 200, f"Expected 200 got {r.status_code}: {r.text[:200]}"
        d = r.json()
        assert d.get("user_type") == "student"
        assert "profile" in d
        assert "consent_records" in d


class TestPrivacyDelete:
    def test_delete_requires_auth(self):
        r = client.post("/api/v1/privacy/delete")
        assert r.status_code == 401

    def test_delete_teacher(self, admin_headers, mock_teacher):
        r = client.post("/api/v1/privacy/delete", headers=admin_headers)
        assert r.status_code == 200, f"Expected 200 got {r.status_code}: {r.text[:200]}"
        d = r.json()
        assert d.get("status") in ("deleted", "partial")

    def test_delete_student(self, student_headers, mock_student_account):
        r = client.post("/api/v1/privacy/delete", headers=student_headers)
        assert r.status_code == 200, f"Expected 200 got {r.status_code}: {r.text[:200]}"
        d = r.json()
        assert d.get("status") in ("deleted", "partial")


class TestPrivacyConsent:
    def test_record_consent_teacher(self, admin_headers, mock_teacher):
        r = client.post("/api/v1/privacy/consent", json={
            "consent_type": "privacy_policy",
        }, headers=admin_headers)
        assert r.status_code == 200, f"Expected 200 got {r.status_code}: {r.text[:200]}"
        assert r.json().get("status") == "recorded"

    def test_record_consent_student(self, student_headers, mock_student_account):
        r = client.post("/api/v1/privacy/consent", json={
            "consent_type": "privacy_policy",
        }, headers=student_headers)
        assert r.status_code == 200, f"Expected 200 got {r.status_code}: {r.text[:200]}"
        assert r.json().get("status") == "recorded"


class TestStudentAppeals:
    def test_appeal_requires_auth(self):
        r = client.post("/api/v1/student/appeal", json={
            "session_key": "owned_session",
            "appeal_type": "violation",
            "description": "I want to dispute this",
        })
        assert r.status_code == 401

    def test_appeal_owned_session_succeeds(self, student_headers, mock_student_account):
        """Appeal succeeds when student owns the session (matches by email)."""
        mock_exec_result = MagicMock(data=[{"student_id": "student-1", "email": "alice@test.com"}])

        def mock_atable(table_name):
            m = MagicMock()
            m.select.return_value = m
            m.eq.return_value = m
            m.limit.return_value = m
            m.insert.return_value = m
            # Make execute always return the same awaitable data
            m.execute = AsyncMock(return_value=mock_exec_result)
            return m

        with patch("app.routers.appeals._atable", side_effect=mock_atable):
            r = client.post("/api/v1/student/appeal", json={
                "session_key": "owned_session",
                "appeal_type": "violation",
                "description": "I want to dispute this violation",
            }, headers=student_headers)
            assert r.status_code == 200, f"Expected 200 got {r.status_code}: {r.text[:200]}"
            d = r.json()
            assert d.get("status") == "submitted"

    def test_appeal_wrong_student_rejected(self, student_headers, mock_student_account):
        """Appeal 403s when session belongs to a different student."""
        mock_exec_other = MagicMock(data=[{"student_id": "student-999", "email": "other@test.com"}])

        def mock_atable(table_name):
            m = MagicMock()
            m.select.return_value = m
            m.eq.return_value = m
            m.limit.return_value = m
            m.execute = AsyncMock(return_value=mock_exec_other)
            m.insert.return_value = m
            return m

        with patch("app.routers.appeals._atable", side_effect=mock_atable):

            r = client.post("/api/v1/student/appeal", json={
                "session_key": "other_session",
                "appeal_type": "grade",
                "description": "This is not my session",
            }, headers=student_headers)
            assert r.status_code == 403


class TestTeacherAppeals:
    def test_list_appeals_requires_auth(self):
        r = client.get("/api/v1/admin/appeals")
        assert r.status_code == 401

    def test_list_appeals(self, admin_headers, mock_teacher):
        r = client.get("/api/v1/admin/appeals", headers=admin_headers)
        assert r.status_code == 200
        d = r.json()
        assert "appeals" in d


class TestExamSessionsStudentId:
    def test_submit_sets_student_id(self, student_headers, mock_student_account):
        """Submit-exam with a student token should set student_id on the session."""
        import uuid
        from tests.conftest import make_student_token
        # Use a token that includes sid claim
        import jwt as _pyjwt
        import os
        _sid = "student-1"
        _token = _pyjwt.encode({
            "sid": _sid, "roll": "ALICE001", "role": "student_account",
            "exp": 9999999999, "iat": 1700000000,
        }, os.environ["SUPABASE_JWT_SECRET"], algorithm="HS256")
        _headers = {"Authorization": f"Bearer {_token}"}

        sid = f"ALICE001_{uuid.uuid4().hex[:8]}"

        with patch("app.routers.exam._recalculate_score", new_callable=AsyncMock) as mock_score:
            mock_score.return_value = (5, 10)
            with patch("app.routers.exam._load_exam_config", new_callable=AsyncMock) as mock_cfg:
                mock_cfg.return_value = {"duration_minutes": 60, "teacher_id": "teacher-1"}

                _upserted_session = {}

                def _mock_atable(table_name):
                    m = MagicMock()
                    m.select.return_value = m
                    m.eq.return_value = m
                    m.neq.return_value = m
                    m.limit.return_value = m
                    m.order.return_value = m
                    m.execute = AsyncMock()
                    m.execute.return_value = MagicMock()
                    m.execute.return_value.data = [{"session_key": sid}]
                    m.insert.return_value = m
                    def _update(row):
                        if "session_key" in row:
                            _upserted_session.clear()
                            _upserted_session.update(row)
                        return m
                    m.update = _update
                    return m

                with patch("app.routers.exam._atable", side_effect=_mock_atable):
                    r = client.post("/api/v1/submit-exam", json={
                        "session_id": sid,
                        "roll_number": "ALICE001",
                        "full_name": "Alice Test",
                        "email": "alice@test.com",
                        "time_taken_secs": 600,
                        "answers": {},
                        "score": 0,
                        "total": 0,
                        "violations": [],
                    }, headers=_headers)
                    assert r.status_code == 200, f"Expected 200 got {r.status_code}: {r.text[:200]}"
                    assert _upserted_session.get("student_id") == "student-1", \
                        f"Expected student_id='student-1' in update, got {_upserted_session.get('student_id')!r}"
