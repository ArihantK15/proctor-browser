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
    mock_cache.set.side_effect = None
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
