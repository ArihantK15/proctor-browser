"""Tests for the student performance history feature."""
import asyncio
import json
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from fastapi import HTTPException
from app.routers.admin_students import get_student_history, search_students


class FakeResponse:
    """Helper to mock Supabase execute() responses."""
    def __init__(self, data=None, count=None):
        self.data = data or []
        self.count = count


def make_async_mock(response_data=None):
    """Create a mock chain where .execute() returns an awaitable."""
    mock = MagicMock()
    resp = FakeResponse(response_data if response_data is not None else [])

    async def async_execute():
        return resp

    # Configure the entire chain to return the same mock (for fluent chaining)
    mock.select.return_value = mock
    mock.eq.return_value = mock
    mock.in_.return_value = mock
    mock.order.return_value = mock
    mock.limit.return_value = mock
    mock.delete.return_value = mock
    mock.update.return_value = mock
    mock.execute.return_value = async_execute()
    return mock


class TestStudentSearch:
    """GET /api/v1/student-search — directory of students with aggregate stats."""

    def _make_request(self, token="stub"):
        req = MagicMock()
        req.headers = {"Authorization": f"Bearer {token}"}
        return req

    def test_empty_student_list(self):
        mock_atable = MagicMock(return_value=make_async_mock([]))
        with patch("app.routers.admin_students._atable", mock_atable), \
             patch("app.routers.admin_students.require_admin") as mock_admin:
            mock_admin.return_value = {"id": "t1", "full_name": "Test"}

            resp = asyncio.run(search_students(self._make_request()))

            assert resp["total"] == 0
            assert resp["students"] == []


class TestStudentHistory:
    """GET /api/v1/student-history/{roll_number} — detailed exam history."""

    def _make_request(self, token="stub"):
        req = MagicMock()
        req.headers = {"Authorization": f"Bearer {token}"}
        return req

    def test_student_not_found(self):
        mock_atable = MagicMock(return_value=make_async_mock([]))
        with patch("app.routers.admin_students._atable", mock_atable), \
             patch("app.routers.admin_students.require_admin") as mock_admin:
            mock_admin.return_value = {"id": "t1", "full_name": "Test"}

            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(get_student_history("R999", self._make_request()))
            assert exc_info.value.status_code == 404

    def test_returns_history_with_aggregates(self):
        student_data = [
            {"roll_number": "R001", "full_name": "Alice", "email": "a@b.com", "teacher_id": "t1", "phone": ""},
        ]
        session_data = [
            {
                "session_key": "R001_001",
                "exam_id": "ex1",
                "roll_number": "R001",
                "full_name": "Alice",
                "email": "a@b.com",
                "score": 8,
                "total": 10,
                "percentage": 80.0,
                "time_taken_secs": 1800,
                "status": "completed",
                "started_at": "2025-01-01T09:00:00Z",
                "submitted_at": "2025-01-01T09:30:00Z",
                "risk_score": 10,
            },
        ]
        violation_data = [
            {"session_key": "R001_001", "violation_type": "gaze_away", "severity": "low", "created_at": "2025-01-01T09:05:00Z"},
        ]
        config_data = [
            {"exam_id": "ex1", "exam_title": "Midterm"},
        ]

        responses = {
            "students": student_data,
            "exam_sessions": session_data,
            "violations": violation_data,
            "exam_config": config_data,
        }

        def table_side_effect(name):
            return make_async_mock(responses.get(name, []))

        mock_atable = MagicMock(side_effect=table_side_effect)
        with patch("app.routers.admin_students._atable", mock_atable), \
             patch("app.routers.admin_students.require_admin") as mock_admin, \
             patch("app.routers.admin_students.SessionStatus") as mock_status, \
             patch("app.routers.admin_students._risk_label") as mock_label, \
             patch("app.routers.admin_students._violation_counts_by_session") as mock_vcounts:
            mock_admin.return_value = {"id": "t1", "full_name": "Test"}
            mock_status.COMPLETED = "completed"
            mock_label.return_value = "Low Risk"
            mock_vcounts.return_value = {"R001_001": 2}

            resp = asyncio.run(get_student_history("R001", self._make_request()))

            assert resp["student"]["roll_number"] == "R001"
            assert resp["student"]["full_name"] == "Alice"
            assert resp["aggregates"]["total_exams"] == 1
            assert resp["aggregates"]["avg_percentage"] == 80.0
            assert len(resp["history"]) == 1
            assert resp["history"][0]["score"] == 8
            assert resp["history"][0]["session_id"] == "R001_001"
            assert resp["history"][0]["percentage"] == 80.0
