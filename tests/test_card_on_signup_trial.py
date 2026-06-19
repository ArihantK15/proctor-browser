"""create_subscription trial deferral (card-on-signup): trial_days → Razorpay start_at.

A future start_at is how Razorpay defers the first charge while still capturing
the mandate at checkout — the basis of card-on-signup with a 14-day free trial.
"""
import os
import time
from unittest.mock import patch

os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")


class _FakeSub:
    def __init__(self, captured):
        self._captured = captured

    def create(self, payload):
        self._captured["payload"] = payload
        return {"id": "sub_x", "short_url": "https://rzp.test/x", "status": "created"}


class _FakeClient:
    def __init__(self, captured):
        self.subscription = _FakeSub(captured)


def _run(trial_days):
    from app.services import billing
    captured = {}
    with patch.dict(os.environ, {"RAZORPAY_PLAN_STARTER": "plan_s"}, clear=False), \
         patch.object(billing, "_is_live", return_value=True), \
         patch.object(billing, "_get_client", return_value=_FakeClient(captured)):
        res = billing.create_subscription("org-1", "starter", trial_days=trial_days)
    return res, captured.get("payload", {})


def test_trial_days_sets_future_start_at():
    before = int(time.time())
    res, payload = _run(14)
    assert res["subscription_id"] == "sub_x"
    assert "start_at" in payload
    # ~14 days out (allow a few seconds of slack for execution time)
    assert payload["start_at"] >= before + 14 * 86400 - 5
    assert payload["start_at"] <= before + 14 * 86400 + 30


def test_no_trial_means_no_start_at():
    _res, payload = _run(0)
    assert "start_at" not in payload  # immediate charge (existing upgrade flow unchanged)
