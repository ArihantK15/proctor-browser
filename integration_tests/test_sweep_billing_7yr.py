"""7-year purge of billing_events and usage_records — storage-limitation ceiling.

Tests that ``sweep_transient_rows()`` (defined in phase86, extended in
phase101 and phase104) deletes financial records whose ``created_at``
is older than 7 years while preserving newer rows.

The conftest applies the real ``phase104_sweep_billing_7yr.sql``
migration on top of the fixture schema, so this validates production
DDL, not a copy.
"""
import uuid
from datetime import datetime, timezone, timedelta

import pytest

from app.database import async_table

pytestmark = pytest.mark.asyncio


async def _org() -> str:
    oid = str(uuid.uuid4())
    await async_table("organizations").insert({
        "id": oid, "name": f"Org {oid[:8]}", "max_students": 30,
    }).execute()
    return oid


async def _billing_event(org_id: str, created_at: str) -> str:
    eid = str(uuid.uuid4())
    await async_table("billing_events").insert({
        "id": eid, "org_id": org_id, "event_type": "payment",
        "amount": 1000, "currency": "INR", "status": "completed",
        "created_at": created_at,
    }).execute()
    return eid


async def _usage_record(org_id: str, created_at: str) -> str:
    uid = str(uuid.uuid4())
    await async_table("usage_records").insert({
        "id": uid, "org_id": org_id, "exam_attempts": 5,
        "overage": 0, "overage_amount": 0, "created_at": created_at,
    }).execute()
    return uid


async def _sweep() -> int:
    from app.postgres_table import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        return int(await conn.fetchval("SELECT public.sweep_transient_rows()") or 0)


async def test_sweep_removes_7year_old_billing_events():
    """A billing_event older than 7 years is deleted; a newer one survives."""
    oid = await _org()
    old = (datetime.now(timezone.utc) - timedelta(days=365 * 8)).isoformat()
    new = (datetime.now(timezone.utc) - timedelta(days=365 * 1)).isoformat()
    old_id = await _billing_event(oid, old)
    new_id = await _billing_event(oid, new)

    total = await _sweep()

    # At least 1 row swept (could be more if other tables have aged rows)
    assert total >= 1

    # Old row is gone
    old_rows = await async_table("billing_events").select("id")\
        .eq("id", old_id).execute()
    assert len(old_rows.data) == 0, "8-year-old billing_event survived sweep"

    # New row remains
    new_rows = await async_table("billing_events").select("id")\
        .eq("id", new_id).execute()
    assert len(new_rows.data) == 1, "1-year-old billing_event was wrongly deleted"


async def test_sweep_removes_7year_old_usage_records():
    """A usage_record older than 7 years is deleted; a newer one survives."""
    oid = await _org()
    old = (datetime.now(timezone.utc) - timedelta(days=365 * 8)).isoformat()
    new = (datetime.now(timezone.utc) - timedelta(days=365 * 1)).isoformat()
    old_id = await _usage_record(oid, old)
    new_id = await _usage_record(oid, new)

    total = await _sweep()

    assert total >= 1

    old_rows = await async_table("usage_records").select("id")\
        .eq("id", old_id).execute()
    assert len(old_rows.data) == 0, "8-year-old usage_record survived sweep"

    new_rows = await async_table("usage_records").select("id")\
        .eq("id", new_id).execute()
    assert len(new_rows.data) == 1, "1-year-old usage_record was wrongly deleted"
