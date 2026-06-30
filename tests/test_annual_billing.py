"""Tests for Gap #15: Annual billing option.

Tests cover:
  - razorpay_plan_key() reads annual env vars
  - create_subscription() uses annual plan key & sandbox note for annual
  - create-subscription endpoint validates billing_cycle
  - list_plans returns annual_price_inr + savings
  - billing_cycle is persisted on the subscriptions row
"""
import os
from unittest.mock import patch, AsyncMock, MagicMock



# ── Helpers (mirror patterns from test_billing_rebuild & test_org_delete) ──

def _chain(data=None):
    m = MagicMock()
    m._data = data if data is not None else []
    for a in ("select", "eq", "neq", "in_", "order", "limit", "insert", "update"):
        getattr(m, a).return_value = m
    async def _execute():
        r = MagicMock()
        r.data = m._data
        r.count = None
        return r
    m.execute = _execute
    return m


def _dm_side_effect(mapping):
    def se(table):
        return _chain(mapping.get(table, []))
    return se


# ── Service-layer: razorpay_plan_key ──────────────────────────────────────

class TestRazorpayPlanKey:
    def test_annual_reads_annual_env(self):
        plan_id = "growth"
        with patch.dict(os.environ, {"RAZORPAY_PLAN_GROWTH": "plan_monthly",
                                      "RAZORPAY_PLAN_GROWTH_ANNUAL": "plan_annual"}, clear=False):
            from app.services.billing import razorpay_plan_key
            assert razorpay_plan_key(plan_id, "monthly") == "plan_monthly"
            assert razorpay_plan_key(plan_id, "annual") == "plan_annual"

    def test_default_is_monthly(self):
        with patch.dict(os.environ, {"RAZORPAY_PLAN_STARTER": "plan_s"}, clear=False):
            from app.services.billing import razorpay_plan_key
            assert razorpay_plan_key("starter") == "plan_s"
            assert razorpay_plan_key("starter", "monthly") == "plan_s"

    def test_annual_missing_returns_none(self):
        with patch.dict(os.environ, {}, clear=False):
            from app.services.billing import razorpay_plan_key
            assert razorpay_plan_key("starter", "annual") is None


# ── Service-layer: create_subscription ───────────────────────────────────

class TestCreateSubscription:
    def test_annual_uses_annual_plan_id(self):
        """Sandbox annual should reference annual pricing in note."""
        from app.services.billing import create_subscription
        with patch.dict(os.environ, {"RAZORPAY_SANDBOX_MODE": "1"}, clear=False):
            result = create_subscription("org-1", "growth", billing_cycle="annual")
        assert result["billing_cycle"] == "annual"
        assert "120000" in result["_note"]
        assert "/ yr" in result["_note"]

    def test_monthly_uses_monthly_plan_id(self):
        """Sandbox monthly should reference monthly pricing in note."""
        from app.services.billing import create_subscription
        with patch.dict(os.environ, {"RAZORPAY_SANDBOX_MODE": "1"}, clear=False):
            result = create_subscription("org-1", "growth", billing_cycle="monthly")
        assert result["billing_cycle"] == "monthly"
        assert "12000" in result["_note"]
        assert "/ mo" in result["_note"]

    def test_default_billing_cycle_is_monthly(self):
        from app.services.billing import create_subscription
        with patch.dict(os.environ, {"RAZORPAY_SANDBOX_MODE": "1"}, clear=False):
            result = create_subscription("org-1", "starter")
        assert result["billing_cycle"] == "monthly"

    def test_starter_annual_pricing(self):
        from app.services.billing import create_subscription
        with patch.dict(os.environ, {"RAZORPAY_SANDBOX_MODE": "1"}, clear=False):
            result = create_subscription("org-1", "starter", billing_cycle="annual")
        assert result["billing_cycle"] == "annual"
        assert "24000" in result["_note"]
        assert "/ yr" in result["_note"]

    def test_pro_annual_pricing(self):
        from app.services.billing import create_subscription
        with patch.dict(os.environ, {"RAZORPAY_SANDBOX_MODE": "1"}, clear=False):
            result = create_subscription("org-1", "pro", billing_cycle="annual")
        assert result["billing_cycle"] == "annual"
        assert "300000" in result["_note"]
        assert "/ yr" in result["_note"]


# ── Endpoint: list_plans ─────────────────────────────────────────────────

class TestListPlans:
    def test_annual_fields_present(self, client):
        r = client.get("/api/v1/billing/plans")
        assert r.status_code == 200
        data = r.json()
        plans = {p["id"]: p for p in data["plans"]}
        # Paid tiers should have annual_price_inr and annual_savings_inr
        for pid in ("starter", "growth", "pro"):
            assert pid in plans
            p = plans[pid]
            assert "annual_price_inr" in p
            assert "annual_savings_inr" in p
            assert p["annual_price_inr"] > 0
            expected_savings = p["price_inr"] * 12 - p["annual_price_inr"]
            assert p["annual_savings_inr"] == expected_savings

    def test_enterprise_has_no_annual(self, client):
        r = client.get("/api/v1/billing/plans")
        data = r.json()
        plans = {p["id"]: p for p in data["plans"]}
        ep = plans.get("enterprise", {})
        assert ep.get("annual_price_inr") == 0
        assert ep.get("annual_savings_inr") == 0


# ── Endpoint: create-subscription (sandbox, with mocks) ──────────────────

SUPERADMIN = {"id": "super-1", "email": "super@admin.com", "org_id": "org-1",
              "org_role": "superadmin", "full_name": "Super Admin"}


class TestCreateSubscriptionEndpoint:
    def _post(self, client, body, headers=None):
        import os as _os
        from unittest.mock import patch as _patch
        with _patch.dict(_os.environ, {"RAZORPAY_SANDBOX_MODE": "1"}, clear=False):
            return client.post("/api/v1/billing/create-subscription",
                              json=body, headers=headers or {})

    def test_annual_stores_billing_cycle(self, client):
        """Annual subscription persists billing_cycle='annual' on the row."""
        data = {"plan_id": "growth", "billing_cycle": "annual"}
        inserted = {}
        sub_data_map = {"subscriptions": [], "organizations": [{"id": "org-1", "gstin": None}]}

        class _Capture:
            def __init__(self):
                self._table = None
            def __call__(self, table):
                self._table = table
                return self
            def select(self, *a): return self
            def eq(self, *a): return self
            def limit(self, *a): return self
            def update(self, f):
                inserted["upd"] = f
                return self
            def insert(self, f):
                inserted["ins"] = f
                return self
            async def execute(self):
                r = MagicMock()
                r.data = sub_data_map.get(self._table, [])
                return r

        cap = _Capture()
        with patch("app.routers.billing.require_admin", new_callable=AsyncMock,
                   return_value=SUPERADMIN), \
             patch("app.routers.billing._atable", side_effect=cap):
            r = self._post(client, data)
        assert r.status_code == 200, r.text
        # billing_cycle should be in the persisted data
        persisted = inserted.get("ins") or inserted.get("upd") or {}
        assert persisted.get("billing_cycle") == "annual"
        assert persisted.get("plan") == "growth"

    def test_invalid_cycle_400(self, client):
        with patch("app.routers.billing.require_admin", new_callable=AsyncMock,
                   return_value=SUPERADMIN):
            r = self._post(client, {"plan_id": "growth", "billing_cycle": "biennial"})
        assert r.status_code == 400

    def test_default_cycle_is_monthly(self, client):
        """No billing_cycle in body defaults to monthly."""
        data = {"plan_id": "starter"}
        inserted = {}
        sub_data_map = {"subscriptions": [], "organizations": [{"id": "org-1", "gstin": None}]}

        class _Capture:
            def __init__(self):
                self._table = None
            def __call__(self, table):
                self._table = table
                return self
            def select(self, *a): return self
            def eq(self, *a): return self
            def limit(self, *a): return self
            def update(self, f):
                inserted["upd"] = f
                return self
            def insert(self, f):
                inserted["ins"] = f
                return self
            async def execute(self):
                r = MagicMock()
                r.data = sub_data_map.get(self._table, [])
                return r

        cap = _Capture()
        with patch("app.routers.billing.require_admin", new_callable=AsyncMock,
                   return_value=SUPERADMIN), \
             patch("app.routers.billing._atable", side_effect=cap):
            r = self._post(client, data)
        assert r.status_code == 200, r.text
        persisted = inserted.get("ins") or inserted.get("upd") or {}
        assert persisted.get("billing_cycle") == "monthly"

    def test_annual_enterprise_400(self, client):
        """Enterprise + annual should be rejected."""
        with patch("app.routers.billing.require_admin", new_callable=AsyncMock,
                   return_value=SUPERADMIN):
            r = self._post(client, {"plan_id": "enterprise", "billing_cycle": "annual",
                                    "gstin": None})
        assert r.status_code == 400
        assert "Enterprise" in r.text

    def test_monthly_with_existing_subscription_409(self, client):
        """Existing entitling subscription should 409."""
        sub_data_map = {
            "subscriptions": [{"status": "active"}],
            "organizations": [{"id": "org-1", "gstin": None}],
        }
        with patch("app.routers.billing.require_admin", new_callable=AsyncMock,
                   return_value=SUPERADMIN), \
             patch("app.routers.billing._atable", side_effect=_dm_side_effect(sub_data_map)):
            r = self._post(client, {"plan_id": "starter"})
        assert r.status_code == 409

    def test_non_admin_403(self, client):
        """Regular teacher without admin role should get 403."""
        teacher = {"id": "t1", "email": "t@t.com", "org_id": "org-1",
                   "org_role": "teacher"}
        with patch("app.routers.billing.require_admin", new_callable=AsyncMock,
                   return_value=teacher):
            r = self._post(client, {"plan_id": "starter"})
        assert r.status_code == 403
