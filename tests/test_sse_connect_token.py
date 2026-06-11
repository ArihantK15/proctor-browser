"""Regression tests for the SSE connect-token single-use consume.

Covers the audit fix that moved the consume from a racy GET-then-DELETE to an
atomic Redis GETDEL — and the subtle decode bug that introduces: cache.set()
stores json.dumps(value), so a raw getdel() returns JSON-quoted text
('"<tid>"') that must be json.loads()'d back, or callers get a teacher_id with
literal quotes around it.
"""
import json

import pytest

from app.routers import sse


class _FakeRedis:
    """Minimal Redis stand-in with atomic getdel + a hit counter."""
    def __init__(self, seed: dict | None = None):
        self.store = dict(seed or {})
        self.getdel_calls = 0

    def getdel(self, key):
        self.getdel_calls += 1
        return self.store.pop(key, None)  # decode_responses=True → str

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        self.store.pop(key, None)


@pytest.mark.asyncio
async def test_consume_decodes_json_quoted_value(monkeypatch):
    key = sse._ct_key("tok-abc")
    fake = _FakeRedis({key: json.dumps("teacher-9")})  # exactly what cache.set stores
    monkeypatch.setattr(sse._cache, "_client", lambda: fake)

    tid = await sse._consume_connect_token("tok-abc")
    assert tid == "teacher-9"  # decoded — no surrounding quotes
    assert fake.getdel_calls == 1


@pytest.mark.asyncio
async def test_consume_is_single_use(monkeypatch):
    key = sse._ct_key("tok-once")
    fake = _FakeRedis({key: json.dumps("teacher-1")})
    monkeypatch.setattr(sse._cache, "_client", lambda: fake)

    first = await sse._consume_connect_token("tok-once")
    second = await sse._consume_connect_token("tok-once")
    assert first == "teacher-1"
    assert second is None  # getdel removed it atomically on the first call


@pytest.mark.asyncio
async def test_consume_missing_token_returns_none(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(sse._cache, "_client", lambda: fake)
    assert await sse._consume_connect_token("nope") is None
