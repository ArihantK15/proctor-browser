"""Razorpay subscription webhook (HTTP wrapper) — against REAL Postgres.

reconcile_org_entitlement is already covered in isolation by
test_billing_integration.py. This file covers the HTTP entry point on top of it:
HMAC signature verification, event routing, the DB-durable idempotency
short-circuit (billing_events.event_id UNIQUE), and the unknown-subscription
retry path. It drives the real razorpay_webhook(request) with a genuinely-signed
body, so the whole chain — verify → route → update subscription → reconcile
max_students → append ledger — runs against real rows and the real UNIQUE
constraint.
"""
import hashlib
import hmac
import json
import os
import uuid

import pytest
import pytest_asyncio
from starlette.requests import Request

from app.database import async_table
from app.routers import billing as billing_mod
from app.routers.billing import razorpay_webhook
from app.limiter import limiter

pytestmark = pytest.mark.asyncio

# Set by the integration harness (conftest os.environ.setdefault).
_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "whsec_test")
FREE_CAP = 30
PRO_CAP = 500


@pytest_asyncio.fixture(autouse=True)
async def _small_pool(monkeypatch):
    from app.postgres_table import close_pool
    monkeypatch.setenv("POSTGRES_POOL_MIN", "1")
    monkeypatch.setenv("POSTGRES_POOL_MAX", "10")
    await close_pool()
    yield
    await close_pool()


def _event(event_id: str, event_type: str, sub_id: str) -> dict:
    return {
        "id": event_id,
        "event": event_type,
        "payload": {"subscription": {"entity": {
            "id": sub_id,
            "current_start": 1_700_000_000,
            "current_end": 1_702_000_000,
            "amount": 30000,
        }}},
    }


def _request(event: dict, *, bad_sig: bool = False) -> Request:
    body = json.dumps(event).encode()
    sig = "deadbeef" if bad_sig else hmac.new(_SECRET.encode(), body, hashlib.sha256).hexdigest()
    scope = {
        "type": "http", "method": "POST", "path": "/api/v1/webhooks/razorpay",
        "query_string": b"", "headers": [
            (b"x-razorpay-signature", sig.encode()),
            (b"content-type", b"application/json"),
        ],
    }
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}
    return Request(scope, receive)


async def _call(event: dict, *, bad_sig: bool = False):
    prev = limiter.enabled
    limiter.enabled = False
    try:
        return await razorpay_webhook(_request(event, bad_sig=bad_sig))
    finally:
        limiter.enabled = prev


async def _org(cap: int) -> str:
    oid = str(uuid.uuid4())
    await async_table("organizations").insert({
        "id": oid, "name": f"Org {oid[:8]}", "max_students": cap}).execute()
    return oid


async def _sub(org_id: str, sub_id: str, plan: str, status: str):
    await async_table("subscriptions").insert({
        "org_id": org_id, "razorpay_subscription_id": sub_id,
        "plan": plan, "status": status}).execute()


async def _max_students(org_id: str) -> int:
    from app.postgres_table import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT max_students FROM organizations WHERE id = $1::uuid", org_id)


async def _ledger_count(event_id: str) -> int:
    rows = (await async_table("billing_events").select("id")
            .eq("event_id", event_id).execute()).data or []
    return len(rows)


async def _sub_status(sub_id: str) -> str:
    rows = (await async_table("subscriptions").select("status")
            .eq("razorpay_subscription_id", sub_id).execute()).data or []
    return rows[0]["status"] if rows else ""


async def test_invalid_signature_is_rejected_and_changes_nothing():
    org = await _org(FREE_CAP)
    await _sub(org, "sub_badsig", "pro", "created")
    with pytest.raises(Exception) as exc:
        await _call(_event("evt_bad", "subscription.activated", "sub_badsig"), bad_sig=True)
    assert getattr(exc.value, "status_code", None) == 400
    assert await _max_students(org) == FREE_CAP      # untouched
    assert await _sub_status("sub_badsig") == "created"
    assert await _ledger_count("evt_bad") == 0       # nothing recorded


async def test_grant_activates_entitlement_and_records_ledger():
    org = await _org(FREE_CAP)
    await _sub(org, "sub_grant", "pro", "created")

    resp = await _call(_event("evt_grant", "subscription.activated", "sub_grant"))
    assert resp["status"] == "ok"
    assert await _sub_status("sub_grant") == "active"
    assert await _max_students(org) == PRO_CAP        # reconciled up to the plan cap
    assert await _ledger_count("evt_grant") == 1


async def test_duplicate_event_short_circuits_and_does_not_reprocess():
    org = await _org(FREE_CAP)
    await _sub(org, "sub_dup", "pro", "created")
    first = await _call(_event("evt_dup", "subscription.activated", "sub_dup"))
    assert first["status"] == "ok"
    assert await _max_students(org) == PRO_CAP

    # Redeliver the SAME event.id — must short-circuit before any side effect.
    second = await _call(_event("evt_dup", "subscription.activated", "sub_dup"))
    assert second["status"] == "duplicate"
    assert await _ledger_count("evt_dup") == 1        # still one ledger row
    assert await _max_students(org) == PRO_CAP


async def test_cancel_downgrades_to_free_cap():
    org = await _org(PRO_CAP)
    await _sub(org, "sub_cancel", "pro", "active")

    resp = await _call(_event("evt_cancel", "subscription.cancelled", "sub_cancel"))
    assert resp["status"] == "ok"
    assert await _sub_status("sub_cancel") == "cancelled"
    assert await _max_students(org) == FREE_CAP        # entitlement revoked
    assert await _ledger_count("evt_cancel") == 1


async def test_unknown_subscription_grant_retries_and_is_not_recorded():
    # A grant for a sub we don't have yet (webhook outran our create-sub commit)
    # must NOT be recorded — returning a retryable 500 lets the redelivery
    # reprocess once the row exists. Recording it would dedup the activation away
    # forever, leaving the org paid-but-unentitled.
    with pytest.raises(Exception) as exc:
        await _call(_event("evt_unknown", "subscription.activated", "sub_does_not_exist"))
    assert getattr(exc.value, "status_code", None) == 500
    assert await _ledger_count("evt_unknown") == 0     # deliberately not recorded
