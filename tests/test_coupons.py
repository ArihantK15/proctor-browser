"""Tests for Gap #35: Coupon codes (Razorpay Offers)."""

import os
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app


SUPERADMIN = {"id": "super-1", "email": "super@admin.com", "org_id": "org-1",
              "org_role": "superadmin", "full_name": "Super Admin"}


@pytest.fixture
def _mock_superadmin():
    with patch("app.routers.admin_coupons.require_admin", new_callable=AsyncMock) as m:
        m.return_value = SUPERADMIN
        yield m


# ── Helper: build a mock _atable that returns rows on .select("*").eq(…) ──

def _coupon_row(overrides=None):
    """Return a coupon dict with defaults for a valid coupon."""
    row = {
        "id": "c-1",
        "code": "save20",
        "razorpay_offer_id": "offer_JrLqXYZ",
        "description": "Save 20%",
        "max_redemptions": None,
        "times_redeemed": 0,
        "expires_at": None,
        "active": True,
        "created_by": "admin-1",
        "created_at": "2025-06-01T00:00:00Z",
    }
    if overrides:
        row.update(overrides)
    return row


def _mock_atable(coupon_data=None, insert_data=None):
    """Return a MagicMock _atable that serves one coupon on select.

    If *insert_data* is given, mt.insert().execute() returns it instead of
    coupon_data (handles the create-coupon flow where no select happens).
    """
    chain = MagicMock()
    sel_exec = AsyncMock(return_value=MagicMock(data=coupon_data or []))
    chain.execute = sel_exec
    chain.eq.return_value = chain
    chain.limit.return_value = chain
    chain.order.return_value = chain
    chain.range.return_value = chain
    chain.ilike = lambda col, val: chain

    mt = MagicMock()
    mt.select.return_value = chain
    mt.insert.return_value = chain
    mt.update.return_value = chain
    mt.update.return_value.eq.return_value = chain

    # Patch insert().execute() separately if insert_data is given.
    if insert_data is not None:
        ins_exec = AsyncMock(return_value=MagicMock(data=insert_data))
        # When insert() is called, return a mock whose execute returns insert_data
        ins_chain = MagicMock()
        ins_chain.execute = ins_exec
        mt.insert.return_value = ins_chain

    return mt


# ── /api/v1/billing/validate-coupon ─────────────────────────────

class TestValidateCouponEndpoint:
    PATH = "/api/v1/billing/validate-coupon"

    @pytest.mark.asyncio
    async def test_valid(self):
        mt = _mock_atable([_coupon_row()])
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            with patch("app.services.billing._atable", return_value=mt):
                r = await ac.get(self.PATH, params={"code": "save20"})
        assert r.status_code == 200
        d = r.json()
        assert d["valid"] is True
        assert d["description"] == "Save 20%"

    @pytest.mark.asyncio
    async def test_expired(self):
        mt = _mock_atable([_coupon_row({"expires_at": "2020-01-01T00:00:00Z"})])
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            with patch("app.services.billing._atable", return_value=mt):
                r = await ac.get(self.PATH, params={"code": "expired"})
        assert r.status_code == 200
        assert r.json()["valid"] is False

    @pytest.mark.asyncio
    async def test_inactive(self):
        mt = _mock_atable([_coupon_row({"active": False})])
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            with patch("app.services.billing._atable", return_value=mt):
                r = await ac.get(self.PATH, params={"code": "inactive"})
        assert r.status_code == 200
        assert r.json()["valid"] is False

    @pytest.mark.asyncio
    async def test_max_redemptions_exhausted(self):
        mt = _mock_atable([_coupon_row({"max_redemptions": 5, "times_redeemed": 5})])
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            with patch("app.services.billing._atable", return_value=mt):
                r = await ac.get(self.PATH, params={"code": "exhausted"})
        assert r.status_code == 200
        assert r.json()["valid"] is False

    @pytest.mark.asyncio
    async def test_not_found(self):
        mt = _mock_atable([])
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            with patch("app.services.billing._atable", return_value=mt):
                r = await ac.get(self.PATH, params={"code": "unknown"})
        assert r.status_code == 200
        assert r.json()["valid"] is False


# ── /api/v1/billing/create-subscription with coupon ────────────

class TestCreateSubscriptionWithCoupon:
    PATH = "/api/v1/billing/create-subscription"

    @pytest.mark.asyncio
    async def test_valid_coupon_passes_offer_id(self):
        """Valid coupon → subscription created, offer_id passed."""
        mt = _mock_atable([_coupon_row()])

        # We need to capture the insert body to verify coupon_code in notes.
        class _Capture:
            def __init__(self):
                self._table = None
                self._inserted = None
            def __call__(self, table):
                self._table = table
                return self
            def select(self, *a): return self
            def eq(self, *a): return self
            def limit(self, *a): return self
            def update(self, f):
                return self
            def insert(self, f):
                self._inserted = f
                return self
            async def execute(self):
                r = MagicMock()
                if self._table == "coupons":
                    # Return coupon data for validate + redemption read
                    r.data = [_coupon_row()]
                elif self._table == "subscriptions":
                    r.data = []
                else:
                    r.data = [{"id": "org-1", "gstin": None}]
                return r

        cap = _Capture()
        with patch("app.routers.billing.require_admin", new_callable=AsyncMock,
                   return_value=SUPERADMIN), \
             patch("app.routers.billing._atable", side_effect=cap), \
             patch("app.services.billing._atable", return_value=mt), \
             patch.dict(os.environ, {"RAZORPAY_SANDBOX_MODE": "1"}, clear=False):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.post(self.PATH, json={
                    "plan_id": "growth",
                    "coupon_code": "save20",
                })
            assert r.status_code == 200, r.text
            d = r.json()
            assert d.get("_is_sandbox")
            assert "coupon: save20" in d.get("_note", "")

    @pytest.mark.asyncio
    async def test_invalid_coupon_400(self):
        """Invalid coupon → 400."""
        mt = _mock_atable([])
        with patch("app.routers.billing.require_admin", new_callable=AsyncMock,
                   return_value=SUPERADMIN), \
             patch("app.services.billing._atable", return_value=mt), \
             patch.dict(os.environ, {"RAZORPAY_SANDBOX_MODE": "1"}, clear=False):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.post(self.PATH, json={
                    "plan_id": "growth",
                    "coupon_code": "invalid",
                })
        assert r.status_code == 400


# ── Coupon management — superadmin only ─────────────────────────

class TestAdminCoupons:
    @pytest.mark.asyncio
    async def test_create_superadmin(self, _mock_superadmin):
        mt = _mock_atable(insert_data=[{"id": "c-1"}])
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            with patch("app.routers.admin_coupons._atable", return_value=mt), \
                 patch("app.routers.admin_coupons.log_admin_action", AsyncMock()):
                r = await ac.post("/api/v1/admin/coupons", json={
                    "code": "NEWYEAR",
                    "razorpay_offer_id": "offer_abc123",
                    "description": "New Year sale",
                })
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_list_coupons(self, _mock_superadmin):
        mt = _mock_atable([_coupon_row()])
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            with patch("app.routers.admin_coupons._atable", return_value=mt):
                r = await ac.get("/api/v1/admin/coupons")
        assert r.status_code == 200
        assert len(r.json()["data"]) == 1

    @pytest.mark.asyncio
    async def test_non_superadmin_403(self):
        """Non-superadmin gets 403 on all admin coupon endpoints."""
        teacher = {"id": "t1", "email": "t@t.com", "org_role": "teacher"}
        with patch("app.routers.admin_coupons.require_admin", new_callable=AsyncMock,
                   return_value=teacher):
            async with AsyncClient(transport=ASGITransport(app=app),
                                   base_url="http://test") as ac:
                r = await ac.post("/api/v1/admin/coupons", json={
                    "code": "X", "razorpay_offer_id": "offer_x",
                })
            assert r.status_code == 403

            async with AsyncClient(transport=ASGITransport(app=app),
                                   base_url="http://test") as ac:
                r = await ac.get("/api/v1/admin/coupons")
            assert r.status_code == 403

            async with AsyncClient(transport=ASGITransport(app=app),
                                   base_url="http://test") as ac:
                r = await ac.patch("/api/v1/admin/coupons/c-1",
                                   json={"active": False})
            assert r.status_code == 403
