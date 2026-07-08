"""Subscription-status gate on exam/question CREATION (not just the
student-seat-limit path check_org_limits already covered).

Forensic audit finding (2026-07-08): a cancelled/expired/halted/paused org
could previously create unlimited new exams and questions forever — only
adding students was ever gated. require_active_subscription() closes that
gap. These tests prove both directions: a lapsed org is actually blocked,
and an active/trialing org is NOT falsely blocked.
"""
from unittest.mock import patch

from tests.conftest import make_admin_token, shared_supabase_mock

TEACHER = {"id": "teacher-1", "email": "prof@test.com", "full_name": "Prof T", "org_id": "org-1"}


def _table_side_effect(mapping):
    from unittest.mock import MagicMock

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


CANCELLED_EXPIRED_SUB = [{
    "id": "sub-1", "org_id": "org-1", "plan": "growth", "status": "cancelled",
    "trial_end": None,
    "current_period_start": "2025-01-01T00:00:00+00:00",
    "current_period_end": "2025-02-01T00:00:00+00:00",  # long past
    "razorpay_subscription_id": "rzp_1", "scheduled_plan": None,
    "scheduled_plan_effective_at": None,
}]

ACTIVE_SUB = [{
    "id": "sub-1", "org_id": "org-1", "plan": "growth", "status": "active",
    "trial_end": None,
    "current_period_start": "2026-07-01T00:00:00+00:00",
    "current_period_end": "2026-08-01T00:00:00+00:00",
    "razorpay_subscription_id": "rzp_1", "scheduled_plan": None,
    "scheduled_plan_effective_at": None,
}]


def _headers():
    return {"Authorization": f"Bearer {make_admin_token()}"}


class TestCreateExamBillingGate:
    def test_cancelled_and_expired_org_cannot_create_exam(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "subscriptions": CANCELLED_EXPIRED_SUB,
        })):
            resp = client.post("/api/v1/admin/exams",
                               json={"exam_title": "X", "duration_minutes": 60, "phone_camera": False},
                               headers=_headers())
        assert resp.status_code == 403
        assert "expired" in resp.text.lower()

    def test_active_org_can_create_exam(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "subscriptions": ACTIVE_SUB,
            "exam_config": [{"exam_id": "new-1", "access_code": "ZZZZZZ"}],
        })):
            resp = client.post("/api/v1/admin/exams",
                               json={"exam_title": "X", "duration_minutes": 60, "phone_camera": False},
                               headers=_headers())
        assert resp.status_code == 200, resp.text

    def test_no_subscription_row_allows_creation(self, client):
        # A brand-new org with no subscriptions row yet is within the
        # implicit trial window — must not be blocked.
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_config": [{"exam_id": "new-1", "access_code": "ZZZZZZ"}],
        })):
            resp = client.post("/api/v1/admin/exams",
                               json={"exam_title": "X", "duration_minutes": 60, "phone_camera": False},
                               headers=_headers())
        assert resp.status_code == 200, resp.text


class TestQuestionBankBillingGate:
    def test_cancelled_org_cannot_add_bank_questions(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "subscriptions": CANCELLED_EXPIRED_SUB,
        })):
            resp = client.post("/api/v1/admin/question-bank",
                               json={"questions": [{"question": "Q?", "options": {"A": "1"}, "correct": "A"}]},
                               headers=_headers())
        assert resp.status_code == 403
        assert "expired" in resp.text.lower()

    def test_active_org_can_add_bank_questions(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "subscriptions": ACTIVE_SUB,
            "question_bank": [{"id": "new1", "question": "Q?", "question_type": "mcq_single",
                               "options": {"A": "1"}, "correct": "A"}],
        })):
            resp = client.post("/api/v1/admin/question-bank",
                               json={"questions": [{"question": "Q?", "options": {"A": "1"}, "correct": "A"}]},
                               headers=_headers())
        assert resp.status_code == 200, resp.text


class TestBillingGateDoesNotBlockAnalyticsOrDownloads:
    """The gate is scoped to CREATION only — viewing existing content
    (analytics, results) must never call require_active_subscription."""

    def test_session_analytics_endpoint_has_no_subscription_gate(self):
        import inspect
        from app.routers import admin_exams
        src = inspect.getsource(admin_exams)
        # Sanity: the gate exists in this module (create/duplicate) but the
        # read-only listing/analytics endpoints must not import/call it
        # beyond the two authoring functions we intentionally gated.
        assert src.count("await require_active_subscription(") == 2, (
            "expected exactly create_exam + duplicate_exam to call the gate; "
            "an unexpected count means a read endpoint got gated (or a "
            "write endpoint lost its gate)"
        )
