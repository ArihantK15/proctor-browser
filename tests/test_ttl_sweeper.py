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


class _Txn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, ret):
        self._ret = ret
        self.queried = None
        self.set_configs = []

    def transaction(self):
        return _Txn()

    async def execute(self, sql, *args):
        # set_config(...) emitted by apply_request_context lands here.
        self.set_configs.append((sql, args))

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


@pytest.mark.asyncio
async def test_no_set_config_when_rls_disabled(monkeypatch):
    from app import db_context
    monkeypatch.setattr(db_context, "RLS_SESSION_CONTEXT", False)
    conn = _FakeConn(5)
    _patch_pool(monkeypatch, conn)
    await ttl_sweeper._sweep_once()
    assert conn.set_configs == []  # gated off → byte-identical to before


@pytest.mark.asyncio
async def test_applies_system_context_when_rls_enabled(monkeypatch):
    from app import db_context
    monkeypatch.setattr(db_context, "RLS_SESSION_CONTEXT", True)
    conn = _FakeConn(5)
    _patch_pool(monkeypatch, conn)
    await ttl_sweeper._sweep_once()
    # apply_request_context(force_system=True) → one set_config call, role=system
    assert len(conn.set_configs) == 1
    _sql, args = conn.set_configs[0]
    assert "set_config" in _sql
    assert args[0] == "system"  # app.role bound param
