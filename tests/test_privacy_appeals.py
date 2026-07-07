"""Tests for privacy center and student appeals."""

from unittest.mock import patch, AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.auth.tokens import issue_reauth_token

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
        r = client.post("/api/v1/privacy/delete", headers=admin_headers,
                        json={"reauth_token": issue_reauth_token("teacher-1")})
        assert r.status_code == 200, f"Expected 200 got {r.status_code}: {r.text[:200]}"
        d = r.json()
        assert d.get("status") in ("deleted", "partial")

    def test_delete_student(self, student_headers, mock_student_account):
        r = client.post("/api/v1/privacy/delete", headers=student_headers,
                        json={"reauth_token": issue_reauth_token("student-1")})
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


class TestPrivacyConsentWithdrawal:
    def test_withdraw_requires_auth(self):
        r = client.post("/api/v1/privacy/consent/withdraw", json={
            "consent_type": "privacy_policy",
        })
        assert r.status_code == 401

    def test_withdraw_happy_path_teacher(self, admin_headers, mock_teacher):
        # Record then withdraw — the mock chain accepts all calls.
        r = client.post("/api/v1/privacy/consent", json={
            "consent_type": "privacy_policy",
        }, headers=admin_headers)
        assert r.status_code == 200

        r = client.post("/api/v1/privacy/consent/withdraw", json={
            "consent_type": "privacy_policy",
        }, headers=admin_headers)
        assert r.status_code == 200, f"Expected 200 got {r.status_code}: {r.text[:200]}"
        d = r.json()
        assert d.get("status") == "withdrawn"
        assert d.get("consent_type") == "privacy_policy"

    def test_withdraw_happy_path_student(self, student_headers, mock_student_account):
        r = client.post("/api/v1/privacy/consent", json={
            "consent_type": "phone_camera",
        }, headers=student_headers)
        assert r.status_code == 200

        r = client.post("/api/v1/privacy/consent/withdraw", json={
            "consent_type": "phone_camera",
        }, headers=student_headers)
        assert r.status_code == 200
        assert r.json().get("status") == "withdrawn"


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
        session_row = MagicMock(data=[{"student_id": "student-1", "email": "alice@test.com"}])
        no_appeals = MagicMock(data=[])  # no prior pending appeal for this session

        def mock_atable(table_name):
            m = MagicMock()
            m.select.return_value = m
            m.eq.return_value = m
            m.limit.return_value = m
            m.insert.return_value = m
            if table_name == "appeals":
                m.execute = AsyncMock(return_value=no_appeals)
            else:  # exam_sessions ownership lookup
                m.execute = AsyncMock(return_value=session_row)
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

    def test_duplicate_pending_appeal_rejected(self, student_headers, mock_student_account):
        """A second appeal for the same target while one is still pending → 409.

        Guards against a double-click / double-submit spamming the teacher with
        identical pending rows. The rate limiter (5/hour) is a coarse backstop;
        this is the precise one."""
        session_row = MagicMock(data=[{"student_id": "student-1", "email": "alice@test.com"}])
        # A session-level pending appeal already exists (violation_id null).
        existing = MagicMock(data=[{"id": "appeal-1", "violation_id": None}])

        def mock_atable(table_name):
            m = MagicMock()
            m.select.return_value = m
            m.eq.return_value = m
            m.limit.return_value = m
            m.insert.return_value = m
            if table_name == "appeals":
                m.execute = AsyncMock(return_value=existing)
            else:
                m.execute = AsyncMock(return_value=session_row)
            return m

        with patch("app.routers.appeals._atable", side_effect=mock_atable):
            r = client.post("/api/v1/student/appeal", json={
                "session_key": "owned_session",
                "appeal_type": "grade",
                "description": "I want to dispute my grade",
            }, headers=student_headers)
            assert r.status_code == 409, f"Expected 409 got {r.status_code}: {r.text[:200]}"

    def test_appeal_for_different_flag_allowed(self, student_headers, mock_student_account):
        """An existing pending appeal for ONE flag must not block appealing a
        DIFFERENT flag on the same session — distinct disputes may coexist."""
        session_row = MagicMock(data=[{"student_id": "student-1", "email": "alice@test.com",
                                       "teacher_id": "teacher-1", "exam_id": "exam-1"}])
        # Pending appeal exists for violation 101; the student now disputes 102.
        other_flag_pending = MagicMock(data=[{"id": "appeal-1", "violation_id": "101"}])
        flag_exists = MagicMock(data=[{"id": "102"}])

        def mock_atable(table_name):
            m = MagicMock()
            m.select.return_value = m
            m.eq.return_value = m
            m.limit.return_value = m
            m.insert.return_value = m
            if table_name == "appeals":
                m.execute = AsyncMock(return_value=other_flag_pending)
            elif table_name == "violations":
                m.execute = AsyncMock(return_value=flag_exists)
            else:
                m.execute = AsyncMock(return_value=session_row)
            return m

        with patch("app.routers.appeals._atable", side_effect=mock_atable):
            r = client.post("/api/v1/student/appeal", json={
                "session_key": "owned_session",
                "appeal_type": "violation",
                "description": "disputing a different flag",
                "violation_id": "102",
            }, headers=student_headers)
            assert r.status_code == 200, f"Expected 200 got {r.status_code}: {r.text[:200]}"

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


class TestFlagLinkedAppeals:
    """Phase 94 — flag-linked appeals + remediation hook."""

    def test_appeal_with_unknown_flag_404(self, student_headers, mock_student_account):
        """Disputing a numeric violation_id that isn't on this session → 404.
        (violation ids are BIGINT since phase147.)"""
        session_row = MagicMock(data=[{"student_id": "student-1", "email": "alice@test.com",
                                       "teacher_id": "teacher-1", "exam_id": "exam-1"}])
        empty = MagicMock(data=[])

        def mock_atable(table_name):
            m = MagicMock()
            for attr in ("select", "eq", "limit", "insert"):
                getattr(m, attr).return_value = m
            if table_name == "exam_sessions":
                m.execute = AsyncMock(return_value=session_row)
            else:  # violations lookup returns nothing
                m.execute = AsyncMock(return_value=empty)
            return m

        with patch("app.routers.appeals._atable", side_effect=mock_atable):
            r = client.post("/api/v1/student/appeal", json={
                "session_key": "owned_session",
                "appeal_type": "violation",
                "description": "dispute this specific flag",
                "violation_id": "999999",
            }, headers=student_headers)
            assert r.status_code == 404, r.text

    def test_flag_specific_appeal_succeeds_and_inserts_int(self, student_headers, mock_student_account):
        """Regression for PYTHON-W: a flag-specific appeal with a numeric
        violation_id (sent as a STRING by the client, per student-app.js) now
        submits cleanly and stores an INT violation_id — previously 500'd
        because appeals.violation_id was uuid vs violations.id bigint."""
        session_row = MagicMock(data=[{"student_id": "student-1", "email": "alice@test.com",
                                       "teacher_id": "teacher-1", "exam_id": "exam-1"}])
        violation_hit = MagicMock(data=[{"id": 52010}])
        captured = {}

        def mock_atable(table_name):
            m = MagicMock()
            for attr in ("select", "eq", "limit"):
                getattr(m, attr).return_value = m

            def _insert(row):
                captured["row"] = row
                return m
            m.insert.side_effect = _insert
            if table_name == "exam_sessions":
                m.execute = AsyncMock(return_value=session_row)
            elif table_name == "violations":
                m.execute = AsyncMock(return_value=violation_hit)
            else:  # appeals insert
                m.execute = AsyncMock(return_value=MagicMock(data=[{"id": "ap-new"}]))
            return m

        with patch("app.routers.appeals._atable", side_effect=mock_atable):
            r = client.post("/api/v1/student/appeal", json={
                "session_key": "owned_session",
                "appeal_type": "violation",
                "description": "dispute this specific flag",
                "violation_id": "52010",  # client sends a string
            }, headers=student_headers)
            assert r.status_code == 200, r.text
            # The stored violation_id must be the coerced INT, not the raw string.
            assert captured["row"]["violation_id"] == 52010
            assert isinstance(captured["row"]["violation_id"], int)

    def test_duplicate_pending_appeal_returns_409_not_500(self, student_headers, mock_student_account):
        """uq_appeals_session_student_type_pending (phase88) makes a second
        pending appeal of the same type on the same session a DB-level unique
        violation. That must surface as a 409 the student can understand,
        not the generic 500 the bare `except Exception` used to raise
        (flagged in PR #72 review; the underlying race was already closed by
        phase88's index, but the graceful-handling gap was real)."""
        session_row = MagicMock(data=[{"student_id": "student-1", "email": "alice@test.com",
                                       "teacher_id": "teacher-1", "exam_id": "exam-1"}])

        def mock_atable(table_name):
            m = MagicMock()
            for attr in ("select", "eq", "limit", "insert"):
                getattr(m, attr).return_value = m
            if table_name == "exam_sessions":
                m.execute = AsyncMock(return_value=session_row)
            else:  # appeals insert hits the unique index
                m.execute = AsyncMock(side_effect=Exception(
                    'duplicate key value violates unique constraint '
                    '"uq_appeals_session_student_type_pending"'))
            return m

        with patch("app.routers.appeals._atable", side_effect=mock_atable):
            r = client.post("/api/v1/student/appeal", json={
                "session_key": "owned_session",
                "appeal_type": "grade",
                "description": "I want to dispute this grade again",
            }, headers=student_headers)
            assert r.status_code == 409, r.text
            assert "pending appeal" in r.json()["detail"].lower()


class TestAppealInModel:
    """AppealIn.violation_id coercion (str→int) — see phase147 / PYTHON-W."""

    def test_numeric_string_coerces_to_int(self):
        from app.routers.appeals import AppealIn
        m = AppealIn(session_key="s", appeal_type="violation", description="d",
                     violation_id="52010")  # type: ignore[arg-type]
        assert m.violation_id == 52010 and isinstance(m.violation_id, int)

    def test_int_passes_through(self):
        from app.routers.appeals import AppealIn
        m = AppealIn(session_key="s", appeal_type="grade", description="d", violation_id=7)
        assert m.violation_id == 7

    def test_none_and_empty_become_none(self):
        from app.routers.appeals import AppealIn
        assert AppealIn(session_key="s", appeal_type="other", description="d").violation_id is None
        assert AppealIn(session_key="s", appeal_type="other", description="d",
                        violation_id="").violation_id is None  # type: ignore[arg-type]

    def test_non_numeric_rejected(self):
        import pytest as _pytest
        from pydantic import ValidationError
        from app.routers.appeals import AppealIn
        with _pytest.raises(ValidationError):
            AppealIn(session_key="s", appeal_type="violation", description="d",
                     violation_id="viol-abc")  # type: ignore[arg-type]

    def test_resolve_accept_dismisses_flag_and_audits(self, admin_headers, mock_teacher):
        """Accepting a flag-linked appeal dismisses the flag, recomputes risk,
        records resolution + an audit row, and returns the new score."""
        appeal_row = MagicMock(data=[{
            "id": "appeal-1", "teacher_id": "teacher-1", "violation_id": 1,
            "session_key": "sess-1", "status": "pending",
        }])
        seen = {"violations_update": 0}

        def mock_atable(table_name):
            m = MagicMock()
            for attr in ("select", "eq", "limit", "update", "insert", "is_"):
                getattr(m, attr).return_value = m
            if table_name == "appeals":
                m.execute = AsyncMock(return_value=appeal_row)
            elif table_name == "violations":
                async def _exec(*a, **k):
                    seen["violations_update"] += 1
                    return MagicMock(data=[{"id": "viol-1"}])
                m.execute = AsyncMock(side_effect=_exec)
            else:
                m.execute = AsyncMock(return_value=MagicMock(data=[]))
            return m

        # NOTE: patch compute_risk_score where it is USED — appeals.py imports
        # it at module top (`from ..services.risk import compute_risk_score`),
        # so the name is bound into the appeals namespace and patching
        # app.services.risk.* would not intercept the call. log_admin_action is
        # imported locally inside resolve_appeal, so patching it on its own
        # module still works.
        with patch("app.routers.appeals._atable", side_effect=mock_atable), \
             patch("app.routers.appeals.compute_risk_score", new_callable=AsyncMock) as mock_risk, \
             patch("app.services.admin_audit.log_admin_action", new_callable=AsyncMock) as mock_audit:
            mock_risk.return_value = {"risk_score": 12, "label": "Low Risk"}
            r = client.post("/api/v1/admin/appeals/appeal-1/resolve",
                            json={"status": "accepted", "teacher_note": "legit reason"},
                            headers=admin_headers)
            assert r.status_code == 200, r.text
            d = r.json()
            assert d["resolution"] == "flag_dismissed"
            assert d["risk_score"] == 12
            assert seen["violations_update"] >= 1
            mock_audit.assert_awaited_once()

    def test_resolve_reject_leaves_flag_untouched(self, admin_headers, mock_teacher):
        """Rejecting (or a session-level appeal) does no remediation."""
        appeal_row = MagicMock(data=[{
            "id": "appeal-2", "teacher_id": "teacher-1", "violation_id": 9,
            "session_key": "sess-2", "status": "pending",
        }])
        seen = {"violations_update": 0}

        def mock_atable(table_name):
            m = MagicMock()
            for attr in ("select", "eq", "limit", "update", "insert", "is_"):
                getattr(m, attr).return_value = m
            if table_name == "appeals":
                m.execute = AsyncMock(return_value=appeal_row)
            elif table_name == "violations":
                async def _exec(*a, **k):
                    seen["violations_update"] += 1
                    return MagicMock(data=[])
                m.execute = AsyncMock(side_effect=_exec)
            else:
                m.execute = AsyncMock(return_value=MagicMock(data=[]))
            return m

        with patch("app.routers.appeals._atable", side_effect=mock_atable), \
             patch("app.services.admin_audit.log_admin_action", new_callable=AsyncMock):
            r = client.post("/api/v1/admin/appeals/appeal-2/resolve",
                            json={"status": "rejected", "teacher_note": "not valid"},
                            headers=admin_headers)
            assert r.status_code == 200, r.text
            d = r.json()
            assert d.get("resolution") is None
            assert "risk_score" not in d
            assert seen["violations_update"] == 0

    def test_resolve_cross_tenant_appeal_404_and_no_dismiss(self, admin_headers, mock_teacher):
        """A teacher must not be able to resolve another teacher's appeal or
        dismiss another teacher's flag. The appeal lookup is
        .eq('id',appeal_id).eq('teacher_id',tid)-scoped, so a non-owned appeal
        returns no rows — modelled here by an empty appeals result (exactly what
        the teacher_id filter yields for someone else's appeal). The endpoint
        must 404 and NEVER touch the violations table."""
        seen = {"violations_update": 0}

        def mock_atable(table_name):
            m = MagicMock()
            for attr in ("select", "eq", "limit", "update", "insert", "is_"):
                getattr(m, attr).return_value = m
            if table_name == "appeals":
                # teacher-scoped lookup for an appeal that isn't ours → empty
                m.execute = AsyncMock(return_value=MagicMock(data=[]))
            elif table_name == "violations":
                async def _exec(*a, **k):
                    seen["violations_update"] += 1
                    return MagicMock(data=[{"id": "viol-1"}])
                m.execute = AsyncMock(side_effect=_exec)
            else:
                m.execute = AsyncMock(return_value=MagicMock(data=[]))
            return m

        with patch("app.routers.appeals._atable", side_effect=mock_atable), \
             patch("app.routers.appeals.compute_risk_score", new_callable=AsyncMock) as mock_risk:
            mock_risk.return_value = {"risk_score": 99, "label": "High Risk"}
            r = client.post("/api/v1/admin/appeals/appeal-1/resolve",
                            json={"status": "accepted", "teacher_note": "not mine"},
                            headers=admin_headers)
            assert r.status_code == 404, r.text
            assert seen["violations_update"] == 0, \
                "a non-owned flag must never be dismissed"


class TestTeacherAppeals:
    def test_list_appeals_requires_auth(self):
        r = client.get("/api/v1/admin/appeals")
        assert r.status_code == 401

    def test_list_appeals(self, admin_headers, mock_teacher):
        r = client.get("/api/v1/admin/appeals", headers=admin_headers)
        assert r.status_code == 200
        d = r.json()
        assert "appeals" in d

    def test_list_appeals_attaches_flag_evidence(self, admin_headers, mock_teacher):
        """A flag-linked appeal carries the disputed flag's pre-violation context
        strip + primary frame as auth-gated screenshot URLs (same matchers + URL
        shape as the forensics timeline) so the teacher adjudicates inline."""
        from pathlib import Path

        violation = {"id": 1, "violation_type": "phone_in_hand",
                     "created_at": "2026-01-01T12:00:05+00:00"}
        appeal = {"id": "ap1", "violation_id": 1, "roll_number": "ALICE001",
                  "teacher_id": "teacher-1", "session_key": "ALICE001_sess",
                  "exam_id": "exam-1", "appeal_type": "violation",
                  "description": "I dropped a pen", "status": "pending",
                  "created_at": "2026-01-01T12:01:00+00:00"}
        # _collect_session_screenshots returns {fname: Path(.../fname)}, so each
        # Path's .name equals its key — mirror that (the code builds URLs from
        # the matched Path's .name).
        _names = [
            "ctx_phone_in_hand_20260101_120002.jpg",  # t-3
            "ctx_phone_in_hand_20260101_120003.jpg",  # t-2
            "ctx_phone_in_hand_20260101_120004.jpg",  # t-1
            "evt_phone_in_hand_20260101_120005.jpg",  # flag (primary)
        ]
        screenshots = {n: Path(n) for n in _names}

        class _Q:
            def __init__(self, data): self._data = data
            def __getattr__(self, _name):
                def _chain(*a, **k): return self
                return _chain
            async def execute(self):
                return MagicMock(data=self._data)

        def mock_atable(table):
            if table == "appeals": return _Q([appeal])
            if table == "violations": return _Q([violation])
            return _Q([])

        with patch("app.routers.appeals._atable", side_effect=mock_atable), \
             patch("app.routers.appeals._collect_session_screenshots", return_value=screenshots):
            r = client.get("/api/v1/admin/appeals", headers=admin_headers)

        assert r.status_code == 200, r.text
        appeals = r.json()["appeals"]
        assert len(appeals) == 1
        a = appeals[0]
        base = "/api/v1/admin/screenshot/ALICE001/"
        sid = "?session_id=ALICE001_sess"
        # Context strip: 3 frames, oldest-first, auth-gated URL shape.
        assert a["evidence_context"] == [
            f"{base}ctx_phone_in_hand_20260101_120002.jpg{sid}",
            f"{base}ctx_phone_in_hand_20260101_120003.jpg{sid}",
            f"{base}ctx_phone_in_hand_20260101_120004.jpg{sid}",
        ]
        assert a["evidence_primary"] == f"{base}evt_phone_in_hand_20260101_120005.jpg{sid}"

    def test_list_appeals_sessionlevel_appeal_gets_no_evidence(self, admin_headers, mock_teacher):
        """A session-level appeal (no violation_id) gets no inline evidence and
        triggers NO screenshot scan — bounded, no wasted filesystem work."""
        appeal = {"id": "ap2", "violation_id": None, "roll_number": "ALICE001",
                  "teacher_id": "teacher-1", "session_key": "ALICE001_x",
                  "appeal_type": "grade", "status": "pending"}

        class _Q:
            def __init__(self, data): self._data = data
            def __getattr__(self, _name):
                def _chain(*a, **k): return self
                return _chain
            async def execute(self):
                return MagicMock(data=self._data)

        def mock_atable(table):
            return _Q([appeal] if table == "appeals" else [])

        with patch("app.routers.appeals._atable", side_effect=mock_atable), \
             patch("app.routers.appeals._collect_session_screenshots") as scan:
            r = client.get("/api/v1/admin/appeals", headers=admin_headers)

        assert r.status_code == 200
        a = r.json()["appeals"][0]
        assert "evidence_context" not in a and "evidence_primary" not in a
        scan.assert_not_called()


class TestExamSessionsStudentId:
    def test_submit_sets_student_id(self, student_headers, mock_student_account):
        """Submit-exam with a student token should set student_id on the session."""
        import uuid
        # Use a token that includes sid claim
        import jwt as _pyjwt
        from app.constants import STUDENT_SIGNING_KEY
        _sid = "student-1"
        _token = _pyjwt.encode({
            "sid": _sid, "roll": "ALICE001", "role": "student_account",
            "exp": 9999999999, "iat": 1700000000,
        }, STUDENT_SIGNING_KEY, algorithm="HS256")
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
                    not_m = MagicMock()
                    not_m.in_.return_value = m
                    m.not_ = not_m
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
