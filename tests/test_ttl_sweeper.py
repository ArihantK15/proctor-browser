"""Fixtures for the TTL sweeper invocation (services/ttl_sweeper.py).

_sweep_once invokes the SQL retention function and returns the deleted-row
count. The SQL is the source of truth; here we pin the thin Python wrapper:
it acquires a pooled connection, calls the function, and coerces a NULL/None
result to 0 (never returns None or raises on an empty sweep).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app import postgres_table
from app.services import ttl_sweeper


class _FakeConn:
    def __init__(self, ret):
        self._ret = ret
        self.queried = None

    async def fetchval(self, sql):
        self.queried = sql
        return self._ret


class _Acquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *a):
        return False


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _Acquire(self._conn)


def _patch_pool(monkeypatch, conn):
    async def _get_pool():
        return _FakePool(conn)
    monkeypatch.setattr(postgres_table, "get_pool", _get_pool)


@pytest.mark.asyncio
async def test_returns_deleted_count(monkeypatch):
    conn = _FakeConn(42)
    _patch_pool(monkeypatch, conn)
    assert await ttl_sweeper._sweep_once() == 42
    assert "sweep_transient_rows" in conn.queried


@pytest.mark.asyncio
async def test_null_result_coerced_to_zero(monkeypatch):
    _patch_pool(monkeypatch, _FakeConn(None))
    assert await ttl_sweeper._sweep_once() == 0


@pytest.mark.asyncio
async def test_zero_sweep_returns_zero(monkeypatch):
    _patch_pool(monkeypatch, _FakeConn(0))
    assert await ttl_sweeper._sweep_once() == 0
