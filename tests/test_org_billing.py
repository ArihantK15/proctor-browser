"""Tests for org management and billing endpoints.

Covers:
  1. Org CRUD — GET /api/v1/org, PATCH /api/v1/org
  2. Member management — GET /api/v1/org/members, POST /api/v1/org/invite,
     DELETE /api/v1/org/members/{teacher_id}
  3. Billing — GET /api/v1/billing/plans, POST /api/v1/billing/create-checkout
  4. Stripe webhooks — checkout.session.completed, customer.subscription.updated,
     customer.subscription.deleted, invoice.payment_failed
  5. Superadmin — GET /api/v1/admin/all-orgs
"""
import contextlib
import json
import os
import sys
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.conftest import make_admin_token

# ── Shared stubs ──────────────────────────────────────────────────
TEACHER = {"id": "teacher-1", "email": "prof@test.com", "org_id": "org-1",
           "org_role": "admin", "full_name": "Prof T"}
NON_ADMIN = {"id": "teacher-2", "email": "staff@test.com", "org_id": "org-1",
             "org_role": "teacher", "full_name": "Staff"}
NO_ORG = {"id": "teacher-3", "email": "noorg@test.com", "org_id": None,
          "org_role": "teacher", "full_name": "No Org"}
SUPERADMIN = {"id": "super-1", "email": "super@admin.com", "org_id": "org-1",
              "org_role": "superadmin", "full_name": "Super Admin"}

ORG = {"id": "org-1", "name": "Test Org", "slug": "test-org",
       "max_students": 30, "created_at": "2025-01-01T00:00:00+00:00"}


def admin_headers():
    return {"Authorization": f"Bearer {make_admin_token(teacher_id='teacher-1', email='prof@test.com')}"}


def superadmin_headers():
    return {"Authorization": f"Bearer {make_admin_token(teacher_id='super-1', email='super@admin.com')}"}


# ── Fluent async chain ────────────────────────────────────────────
def _chain(data=None, count=None):
    m = MagicMock()
    m._data = data if data is not None else []
    m._count = count
    for attr in ("select", "eq", "neq", "is_", "in_", "order", "limit",
                 "single", "range", "insert", "update", "delete",
                 "gte", "lte", "gt", "lt", "like"):
        getattr(m, attr).return_value = m
    async def _execute():
        r = MagicMock()
        r.data = m._data
        r.count = m._count
        return r
    m.execute = _execute
    return m


def _data_map_side_effect(mapping):
    def side_effect(table):
        return _chain(mapping.get(table, []))
    return side_effect


# ── Patch helpers ─────────────────────────────────────────────────
def _admin_patch(teacher=TEACHER):
    return patch("app.auth.admin_auth._get_teacher_by_id", return_value=teacher)


def _apply_atable_patches(data_map):
    """Patch _atable in every module that imports it from dependencies."""
    se = _data_map_side_effect(data_map)
    return [
        patch("app.routers.admin_org._atable", side_effect=se),
        patch("app.routers.billing._atable", side_effect=se),
        patch("app.services.sessions._atable", side_effect=se),
    ]


# ═══════════════════════════════════════════════════════════════════
#  GET /api/v1/org
# ═══════════════════════════════════════════════════════════════════
class TestGetOrg:
    def test_happy_path(self, client):
        with _admin_patch(), contextlib.ExitStack() as es:
            for p in _apply_atable_patches({"organizations": [ORG]}):
                es.enter_context(p)
            resp = client.get("/api/v1/org", headers=admin_headers())
        assert resp.status_code == 200
        d = resp.json()
        assert d["name"] == "Test Org" and d["slug"] == "test-org"

    def test_no_org_403(self, client):
        with _admin_patch(NO_ORG):
            resp = client.get("/api/v1/org", headers=admin_headers())
        assert resp.status_code == 403

    def test_not_found_404(self, client):
        with _admin_patch(), contextlib.ExitStack() as es:
            for p in _apply_atable_patches({"organizations": []}):
                es.enter_context(p)
            resp = client.get("/api/v1/org", headers=admin_headers())
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════
#  GET /api/v1/org/members
# ═══════════════════════════════════════════════════════════════════
class TestListMembers:
    def test_happy_path(self, client):
        members = [
            {"id": "t1", "email": "a@t.com", "full_name": "A",
             "org_role": "admin", "created_at": "2025-01-01T00:00:00+00:00"},
            {"id": "t2", "email": "b@t.com", "full_name": "B",
             "org_role": "teacher", "created_at": "2025-01-02T00:00:00+00:00"},
        ]
        with _admin_patch(), contextlib.ExitStack() as es:
            for p in _apply_atable_patches({"teachers": members}):
                es.enter_context(p)
            resp = client.get("/api/v1/org/members", headers=admin_headers())
        assert resp.status_code == 200
        assert len(resp.json()["members"]) == 2

    def test_non_admin_403(self, client):
        with _admin_patch(NON_ADMIN):
            resp = client.get("/api/v1/org/members", headers=admin_headers())
        assert resp.status_code == 403

    def test_no_org_403(self, client):
        with _admin_patch(NO_ORG):
            resp = client.get("/api/v1/org/members", headers=admin_headers())
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════
#  POST /api/v1/org/invite
# ═══════════════════════════════════════════════════════════════════
class TestInviteOrgMember:
    def test_happy_path(self, client):
        data_map = {
            "teachers": [],
            "org_invites": [],
            "organizations": [{"name": "Test Org"}],
        }
        with _admin_patch(), \
             patch("app.routers.admin_org.enqueue_job") as enq, \
             contextlib.ExitStack() as es:
            for p in _apply_atable_patches(data_map):
                es.enter_context(p)
            resp = client.post("/api/v1/org/invite",
                               json={"email": "new@teacher.com", "full_name": "New Teacher"},
                               headers=admin_headers())
        assert resp.status_code == 200, resp.text
        assert resp.json()["ok"] is True
        enq.assert_called_once()

    def test_bad_email_400(self, client):
        with _admin_patch():
            resp = client.post("/api/v1/org/invite",
                               json={"email": "not-an-email", "full_name": "Bad"},
                               headers=admin_headers())
        assert resp.status_code == 400

    def test_already_member_409(self, client):
        data_map = {"teachers": [{"id": "existing", "email": "dup@t.com"}]}
        with _admin_patch(), contextlib.ExitStack() as es:
            for p in _apply_atable_patches(data_map):
                es.enter_context(p)
            resp = client.post("/api/v1/org/invite",
                               json={"email": "dup@t.com", "full_name": "Dup"},
                               headers=admin_headers())
        assert resp.status_code == 409

    def test_already_invited_409(self, client):
        def side_effect(table):
            if table == "teachers":
                return _chain([])
            if table == "org_invites":
                return _chain([{"id": "inv1", "status": "pending"}])
            return _chain([])
        with _admin_patch(), contextlib.ExitStack() as es:
            for p in [
                patch("app.routers.admin_org._atable", side_effect=side_effect),
                patch("app.routers.billing._atable", side_effect=side_effect),
            ]:
                es.enter_context(p)
            resp = client.post("/api/v1/org/invite",
                               json={"email": "invited@t.com", "full_name": "Invited"},
                               headers=admin_headers())
        assert resp.status_code == 409

    def test_non_admin_403(self, client):
        with _admin_patch(NON_ADMIN):
            resp = client.post("/api/v1/org/invite",
                               json={"email": "new@teacher.com", "full_name": "New"},
                               headers=admin_headers())
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════
#  DELETE /api/v1/org/members/{teacher_id}
# ═══════════════════════════════════════════════════════════════════
class TestRemoveMember:
    def test_happy_path(self, client):
        data_map = {"teachers": [{"id": "teacher-2", "org_id": "org-1", "org_role": "teacher"}]}
        with _admin_patch(), contextlib.ExitStack() as es:
            for p in _apply_atable_patches(data_map):
                es.enter_context(p)
            resp = client.delete("/api/v1/org/members/teacher-2", headers=admin_headers())
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_remove_self_400(self, client):
        with _admin_patch():
            resp = client.delete("/api/v1/org/members/teacher-1", headers=admin_headers())
        assert resp.status_code == 400

    def test_not_found_404(self, client):
        with _admin_patch(), contextlib.ExitStack() as es:
            for p in _apply_atable_patches({"teachers": []}):
                es.enter_context(p)
            resp = client.delete("/api/v1/org/members/unknown", headers=admin_headers())
        assert resp.status_code == 404

    def test_non_admin_403(self, client):
        with _admin_patch(NON_ADMIN):
            resp = client.delete("/api/v1/org/members/teacher-2", headers=admin_headers())
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════
#  PATCH /api/v1/org
# ═══════════════════════════════════════════════════════════════════
class TestUpdateOrg:
    def test_happy_path(self, client):
        with _admin_patch(), contextlib.ExitStack() as es:
            for p in _apply_atable_patches({"organizations": []}):
                es.enter_context(p)
            resp = client.patch("/api/v1/org",
                                json={"name": "Renamed Org"},
                                headers=admin_headers())
        assert resp.status_code == 200
        d = resp.json()
        assert d["name"] == "Renamed Org"
        assert d["slug"] == "renamed-org"

    def test_empty_name_400(self, client):
        with _admin_patch():
            resp = client.patch("/api/v1/org",
                                json={"name": ""},
                                headers=admin_headers())
        assert resp.status_code == 400

    def test_non_admin_403(self, client):
        with _admin_patch(NON_ADMIN):
            resp = client.patch("/api/v1/org",
                                json={"name": "Whatever"},
                                headers=admin_headers())
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════
#  GET /api/v1/org/billing
# ═══════════════════════════════════════════════════════════════════
class TestGetBilling:
    def test_happy_path_with_subscription(self, client):
        data_map = {
            "subscriptions": [{"plan": "growth", "status": "active",
                               "trial_end": None, "current_period_end": None}],
            "students": [],
        }
        with _admin_patch(), contextlib.ExitStack() as es:
            for p in _apply_atable_patches(data_map):
                es.enter_context(p)
            resp = client.get("/api/v1/org/billing", headers=admin_headers())
        assert resp.status_code == 200
        d = resp.json()
        assert d["plan"] == "growth"
        assert d["status"] == "active"
        assert d["max_students"] == 150

    def test_no_subscription_uses_starter_defaults(self, client):
        data_map = {"subscriptions": [], "students": []}
        with _admin_patch(), contextlib.ExitStack() as es:
            for p in _apply_atable_patches(data_map):
                es.enter_context(p)
            resp = client.get("/api/v1/org/billing", headers=admin_headers())
        assert resp.status_code == 200
        d = resp.json()
        assert d["plan"] == "starter"
        assert d["status"] == "unknown"
        assert d["max_students"] == 30

    def test_no_org_403(self, client):
        with _admin_patch(NO_ORG):
            resp = client.get("/api/v1/org/billing", headers=admin_headers())
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════
#  GET /api/v1/admin/all-orgs
# ═══════════════════════════════════════════════════════════════════
class TestListAllOrgs:
    def test_non_superadmin_403(self, client):
        with _admin_patch(TEACHER):
            resp = client.get("/api/v1/admin/all-orgs", headers=admin_headers())
        assert resp.status_code == 403

    def test_happy_path(self, client):
        def atable_side_effect(table):
            if table == "organizations":
                return _chain([ORG])
            if table == "subscriptions":
                return _chain([{"plan": "starter", "status": "active"}])
            if table == "students":
                return _chain([], count=10)
            if table == "teachers":
                return _chain([{"id": "t1"}, {"id": "t2"}])
            return _chain([])
        with _admin_patch(SUPERADMIN), \
             patch("app.auth.admin_auth.SUPER_ADMIN_EMAIL", "super@admin.com"), \
             patch("app.routers.admin_org.SUPER_ADMIN_EMAIL", "super@admin.com"), \
             contextlib.ExitStack() as es:
            for p in [
                patch("app.routers.admin_org._atable", side_effect=atable_side_effect),
                patch("app.routers.billing._atable", side_effect=atable_side_effect),
            ]:
                es.enter_context(p)
            resp = client.get("/api/v1/admin/all-orgs", headers=superadmin_headers())
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["orgs"]) == 1
        o = body["orgs"][0]
        assert o["name"] == "Test Org"
        assert o["student_count"] == 10
        assert o["teacher_count"] == 2


# ═══════════════════════════════════════════════════════════════════
#  GET /api/v1/billing/plans
# ═══════════════════════════════════════════════════════════════════
class TestListPlans:
    def test_returns_all_plans(self, client):
        resp = client.get("/api/v1/billing/plans")
        assert resp.status_code == 200
        plan_ids = {p["id"] for p in resp.json()["plans"]}
        assert plan_ids == {"starter", "growth", "pro", "enterprise"}

    def test_plan_has_required_fields(self, client):
        resp = client.get("/api/v1/billing/plans")
        for p in resp.json()["plans"]:
            assert "name" in p and "price_inr" in p
            assert "students" in p and "description" in p


# ═══════════════════════════════════════════════════════════════════
#  POST /api/v1/billing/create-checkout
# ═══════════════════════════════════════════════════════════════════
class TestCreateCheckout:
    MOCK_RESULT = {
        "session_id": "mock_ses_org-1",
        "url": "https://checkout.stripe.com/mock/org-1",
        "status": "created",
        "_sandbox": True,
        "_note": "Sandbox: ...",
    }

    def test_non_admin_403(self, client):
        with _admin_patch(NON_ADMIN):
            resp = client.post("/api/v1/billing/create-checkout",
                               json={"plan_id": "growth"}, headers=admin_headers())
        assert resp.status_code == 403

    def test_invalid_plan_400(self, client):
        with _admin_patch():
            resp = client.post("/api/v1/billing/create-checkout",
                               json={"plan_id": "nonexistent"}, headers=admin_headers())
        assert resp.status_code == 400

    def test_happy_path_sandbox(self, client):
        data_map = {"subscriptions": [], "organizations": []}
        with _admin_patch(), \
             patch("app.routers.billing.billing_create_checkout_session",
                   return_value=self.MOCK_RESULT), \
             contextlib.ExitStack() as es:
            for p in _apply_atable_patches(data_map):
                es.enter_context(p)
            resp = client.post("/api/v1/billing/create-checkout",
                               json={"plan_id": "growth"}, headers=admin_headers())
        assert resp.status_code == 200
        d = resp.json()
        assert d["session_id"] == "mock_ses_org-1"

    def test_updates_existing_subscription(self, client):
        data_map = {"subscriptions": [{"id": "sub_1", "org_id": "org-1"}], "organizations": []}
        with _admin_patch(), \
             patch("app.routers.billing.billing_create_checkout_session",
                   return_value=self.MOCK_RESULT), \
             contextlib.ExitStack() as es:
            for p in _apply_atable_patches(data_map):
                es.enter_context(p)
            resp = client.post("/api/v1/billing/create-checkout",
                               json={"plan_id": "growth"}, headers=admin_headers())
        assert resp.status_code == 200

    def test_value_error_400(self, client):
        with _admin_patch(), \
             patch("app.routers.billing.billing_create_checkout_session",
                   side_effect=ValueError("bad")):
            resp = client.post("/api/v1/billing/create-checkout",
                               json={"plan_id": "growth"}, headers=admin_headers())
        assert resp.status_code == 400

    def test_unexpected_error_500(self, client):
        with _admin_patch(), \
             patch("app.routers.billing.billing_create_checkout_session",
                   side_effect=Exception("boom")):
            resp = client.post("/api/v1/billing/create-checkout",
                               json={"plan_id": "growth"}, headers=admin_headers())
        assert resp.status_code == 500


# ═══════════════════════════════════════════════════════════════════
#  POST /api/v1/webhooks/stripe
# ═══════════════════════════════════════════════════════════════════
class TestStripeWebhook:
    SUB_PAYLOAD = {"object": {
        "id": "sub_abc123",
        "current_period_end": 1700086400,
    }}

    def _post(self, client, event_type, data=None, sig="test-sig"):
        body = json.dumps({
            "type": event_type,
            "data": {"object": data or self.SUB_PAYLOAD["object"]},
        }).encode()
        return client.post(
            "/api/v1/webhooks/stripe", content=body,
            headers={"Stripe-Signature": sig, "content-type": "application/json"},
        )

    def test_invalid_signature_400(self, client):
        with patch("app.routers.billing.verify_webhook", return_value=False):
            resp = self._post(client, "checkout.session.completed")
        assert resp.status_code == 400

    def test_invalid_json_400(self, client):
        with patch("app.routers.billing.verify_webhook", return_value=True):
            resp = client.post("/api/v1/webhooks/stripe",
                               content=b"not-json",
                               headers={"Stripe-Signature": "x"})
        assert resp.status_code == 400

    def test_checkout_completed_creates_subscription(self, client):
        data_map = {"subscriptions": [], "organizations": []}
        with patch("app.routers.billing.verify_webhook", return_value=True), \
             contextlib.ExitStack() as es:
            for p in _apply_atable_patches(data_map):
                es.enter_context(p)
            resp = self._post(client, "checkout.session.completed", {
                "id": "cs_test_123",
                "metadata": {"org_id": "org-1", "plan": "growth"},
                "subscription": "sub_abc123",
                "customer": "cus_test_123",
            })
        assert resp.status_code == 200

    def test_unknown_subscription_ignored(self, client):
        with patch("app.routers.billing.verify_webhook", return_value=True), \
             contextlib.ExitStack() as es:
            for p in _apply_atable_patches({"subscriptions": []}):
                es.enter_context(p)
            resp = self._post(client, "customer.subscription.updated")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"

    def test_subscription_updated_active(self, client):
        data_map = {"subscriptions": [{"id": "db_sub_1", "org_id": "org-1"}]}
        with patch("app.routers.billing.verify_webhook", return_value=True), \
             contextlib.ExitStack() as es:
            for p in _apply_atable_patches(data_map):
                es.enter_context(p)
            resp = self._post(client, "customer.subscription.updated", {
                "id": "sub_abc123", "status": "active",
            })
        assert resp.status_code == 200

    def test_subscription_deleted_downgrades_to_starter(self, client):
        data_map = {"subscriptions": [{"id": "db_sub_1", "org_id": "org-1"}], "organizations": []}
        with patch("app.routers.billing.verify_webhook", return_value=True), \
             contextlib.ExitStack() as es:
            for p in _apply_atable_patches(data_map):
                es.enter_context(p)
            resp = self._post(client, "customer.subscription.deleted", {
                "id": "sub_abc123", "status": "canceled",
            })
        assert resp.status_code == 200

    def test_payment_failed_logs_warning(self, client):
        data_map = {"subscriptions": [{"id": "db_sub_1", "org_id": "org-1"}]}
        with patch("app.routers.billing.verify_webhook", return_value=True), \
             contextlib.ExitStack() as es:
            for p in _apply_atable_patches(data_map):
                es.enter_context(p)
            resp = self._post(client, "invoice.payment_failed", {
                "id": "in_test", "subscription": "sub_abc123",
            })
        assert resp.status_code == 200
