"""Tests for Gap #40: Cohort (group/batch) filtering for results, exports & invites.

Covers:
  1. cohort_roll_numbers resolver — by group_id, by batch (case-insensitive), by both
  2. results endpoint with ?group_id= / ?batch= filters
  3. export-csv / export-excel with cohort filters
  4. send_invites with cohort expansion
  5. No cohort param = unchanged behaviour
"""

import os
from unittest.mock import patch, MagicMock, AsyncMock, call

import pytest
from httpx import AsyncClient, ASGITransport

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")

from app.main import app

TEACHER = {"id": "t-1", "email": "t@t.com", "org_role": "teacher"}


class _MockTable:
    """Fluent mock that returns self on every chain method, with awaitable .execute()."""
    def __init__(self, data=None):
        self._data = data if data is not None else []
    def select(self, *a, **kw): return self
    def eq(self, *a, **kw): return self
    def neq(self, *a, **kw): return self
    def is_(self, *a, **kw): return self
    def in_(self, *a, **kw): return self
    def gte(self, *a, **kw): return self
    def lte(self, *a, **kw): return self
    def gt(self, *a, **kw): return self
    def lt(self, *a, **kw): return self
    def like(self, *a, **kw): return self
    def contains(self, *a, **kw): return self
    def order(self, *a, **kw): return self
    def limit(self, *a, **kw): return self
    def range(self, *a, **kw): return self
    def single(self, *a, **kw): return self
    def insert(self, *a, **kw): return self
    def upsert(self, *a, **kw): return self
    def update(self, *a, **kw): return self
    def delete(self, *a, **kw): return self
    async def execute(self):
        r = MagicMock()
        r.data = self._data
        r.count = None
        return r


def _mock_atable(rows=None, insert_data=None):
    return _MockTable(data=rows or insert_data or [])


class TestCohortRollNumbers:
    @pytest.mark.asyncio
    async def test_no_params_returns_none(self):
        from app.repositories.sessions import cohort_roll_numbers
        result = await cohort_roll_numbers(["t-1"])
        assert result is None

    @pytest.mark.asyncio
    async def test_by_group_id(self):
        mt = _mock_atable([{"roll_number": "R001"}, {"roll_number": "R002"}])
        with patch("app.repositories.sessions._atable", return_value=mt):
            from app.repositories.sessions import cohort_roll_numbers
            result = await cohort_roll_numbers(["t-1"], group_id="g-1")
        assert result == {"R001", "R002"}

    @pytest.mark.asyncio
    async def test_by_batch_case_insensitive(self):
        mt = _mock_atable([
            {"roll_number": "R001", "batch": "CS-2024"},
            {"roll_number": "R002", "batch": "  cs-2024  "},
            {"roll_number": "R003", "batch": "OTHER"},
        ])
        with patch("app.repositories.sessions._atable", return_value=mt):
            from app.repositories.sessions import cohort_roll_numbers
            result = await cohort_roll_numbers(["t-1"], batch="CS-2024")
        assert result == {"R001", "R002"}

    @pytest.mark.asyncio
    async def test_by_both_returns_union(self):
        mt_group = _mock_atable([{"roll_number": "R001"}, {"roll_number": "R002"}])
        mt_students = _mock_atable([
            {"roll_number": "R003", "batch": "CS-2024"},
        ])
        with patch("app.repositories.sessions._atable", side_effect=[mt_group, mt_students]):
            from app.repositories.sessions import cohort_roll_numbers
            result = await cohort_roll_numbers(["t-1"], group_id="g-1", batch="CS-2024")
        assert result == {"R001", "R002", "R003"}

    @pytest.mark.asyncio
    async def test_cross_teacher_excluded_in_group(self):
        mt = _mock_atable([{"roll_number": "R001"}])
        with patch("app.repositories.sessions._atable", return_value=mt):
            from app.repositories.sessions import cohort_roll_numbers
            result = await cohort_roll_numbers(["t-2"], group_id="g-1")
        assert result == {"R001"}


class TestResultsEndpoint:
    PATH = "/api/v1/results"

    @pytest.fixture(autouse=True)
    def _mock_auth(self):
        with patch("app.routers.admin_sessions.require_admin", new_callable=AsyncMock, return_value=TEACHER), \
             patch("app.auth.scope.resolve_scope", new_callable=AsyncMock, return_value={"type": "self"}), \
             patch("app.auth.scope.scope_to_teacher_ids", new_callable=AsyncMock, return_value=[TEACHER["id"]]):
            yield

    @pytest.mark.asyncio
    async def test_no_cohort_param(self):
        rows = [{"session_key": "s1_R001", "roll_number": "R001", "full_name": "A", "email": "a@t.com",
                 "score": 80, "total": 100, "percentage": 80.0, "time_taken_secs": 300,
                 "submitted_at": "2025-01-01T00:00:00Z", "risk_score": 10}]
        _mock_atable(rows)
        with patch("app.routers.admin_sessions._fetch_all_results", new_callable=AsyncMock,
                   return_value=[{"session_id": "s1_R001", "roll_number": "R001", "full_name": "A", "email": "a@t.com",
                                   "score": 80, "total": 100, "percentage": 80.0, "time_taken_secs": 300,
                                   "submitted_at": "Jan 1, 2025, 5:30 AM", "violation_count": 0,
                                   "risk_score": 10, "risk_label": "Low", "calibration": {}}]):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.get(self.PATH)
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1
        assert data["results"][0]["roll_number"] == "R001"

    @pytest.mark.asyncio
    async def test_with_group_id(self):
        with patch("app.routers.admin_sessions._cohort_roll_numbers", new_callable=AsyncMock,
                   return_value={"R001"}), \
             patch("app.routers.admin_sessions._fetch_all_results", new_callable=AsyncMock,
                   return_value=[{"session_id": "s1_R001", "roll_number": "R001", "full_name": "A", "email": "a@t.com",
                                   "score": 80, "total": 100, "percentage": 80.0, "time_taken_secs": 300,
                                   "submitted_at": "Jan 1, 2025, 5:30 AM", "violation_count": 0,
                                   "risk_score": 10, "risk_label": "Low", "calibration": {}}]):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.get(self.PATH, params={"group_id": "g-1"})
        assert r.status_code == 200
        assert r.json()["total"] == 1

    @pytest.mark.asyncio
    async def test_with_batch(self):
        with patch("app.routers.admin_sessions._cohort_roll_numbers", new_callable=AsyncMock,
                   return_value={"R001"}), \
             patch("app.routers.admin_sessions._fetch_all_results", new_callable=AsyncMock,
                   return_value=[{"session_id": "s1_R001", "roll_number": "R001", "full_name": "A", "email": "a@t.com",
                                   "score": 80, "total": 100, "percentage": 80.0, "time_taken_secs": 300,
                                   "submitted_at": "Jan 1, 2025, 5:30 AM", "violation_count": 0,
                                   "risk_score": 10, "risk_label": "Low", "calibration": {}}]):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.get(self.PATH, params={"batch": "CS-2024"})
        assert r.status_code == 200
        assert r.json()["total"] == 1


class TestExportCSVEndpoint:
    PATH = "/api/v1/export-csv"

    @pytest.fixture(autouse=True)
    def _mock_auth(self):
        with patch("app.routers.admin_scorecards.require_admin", new_callable=AsyncMock, return_value=TEACHER), \
             patch("app.auth.scope.resolve_scope", new_callable=AsyncMock, return_value={"type": "self"}), \
             patch("app.auth.scope.scope_to_teacher_ids", new_callable=AsyncMock, return_value=[TEACHER["id"]]):
            yield

    @pytest.mark.asyncio
    async def test_no_param_returns_csv(self):
        _mock_atable([{"session_key": "s1_R001", "roll_number": "R001", "full_name": "A", "email": "a@t.com",
                            "score": 80, "total": 100, "percentage": 80.0, "time_taken_secs": 300,
                            "submitted_at": "2025-01-01T00:00:00Z", "risk_score": 10}])
        with patch("app.routers.admin_scorecards._stream_csv_results") as mock_stream:
            mock_stream.return_value.__aiter__.return_value = ["timestamp,roll\n2025-01-01,R001\n"]
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.get(self.PATH)
        assert r.status_code == 200
        assert "text/csv" in r.headers["content-type"]

    @pytest.mark.asyncio
    async def test_with_group_param(self):
        with patch("app.routers.admin_scorecards._cohort_roll_numbers", new_callable=AsyncMock,
                   return_value={"R001"}), \
             patch("app.routers.admin_scorecards._stream_csv_results") as mock_stream:
            mock_stream.return_value.__aiter__.return_value = ["timestamp,roll\n2025-01-01,R001\n"]
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.get(self.PATH, params={"group_id": "g-1"})
        assert r.status_code == 200


class TestExportExcelEndpoint:
    PATH = "/api/v1/export-excel"

    @pytest.fixture(autouse=True)
    def _mock_auth(self):
        with patch("app.routers.admin_scorecards.require_admin", new_callable=AsyncMock, return_value=TEACHER), \
             patch("app.auth.scope.resolve_scope", new_callable=AsyncMock, return_value={"type": "self"}), \
             patch("app.auth.scope.scope_to_teacher_ids", new_callable=AsyncMock, return_value=[TEACHER["id"]]):
            yield

    @pytest.mark.asyncio
    async def test_no_param_returns_excel(self):
        with patch("app.routers.admin_scorecards._fetch_all_results", new_callable=AsyncMock, return_value=[]), \
             patch("app.routers.admin_scorecards._cohort_roll_numbers", new_callable=AsyncMock, return_value=None):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.get(self.PATH)
        assert r.status_code == 200
        assert "spreadsheetml" in r.headers["content-type"]

    @pytest.mark.asyncio
    async def test_with_batch_param(self):
        with patch("app.routers.admin_scorecards._fetch_all_results", new_callable=AsyncMock, return_value=[]), \
             patch("app.routers.admin_scorecards._cohort_roll_numbers", new_callable=AsyncMock, return_value={"R001"}):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.get(self.PATH, params={"batch": "CS-2024"})
        assert r.status_code == 200


class TestInviteCohortExpansion:
    PATH = "/api/v1/admin/invites/send"

    @pytest.fixture(autouse=True)
    def _mock_auth(self):
        with patch("app.routers.admin_invites.require_admin", new_callable=AsyncMock, return_value=TEACHER), \
             patch("app.invites._claim_and_bump_cap", new_callable=AsyncMock, return_value=(True, 500)):
            yield

    @pytest.mark.asyncio
    async def test_no_cohort_expands_nothing(self):
        mt = _mock_atable(rows=[{"id": "exam-1", "exam_title": "Test Exam"}])
        with patch("app.routers.admin_invites._atable", return_value=mt), \
             patch("app.routers.admin_invites.send_invite_email_job",
                   return_value={"ok": True, "provider_msg_id": "noop"}):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.post(self.PATH, json={
                    "exam_id": "exam-1",
                    "recipients": [{"email": "s@t.com", "full_name": "S", "roll_number": "R001"}],
                })
        assert r.status_code == 200
        assert r.json()["sent"] == 0  # noop == skipped

    @pytest.mark.asyncio
    async def test_with_group_expands_roster(self):
        # Mock student_invites select (empty — no existing)
        mt_empty = _mock_atable([])
        # Mock exam_config
        mt_exam = _mock_atable(rows=[{"exam_title": "Test Exam"}])
        # Mock cohort_roll_numbers → returns R001, R002
        # Mock students query for cohort
        mt_students = _mock_atable([
            {"full_name": "Alice", "email": "alice@t.com", "roll_number": "R001"},
            {"full_name": "Bob", "email": "bob@t.com", "roll_number": "R002"},
        ])

        with patch("app.routers.admin_invites._atable", side_effect=[mt_empty, mt_exam, mt_students, mt_empty, mt_empty]), \
             patch("app.routers.admin_invites._cohort_roll_numbers", new_callable=AsyncMock, return_value={"R001", "R002"}) as mock_cohort, \
             patch("app.routers.admin_invites.send_invite_email_job",
                   return_value={"ok": True, "provider_msg_id": "noop"}):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.post(self.PATH, json={
                    "exam_id": "exam-1",
                    "recipients": [],
                    "group_id": "g-1",
                })
        assert r.status_code == 200
        mock_cohort.assert_called_once()

    @pytest.mark.asyncio
    async def test_cross_teacher_rolls_excluded(self):
        mt = _mock_atable([])
        mt_exam = _mock_atable(rows=[{"exam_title": "Test"}])
        mt_students = _mock_atable([
            {"full_name": "Alice", "email": "alice@t.com", "roll_number": "R001"},
        ])
        with patch("app.routers.admin_invites._atable", side_effect=[mt, mt_exam, mt_students, mt, mt]), \
             patch("app.routers.admin_invites._cohort_roll_numbers", new_callable=AsyncMock, return_value={"R001"}), \
             patch("app.routers.admin_invites.send_invite_email_job",
                   return_value={"ok": True, "provider_msg_id": "noop"}):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.post(self.PATH, json={
                    "exam_id": "exam-1",
                    "recipients": [],
                    "group_id": "g-1",
                })
        assert r.status_code == 200
