"""Tests for atomic coupon-redemption increment (billing._bump_coupon_redemption).

The increment must be a single conditional UPDATE (not read-modify-write) so
concurrent redemptions can't lose an increment and let a capped coupon exceed
max_redemptions. It is best-effort: a None RETURNING (cap hit / code gone) and
any DB error are swallowed, never raised into create_subscription.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app import postgres_table
from app.routers import billing


class _Txn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Conn:
    def __init__(self, ret, raise_on_fetch=False):
        self._ret = ret
        self._raise = raise_on_fetch
        self.fetched = None

    def transaction(self):
        return _Txn()

    async def execute(self, sql, *args):  # set_config from apply_request_context (no-op when RLS off)
        pass

    async def fetchval(self, sql, *args):
        self.fetched = (sql, args)
        if self._raise:
            raise RuntimeError("db down")
        return self._ret


class _Acquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *a):
        return False


class _Pool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _Acquire(self._conn)


def _patch(monkeypatch, conn):
    async def _get_pool():
        return _Pool(conn)
    monkeypatch.setattr(postgres_table, "get_pool", _get_pool)


@pytest.mark.asyncio
async def test_issues_atomic_conditional_update(monkeypatch):
    conn = _Conn(ret=3)
    _patch(monkeypatch, conn)
    await billing._bump_coupon_redemption("SAVE20")
    sql, args = conn.fetched
    # single atomic increment, capped, parameterised, lowercased code
    assert "times_redeemed = times_redeemed + 1" in sql
    assert "times_redeemed < max_redemptions" in sql
    assert "RETURNING times_redeemed" in sql
    assert args == ("save20",)


@pytest.mark.asyncio
async def test_none_returning_is_handled(monkeypatch):
    # cap already hit (or code gone) → UPDATE matches no row → NULL; must not raise
    conn = _Conn(ret=None)
    _patch(monkeypatch, conn)
    await billing._bump_coupon_redemption("MAXEDOUT")  # no exception


@pytest.mark.asyncio
async def test_db_error_is_swallowed(monkeypatch):
    conn = _Conn(ret=None, raise_on_fetch=True)
    _patch(monkeypatch, conn)
    await billing._bump_coupon_redemption("SAVE20")  # best-effort: no raise
