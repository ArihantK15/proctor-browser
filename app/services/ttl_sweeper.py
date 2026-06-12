"""Periodic invocation of the SQL TTL sweeper function.

Calls ``public.sweep_transient_rows()`` (defined in phase86, extended in
phase101) every TTL_SWEEPER_INTERVAL_SECS seconds. The function deletes
aged rows from google_oauth_states, email_otps, refresh_tokens,
auth_sessions, auth_events and violations using retention windows tuned
for forensic value vs storage cost (see migrations/phase101_ttl_sweep_violations.sql
for the per-table windows).

Only the leader worker should run this loop (enforced in main.py via the
same is_leader check the heartbeat reaper uses). Running on every worker
just duplicates work — the SQL function is idempotent, so it wouldn't
delete anything twice, but it wastes a query per worker per cycle.

The first run after startup waits TTL_SWEEPER_STARTUP_DELAY_SECS so the
app isn't competing for DB connections during the boot rush.
"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

TTL_SWEEPER_INTERVAL_SECS      = int(os.environ.get("TTL_SWEEPER_INTERVAL_SECS",      "21600"))  # 6 hours
TTL_SWEEPER_STARTUP_DELAY_SECS = int(os.environ.get("TTL_SWEEPER_STARTUP_DELAY_SECS", "300"))    # 5 min


async def ttl_sweeper_loop() -> None:
    """Run forever, sweeping aged transient rows every interval."""
    await asyncio.sleep(TTL_SWEEPER_STARTUP_DELAY_SECS)
    while True:
        try:
            deleted = await _sweep_once()
            logger.info("[ttl-sweeper] sweep complete — deleted %d row(s)", deleted)
        except Exception as e:
            logger.exception("[ttl-sweeper] unhandled error: %s", e)
        await asyncio.sleep(TTL_SWEEPER_INTERVAL_SECS)


async def _sweep_once() -> int:
    """Invoke the SQL function and return the total rows deleted."""
    from ..postgres_table import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.fetchval("SELECT public.sweep_transient_rows()")
    return int(result or 0)
