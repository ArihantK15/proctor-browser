"""Unit tests for org support overrides (gap #13).

Covers:
  - reconcile_org_entitlement respects max_students_override
  - bill_cycle_overage applies billing_credit_inr
  - POST /admin/orgs/{org_id}/limit-override endpoint
  - POST /admin/orgs/{org_id}/credit endpoint
"""

import contextlib
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.billing import reconcile_org_entitlement, bill_cycle_overage


# ─── Shared mock helpers ───────────────────────────────────────

def _chain(data=None, count=None):
    m = MagicMock()
    m._data = data if data is not None else []
    m._count = count
    for attr in ("select", "eq", "neq", "is_", "in_", "order", "limit",
                 "single", "range", "insert", "upsert", "update", "delete",
                 "gte", "lte", "gt", "lt", "like", "distinct_on"):
        getattr(m, attr).return_value = m

    async def _execute():
        r = MagicMock()
        r.data = m._data
        r.count = m._count if m._count is not None else len(m._data)
        return r
    m.execute = _execute
    return m


def _table_side_effect(data_map):
    def se(table):
        return _chain(data_map.get(table, []))
    return se


def _atable_patches(data_map, *modules):
    se = _table_side_effect(data_map)
    return [patch(m + "._atable", side_effect=se) for m in modules]


# ─── Fixtures ──────────────────────────────────────────────────

ORG_ID = "org-1"
TEACHER = {"id": "t-super", "email": "super@test.com", "org_role": "superadmin"}
REGULAR_ADMIN = {"id": "t-admin", "email": "admin@test.com", "org_role": "admin"}

BASE_ORG = {"id": ORG_ID, "name": "Test Org", "max_students": 30}
SUBSCRIPTION_GROWTH = {
    "org_id": ORG_ID, "plan": "growth", "status": "active",
    "current_period_start": "2026-01-01T00:00:00", "current_period_end": "2026-02-01T00:00:00",
    "razorpay_subscription_id": "sub_123",
}


# ─── Reconcile respects override ────────────────────────────────

class TestReconcileOverride:
    """reconcile_org_entitlement: override overrides plan cap."""

    @pytest.mark.asyncio
    async def test_override_set_cap_from_override(self):
        """Override=50, plan_cap=30 → cap=50."""
        data_map = {
            "subscriptions": [dict(SUBSCRIPTION_GROWTH)],
            "organizations": [{"id": ORG_ID, "max_students_override": 50}],
        }
        with patch("app.database.async_table", side_effect=_table_side_effect(data_map)):
            cap = await reconcile_org_entitlement(ORG_ID)
        assert cap == 50

    @pytest.mark.asyncio
    async def test_override_null_uses_plan(self):
        """Override=None → cap=plan cap (150 for growth)."""
        data_map = {
            "subscriptions": [dict(SUBSCRIPTION_GROWTH)],
            "organizations": [{"id": ORG_ID, "max_students_override": None}],
        }
        with patch("app.database.async_table", side_effect=_table_side_effect(data_map)):
            cap = await reconcile_org_entitlement(ORG_ID)
        assert cap == 150

    @pytest.mark.asyncio
    async def test_override_non_entitled(self):
        """Override=50, status=none → cap=50 (override wins over FREE_CAP)."""
        sub = dict(SUBSCRIPTION_GROWTH)
        sub["status"] = "none"
        data_map = {
            "subscriptions": [sub],
            "organizations": [{"id": ORG_ID, "max_students_override": 50}],
        }
        with patch("app.database.async_table", side_effect=_table_side_effect(data_map)):
            cap = await reconcile_org_entitlement(ORG_ID)
        assert cap == 50

    @pytest.mark.asyncio
    async def test_override_null_non_entitled(self):
        """Override=None, status=none → cap=FREE_CAP (30)."""
        sub = dict(SUBSCRIPTION_GROWTH)
        sub["status"] = "none"
        data_map = {
            "subscriptions": [sub],
            "organizations": [{"id": ORG_ID, "max_students_override": None}],
        }
        with patch("app.database.async_table", side_effect=_table_side_effect(data_map)):
            cap = await reconcile_org_entitlement(ORG_ID)
        assert cap == 30


# ─── Credit consumption ─────────────────────────────────────────

class TestCreditConsumption:
    """bill_cycle_overage: billing_credit_inr offsets add-on amount."""

    SUB_BEFORE = {
        "razorpay_subscription_id": "sub_123",
        "current_period_start": "2026-01-01T00:00:00",
        "current_period_end": "2026-02-01T00:00:00",
    }
    OVERAGE = {"students_used": 50, "plan_limit": 30, "overage_count": 20, "amount_inr": 500}

    def _base_data_map(self, credit=0):
        return {
            "subscriptions": [{"plan": "growth", "org_id": ORG_ID,
                               "current_period_start": "2026-01-01T00:00:00",
                               "current_period_end": "2026-02-01T00:00:00"}],
            "organizations": [{"id": ORG_ID, "max_students": 30, "billing_credit_inr": credit}],
            "teachers": [{"id": "t1"}],
            "exam_sessions": [],
            "overage_charges": [],
        }

    @pytest.mark.asyncio
    async def test_credit_fully_comps(self):
        """Credit >= amount → net=0, status='comped'."""
        dm = self._base_data_map(credit=500)
        mock_client = MagicMock()
        with contextlib.ExitStack() as es:
            es.enter_context(patch("app.services.billing.OVERAGE_BILLING_ENABLED", True))
            es.enter_context(patch("app.services.billing.OVERAGE_GRACE", 0))
            es.enter_context(patch("app.services.billing._is_live", return_value=True))
            es.enter_context(patch("app.services.billing._get_client", return_value=mock_client))
            es.enter_context(patch("app.services.billing._atable", side_effect=_table_side_effect(dm)))
            es.enter_context(patch("app.services.billing.compute_overage",
                                   return_value={**self.OVERAGE, "amount_inr": 300}))
            result = await bill_cycle_overage(ORG_ID, self.SUB_BEFORE)
        assert result["status"] == "comped"
        assert result["amount_inr"] == 0
        mock_client.subscription.createAddon.assert_not_called()

    @pytest.mark.asyncio
    async def test_credit_partially_covers(self):
        """Credit < amount → add-on for net, credit balance→0."""
        dm = self._base_data_map(credit=200)
        mock_client = MagicMock()
        mock_client.subscription.createAddon.return_value = {"id": "addon_1"}
        with contextlib.ExitStack() as es:
            es.enter_context(patch("app.services.billing.OVERAGE_BILLING_ENABLED", True))
            es.enter_context(patch("app.services.billing.OVERAGE_GRACE", 0))
            es.enter_context(patch("app.services.billing._is_live", return_value=True))
            es.enter_context(patch("app.services.billing._get_client", return_value=mock_client))
            es.enter_context(patch("app.services.billing._atable", side_effect=_table_side_effect(dm)))
            es.enter_context(patch("app.services.billing.compute_overage",
                                   return_value=self.OVERAGE))
            result = await bill_cycle_overage(ORG_ID, self.SUB_BEFORE)
        assert result["status"] == "charged"
        assert result["amount_inr"] == 300  # net after 200 credit applied to 500
        # Add-on should be for net amount (300 INR = 30000 paise)
        call_kwargs = mock_client.subscription.createAddon.call_args
        assert call_kwargs is not None
        assert call_kwargs[0][1]["item"]["amount"] == 30000  # 300 * 100

    @pytest.mark.asyncio
    async def test_credit_no_credit_unchanged(self):
        """No credit → unchanged bill_cycle_overage (skipped because not live)."""
        dm = self._base_data_map(credit=0)
        with patch("app.services.billing._atable", side_effect=_table_side_effect(dm)), \
             patch("app.services.billing.compute_overage",
                   return_value=self.OVERAGE):
            result = await bill_cycle_overage(ORG_ID, self.SUB_BEFORE)
        assert result["status"] == "skipped"


# ─── Endpoint: limit-override ───────────────────────────────────

class TestLimitOverrideEndpoint:
    """POST /api/v1/admin/orgs/{org_id}/limit-override"""

    def _headers(self):
        return {"Authorization": "Bearer fake-token"}

    @pytest.mark.asyncio
    async def test_non_superadmin_returns_403(self):
        from app.main import app as _app
        from httpx import AsyncClient, ASGITransport
        with patch("app.routers.admin_org.require_admin", new_callable=AsyncMock, return_value=REGULAR_ADMIN):
            async with AsyncClient(transport=ASGITransport(app=_app), base_url="http://test") as ac:
                resp = await ac.post(f"/api/v1/admin/orgs/{ORG_ID}/limit-override",
                                     json={"max_students_override": 100},
                                     headers=self._headers())
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_no_reauth_returns_403(self):
        from app.main import app as _app
        from httpx import AsyncClient, ASGITransport
        with patch("app.routers.admin_org.require_admin", new_callable=AsyncMock, return_value=TEACHER), \
             patch("app.routers.admin_org.require_reauth_or_403",
                   side_effect=__import__("fastapi").HTTPException(403)):
            async with AsyncClient(transport=ASGITransport(app=_app), base_url="http://test") as ac:
                resp = await ac.post(f"/api/v1/admin/orgs/{ORG_ID}/limit-override",
                                     json={"max_students_override": 100},
                                     headers=self._headers())
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_unknown_org_returns_404(self):
        from app.main import app as _app
        from httpx import AsyncClient, ASGITransport
        with patch("app.routers.admin_org.require_admin", new_callable=AsyncMock, return_value=TEACHER), \
             patch("app.routers.admin_org.require_reauth_or_403", return_value=None), \
             patch("app.routers.admin_org._atable", side_effect=_table_side_effect({"organizations": []})):
            async with AsyncClient(transport=ASGITransport(app=_app), base_url="http://test") as ac:
                resp = await ac.post("/api/v1/admin/orgs/unknown/limit-override",
                                     json={"max_students_override": 100},
                                     headers=self._headers())
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_out_of_range_returns_400(self):
        from app.main import app as _app
        from httpx import AsyncClient, ASGITransport
        with patch("app.routers.admin_org.require_admin", new_callable=AsyncMock, return_value=TEACHER), \
             patch("app.routers.admin_org.require_reauth_or_403", return_value=None), \
             patch("app.routers.admin_org._atable", side_effect=_table_side_effect({"organizations": [BASE_ORG]})):
            async with AsyncClient(transport=ASGITransport(app=_app), base_url="http://test") as ac:
                resp = await ac.post(f"/api/v1/admin/orgs/{ORG_ID}/limit-override",
                                     json={"max_students_override": 200000},
                                     headers=self._headers())
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_sets_override_and_reconciles(self):
        from app.main import app as _app
        from httpx import AsyncClient, ASGITransport
        data_map = {
            "organizations": [BASE_ORG],
            "subscriptions": [dict(SUBSCRIPTION_GROWTH)],
        }
        with patch("app.routers.admin_org.require_admin", new_callable=AsyncMock, return_value=TEACHER), \
             patch("app.routers.admin_org.require_reauth_or_403", return_value=None), \
             patch("app.routers.admin_org._atable", side_effect=_table_side_effect(data_map)), \
             patch("app.services.billing._atable", side_effect=_table_side_effect(data_map)), \
             patch("app.services.admin_audit.log_admin_action", new_callable=AsyncMock):
            async with AsyncClient(transport=ASGITransport(app=_app), base_url="http://test") as ac:
                resp = await ac.post(f"/api/v1/admin/orgs/{ORG_ID}/limit-override",
                                     json={"max_students_override": 42},
                                     headers=self._headers())
        assert resp.status_code == 200, resp.text[:200]
        d = resp.json()
        assert d.get("ok") is True
        assert d.get("max_students_override") == 42
        assert d.get("effective_max_students") is not None

    @pytest.mark.asyncio
    async def test_clears_override(self):
        from app.main import app as _app
        from httpx import AsyncClient, ASGITransport
        data_map = {
            "organizations": [dict(BASE_ORG, max_students_override=50)],
            "subscriptions": [dict(SUBSCRIPTION_GROWTH)],
        }
        with patch("app.routers.admin_org.require_admin", new_callable=AsyncMock, return_value=TEACHER), \
             patch("app.routers.admin_org.require_reauth_or_403", return_value=None), \
             patch("app.routers.admin_org._atable", side_effect=_table_side_effect(data_map)), \
             patch("app.services.billing._atable", side_effect=_table_side_effect(data_map)), \
             patch("app.services.admin_audit.log_admin_action", new_callable=AsyncMock):
            async with AsyncClient(transport=ASGITransport(app=_app), base_url="http://test") as ac:
                resp = await ac.post(f"/api/v1/admin/orgs/{ORG_ID}/limit-override",
                                     json={"max_students_override": None},
                                     headers=self._headers())
        assert resp.status_code == 200, resp.text[:200]
        d = resp.json()
        assert d.get("ok") is True
        assert d.get("max_students_override") is None


# ─── Endpoint: credit ───────────────────────────────────────────

class TestCreditEndpoint:
    """POST /api/v1/admin/orgs/{org_id}/credit"""

    def _headers(self):
        return {"Authorization": "Bearer fake-token"}

    @pytest.mark.asyncio
    async def test_non_superadmin_returns_403(self):
        from app.main import app as _app
        from httpx import AsyncClient, ASGITransport
        with patch("app.routers.admin_org.require_admin", new_callable=AsyncMock, return_value=REGULAR_ADMIN):
            async with AsyncClient(transport=ASGITransport(app=_app), base_url="http://test") as ac:
                resp = await ac.post(f"/api/v1/admin/orgs/{ORG_ID}/credit",
                                     json={"amount_inr": 500, "reason": "Goodwill"},
                                     headers=self._headers())
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_no_reauth_returns_403(self):
        from app.main import app as _app
        from httpx import AsyncClient, ASGITransport
        with patch("app.routers.admin_org.require_admin", new_callable=AsyncMock, return_value=TEACHER), \
             patch("app.routers.admin_org.require_reauth_or_403",
                   side_effect=__import__("fastapi").HTTPException(403)):
            async with AsyncClient(transport=ASGITransport(app=_app), base_url="http://test") as ac:
                resp = await ac.post(f"/api/v1/admin/orgs/{ORG_ID}/credit",
                                     json={"amount_inr": 500, "reason": "Goodwill"},
                                     headers=self._headers())
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_unknown_org_returns_404(self):
        from app.main import app as _app
        from httpx import AsyncClient, ASGITransport
        with patch("app.routers.admin_org.require_admin", new_callable=AsyncMock, return_value=TEACHER), \
             patch("app.routers.admin_org.require_reauth_or_403", return_value=None), \
             patch("app.routers.admin_org._atable", side_effect=_table_side_effect({"organizations": []})):
            async with AsyncClient(transport=ASGITransport(app=_app), base_url="http://test") as ac:
                resp = await ac.post("/api/v1/admin/orgs/unknown/credit",
                                     json={"amount_inr": 500, "reason": "Goodwill"},
                                     headers=self._headers())
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_missing_reason_returns_400(self):
        from app.main import app as _app
        from httpx import AsyncClient, ASGITransport
        with patch("app.routers.admin_org.require_admin", new_callable=AsyncMock, return_value=TEACHER), \
             patch("app.routers.admin_org.require_reauth_or_403", return_value=None), \
             patch("app.routers.admin_org._atable",
                   side_effect=_table_side_effect({"organizations": [BASE_ORG]})):
            async with AsyncClient(transport=ASGITransport(app=_app), base_url="http://test") as ac:
                resp = await ac.post(f"/api/v1/admin/orgs/{ORG_ID}/credit",
                                     json={"amount_inr": 500, "reason": ""},
                                     headers=self._headers())
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_grants_credit(self):
        from app.main import app as _app
        from httpx import AsyncClient, ASGITransport
        with patch("app.routers.admin_org.require_admin", new_callable=AsyncMock, return_value=TEACHER), \
             patch("app.routers.admin_org.require_reauth_or_403", return_value=None), \
             patch("app.routers.admin_org._atable", side_effect=_table_side_effect(
                 {"organizations": [{"id": ORG_ID, "name": "Test Org",
                                     "billing_credit_inr": 0}]})), \
             patch("app.services.admin_audit.log_admin_action", new_callable=AsyncMock):
            async with AsyncClient(transport=ASGITransport(app=_app), base_url="http://test") as ac:
                resp = await ac.post(f"/api/v1/admin/orgs/{ORG_ID}/credit",
                                     json={"amount_inr": 1000, "reason": "Goodwill credit"},
                                     headers=self._headers())
        assert resp.status_code == 200, resp.text[:200]
        d = resp.json()
        assert d.get("ok") is True
        assert d.get("billing_credit_inr") == 1000
        assert d.get("delta") == 1000

    @pytest.mark.asyncio
    async def test_negative_credit_floors_at_0(self):
        from app.main import app as _app
        from httpx import AsyncClient, ASGITransport
        with patch("app.routers.admin_org.require_admin", new_callable=AsyncMock, return_value=TEACHER), \
             patch("app.routers.admin_org.require_reauth_or_403", return_value=None), \
             patch("app.routers.admin_org._atable", side_effect=_table_side_effect(
                 {"organizations": [{"id": ORG_ID, "name": "Test Org",
                                     "billing_credit_inr": 200}]})), \
             patch("app.services.admin_audit.log_admin_action", new_callable=AsyncMock):
            async with AsyncClient(transport=ASGITransport(app=_app), base_url="http://test") as ac:
                resp = await ac.post(f"/api/v1/admin/orgs/{ORG_ID}/credit",
                                     json={"amount_inr": -500, "reason": "Correction"},
                                     headers=self._headers())
        assert resp.status_code == 200, resp.text[:200]
        d = resp.json()
        assert d.get("ok") is True
        assert d.get("billing_credit_inr") == 0  # floors at 0
        assert d.get("delta") == -500
