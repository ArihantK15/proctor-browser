"""Billing entitlement + ledger — against REAL Postgres.

Covers the phase96 work: entitlement reconciliation as a projection of
subscription state, and the DB-durable webhook idempotency via the
billing_events UNIQUE constraint (the bit the mocked unit suite literally
cannot exercise — a MagicMock never enforces a UNIQUE).
"""
import pytest

from app.database import async_table
from app.services.billing import (
    reconcile_org_entitlement,
    record_billing_event,
    billing_event_seen,
)

pytestmark = pytest.mark.asyncio


async def _new_org(max_students: int = 30) -> str:
    row = (await async_table("organizations")
           .insert({"name": "Acme", "max_students": max_students}).execute()).data[0]
    return str(row["id"])


async def _set_sub(org_id: str, plan: str, status: str) -> None:
    await async_table("subscriptions").insert({
        "org_id": org_id, "plan": plan, "status": status,
        "razorpay_subscription_id": f"sub_{org_id[:8]}",
    }).execute()


async def test_phase96_migration_applied_on_top_of_fixture():
    # billing_events exists and is queryable (table created by phase96).
    assert (await async_table("billing_events").select("id").limit(1).execute()).data == []
    # organizations.gstin column added by phase96.
    org = (await async_table("organizations")
           .insert({"name": "GST Co", "gstin": "22AAAAA0000A1Z5"}).execute()).data[0]
    assert org.get("gstin") == "22AAAAA0000A1Z5"
    # subscriptions.past_due_since column added by phase96.
    oid = str(org["id"])
    await _set_sub(oid, "growth", "past_due")
    await async_table("subscriptions").update(
        {"past_due_since": "2026-06-10T00:00:00+00:00"}).eq("org_id", oid).execute()
    sub = (await async_table("subscriptions").select("past_due_since")
           .eq("org_id", oid).execute()).data[0]
    assert sub["past_due_since"] is not None


async def test_reconcile_grants_then_revokes():
    oid = await _new_org()
    await _set_sub(oid, "growth", "active")
    assert await reconcile_org_entitlement(oid) == 150          # plan cap granted
    persisted = (await async_table("organizations").select("max_students")
                 .eq("id", oid).execute()).data[0]
    assert persisted["max_students"] == 150                     # actually written

    # `created` = never authorised → must NOT entitle → free cap.
    await async_table("subscriptions").update({"status": "created"}).eq("org_id", oid).execute()
    assert await reconcile_org_entitlement(oid) == 30

    # cancelled → free cap.
    await async_table("subscriptions").update({"status": "cancelled"}).eq("org_id", oid).execute()
    assert await reconcile_org_entitlement(oid) == 30


async def test_reconcile_past_due_keeps_access():
    oid = await _new_org()
    await _set_sub(oid, "pro", "past_due")           # dunning grace window
    assert await reconcile_org_entitlement(oid) == 500


async def test_reconcile_no_subscription_is_free_cap():
    oid = await _new_org(max_students=999)           # stale/incorrect cap
    assert await reconcile_org_entitlement(oid) == 30  # reconciled down to free


async def test_billing_event_idempotency_uses_real_unique():
    oid = await _new_org()
    assert await billing_event_seen("evt_1") is False
    first = await record_billing_event(
        event_id="evt_1", org_id=oid, event_type="subscription.charged", status="grant")
    assert first is True
    assert await billing_event_seen("evt_1") is True

    # Second delivery of the same event.id must be a no-op against the real
    # UNIQUE(event_id) constraint — not a mock that always "succeeds".
    second = await record_billing_event(
        event_id="evt_1", org_id=oid, event_type="subscription.charged", status="grant")
    assert second is False
    rows = (await async_table("billing_events").select("id")
            .eq("event_id", "evt_1").execute()).data
    assert len(rows) == 1


async def test_billing_event_without_id_always_inserts():
    oid = await _new_org()
    a = await record_billing_event(event_id="", org_id=oid, event_type="x", status="ignored")
    b = await record_billing_event(event_id="", org_id=oid, event_type="x", status="ignored")
    assert a is True and b is True
    rows = (await async_table("billing_events").select("id").execute()).data
    assert len(rows) == 2
