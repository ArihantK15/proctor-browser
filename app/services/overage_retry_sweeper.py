"""Periodic retry of failed overage add-on charges (follow-up #20 / #4c).

bill_cycle_overage() (services/billing.py) records an overage_charges row as
status='failed' when the Razorpay add-on API call errors, and deliberately
never retries inline — the base subscription.charged webhook must still
return 200 regardless. Nothing else retried it: a transient Razorpay/network
blip on overage day meant that month's overage revenue was gone unless a
human noticed the 'failed' row and settled it by hand.

This loop retries those rows on a timer, capped by OVERAGE_SWEEPER_MAX_RETRIES
so a permanently-broken org (e.g. a cancelled mandate) isn't hammered forever,
and skips rows retried too recently via last_retry_at so a string of quick
failures doesn't turn into a tight API-call loop.

Only the leader worker should run this (same is_leader gate as the other
sweepers in main.py).
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

OVERAGE_SWEEPER_INTERVAL_SECS      = int(os.environ.get("OVERAGE_SWEEPER_INTERVAL_SECS",      "3600"))  # 1 hour
OVERAGE_SWEEPER_STARTUP_DELAY_SECS = int(os.environ.get("OVERAGE_SWEEPER_STARTUP_DELAY_SECS", "300"))   # 5 min
OVERAGE_SWEEPER_MAX_RETRIES        = int(os.environ.get("OVERAGE_SWEEPER_MAX_RETRIES",        "5"))
OVERAGE_SWEEPER_MIN_RETRY_INTERVAL_SECS = int(
    os.environ.get("OVERAGE_SWEEPER_MIN_RETRY_INTERVAL_SECS", "1800")  # 30 min
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def overage_retry_sweeper_loop() -> None:
    """Run forever, retrying failed overage add-ons every interval."""
    await asyncio.sleep(OVERAGE_SWEEPER_STARTUP_DELAY_SECS)
    while True:
        try:
            result = await _sweep_once()
            if result["attempted"]:
                logger.info(
                    "[overage-sweeper] retried %d row(s): %d charged, %d still failed, %d exhausted",
                    result["attempted"], result["charged"], result["failed"], result["exhausted"],
                )
        except Exception as e:
            logger.exception("[overage-sweeper] unhandled error: %s", e)
        await asyncio.sleep(OVERAGE_SWEEPER_INTERVAL_SECS)


async def _sweep_once() -> dict[str, int]:
    """Retry every eligible 'failed' overage_charges row once. Returns counts."""
    from ..database import async_table as _atable
    from .billing import _get_client, _is_live, _create_overage_addon, record_billing_event

    counts = {"attempted": 0, "charged": 0, "failed": 0, "exhausted": 0}

    if not _is_live():
        return counts  # nothing to retry against in sandbox — no real add-on API to call

    rows = (
        await _atable("overage_charges")
        .select("org_id,period_start,overage_count,amount_inr,credit_applied_inr,"
                "retry_count,last_retry_at")
        .eq("status", "failed")
        .lt("retry_count", OVERAGE_SWEEPER_MAX_RETRIES)
        .limit(200)
        .execute()
    ).data or []

    if not rows:
        return counts

    now = datetime.now(timezone.utc)
    client = _get_client()

    for row in rows:
        org_id = row["org_id"]
        period_start = row["period_start"]
        retry_count = int(row.get("retry_count") or 0)

        last_retry_at = row.get("last_retry_at")
        if last_retry_at:
            try:
                last_ts = datetime.fromisoformat(str(last_retry_at).replace("Z", "+00:00"))
                if (now - last_ts).total_seconds() < OVERAGE_SWEEPER_MIN_RETRY_INTERVAL_SECS:
                    continue
            except Exception:
                pass

        sub_rows = (
            await _atable("subscriptions").select("razorpay_subscription_id")
            .eq("org_id", org_id).limit(1).execute()
        ).data or []
        sub_id = (sub_rows[0].get("razorpay_subscription_id") or "") if sub_rows else ""

        counts["attempted"] += 1
        new_retry_count = retry_count + 1
        net = int(row.get("amount_inr") or 0) - int(row.get("credit_applied_inr") or 0)

        if not sub_id or net <= 0:
            # No subscription left to bill against (org cancelled since), or
            # nothing left to charge after credit — give up on this row rather
            # than burn retries it can never clear.
            await _atable("overage_charges").update(
                {"retry_count": OVERAGE_SWEEPER_MAX_RETRIES, "last_retry_at": _now_iso()}
            ).eq("org_id", org_id).eq("period_start", period_start).execute()
            counts["exhausted"] += 1
            continue

        try:
            addon_id = _create_overage_addon(client, sub_id, int(row.get("overage_count") or 0), net)
            await _atable("overage_charges").update(
                {"status": "charged", "razorpay_addon_id": addon_id,
                 "retry_count": new_retry_count, "last_retry_at": _now_iso()}
            ).eq("org_id", org_id).eq("period_start", period_start).execute()
            counts["charged"] += 1
            try:
                await record_billing_event(
                    event_id="", org_id=org_id, event_type="overage.addon.retry",
                    amount=net, status="charged",
                    razorpay_subscription_id=sub_id,
                    payload={"overage_charges_retry": {"org_id": org_id, "period_start": str(period_start),
                             "razorpay_addon_id": addon_id, "attempt": new_retry_count}},
                )
            except Exception as exc:
                logger.warning("[overage-sweeper] billing_event record failed for org=%s: %s", org_id, exc)
        except Exception:
            logger.exception("[overage-sweeper] retry #%d still failing for org=%s period=%s",
                              new_retry_count, org_id, period_start)
            await _atable("overage_charges").update(
                {"retry_count": new_retry_count, "last_retry_at": _now_iso()}
            ).eq("org_id", org_id).eq("period_start", period_start).execute()
            if new_retry_count >= OVERAGE_SWEEPER_MAX_RETRIES:
                counts["exhausted"] += 1
            else:
                counts["failed"] += 1

    return counts
