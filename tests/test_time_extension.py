"""Unit tests for per-student exam time extensions (Gap #22).

Covers:
  - get_time_extension: none → 0; set → N; lookup error → 0 (fail-open)
  - validate response duration_minutes = base + extension
  - submit handler allowed_secs includes extension
  - endpoints: upsert sets, extra_minutes=0 clears, non-owner → 404,
    out-of-range → 400; GET list returns {roll: minutes}
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.time_extension import get_time_extension

client = TestClient(app)


def _run(coro):
    return asyncio.run(coro)


# ── get_time_extension ──────────────────────────────────────────────


class TestGetTimeExtension:
    def test_no_row_returns_0(self):
        mock = MagicMock()
        mock.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.limit.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[])
        )
        with patch("app.services.time_extension._atable", mock):
            assert _run(get_time_extension("t1", "exam-1", "R001")) == 0

    def test_returns_extra_minutes(self):
        mock = MagicMock()
        mock.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.limit.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[{"extra_minutes": 30}])
        )
        with patch("app.services.time_extension._atable", mock):
            assert _run(get_time_extension("t1", "exam-1", "R001")) == 30

    def test_lookup_error_returns_0(self):
        mock = MagicMock()
        mock.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.limit.return_value.execute = AsyncMock(
            side_effect=Exception("DB down")
        )
        with patch("app.services.time_extension._atable", mock):
            assert _run(get_time_extension("t1", "exam-1", "R001")) == 0

    def test_zero_minutes_returns_0(self):
        mock = MagicMock()
        mock.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.limit.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[{"extra_minutes": 0}])
        )
        with patch("app.services.time_extension._atable", mock):
            assert _run(get_time_extension("t1", "exam-1", "R001")) == 0


# ── validate response: duration_minutes ──────────────────────────────


class TestValidateDuration:
    @patch("app.routers.exam.get_time_extension", new_callable=AsyncMock)
    def test_duration_includes_extension(self, mock_ext):
        mock_ext.return_value = 30
        with patch("app.routers.exam._resolve_teacher",
                   return_value=("t1", "exam-1")):
            with patch("app.routers.exam._load_exam_config",
                       return_value={"duration_minutes": 60}):
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
                                                resp = client.post("/api/v1/validate-student",
                                                                   json={
                                                                       "roll_number": "R001",
                                                                       "teacher_id": "t1",
                                                                       "exam_id": "exam-1",
                                                                       "access_code": "",
                                                                   })
        assert resp.status_code == 200, resp.text[:200]
        assert resp.json().get("duration_minutes") == 90

    @patch("app.routers.exam.get_time_extension", new_callable=AsyncMock)
    def test_no_extension_returns_base_duration(self, mock_ext):
        mock_ext.return_value = 0
        with patch("app.routers.exam._resolve_teacher",
                   return_value=("t1", "exam-1")):
            with patch("app.routers.exam._load_exam_config",
                       return_value={"duration_minutes": 60}):
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
                                                resp = client.post("/api/v1/validate-student",
                                                                   json={
                                                                       "roll_number": "R001",
                                                                       "teacher_id": "t1",
                                                                       "exam_id": "exam-1",
                                                                       "access_code": "",
                                                                   })
        assert resp.status_code == 200, resp.text[:200]
        assert resp.json().get("duration_minutes") == 60


# ── submit handler: allowed_secs ─────────────────────────────────────


class TestSubmitAllowedSecs:
    @patch("app.routers.exam.get_time_extension", new_callable=AsyncMock)
    def test_submit_allowed_secs_includes_extension(self, mock_ext):
        mock_ext.return_value = 30
        token = self._make_token()
        with patch("app.routers.exam._recalculate_score",
                   return_value=(5, 10)):
            with patch("app.routers.exam._load_exam_config",
                       return_value={"duration_minutes": 60}):
                with patch("app.routers.exam.compute_risk_score",
                           new_callable=AsyncMock(
                               return_value={"risk_score": 30, "label": "Moderate"})):
                    with patch("app.routers.exam._atable") as atable_mock:
                        atable_mock.return_value.select.return_value.eq.return_value.execute = AsyncMock(
                            return_value=MagicMock(data=[{
                                "session_key": "R001_E1",
                                "status": "in_progress",
                                "started_at": "2025-06-01T10:00:00+00:00",
                                "full_name": "Alice",
                                "email": "a@b.com",
                                "score": 5,
                                "total": 10,
                                "percentage": 50.0,
                                "risk_score": 30,
                                "paused_secs_total": 0,
                            }])
                        )
                        atable_mock.return_value.upsert.return_value.execute = AsyncMock(
                            return_value=MagicMock(data=[])
                        )
                        atable_mock.return_value.update.return_value.eq.return_value.not_.in_.return_value.execute = AsyncMock(
                            return_value=MagicMock(data=[{"session_key": "R001_E1"}])
                        )
                        atable_mock.return_value.insert.return_value.execute = AsyncMock(
                            return_value=MagicMock(data=[])
                        )
                        resp = client.post("/api/v1/submit-exam",
                                           json={
                                               "session_id": "R001_E1",
                                               "roll_number": "R001",
                                               "full_name": "Alice",
                                               "email": "a@b.com",
                                               "time_taken_secs": 5400,
                                               "answers": {},
                                           },
                                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text[:200]
        assert resp.json().get("time_exceeded") is not True, "Should NOT exceed 60+30=90min"

    def _make_token(self):
        from .conftest import make_student_token
        return make_student_token(roll="R001")

    @patch("app.routers.exam.get_time_extension", new_callable=AsyncMock)
    def test_submit_no_extension_uses_base(self, mock_ext):
        """Without extension, a submit just past base+120s logs time_exceeded."""
        mock_ext.return_value = 0
        token = self._make_token()
        insert_calls = []
        def track_insert(data):
            insert_calls.append(data)
            result = MagicMock()
            result.execute = AsyncMock(return_value=MagicMock(data=[]))
            return result
        with patch("app.routers.exam._recalculate_score",
                   return_value=(5, 10)):
            with patch("app.routers.exam._load_exam_config",
                       return_value={"duration_minutes": 60}):
                with patch("app.routers.exam.compute_risk_score",
                           new_callable=AsyncMock(
                               return_value={"risk_score": 30, "label": "Moderate"})):
                    with patch("app.routers.exam._atable") as atable_mock:
                        atable_mock.return_value.select.return_value.eq.return_value.execute = AsyncMock(
                            return_value=MagicMock(data=[{
                                "session_key": "R001_E1",
                                "status": "in_progress",
                                "started_at": "2025-06-01T10:00:00+00:00",
                                "full_name": "Alice",
                                "email": "a@b.com",
                                "score": 5,
                                "total": 10,
                                "percentage": 50.0,
                                "risk_score": 30,
                                "paused_secs_total": 0,
                            }])
                        )
                        atable_mock.return_value.upsert.return_value.execute = AsyncMock(
                            return_value=MagicMock(data=[])
                        )
                        atable_mock.return_value.update.return_value.eq.return_value.not_.in_.return_value.execute = AsyncMock(
                            return_value=MagicMock(data=[{"session_key": "R001_E1"}])
                        )
                        atable_mock.return_value.insert.side_effect = track_insert
                        resp = client.post("/api/v1/submit-exam",
                                           json={
                                               "session_id": "R001_E1",
                                               "roll_number": "R001",
                                               "full_name": "Alice",
                                               "email": "a@b.com",
                                               "time_taken_secs": 3800,
                                               "answers": {},
                                           },
                                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text[:200]
        time_viols = [c for c in insert_calls
                      if isinstance(c, dict) and c.get("violation_type") == "time_exceeded"]
        assert time_viols, "Should log time_exceeded violation (past 60+2min)"


# ── endpoints: POST + GET ───────────────────────────────────────────


class TestTimeExtensionEndpoints:
    TEACHER = {"id": "t1", "email": "prof@test.com"}

    def _headers(self):
        from .conftest import make_admin_token
        token = make_admin_token(teacher_id="t1")
        return {"Authorization": f"Bearer {token}"}

    # ── POST ──────────────────────────────────────────────────────────

    @patch("app.routers.admin_exams.require_admin", new_callable=AsyncMock)
    def test_post_sets_extension(self, mock_admin):
        mock_admin.return_value = self.TEACHER
        with patch("app.services.admin_audit.log_admin_action", new_callable=AsyncMock):
            with patch("app.routers.admin_exams._atable") as atable_mock:
                atable_mock.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute = AsyncMock(
                    return_value=MagicMock(data=[{"exam_id": "exam-1"}])
                )
                atable_mock.return_value.upsert.return_value.execute = AsyncMock(
                    return_value=MagicMock(data=[])
                )
                resp = client.post("/api/v1/admin/exams/exam-1/time-extension",
                                   json={"roll_number": "R001", "extra_minutes": 30},
                                   headers=self._headers())
        assert resp.status_code == 200, resp.text[:200]
        d = resp.json()
        assert d.get("ok") is True
        assert d.get("roll_number") == "R001"
        assert d.get("extra_minutes") == 30

    @patch("app.routers.admin_exams.require_admin", new_callable=AsyncMock)
    def test_post_zero_clears_extension(self, mock_admin):
        mock_admin.return_value = self.TEACHER
        delete_mock = MagicMock()
        delete_mock.eq.return_value.eq.return_value.eq.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[])
        )
        def atable_side(table):
            if table == "exam_config":
                m = MagicMock()
                m.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute = AsyncMock(
                    return_value=MagicMock(data=[{"exam_id": "exam-1"}])
                )
                return m
            if table == "exam_time_extensions":
                m = MagicMock()
                m.delete.return_value = delete_mock
                return m
            return MagicMock()
        with patch("app.services.admin_audit.log_admin_action", new_callable=AsyncMock):
            with patch("app.routers.admin_exams._atable", side_effect=atable_side):
                resp = client.post("/api/v1/admin/exams/exam-1/time-extension",
                                   json={"roll_number": "R001", "extra_minutes": 0},
                                   headers=self._headers())
        assert resp.status_code == 200, resp.text[:200]
        d = resp.json()
        assert d.get("ok") is True

    @patch("app.routers.admin_exams.require_admin", new_callable=AsyncMock)
    def test_post_missing_exam_returns_404(self, mock_admin):
        mock_admin.return_value = self.TEACHER
        with patch("app.routers.admin_exams._atable") as atable_mock:
            atable_mock.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute = AsyncMock(
                return_value=MagicMock(data=[])
            )
            resp = client.post("/api/v1/admin/exams/exam-404/time-extension",
                               json={"roll_number": "R001", "extra_minutes": 30},
                               headers=self._headers())
        assert resp.status_code == 404

    @patch("app.routers.admin_exams.require_admin", new_callable=AsyncMock)
    def test_post_out_of_range_returns_400(self, mock_admin):
        mock_admin.return_value = self.TEACHER
        with patch("app.routers.admin_exams._atable"):
            resp = client.post("/api/v1/admin/exams/exam-1/time-extension",
                               json={"roll_number": "R001", "extra_minutes": 999},
                               headers=self._headers())
        assert resp.status_code == 400

    @patch("app.routers.admin_exams.require_admin", new_callable=AsyncMock)
    def test_post_missing_roll_returns_400(self, mock_admin):
        mock_admin.return_value = self.TEACHER
        with patch("app.routers.admin_exams._atable"):
            resp = client.post("/api/v1/admin/exams/exam-1/time-extension",
                               json={"roll_number": "", "extra_minutes": 30},
                               headers=self._headers())
        assert resp.status_code == 400

    @patch("app.routers.admin_exams.require_admin", new_callable=AsyncMock)
    def test_post_negative_returns_400(self, mock_admin):
        mock_admin.return_value = self.TEACHER
        with patch("app.routers.admin_exams._atable"):
            resp = client.post("/api/v1/admin/exams/exam-1/time-extension",
                               json={"roll_number": "R001", "extra_minutes": -5},
                               headers=self._headers())
        assert resp.status_code == 400

    # ── GET ───────────────────────────────────────────────────────────

    @patch("app.routers.admin_exams.require_admin", new_callable=AsyncMock)
    def test_get_returns_extensions(self, mock_admin):
        mock_admin.return_value = self.TEACHER
        with patch("app.routers.admin_exams._atable") as atable_mock:
            atable_mock.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(
                return_value=MagicMock(data=[
                    {"roll_number": "R001", "extra_minutes": 30},
                    {"roll_number": "R002", "extra_minutes": 15},
                ])
            )
            resp = client.get("/api/v1/admin/exams/exam-1/time-extensions",
                              headers=self._headers())
        assert resp.status_code == 200
        d = resp.json()
        assert d.get("R001") == 30
        assert d.get("R002") == 15

    @patch("app.routers.admin_exams.require_admin", new_callable=AsyncMock)
    def test_get_empty_returns_empty_dict(self, mock_admin):
        mock_admin.return_value = self.TEACHER
        with patch("app.routers.admin_exams._atable") as atable_mock:
            atable_mock.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(
                return_value=MagicMock(data=[])
            )
            resp = client.get("/api/v1/admin/exams/exam-1/time-extensions",
                              headers=self._headers())
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_get_requires_auth(self):
        resp = client.get("/api/v1/admin/exams/exam-1/time-extensions")
        assert resp.status_code == 401, f"expected 401 got {resp.status_code}"

    def test_post_requires_auth(self):
        resp = client.post("/api/v1/admin/exams/exam-1/time-extension",
                           json={"roll_number": "R001", "extra_minutes": 30})
        assert resp.status_code == 401, f"expected 401 got {resp.status_code}"
