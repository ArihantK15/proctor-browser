"""Overage billing (gap #4) — REAL Postgres.

Proves the parts a MagicMock can't, which is exactly where the unit suite was
blind:

  • compute_overage counts DISTINCT submitters across every teacher in the org
    over a real period window.
  • the (org_id, period_start) UNIQUE constraint genuinely makes bill_cycle_overage
    idempotent — a second run for the same cycle does not create a second row
    (the claim-then-charge guard against double-billing).
  • END-TO-END: a subscription.charged webhook actually produces an
    overage_charges row. This is the regression guard for the bug where the
    webhook's subscription SELECT omitted current_period_start/end, so the
    _sub_before snapshot had no period and overage billing silently no-op'd.
    A unit mock can't catch it (the mock ignores column projection); real
    Postgres only returns the columns actually selected.
"""
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from starlette.requests import Request

from app.database import async_table
from app.routers.billing import razorpay_webhook
from app.services.billing import compute_overage, bill_cycle_overage
from app.limiter import limiter

pytestmark = pytest.mark.asyncio

P_START = datetime(2025, 1, 1, tzinfo=timezone.utc)
P_END = datetime(2025, 2, 1, tzinfo=timezone.utc)


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
    await async_table("organizations").insert(
        {"id": oid, "name": "O", "max_students": cap}).execute()
    return oid


async def _teacher(org_id) -> str:
    tid = str(uuid.uuid4())
    await async_table("teachers").insert({
        "id": tid, "org_id": org_id, "org_role": "teacher",
        "email": f"{tid[:8]}@x.test"}).execute()
    return tid


async def _sub(org_id, plan="starter", status="active", rzp="sub_int_1"):
    await async_table("subscriptions").insert({
        "org_id": org_id, "plan": plan, "status": status,
        "razorpay_subscription_id": rzp,
        "current_period_start": P_START.isoformat(),
        "current_period_end": P_END.isoformat(),
    }).execute()


async def _submit(teacher_id, n, *, when=datetime(2025, 1, 15, tzinfo=timezone.utc)):
    """Insert n exam_sessions with distinct students submitted at `when`."""
    for _ in range(n):
        await async_table("exam_sessions").insert({
            "session_key": str(uuid.uuid4()), "teacher_id": teacher_id,
            "exam_id": "ex1", "roll_number": "R", "status": "submitted",
            "student_id": str(uuid.uuid4()), "submitted_at": when.isoformat(),
        }).execute()


async def _overage_rows(org_id):
    return (await async_table("overage_charges")
            .select("period_start,overage_count,amount_inr,status")
            .eq("org_id", org_id).execute()).data or []


# ── compute_overage across teachers ──────────────────────────────────────

async def test_compute_overage_counts_distinct_across_teachers():
    org = await _org(cap=10)
    await _sub(org, plan="growth")          # growth → ₹70/overage student
    t1, t2 = await _teacher(org), await _teacher(org)
    await _submit(t1, 8)
    await _submit(t2, 7)                      # 15 distinct submitters, cap 10
    res = await compute_overage(org, P_START, P_END)
    assert res["students_used"] == 15
    assert res["plan_limit"] == 10
    assert res["overage_count"] == 5
    # Growth overage rate is ₹70/student (per-plan: starter 80, growth 70, pro 60)
    assert res["amount_inr"] == 5 * 70


# ── idempotency via the real UNIQUE constraint ───────────────────────────

async def test_bill_cycle_overage_idempotent():
    org = await _org(cap=10)
    await _sub(org)
    t = await _teacher(org)
    await _submit(t, 15)
    sub_before = {"current_period_start": P_START, "current_period_end": P_END,
                  "razorpay_subscription_id": "sub_int_1"}
    # Flag is off by default → recorded ('skipped'), not charged; the point is
    # the ledger row + UNIQUE idempotency, not the Razorpay call.
    r1 = await bill_cycle_overage(org, sub_before)
    r2 = await bill_cycle_overage(org, sub_before)
    assert r1["status"] == "skipped"
    assert r2["status"] == "duplicate"
    rows = await _overage_rows(org)
    assert len(rows) == 1                      # exactly one ledger row, not two
    assert rows[0]["overage_count"] == 5


# ── END-TO-END webhook (regression guard for the missing-SELECT bug) ─────

def _signed_charged_request(monkeypatch, rzp_sub_id="sub_int_1"):
    secret = "test-webhook-secret"
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", secret)
    event = {
        "event": "subscription.charged",
        "id": f"evt_{uuid.uuid4().hex[:12]}",
        "payload": {"subscription": {"entity": {
            "id": rzp_sub_id,
            "current_start": int(datetime(2025, 2, 1, tzinfo=timezone.utc).timestamp()),
            "current_end": int(datetime(2025, 3, 1, tzinfo=timezone.utc).timestamp()),
        }}},
    }
    body = json.dumps(event).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    scope = {"type": "http", "method": "POST", "path": "/api/v1/webhooks/razorpay",
             "query_string": b"",
             "headers": [(b"x-razorpay-signature", sig.encode()),
                         (b"content-type", b"application/json")]}
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}
    return Request(scope, receive)


async def test_charged_webhook_creates_overage_row(monkeypatch):
    # starter plan: reconcile_org_entitlement will set max_students=30 on this
    # 'active' sub, so 35 distinct submitters in the just-ended cycle = 5 over.
    org = await _org(cap=30)
    await _sub(org, plan="starter", status="active", rzp="sub_int_1")
    t = await _teacher(org)
    await _submit(t, 35)

    req = _signed_charged_request(monkeypatch)
    prev = limiter.enabled
    limiter.enabled = False
    try:
        resp = await razorpay_webhook(req)
    finally:
        limiter.enabled = prev

    assert resp.get("status") not in ("duplicate", "ignored")
    rows = await _overage_rows(org)
    assert len(rows) == 1, "subscription.charged must produce an overage_charges row"
    assert rows[0]["overage_count"] == 5
