"""Tests for the student performance history feature."""
import json
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from app.routers.admin import get_student_history, search_students


class FakeResponse:
    """Helper to mock Supabase execute() responses."""
    def __init__(self, data=None, count=None):
        self.data = data or []
        self.count = count


class MockSupabaseTable:
    """Chained mock for supabase.table().select().eq().execute()."""
    def __init__(self, table_name, responses):
        self._table = table_name
        self._responses = responses
        self._filters = []

    def select(self, *args):
        return self
    def eq(self, *args):
        self._filters.append(("eq", args))
        return self
    def in_(self, *args):
        self._filters.append(("in", args))
        return self
    def order(self, *args, **kwargs):
        return self
    def limit(self, *args):
        return self
    def execute(self):
        key = self._table
        return FakeResponse(self._responses.get(key, []), self._responses.get(key + "_count"))


class TestStudentSearch:
    """GET /api/v1/student-search — directory of students with aggregate stats."""

    def _make_request(self, token="stub"):
        req = MagicMock()
        req.headers = {"Authorization": f"Bearer {token}"}
        return req

    def test_empty_student_list(self):
        with patch("app.routers.admin.supabase") as mock_supabase, \
             patch("app.routers.admin.require_admin") as mock_admin:
            mock_admin.return_value = {"id": "t1", "full_name": "Test"}
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = FakeResponse([])

            resp = search_students(self._make_request())

            assert resp["total"] == 0
            assert resp["students"] == []


class TestStudentHistory:
    """GET /api/v1/student-history/{roll_number} — detailed exam history."""

    def _make_request(self, token="stub"):
        req = MagicMock()
        req.headers = {"Authorization": f"Bearer {token}"}
        return req

    def test_student_not_found(self):
        with patch("app.routers.admin.supabase") as mock_supabase, \
             patch("app.routers.admin.require_admin") as mock_admin:
            mock_admin.return_value = {"id": "t1", "full_name": "Test"}
            mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = FakeResponse([])

            with pytest.raises(Exception) as exc_info:
                get_student_history("R999", self._make_request())
            assert exc_info.value.status_code == 404

    def test_returns_history_with_aggregates(self):
        def table_side_effect(name):
            mock = MagicMock()
            chain = mock.select.return_value.eq.return_value
            if name == "students":
                chain.eq.return_value.limit.return_value.execute.return_value = FakeResponse([
                    {"roll_number": "R001", "full_name": "Alice", "email": "a@b.com", "teacher_id": "t1", "phone": ""},
                ])
            elif name == "exam_sessions":
                chain.eq.return_value.eq.return_value.order.return_value.execute.return_value = FakeResponse([
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
                ])
            elif name == "violations":
                in_mock = MagicMock()
                in_mock.execute.return_value = FakeResponse([
                    {"session_key": "R001_001", "violation_type": "gaze_away", "severity": "low", "created_at": "2025-01-01T09:05:00Z"},
                ])
                chain.eq.return_value.in_.return_value = in_mock
            elif name == "exam_config":
                in_mock = MagicMock()
                in_mock.execute.return_value = FakeResponse([
                    {"exam_id": "ex1", "exam_title": "Midterm"},
                ])
                chain.eq.return_value.in_.return_value = in_mock
            return mock

        with patch("app.routers.admin.supabase") as mock_supabase, \
             patch("app.routers.admin.require_admin") as mock_admin, \
             patch("app.routers.admin.SessionStatus") as mock_status, \
             patch("app.routers.admin._risk_label") as mock_label, \
             patch("app.routers.admin._violation_counts_by_session") as mock_vcounts:
            mock_admin.return_value = {"id": "t1", "full_name": "Test"}
            mock_status.COMPLETED = "completed"
            mock_label.return_value = "Low Risk"
            mock_vcounts.return_value = {"R001_001": 2}
            mock_supabase.table.side_effect = table_side_effect

            resp = get_student_history("R001", self._make_request())

            assert resp["student"]["roll_number"] == "R001"
            assert resp["student"]["full_name"] == "Alice"
            assert resp["aggregates"]["total_exams"] == 1
            assert resp["aggregates"]["avg_percentage"] == 80.0
            assert len(resp["history"]) == 1
            assert resp["history"][0]["score"] == 8
            assert resp["history"][0]["session_id"] == "R001_001"
            assert resp["history"][0]["percentage"] == 80.0
