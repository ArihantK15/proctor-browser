"""Fixtures for the idempotency helper used by grade-confirm and billing.

Contract: a previously-seen key returns its cached response (so a retried
POST never double-applies), a miss returns None, and a cache outage must
degrade to "not seen" (None) rather than raise — a Redis blip must never
turn a retryable write into a 500.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.services import idempotency as idem
from tests.conftest import mock_cache


@pytest.fixture(autouse=True)
def _reset_cache():
    mock_cache.reset_mock()
    mock_cache.get.return_value = None
    mock_cache.get.side_effect = None
    # mock_cache.set / .delete are EXPLICITLY assigned child mocks in
    # conftest, so mock_cache.reset_mock() (which only cascades to
    # auto-created children) does NOT clear their call counts. Reset them
    # by hand or call counts leak across the whole suite — e.g.
    # test_mark_stores_with_ttl's assert_called_once() saw 39 stale calls.
    mock_cache.set.reset_mock()
    mock_cache.set.side_effect = None
    mock_cache.set_if_absent.return_value = True
    mock_cache.set_if_absent.side_effect = None
    mock_cache.delete.reset_mock()
    yield


def test_key_is_namespaced():
    assert idem.idempotency_key("grade-confirm", "t1", "abc") == "idem:grade-confirm:t1:abc"
    assert idem.idempotency_key("p", "t", "a", "b") == "idem:p:t:a:b"


@pytest.mark.asyncio
async def test_check_returns_cached_dict():
    mock_cache.get.return_value = {"ok": True, "answer_id": "a1"}
    assert await idem.check_idempotency("idem:x") == {"ok": True, "answer_id": "a1"}


@pytest.mark.asyncio
async def test_check_miss_returns_none():
    mock_cache.get.return_value = None
    assert await idem.check_idempotency("idem:x") is None


@pytest.mark.asyncio
async def test_check_non_dict_value_returns_none():
    """A corrupt/legacy non-dict cache value must not be returned as a response."""
    mock_cache.get.return_value = ["not", "a", "dict"]
    assert await idem.check_idempotency("idem:x") is None


@pytest.mark.asyncio
async def test_check_swallows_cache_error():
    mock_cache.get.side_effect = RuntimeError("redis down")
    assert await idem.check_idempotency("idem:x") is None


@pytest.mark.asyncio
async def test_mark_stores_with_ttl():
    await idem.mark_idempotent("idem:x", {"ok": True})
    mock_cache.set.assert_called_once()
    args, kwargs = mock_cache.set.call_args
    assert args[0] == "idem:x"
    assert args[1] == {"ok": True}
    assert kwargs.get("ttl") == idem._IDEM_TTL


@pytest.mark.asyncio
async def test_mark_swallows_cache_error():
    mock_cache.set.side_effect = RuntimeError("redis down")
    # Must not raise.
    await idem.mark_idempotent("idem:x", {"ok": True})


# ── Atomic reserve (the TOCTOU fix) ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_reserve_acquires_when_unseen():
    """First caller wins the atomic SET NX → (True, None) → it processes."""
    mock_cache.set_if_absent.return_value = True
    assert await idem.reserve_idempotency("idem:x") == (True, None)


@pytest.mark.asyncio
async def test_reserve_returns_cached_when_already_completed():
    """Loser whose key already holds the completed response → (False, dict)."""
    mock_cache.set_if_absent.return_value = False
    mock_cache.get.return_value = {"ok": True, "answer_id": "a1"}
    assert await idem.reserve_idempotency("idem:x") == (False, {"ok": True, "answer_id": "a1"})


@pytest.mark.asyncio
async def test_reserve_in_flight_returns_false_none():
    """Concurrent duplicate: key holds the in-flight marker (non-dict) → 409 path."""
    mock_cache.set_if_absent.return_value = False
    mock_cache.get.return_value = 1  # the "1" reservation marker, not a dict
    assert await idem.reserve_idempotency("idem:x") == (False, None)


@pytest.mark.asyncio
async def test_reserve_fails_open_on_cache_error():
    """A cache outage must never wedge a billing endpoint → fail open (acquire)."""
    mock_cache.set_if_absent.side_effect = RuntimeError("redis down")
    assert await idem.reserve_idempotency("idem:x") == (True, None)


@pytest.mark.asyncio
async def test_release_deletes_key():
    await idem.release_idempotency("idem:x")
    mock_cache.delete.assert_called_once_with("idem:x")
