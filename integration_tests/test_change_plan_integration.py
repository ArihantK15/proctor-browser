"""Mid-cycle plan change (gap #1) — REAL Postgres.

Covers the parts that need a live DB:
  • upgrade flips the local plan and reconcile_org_entitlement raises the cap;
  • downgrade stores scheduled_plan WITHOUT touching the live plan/cap;
  • the subscription.charged webhook APPLIES a due scheduled downgrade and the
    reconcile lowers the cap — the §3 apply step + the #1↔#4 seam.
Razorpay is mocked; the DB transitions are what we assert.
"""
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from starlette.requests import Request

from app.database import async_table
from app.routers import billing as billing_mod
from app.routers.billing import change_plan, razorpay_webhook
from app.limiter import limiter

pytestmark = pytest.mark.asyncio

ADMIN_TID = str(uuid.uuid4())


@pytest_asyncio.fixture(autouse=True)
async def _small_pool(monkeypatch):
    from app.postgres_table import close_pool
    monkeypatch.setenv("POSTGRES_POOL_MIN", "1")
    monkeypatch.setenv("POSTGRES_POOL_MAX", "10")
    await close_pool()
    yield
    await close_pool()


async def _org(cap=30) -> str:
    oid = str(uuid.uuid4())
    await async_table("organizations").insert({"id": oid, "name": "O", "max_students": cap}).execute()
    # the admin caller must belong to the org for change_plan's org scoping
    await async_table("teachers").insert({
        "id": ADMIN_TID, "org_id": oid, "org_role": "admin", "status": "active",
        "email": f"{ADMIN_TID[:8]}@x.test"}).execute()
    return oid


async def _sub(org_id, plan, *, status="active", rzp="sub_real_1",
               scheduled_plan=None, scheduled_effective=None,
               start=None, end=None):
    now = datetime.now(timezone.utc)
    await async_table("subscriptions").insert({
        "org_id": org_id, "plan": plan, "status": status,
        "razorpay_subscription_id": rzp,
        "current_period_start": (start or now - timedelta(days=15)).isoformat(),
        "current_period_end": (end or now + timedelta(days=15)).isoformat(),
        "scheduled_plan": scheduled_plan,
        "scheduled_plan_effective_at": scheduled_effective.isoformat() if scheduled_effective else None,
    }).execute()


def _req(body):
    raw = json.dumps(body).encode()
    scope = {"type": "http", "method": "POST", "path": "/x", "query_string": b"",
             "headers": [(b"content-type", b"application/json")]}
    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}
    return Request(scope, receive)


async def _change_plan(org_id, plan_id, client):
    admin = {"id": ADMIN_TID, "org_id": org_id, "org_role": "admin"}
    prev = limiter.enabled
    limiter.enabled = False
    try:
        with patch.object(billing_mod, "require_admin", AsyncMock(return_value=admin)), \
             patch.object(billing_mod, "require_reauth_or_403", lambda *a, **k: None), \
             patch.object(billing_mod, "_get_client", return_value=client):
            return await change_plan(_req({"plan_id": plan_id}))
    finally:
        limiter.enabled = prev


async def _sub_row(org_id):
    r = (await async_table("subscriptions").select("plan,scheduled_plan,scheduled_plan_effective_at")
         .eq("org_id", org_id).limit(1).execute()).data
    return r[0] if r else None


async def _cap(org_id):
    r = (await async_table("organizations").select("max_students").eq("id", org_id).limit(1).execute()).data
    return r[0]["max_students"] if r else None


def _mock_client():
    c = MagicMock()
    c.subscription.update = MagicMock(return_value={})
    c.subscription.createAddon = MagicMock(return_value={"id": "addon_1"})
    return c


# ── upgrade ───────────────────────────────────────────────────────────────

async def test_upgrade_flips_plan_and_raises_cap():
    org = await _org(cap=30)
    await _sub(org, "starter")
    client = _mock_client()
    resp = await _change_plan(org, "growth", client)
    assert resp["ok"] is True and resp["plan_id"] == "growth"
    assert resp["proration_inr"] > 0          # ~half-cycle of (12000-2400)
    assert (await _sub_row(org))["plan"] == "growth"
    assert await _cap(org) == 150              # reconcile raised it immediately
    client.subscription.createAddon.assert_called_once()


# ── downgrade is deferred ───────────────────────────────────────────────────

async def test_downgrade_schedules_without_touching_cap():
    org = await _org(cap=150)
    await _sub(org, "growth")
    client = _mock_client()
    resp = await _change_plan(org, "starter", client)
    assert resp["ok"] is True
    row = await _sub_row(org)
    assert row["plan"] == "growth"             # live plan unchanged
    assert row["scheduled_plan"] == "starter"  # pending
    assert await _cap(org) == 150              # cap NOT dropped yet
    client.subscription.createAddon.assert_not_called()


# ── webhook applies the scheduled downgrade (§3 + #1↔#4 seam) ───────────────

def _signed_charged(monkeypatch, rzp):
    secret = "test-webhook-secret"
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", secret)
    event = {"event": "subscription.charged", "id": f"evt_{uuid.uuid4().hex[:10]}",
             "payload": {"subscription": {"entity": {
                 "id": rzp,
                 "current_start": int(datetime(2025, 3, 1, tzinfo=timezone.utc).timestamp()),
                 "current_end": int(datetime(2025, 4, 1, tzinfo=timezone.utc).timestamp())}}}}
    body = json.dumps(event).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    scope = {"type": "http", "method": "POST", "path": "/api/v1/webhooks/razorpay",
             "query_string": b"",
             "headers": [(b"x-razorpay-signature", sig.encode()),
                         (b"content-type", b"application/json")]}
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}
    return Request(scope, receive)


async def test_charged_webhook_applies_due_downgrade_and_drops_cap(monkeypatch):
    org = await _org(cap=150)
    past = datetime.now(timezone.utc) - timedelta(days=1)
    await _sub(org, "growth", rzp="sub_wh_1", scheduled_plan="starter", scheduled_effective=past)
    prev = limiter.enabled
    limiter.enabled = False
    try:
        resp = await razorpay_webhook(_signed_charged(monkeypatch, "sub_wh_1"))
    finally:
        limiter.enabled = prev
    assert resp.get("status") not in ("duplicate", "ignored")
    row = await _sub_row(org)
    assert row["plan"] == "starter"            # scheduled change applied
    assert row["scheduled_plan"] is None       # cleared
    assert await _cap(org) == 30               # reconcile lowered the cap
