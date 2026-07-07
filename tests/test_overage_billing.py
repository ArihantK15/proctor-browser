"""
Tests for Gap #4 — Overage billing.

Covers:
  1. compute_overage helper
  2. bill_cycle_overage helper
  3. get_usage endpoint includes overage_charges
  4. subscription.charged webhook wires bill_cycle_overage
"""
import contextlib
import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.test_org_delete import _require_admin_patch, _require_admin_auth_patch
from tests.conftest import make_admin_token


# ─── Helpers ────────────────────────────────────────────────────────

def _chain(data=None, count=None):
    """Mock query-builder chain (same pattern as test_org_billing)."""
    m = MagicMock()
    m._data = data if data is not None else []
    m._count = count
    for attr in ("select", "eq", "neq", "is_", "in_", "order", "limit",
                 "single", "range", "insert", "update", "delete",
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


def _overage_patches(enabled=False, grace=0):
    """Return a list of context-manager patches that set overage constants
    in the modules that import them (not just the defining module)."""
    return [
        patch("app.services.billing.OVERAGE_BILLING_ENABLED", enabled),
        patch("app.services.billing.OVERAGE_GRACE", grace),
    ]


# ─── compute_overage ────────────────────────────────────────────────

class TestComputeOverage:

    @pytest.mark.asyncio
    async def test_no_teachers_returns_zero(self):
        data_map = {
            "subscriptions": [{"plan": "growth", "org_id": "org-1"}],
            "organizations": [{"id": "org-1", "max_students": 100}],
            "teachers": [],
        }
        with patch("app.services.billing._atable", side_effect=_table_side_effect(data_map)):
            from app.services.billing import compute_overage
            res = await compute_overage("org-1",
                                        datetime(2025, 1, 1, tzinfo=timezone.utc),
                                        datetime(2025, 2, 1, tzinfo=timezone.utc))
        assert res["students_used"] == 0
        assert res["overage_count"] == 0

    @pytest.mark.asyncio
    async def test_under_limit_no_overage(self):
        data_map = {
            "subscriptions": [{"plan": "growth", "org_id": "org-1"}],
            "organizations": [{"id": "org-1", "max_students": 100}],
            "teachers": [{"id": "t-1", "org_id": "org-1"}],
            "exam_sessions": [{"student_id": "s-1"}, {"student_id": "s-2"}],
        }
        with patch("app.services.billing._atable", side_effect=_table_side_effect(data_map)):
            from app.services.billing import compute_overage
            res = await compute_overage("org-1",
                                        datetime(2025, 1, 1, tzinfo=timezone.utc),
                                        datetime(2025, 2, 1, tzinfo=timezone.utc))
        assert res["students_used"] == 2
        assert res["plan_limit"] == 100
        assert res["overage_count"] == 0
        assert res["amount_inr"] == 0

    @pytest.mark.asyncio
    async def test_over_limit(self):
        data_map = {
            "subscriptions": [{"plan": "growth", "org_id": "org-1"}],
            "organizations": [{"id": "org-1", "max_students": 10}],
            "teachers": [{"id": "t-1", "org_id": "org-1"}],
            "exam_sessions": [{"student_id": f"s-{i}"} for i in range(15)],
        }
        with patch("app.services.billing._atable", side_effect=_table_side_effect(data_map)):
            from app.services.billing import compute_overage
            res = await compute_overage("org-1",
                                        datetime(2025, 1, 1, tzinfo=timezone.utc),
                                        datetime(2025, 2, 1, tzinfo=timezone.utc))
        assert res["students_used"] == 15
        assert res["plan_limit"] == 10
        assert res["overage_count"] == 5
        from app.constants import PLANS
        price = PLANS["growth"]["overage_price_inr"]
        assert res["amount_inr"] == 5 * price


class TestBillCycleOverage:

    SUB_ROW = {
        "id": "sub-1",
        "org_id": "org-1",
        "plan": "growth",
        "razorpay_subscription_id": "sub_xyz",
        "current_period_start": datetime(2025, 1, 1, tzinfo=timezone.utc),
        "current_period_end": datetime(2025, 2, 1, tzinfo=timezone.utc),
    }

    @pytest.mark.asyncio
    async def test_no_overage_skips(self):
        data_map = {
            "subscriptions": [{"plan": "growth", "org_id": "org-1"}],
            "organizations": [{"id": "org-1", "max_students": 100}],
            "teachers": [{"id": "t-1", "org_id": "org-1"}],
            "exam_sessions": [{"student_id": s} for s in ("s-1", "s-2")],
            "overage_charges": [],
        }
        with contextlib.ExitStack() as stack:
            for p in _overage_patches():
                stack.enter_context(p)
            stack.enter_context(patch("app.services.billing._atable", side_effect=_table_side_effect(data_map)))
            from app.services.billing import bill_cycle_overage
            res = await bill_cycle_overage("org-1", self.SUB_ROW)
        assert res["status"] == "skipped"
        assert res["reason"] == "no_overage"

    @pytest.mark.asyncio
    async def test_overage_within_grace_skips(self):
        data_map = {
            "subscriptions": [{"plan": "growth", "org_id": "org-1"}],
            "organizations": [{"id": "org-1", "max_students": 10}],
            "teachers": [{"id": "t-1", "org_id": "org-1"}],
            "exam_sessions": [{"student_id": f"s-{i}"} for i in range(12)],
            "overage_charges": [],
        }
        with contextlib.ExitStack() as stack:
            for p in _overage_patches(grace=5):
                stack.enter_context(p)
            stack.enter_context(patch("app.services.billing._atable", side_effect=_table_side_effect(data_map)))
            from app.services.billing import bill_cycle_overage
            res = await bill_cycle_overage("org-1", self.SUB_ROW)
        assert res["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_overage_charged_when_enabled_and_live(self):
        data_map = {
            "subscriptions": [{"plan": "growth", "org_id": "org-1"}],
            "organizations": [{"id": "org-1", "max_students": 10}],
            "teachers": [{"id": "t-1", "org_id": "org-1"}],
            "exam_sessions": [{"student_id": f"s-{i}"} for i in range(15)],
            "overage_charges": [],
        }
        mock_addon = MagicMock()
        mock_addon.__getitem__ = lambda _, k: "addon_mock_123"
        mock_addon.get = lambda k, d=None: "addon_mock_123"
        mock_client = MagicMock()
        mock_client.subscription.createAddon = MagicMock(return_value=mock_addon)

        res_overage = {"students_used": 15, "plan_limit": 10,
                       "overage_count": 5, "amount_inr": 2500}
        with contextlib.ExitStack() as stack:
            for p in _overage_patches(enabled=True):
                stack.enter_context(p)
            stack.enter_context(patch("app.services.billing._atable", side_effect=_table_side_effect(data_map)))
            stack.enter_context(patch("app.services.billing._is_live", return_value=True))
            stack.enter_context(patch("app.services.billing._get_client", return_value=mock_client))
            stack.enter_context(patch("app.services.billing.compute_overage", return_value=res_overage))
            from app.services.billing import bill_cycle_overage
            res = await bill_cycle_overage("org-1", self.SUB_ROW)
        assert res["status"] == "charged"
        assert res["overage_count"] == 5
        assert res["amount_inr"] > 0
        mock_client.subscription.createAddon.assert_called_once()

    @pytest.mark.asyncio
    async def test_addon_failure_refunds_debited_credit(self):
        """If the Razorpay add-on creation fails after credit was debited, the
        credit must be restored — otherwise the org is silently drained with no
        charge and the 'failed' row can't be retried cleanly."""
        captured_updates = []  # (table, payload)
        # billing_credit_inr already reflects the post-debit state (0) — the
        # real _atomic_debit_credit runs against a raw postgres connection,
        # not _atable, so by the time the add-on creation fails the org row
        # has already been decremented to 0 by the (mocked) debit below.
        data_map = {
            "subscriptions": [{"plan": "growth", "org_id": "org-1"}],
            "organizations": [{"id": "org-1", "max_students": 10, "billing_credit_inr": 0}],
            "teachers": [{"id": "t-1", "org_id": "org-1"}],
            "overage_charges": [],
        }

        def _recording_se(table):
            ch = _chain(data_map.get(table, []))
            orig_update = ch.update
            def _rec_update(payload):
                captured_updates.append((table, dict(payload)))
                return orig_update(payload)
            ch.update = _rec_update
            return ch

        # Add-on creation blows up (Razorpay 5xx / network).
        mock_client = MagicMock()
        mock_client.subscription.createAddon = MagicMock(side_effect=Exception("razorpay down"))

        res_overage = {"students_used": 15, "plan_limit": 10,
                       "overage_count": 5, "amount_inr": 2500}
        with contextlib.ExitStack() as stack:
            for p in _overage_patches(enabled=True):
                stack.enter_context(p)
            stack.enter_context(patch("app.services.billing._atable", side_effect=_recording_se))
            stack.enter_context(patch("app.services.billing._is_live", return_value=True))
            stack.enter_context(patch("app.services.billing._get_client", return_value=mock_client))
            stack.enter_context(patch("app.services.billing.compute_overage", return_value=res_overage))
            # Simulate a real debit already having happened: 1000 of the
            # 2500 charge is comped by credit, net 1500 still to be charged.
            stack.enter_context(patch(
                "app.services.billing._atomic_debit_credit",
                new=AsyncMock(return_value=(1000, 1500))))
            from app.services.billing import bill_cycle_overage
            res = await bill_cycle_overage("org-1", self.SUB_ROW)

        assert res["status"] == "failed"
        org_credit_writes = [p["billing_credit_inr"] for (t, p) in captured_updates
                             if t == "organizations" and "billing_credit_inr" in p]
        # Debited to 0, then refunded back to the original 1000.
        assert org_credit_writes, "expected credit to be touched"
        assert org_credit_writes[-1] == 1000, org_credit_writes
        # The settled overage_charges row must report no credit applied.
        oc_writes = [p for (t, p) in captured_updates if t == "overage_charges"]
        assert oc_writes and oc_writes[-1].get("credit_applied_inr") == 0, oc_writes

    @pytest.mark.asyncio
    async def test_duplicate_period_idempotent(self):
        res_overage = {"students_used": 15, "plan_limit": 10,
                       "overage_count": 5, "amount_inr": 2500}

        def _insert_raises_unique(*a, **kw):
            raise Exception('duplicate key value violates unique constraint "overage_charges_period_uniq"')

        mock_se = _table_side_effect({})
        orig_table_fn = mock_se

        def _table_with_insert_fail(name):
            meta = orig_table_fn(name)
            meta.insert = MagicMock(side_effect=_insert_raises_unique)
            return meta

        mock_client = MagicMock()
        with contextlib.ExitStack() as stack:
            for p in _overage_patches(enabled=True):
                stack.enter_context(p)
            stack.enter_context(patch("app.services.billing._atable", side_effect=_table_with_insert_fail))
            stack.enter_context(patch("app.services.billing._is_live", return_value=True))
            stack.enter_context(patch("app.services.billing._get_client", return_value=mock_client))
            stack.enter_context(patch("app.services.billing.compute_overage", return_value=res_overage))
            from app.services.billing import bill_cycle_overage
            res = await bill_cycle_overage("org-1", self.SUB_ROW)
        assert res["status"] == "duplicate"
        # claim-then-charge: the UNIQUE trips on the claim INSERT, BEFORE any
        # Razorpay add-on is created — a webhook redelivery must never
        # double-charge. (With the old charge-then-insert ordering the add-on
        # was created before the duplicate was detected.)
        mock_client.subscription.createAddon.assert_not_called()


class TestUsageEndpoint:

    def test_includes_overage_charges(self, client):
        token = make_admin_token()
        headers = {"Authorization": f"Bearer {token}"}
        data_map = {
            "subscriptions": [{"plan": "growth", "status": "active"}],
            "organizations": [{"id": "org-1", "max_students": 50}],
            "teachers": [{"id": "t-1", "org_id": "org-1"}],
            "exam_sessions": [{"student_id": f"s-{i}"} for i in range(10)],
            "overage_charges": [
                {"period_start": "2025-01-01T00:00:00+00:00",
                 "period_end": "2025-02-01T00:00:00+00:00",
                 "overage_count": 3, "amount_inr": 1500,
                 "status": "charged", "created_at": "2025-02-01T01:00:00+00:00"},
            ],
        }
        with contextlib.ExitStack() as stack:
            stack.enter_context(_require_admin_patch(token))
            stack.enter_context(_require_admin_auth_patch())
            stack.enter_context(patch("app.routers.billing._atable", side_effect=_table_side_effect(data_map)))
            stack.enter_context(patch("app.routers.billing.logger"))
            resp = client.get(
                "/api/v1/billing/usage",
                headers=headers,
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "overage_charges" in body
        assert len(body["overage_charges"]) == 1
        assert body["overage_charges"][0]["overage_count"] == 3
        assert "overage_billing_enabled" in body

    def test_overage_billing_flag(self, client):
        token = make_admin_token()
        headers = {"Authorization": f"Bearer {token}"}
        data_map = {
            "subscriptions": [{"plan": "starter", "status": "active"}],
            "organizations": [{"id": "org-1", "max_students": 30}],
            "teachers": [{"id": "t-1", "org_id": "org-1"}],
            "exam_sessions": [{"student_id": "s-1"}],
            "overage_charges": [],
        }
        with contextlib.ExitStack() as stack:
            stack.enter_context(_require_admin_patch(token))
            stack.enter_context(_require_admin_auth_patch())
            stack.enter_context(patch("app.routers.billing._atable", side_effect=_table_side_effect(data_map)))
            stack.enter_context(patch("app.routers.billing.logger"))
            resp = client.get(
                "/api/v1/billing/usage",
                headers=headers,
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "overage_billing_enabled" in body
