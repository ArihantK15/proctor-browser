"""Tests for admin scorecard endpoints: email, Excel export, PDF.

Covers:
  1. ``email_scorecards`` (POST .../email-scorecards)
  2. ``export_excel`` (GET /api/v1/export-excel)
  3. ``scorecard_pdf``   (GET /api/v1/admin/scorecard-pdf/{session_id})
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")

from tests.conftest import make_admin_token, shared_supabase_mock  # noqa: E402


# ─── Helpers ─────────────────────────────────────────────────────────

TEACHER = {"id": "teacher-1", "email": "prof@test.com", "full_name": "Prof T"}


def _admin_headers():
    return {"Authorization": f"Bearer {make_admin_token(teacher_id='teacher-1', email='prof@test.com')}"}


def _table_side_effect(mapping):
    """Return a side_effect function for ``patch.object(sm, 'table')``.

    *mapping* is a ``{table_name: data_list}`` dict.  Each call to
    ``table(name)`` returns a fluent mock whose ``.execute()`` is a
    proper async coroutine returning ``data=data_list``.
    """

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
#  POST /api/v1/admin/exams/{exam_id}/email-scorecards
# ═══════════════════════════════════════════════════════════════════


class TestEmailScorecards:
    """Bulk-email scorecards for all completed sessions in an exam."""

    SESSION = {
        "session_key": "sess-1", "roll_number": "R001",
        "full_name": "Alice", "exam_id": "exam-1",
        "scorecard_emailed_at": None,
    }

    def test_no_completed_sessions_returns_404(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_sessions": [],  # no completed sessions
        })):
            resp = client.post(
                "/api/v1/admin/exams/exam-1/email-scorecards",
                json={},
                headers=_admin_headers(),
            )
        assert resp.status_code == 404
        assert "No completed sessions" in resp.json().get("detail", "")

    def test_enqueues_for_completed_sessions(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_sessions": [self.SESSION],
            "student_invites": [{"roll_number": "R001", "email": "alice@test.com"}],
            "students": [],
        })), \
            patch("app.routers.admin_scorecards.enqueue_job") as enq:
            enq.return_value = None  # simulates RQ enqueue

            resp = client.post(
                "/api/v1/admin/exams/exam-1/email-scorecards",
                json={},
                headers=_admin_headers(),
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["sent"] == 1
        assert body["failed"] == 0

    def test_skips_already_emailed(self, client):
        session_emailed = dict(self.SESSION)
        session_emailed["scorecard_emailed_at"] = "2025-06-01T00:00:00Z"
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_sessions": [session_emailed],
            "student_invites": [{"roll_number": "R001", "email": "alice@test.com"}],
            "students": [],
        })), \
            patch("app.routers.admin_scorecards.enqueue_job") as enq:
            resp = client.post(
                "/api/v1/admin/exams/exam-1/email-scorecards",
                json={},
                headers=_admin_headers(),
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["already_sent"] == 1
        assert body["sent"] == 0
        enq.assert_not_called()

    def test_skips_sessions_without_email(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_sessions": [self.SESSION],
            "student_invites": [],  # no email mapping
            "students": [],
        })), \
            patch("app.routers.admin_scorecards.enqueue_job") as enq:
            resp = client.post(
                "/api/v1/admin/exams/exam-1/email-scorecards",
                json={},
                headers=_admin_headers(),
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["skipped_no_email"] == 1

    def test_resend_all_overrides_already_emailed(self, client):
        session_emailed = dict(self.SESSION)
        session_emailed["scorecard_emailed_at"] = "2025-06-01T00:00:00Z"
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_sessions": [session_emailed],
            "student_invites": [{"roll_number": "R001", "email": "alice@test.com"}],
            "students": [],
        })), \
            patch("app.routers.admin_scorecards.enqueue_job") as enq:
            enq.return_value = None
            resp = client.post(
                "/api/v1/admin/exams/exam-1/email-scorecards",
                json={"resend_all": True},
                headers=_admin_headers(),
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["sent"] == 1


# ═══════════════════════════════════════════════════════════════════
#  GET /api/v1/export-excel
# ═══════════════════════════════════════════════════════════════════


class TestExportExcel:
    """Excel spreadsheet export of completed sessions."""

    RESULTS = [
        {"session_key": "s-1", "roll_number": "R001", "full_name": "Alice",
         "email": "a@t.com", "score": 8, "total": 10, "percentage": 80.0,
         "time_taken_secs": 1200, "submitted_at": "2025-06-01T00:00:00Z",
         "risk_score": 0.2, "risk_label": "Low", "violation_count": 1,
         "calibration": None},
    ]

    def test_returns_excel_file(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_sessions": self.RESULTS,
            "violations": [],
        })):
            resp = client.get(
                "/api/v1/export-excel",
                params={"exam_id": "exam-1"},
                headers=_admin_headers(),
            )
        # Should return a file download (200 or 201)
        assert resp.status_code in (200, 201)
        assert "application/" in (resp.headers.get("content-type") or "")
        # openpyxl creates application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
        assert "spreadsheet" in resp.headers.get("content-type", "").lower() or \
               "octet" in resp.headers.get("content-type", "").lower()

    def test_empty_results_returns_empty_excel(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_sessions": [],
            "violations": [],
        })):
            resp = client.get(
                "/api/v1/export-excel",
                params={"exam_id": "exam-1"},
                headers=_admin_headers(),
            )
        assert resp.status_code in (200, 201)


# ═══════════════════════════════════════════════════════════════════
#  GET /api/v1/admin/scorecard-pdf/{session_id}
# ═══════════════════════════════════════════════════════════════════


class TestScorecardPDF:
    """Single student scorecard as PDF."""

    SESSION = {
        "session_key": "sess-1", "roll_number": "R001",
        "full_name": "Alice", "exam_id": "exam-1",
        "teacher_id": "teacher-1",
        "score": 8, "total": 10, "percentage": 80.0,
        "passed": True, "submitted_at": "2025-06-01T00:00:00Z",
        "time_taken_secs": 1200,
    }

    def test_returns_pdf(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_sessions": [self.SESSION],
            "questions": [],
            "answers": [],
            "violations": [],
            "exam_config": [],
        })), \
            patch("app.routers.admin_scorecards._build_scorecard_pdf") as mock_build:
            mock_build.return_value = (
                b"%PDF-1.4 fake pdf content ...",
                "scorecard_R001_20250601.pdf",
                {"exam_title": "Midterm", "score": 8, "total": 10},
            )
            resp = client.get(
                "/api/v1/admin/scorecard-pdf/sess-1",
                headers=_admin_headers(),
            )
        assert resp.status_code == 200
        assert resp.headers.get("content-type") == "application/pdf"
        assert resp.content.startswith(b"%PDF")

    def test_404_for_missing_session(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_sessions": [],  # session does not exist
        })):
            resp = client.get(
                "/api/v1/admin/scorecard-pdf/nonexistent",
                headers=_admin_headers(),
            )
        assert resp.status_code in (403, 404)


# ═══════════════════════════════════════════════════════════════════
#  GET /api/v1/export-pdf/{session_id}  — single-session PDF
# ═══════════════════════════════════════════════════════════════════


class TestExportPDF:
    """Single-session detailed PDF export (reportlab)."""

    SESSION = {
        "session_key": "sess-1", "roll_number": "R001",
        "full_name": "Alice", "exam_id": "exam-1",
        "teacher_id": "teacher-1",
        "score": 8, "total": 10, "percentage": 80.0,
        "submitted_at": "2025-06-01T00:00:00Z",
        "time_taken_secs": 1200,
    }

    def test_returns_pdf(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_sessions": [self.SESSION],
            "violations": [],
            "answers": [],
        })), \
            patch("app.routers.admin_scorecards.compute_risk_score") as mock_risk:
            mock_risk.return_value = {"risk_score": 15, "label": "Low", "risk_level": "low"}
            resp = client.get(
                "/api/v1/export-pdf/sess-1",
                headers=_admin_headers(),
            )
        assert resp.status_code == 200
        assert resp.headers.get("content-type") == "application/pdf"
        assert resp.content.startswith(b"%PDF")

    def test_404_for_missing_session(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_sessions": [],
        })):
            resp = client.get(
                "/api/v1/export-pdf/nonexistent",
                headers=_admin_headers(),
            )
        assert resp.status_code == 404

    def test_requires_auth(self, client):
        resp = client.get("/api/v1/export-pdf/sess-1")
        assert resp.status_code == 401
