"""Invite daily-cap claim — against REAL Postgres.

Locks in the fix that routed the production (postgres) backend off the racy
read-then-write legacy path onto an atomic row-locked claim. The race can only
be exercised against a real DB: the legacy path lets two concurrent callers
both read the same `count` and both write, overshooting the cap. The atomic
claim's conditional UPDATE (`count + batch <= cap` under the row lock)
serialises them. A MagicMock has no row lock, so only this proves it.

Each test pins a small cap via monkeypatch so the concurrency test stays fast
and deterministic instead of firing 5000 row-locked transactions.
"""
import asyncio

import pytest
import pytest_asyncio

from app.database import async_table
from app import invites

pytestmark = pytest.mark.asyncio

TID = "33333333-3333-3333-3333-333333333333"


@pytest_asyncio.fixture(autouse=True)
async def _small_pool(monkeypatch):
    """Force a tiny asyncpg pool for this module. The app default
    (min_size=20) is sized for production join-waves; against a small dev
    Postgres, 20 warm connections per test plus the concurrency fan-out can
    trip max_connections. Drop any existing pool, shrink, and drop again
    after so other suites are unaffected."""
    from app.postgres_table import close_pool
    monkeypatch.setenv("POSTGRES_POOL_MIN", "1")
    monkeypatch.setenv("POSTGRES_POOL_MAX", "10")
    await close_pool()
    yield
    await close_pool()


async def _count() -> int:
    rows = (await async_table("invite_send_counters").select("count")
            .eq("teacher_id", TID).execute()).data or []
    return rows[0]["count"] if rows else 0


async def test_first_claim_creates_row_and_bumps(monkeypatch):
    monkeypatch.setattr(invites, "INVITE_DAILY_CAP", 100)
    allowed, remaining = await invites._claim_and_bump_cap_postgres(TID, 5)
    assert allowed is True
    assert remaining == 95
    assert await _count() == 5


async def test_claim_at_cap_is_denied_without_overshoot(monkeypatch):
    monkeypatch.setattr(invites, "INVITE_DAILY_CAP", 100)
    # Claim everything but 2.
    allowed, _ = await invites._claim_and_bump_cap_postgres(TID, 98)
    assert allowed is True
    # A 5-batch would overshoot by 3 → denied, counter untouched.
    allowed, remaining = await invites._claim_and_bump_cap_postgres(TID, 5)
    assert allowed is False
    assert remaining == 2
    assert await _count() == 98  # not bumped on denial


async def test_concurrent_claims_never_overshoot_cap(monkeypatch):
    cap = 5
    monkeypatch.setattr(invites, "INVITE_DAILY_CAP", cap)
    # Fire more single claims than the cap, concurrently. With the racy legacy
    # path some would overshoot; the atomic claim must grant exactly `cap` and
    # deny the rest, leaving the counter at exactly `cap`.
    n = 12
    # Bound simultaneity with a small semaphore: 4 claims still execute at once
    # and contend on the same row lock (which is what exercises the atomic
    # predicate), while keeping the test's connection footprint tiny so it
    # survives any test ordering on a small shared Postgres.
    sem = asyncio.Semaphore(4)

    async def _claim():
        async with sem:
            return await invites._claim_and_bump_cap_postgres(TID, 1)

    results = await asyncio.gather(*[_claim() for _ in range(n)])
    granted = sum(1 for allowed, _ in results if allowed)
    assert granted == cap
    assert await _count() == cap  # never exceeded
