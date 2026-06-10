"""Tests for the enterprise billing rebuild (recurring Subscriptions only):
  - reconcile_org_entitlement is the single writer of max_students
  - record_billing_event gives DB-durable idempotency
  - webhook routes grant / grace(dunning) / downgrade and reconciles
  - create-subscription does NOT grant entitlement prematurely
"""
import asyncio
import json
import os
import sys
from unittest.mock import patch, AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")
os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")

import app.services.billing as B  # noqa: E402


# ── reconcile_org_entitlement: single source of truth ──────────────

class _SubChain:
    """Mock for one (subscriptions/organizations) table; records updates."""
    def __init__(self, store, sub_rows):
        self.store, self.sub_rows = store, sub_rows
        self._table = None
    def __call__(self, table):
        self._table = table
        return self
    def select(self, *a): return self
    def update(self, fields):
        self.store.setdefault("updates", {})[self._table] = fields
        return self
    def eq(self, *a): return self
    def limit(self, *a): return self
    async def execute(self):
        r = MagicMock()
        r.data = self.sub_rows if self._table == "subscriptions" else [{"id": "org-1"}]
        return r


def _reconcile(sub_rows):
    store = {}
    chain = _SubChain(store, sub_rows)
    with patch.object(B, "async_table", chain, create=True), \
         patch("app.database.async_table", chain):
        cap = asyncio.new_event_loop().run_until_complete(B.reconcile_org_entitlement("org-1"))
    return cap, store.get("updates", {}).get("organizations", {})


def test_reconcile_active_grants_plan_cap():
    cap, org_upd = _reconcile([{"plan": "growth", "status": "active"}])
    assert cap == 150 and org_upd.get("max_students") == 150


def test_reconcile_created_does_not_grant():
    # "created" (subscription not yet authorised) must NOT entitle.
    cap, _ = _reconcile([{"plan": "pro", "status": "created"}])
    assert cap == 30


def test_reconcile_past_due_keeps_access():
    # Dunning grace window — keep the plan cap.
    cap, _ = _reconcile([{"plan": "pro", "status": "past_due"}])
    assert cap == 500


def test_reconcile_cancelled_downgrades():
    cap, _ = _reconcile([{"plan": "pro", "status": "cancelled"}])
    assert cap == 30


def test_reconcile_no_subscription_is_free_cap():
    cap, _ = _reconcile([])
    assert cap == 30


# ── webhook routing (patch service fns to isolate routing logic) ───

def _signed_event(secret, event_type, sub_id="sub_x", event_id="evt_1", extra=None):
    body = {"id": event_id, "event": event_type,
            "payload": {"subscription": {"entity": {"id": sub_id, **(extra or {})}}}}
    raw = json.dumps(body).encode()
    return raw


def _webhook_post(client, raw):
    import hmac, hashlib
    secret = "whsec_test"
    with patch.dict(os.environ, {"RAZORPAY_WEBHOOK_SECRET": secret}):
        sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        return client.post("/api/v1/webhooks/razorpay", content=raw,
                           headers={"X-Razorpay-Signature": sig})


def test_webhook_idempotent_duplicate(client):
    raw = _signed_event("whsec_test", "subscription.activated")
    with patch("app.routers.billing.billing_event_seen", AsyncMock(return_value=True)):
        r = _webhook_post(client, raw)
    assert r.status_code == 200
    assert r.json()["status"] == "duplicate"


def test_webhook_activated_grants_and_reconciles(client):
    raw = _signed_event("whsec_test", "subscription.activated")
    sub_row = [{"id": "db1", "org_id": "org-9", "plan": "growth", "status": "created",
                "past_due_since": None}]
    captured = {}

    class _Chain:
        def __init__(self, t): self.t = t
        def select(self, *a): return self
        def update(self, f): captured["update"] = f; return self
        def eq(self, *a): return self
        def limit(self, *a): return self
        async def execute(self):
            r = MagicMock()
            r.data = sub_row if self.t == "subscriptions" else [{"id": "x"}]
            return r

    with patch("app.routers.billing.billing_event_seen", AsyncMock(return_value=False)), \
         patch("app.routers.billing.record_billing_event", AsyncMock(return_value=True)), \
         patch("app.routers.billing.reconcile_org_entitlement", AsyncMock(return_value=150)) as rec, \
         patch("app.routers.billing._atable", side_effect=lambda t: _Chain(t)):
        r = _webhook_post(client, raw)
    assert r.status_code == 200, r.text
    assert captured["update"]["status"] == "active"
    rec.assert_awaited_once()                       # entitlement reconciled


def test_webhook_pending_keeps_access_grace(client):
    raw = _signed_event("whsec_test", "subscription.pending")
    sub_row = [{"id": "db1", "org_id": "org-9", "plan": "pro", "status": "active",
                "past_due_since": None}]
    captured = {}

    class _Chain:
        def __init__(self, t): self.t = t
        def select(self, *a): return self
        def update(self, f): captured["update"] = f; return self
        def eq(self, *a): return self
        def limit(self, *a): return self
        async def execute(self):
            r = MagicMock()
            r.data = sub_row if self.t == "subscriptions" else [{"id": "x"}]
            return r

    with patch("app.routers.billing.billing_event_seen", AsyncMock(return_value=False)), \
         patch("app.routers.billing.record_billing_event", AsyncMock(return_value=True)), \
         patch("app.routers.billing.reconcile_org_entitlement", AsyncMock(return_value=500)), \
         patch("app.routers.billing._notify_payment_issue", AsyncMock()) as notify, \
         patch("app.routers.billing._atable", side_effect=lambda t: _Chain(t)):
        r = _webhook_post(client, raw)
    assert r.status_code == 200, r.text
    assert captured["update"]["status"] == "past_due"   # grace, not downgrade
    notify.assert_awaited_once()                         # admin emailed


def test_webhook_bad_signature_400(client):
    raw = _signed_event("whsec_test", "subscription.activated")
    with patch.dict(os.environ, {"RAZORPAY_WEBHOOK_SECRET": "whsec_test"}):
        r = client.post("/api/v1/webhooks/razorpay", content=raw,
                        headers={"X-Razorpay-Signature": "deadbeef"})
    assert r.status_code == 400
