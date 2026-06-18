"""Regression test for the get_pool() concurrency guard (postgres_table.py).

A burst of concurrent first-callers must create exactly ONE asyncpg pool;
without the lock each racer ran create_pool() and orphaned all but the last,
leaking min_size connections per lost pool.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app import postgres_table as pt


@pytest.mark.asyncio
async def test_concurrent_get_pool_creates_one_pool(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/db")
    monkeypatch.setattr(pt, "_pool", None)
    # Fresh lock bound to this test's running loop.
    monkeypatch.setattr(pt, "_pool_lock", asyncio.Lock())

    calls = {"n": 0}

    async def _fake_create_pool(**kw):
        calls["n"] += 1
        await asyncio.sleep(0.01)  # widen the race window
        return object()  # stand-in pool

    monkeypatch.setattr(pt.asyncpg, "create_pool", _fake_create_pool)

    pools = await asyncio.gather(*(pt.get_pool() for _ in range(25)))

    assert calls["n"] == 1                      # created exactly once
    assert len({id(p) for p in pools}) == 1     # everyone got the same pool
    assert pt._pool is pools[0]

    monkeypatch.setattr(pt, "_pool", None)  # don't leak the stub into other tests
