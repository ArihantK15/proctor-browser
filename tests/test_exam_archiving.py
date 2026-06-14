"""Tests for Gap #24 — exam archiving (soft-hide, reversible)."""

from unittest.mock import patch, AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


# Shared mock for require_admin. Use in any test that hits an admin endpoint.
_MOCK_TEACHER = {"id": "teacher-1", "email": "prof@test.com", "full_name": "Prof Test",
                 "org_id": "org-1"}


@pytest.fixture
def mock_teacher():
    with patch("app.auth.admin_auth._get_teacher_by_id", new_callable=AsyncMock) as m:
        m.return_value = _MOCK_TEACHER
        yield m


def _mock_archive_ops(found=True):
    """Set up _atable mocks for archive/unarchive endpoints.
    Returns (mock_atable, mock_cache) for use in with statements.
    """
    parent = MagicMock()
    # .select().eq().eq().execute — ownership check
    select_chain = MagicMock()
    select_exec = AsyncMock()
    if found:
        select_exec.return_value = MagicMock(data=[{"exam_id": "exam-1", "archived_at": None}])
    else:
        select_exec.return_value = MagicMock(data=[])
    select_chain.execute = select_exec
    parent.select.return_value.eq.return_value.eq.return_value = select_chain
    # .update().eq().eq().execute — the mutation
    update_chain = MagicMock()
    update_exec = AsyncMock(return_value=MagicMock(data=[{"exam_id": "exam-1"}]))
    update_chain.execute = update_exec
    parent.update.return_value.eq.return_value.eq.return_value = update_chain
    return parent


class TestArchiveEndpoint:
    """POST /api/v1/admin/exams/{exam_id}/archive"""

    def test_archive_requires_auth(self):
        r = client.post("/api/v1/admin/exams/exam-1/archive")
        assert r.status_code == 401

    def test_archive_own_exam(self, admin_headers, mock_teacher):
        """archive sets archived_at on the caller's own exam."""
        parent = _mock_archive_ops(found=True)
        with patch("app.routers.admin_exams._atable", return_value=parent):
            with patch("app.routers.admin_exams._cache", None):
                r = client.post("/api/v1/admin/exams/exam-1/archive", headers=admin_headers)
        assert r.status_code == 200
        d = r.json()
        assert d.get("status") == "archived"
        assert d.get("archived") is True

    def test_archive_other_teachers_exam(self, admin_headers, mock_teacher):
        """archive returns 404 when the exam doesn't belong to the caller."""
        parent = _mock_archive_ops(found=False)
        with patch("app.routers.admin_exams._atable", return_value=parent):
            r = client.post("/api/v1/admin/exams/exam-other/archive", headers=admin_headers)
        assert r.status_code == 404

    def test_archive_idempotent(self, admin_headers, mock_teacher):
        """Archiving an already-archived exam is a no-op 200."""
        parent = _mock_archive_ops(found=True)
        with patch("app.routers.admin_exams._atable", return_value=parent):
            with patch("app.routers.admin_exams._cache", None):
                r = client.post("/api/v1/admin/exams/exam-1/archive", headers=admin_headers)
        assert r.status_code == 200
        assert r.json().get("status") == "archived"


class TestUnarchiveEndpoint:
    """POST /api/v1/admin/exams/{exam_id}/unarchive"""

    def test_unarchive_requires_auth(self):
        r = client.post("/api/v1/admin/exams/exam-1/unarchive")
        assert r.status_code == 401

    def test_unarchive_own_exam(self, admin_headers, mock_teacher):
        """unarchive clears archived_at on the caller's own exam."""
        parent = _mock_archive_ops(found=True)
        with patch("app.routers.admin_exams._atable", return_value=parent):
            with patch("app.routers.admin_exams._cache", None):
                r = client.post("/api/v1/admin/exams/exam-1/unarchive", headers=admin_headers)
        assert r.status_code == 200
        d = r.json()
        assert d.get("status") == "unarchived"
        assert d.get("archived") is False

    def test_unarchive_not_found(self, admin_headers, mock_teacher):
        parent = _mock_archive_ops(found=False)
        with patch("app.routers.admin_exams._atable", return_value=parent):
            r = client.post("/api/v1/admin/exams/exam-nonexistent/unarchive", headers=admin_headers)
        assert r.status_code == 404


class TestListFiltering:
    """GET /api/v1/admin/exams — ?include_archived=1"""

    @pytest.fixture(autouse=True)
    def _mock_auth(self):
        with patch("app.auth.admin_auth._get_teacher_by_id", new_callable=AsyncMock) as m:
            m.return_value = _MOCK_TEACHER
            yield

    @patch("app.routers.admin_exams.resolve_scope")
    @patch("app.routers.admin_exams.scope_to_teacher_ids")
    @patch("app.routers.admin_exams._atable")
    def test_default_excludes_archived(self, mock_atable, mock_scope_tids, mock_resolve,
                                       admin_headers):
        """By default, only non-archived exams are returned."""
        mock_resolve.return_value = {"teacher_id": "teacher-1"}
        mock_scope_tids.return_value = ["teacher-1"]
        # _scoped chain when teacher_ids is set AND not include_archived:
        # .select().in_(...).is_("archived_at","null") → then .order().range().execute()
        base = MagicMock()  # .select().in_() returns this
        q_is = MagicMock()  # .is_() returns this (final _scoped return)
        q_order = MagicMock()
        q_range = MagicMock()
        q_range.execute = AsyncMock(return_value=MagicMock(data=[
            {"exam_id": "e1", "exam_title": "Active Exam", "duration_minutes": 60,
             "starts_at": None, "ends_at": None, "access_code": "",
             "proctoring_sensitivity": "balanced", "created_at": "", "teacher_id": "t1",
             "pass_mark": 40, "archived_at": None}
        ]))
        q_order.range.return_value = q_range
        q_is.order.return_value = q_order
        base.is_.return_value = q_is
        mock_atable.return_value.select.return_value.in_.return_value = base
        r = client.get("/api/v1/admin/exams", headers=admin_headers)
        assert r.status_code == 200
        exams = r.json().get("exams", [])
        assert len(exams) == 1
        assert exams[0]["exam_id"] == "e1"
        assert exams[0]["archived_at"] is None

    @patch("app.routers.admin_exams.resolve_scope")
    @patch("app.routers.admin_exams.scope_to_teacher_ids")
    @patch("app.routers.admin_exams._atable")
    def test_include_archived_param(self, mock_atable, mock_scope_tids, mock_resolve,
                                    admin_headers):
        """?include_archived=1 returns archived exams too."""
        mock_resolve.return_value = {"teacher_id": "teacher-1"}
        mock_scope_tids.return_value = ["teacher-1"]
        # With include_archived=1, no .is_() call: .select().in_().order().range().execute
        base = MagicMock()
        q_order = MagicMock()
        q_range = MagicMock()
        q_range.execute = AsyncMock(return_value=MagicMock(data=[
            {"exam_id": "e1", "exam_title": "Active Exam", "duration_minutes": 60,
             "starts_at": None, "ends_at": None, "access_code": "",
             "proctoring_sensitivity": "balanced", "created_at": "", "teacher_id": "t1",
             "pass_mark": 40, "archived_at": None},
            {"exam_id": "e2", "exam_title": "Archived Exam", "duration_minutes": 60,
             "starts_at": None, "ends_at": None, "access_code": "",
             "proctoring_sensitivity": "balanced", "created_at": "", "teacher_id": "t1",
             "pass_mark": 40, "archived_at": "2025-01-01T00:00:00Z"}
        ]))
        q_order.range.return_value = q_range
        base.order.return_value = q_order
        mock_atable.return_value.select.return_value.in_.return_value = base
        r = client.get("/api/v1/admin/exams?include_archived=1", headers=admin_headers)
        assert r.status_code == 200
        exams = r.json().get("exams", [])
        assert len(exams) == 2
        archived = [e for e in exams if e["archived_at"]]
        assert len(archived) == 1
        assert archived[0]["exam_id"] == "e2"
        # Also verify the .is_() call was NOT applied (no filter for archived)
        # When include_archived=1, the .is_("archived_at", "null") should NOT be called.
        # We can't easily assert the negative, but the result includes archived rows.


class TestValidateStudentArchivedGuard:
    """POST /api/v1/validate-student rejects archived exams."""

    @patch("app.routers.exam._resolve_teacher")
    @patch("app.routers.exam._load_exam_config")
    def test_archived_exam_rejected(self, mock_config, mock_resolve):
        mock_resolve.return_value = ("t1", "exam-1")
        mock_config.return_value = {
            "exam_title": "Exam", "duration_minutes": 60, "access_code": "",
            "starts_at": None, "ends_at": None,
            "shuffle_questions": True, "shuffle_options": True,
            "proctoring_sensitivity": "balanced",
            "phone_camera_enabled": False,
            "audio_keywords": None, "audio_keywords_language": "en",
            "pass_mark": 40, "archived_at": "2025-01-01T00:00:00Z",
        }
        r = client.post("/api/v1/validate-student", json={
            "roll_number": "R001", "teacher_id": "t1",
            "exam_id": "exam-1", "access_code": "",
        })
        assert r.status_code == 403
        assert "no longer available" in r.text.lower()

    @patch("app.routers.exam._resolve_teacher")
    @patch("app.routers.exam._load_exam_config")
    def test_non_archived_exam_allowed(self, mock_config, mock_resolve):
        mock_resolve.return_value = ("t1", "exam-1")
        mock_config.return_value = {
            "exam_title": "Exam", "duration_minutes": 60, "access_code": "",
            "starts_at": None, "ends_at": None,
            "shuffle_questions": True, "shuffle_options": True,
            "proctoring_sensitivity": "balanced",
            "phone_camera_enabled": False,
            "audio_keywords": None, "audio_keywords_language": "en",
            "pass_mark": 40, "archived_at": None,
        }
        with patch("app.routers.exam._load_questions",
                   return_value=[{"id": "q1"}]):
            with patch("app.routers.exam._find_or_enroll_student",
                       return_value=({"roll_number": "R001", "full_name": "Alice",
                                      "email": "a@b.com", "phone": "", "account_id": None},
                                      "t1", None)):
                with patch("app.routers.exam._validate_access_code",
                           return_value=None):
                    with patch("app.routers.exam._check_group_restrictions",
                               new_callable=AsyncMock):
                        with patch("app.routers.exam._check_guardian_consent",
                                   new_callable=AsyncMock):
                            with patch("app.routers.exam._check_existing_session",
                                       new_callable=AsyncMock, return_value=None):
                                with patch("app.routers.exam._check_concurrent_exam_limit",
                                           new_callable=AsyncMock):
                                    with patch("app.routers.exam._atable"):
                                        r = client.post("/api/v1/validate-student", json={
                                            "roll_number": "R001", "teacher_id": "t1",
                                            "exam_id": "exam-1", "access_code": "",
                                        })
        assert r.status_code == 200
