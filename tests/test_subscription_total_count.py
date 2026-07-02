"""create_subscription's total_count (#27).

Razorpay subscriptions aren't evergreen — total_count is a hard number of
billing cycles, and the subscription auto-completes once exhausted, dropping
the org to FREE_CAP even for a customer who is still happily paying. This was
previously 5 (annual) / 12 (monthly) — 5 YEARS / 12 MONTHS — meaning every
monthly self-serve subscription silently downgraded at its 12th renewal.
Razorpay's own docs: "We support subscriptions for a maximum duration of 100
years" — total_count is now effectively-evergreen at that ceiling.
"""
import os
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


def _run(billing_cycle):
    from app.services import billing
    captured = {}
    with patch.dict(os.environ, {"RAZORPAY_PLAN_STARTER": "plan_s",
                                  "RAZORPAY_PLAN_STARTER_ANNUAL": "plan_s_annual"}, clear=False), \
         patch.object(billing, "_is_live", return_value=True), \
         patch.object(billing, "_get_client", return_value=_FakeClient(captured)):
        billing.create_subscription("org-1", "starter", billing_cycle=billing_cycle)
    return captured["payload"]


def test_monthly_total_count_is_100_years_of_cycles():
    from app.services import billing
    payload = _run("monthly")
    assert payload["total_count"] == billing.TOTAL_COUNT_MONTHLY == 1200


def test_annual_total_count_is_100_years_of_cycles():
    from app.services import billing
    payload = _run("annual")
    assert payload["total_count"] == billing.TOTAL_COUNT_ANNUAL == 100


def test_total_count_is_not_the_old_short_values():
    # Lock against a regression back to the old 5/12 short-lived values.
    monthly = _run("monthly")
    annual = _run("annual")
    assert monthly["total_count"] != 12
    assert annual["total_count"] != 5
