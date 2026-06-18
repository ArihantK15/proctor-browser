"""Fixtures for brute-force account lockout (services/auth_lockout.py).

The gate locks an identifier after _MAX_FAILURES failed attempts within
the window. Critically it must DEGRADE — when Redis is unavailable the
in-process fallback still counts attempts, so a Redis outage can't fail
the gate open. These tests pin the fallback counter, expiry, and the
redis-vs-fallback selection in check/record/clear.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.services import auth_lockout as lk
from tests.conftest import mock_cache


@pytest.fixture(autouse=True)
def _clear_state():
    lk._FALLBACK.clear()
    mock_cache.reset_mock()
    mock_cache.get.return_value = None
    mock_cache.get.side_effect = None
    yield
    lk._FALLBACK.clear()


# ── in-memory fallback primitives ────────────────────────────────────

def test_fallback_incr_counts_up():
    k = lk._key("login", "u1")
    assert lk._fallback_incr(k) == 1
    assert lk._fallback_incr(k) == 2
    assert lk._fallback_get(k) == 2


def test_fallback_resets_after_window(monkeypatch):
    k = lk._key("login", "u1")
    t = [1000.0]
    monkeypatch.setattr(lk.time, "monotonic", lambda: t[0])
    assert lk._fallback_incr(k) == 1
    t[0] += lk._LOCKOUT_WINDOW + 1  # advance past the window
    assert lk._fallback_get(k) == 0          # expired
    assert lk._fallback_incr(k) == 1         # fresh window


def test_fallback_delete():
    k = lk._key("login", "u1")
    lk._fallback_incr(k)
    lk._fallback_delete(k)
    assert lk._fallback_get(k) == 0


# ── check_lockout via Redis ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_not_locked_when_no_counter():
    mock_cache.get.return_value = None
    assert await lk.check_lockout("login", "u1") == (False, 0)


@pytest.mark.asyncio
async def test_check_locked_at_threshold():
    mock_cache.get.return_value = lk._MAX_FAILURES
    locked, retry = await lk.check_lockout("login", "u1")
    assert locked is True
    assert retry == lk._LOCKOUT_DURATION


@pytest.mark.asyncio
async def test_check_falls_back_when_redis_errors():
    mock_cache.get.side_effect = RuntimeError("redis down")
    k = lk._key("login", "u1")
    for _ in range(lk._MAX_FAILURES):
        lk._fallback_incr(k)
    locked, _ = await lk.check_lockout("login", "u1")
    assert locked is True  # fallback counted the failures → still gated


# ── record / clear via fallback (Redis client unavailable) ───────────

@pytest.mark.asyncio
async def test_record_failure_uses_fallback_when_client_none(monkeypatch):
    monkeypatch.setattr(mock_cache, "_client", lambda: None)
    n1 = await lk.record_failure("login", "u1")
    n2 = await lk.record_failure("login", "u1")
    assert (n1, n2) == (1, 2)


@pytest.mark.asyncio
async def test_clear_failures_clears_fallback(monkeypatch):
    monkeypatch.setattr(mock_cache, "_client", lambda: None)
    k = lk._key("login", "u1")
    lk._fallback_incr(k)
    await lk.clear_failures("login", "u1")
    assert lk._fallback_get(k) == 0
