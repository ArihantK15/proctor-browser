"""Fixtures for API-key auth (auth/api_auth.py).

Security contract: the raw key is never stored — only its SHA-256 hash and
a display prefix — and authentication rejects missing / unknown / revoked
keys with 401, returning the owning teacher_id only on a valid active key.
"""
import hashlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi import HTTPException

from tests.conftest import _AsyncTableMock
from app.auth import api_auth


class _CapTable(_AsyncTableMock):
    def __init__(self, data, sink=None):
        super().__init__(data=data)
        self._sink = sink

    def insert(self, payload, *a, **kw):
        if self._sink is not None:
            self._sink["insert"] = payload
        return self


class _Req:
    def __init__(self, key=None):
        self.headers = {"X-API-Key": key} if key is not None else {}


@pytest.mark.asyncio
async def test_generate_returns_prefixed_key_and_stores_hash(monkeypatch):
    sink = {}
    monkeypatch.setattr(api_auth, "_atable",
                        lambda name: _CapTable([{"id": "key-1"}], sink))
    key_id, raw = await api_auth.generate_api_key("t1", "CI token")
    assert key_id == "key-1"
    assert raw.startswith("pk_")
    p = sink["insert"]
    # raw key is NOT stored; only its sha256 hash + a masked prefix
    assert p["key_hash"] == hashlib.sha256(raw.encode()).hexdigest()
    assert "key" not in p or p.get("key_hash") != raw
    assert p["key_prefix"].startswith("pk_...") and raw[-8:] in p["key_prefix"]
    assert p["is_active"] is True


@pytest.mark.asyncio
async def test_generate_raises_when_insert_returns_nothing(monkeypatch):
    monkeypatch.setattr(api_auth, "_atable", lambda name: _AsyncTableMock(data=[]))
    with pytest.raises(HTTPException) as ei:
        await api_auth.generate_api_key("t1", "x")
    assert ei.value.status_code == 500


@pytest.mark.asyncio
async def test_revoke_returns_true_when_row_matched(monkeypatch):
    monkeypatch.setattr(api_auth, "_atable", lambda name: _AsyncTableMock(data=[{"id": "k"}]))
    assert await api_auth.revoke_api_key("k", "t1") is True


@pytest.mark.asyncio
async def test_revoke_returns_false_when_no_match(monkeypatch):
    monkeypatch.setattr(api_auth, "_atable", lambda name: _AsyncTableMock(data=[]))
    assert await api_auth.revoke_api_key("k", "t1") is False


@pytest.mark.asyncio
async def test_authenticate_missing_header_401(monkeypatch):
    monkeypatch.setattr(api_auth, "_atable", lambda name: _AsyncTableMock(data=[]))
    with pytest.raises(HTTPException) as ei:
        await api_auth.authenticate_api_key(_Req())
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_authenticate_unknown_key_401(monkeypatch):
    monkeypatch.setattr(api_auth, "_atable", lambda name: _AsyncTableMock(data=[]))
    with pytest.raises(HTTPException) as ei:
        await api_auth.authenticate_api_key(_Req("pk_whatever"))
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_authenticate_revoked_key_401(monkeypatch):
    monkeypatch.setattr(api_auth, "_atable",
                        lambda name: _AsyncTableMock(data=[{"id": "k", "teacher_id": "t1", "is_active": False}]))
    with pytest.raises(HTTPException) as ei:
        await api_auth.authenticate_api_key(_Req("pk_x"))
    assert ei.value.status_code == 401
    assert "revoked" in ei.value.detail.lower()


@pytest.mark.asyncio
async def test_authenticate_valid_key_returns_teacher_id(monkeypatch):
    monkeypatch.setattr(api_auth, "_atable",
                        lambda name: _AsyncTableMock(data=[{"id": "k", "teacher_id": "t-42", "is_active": True}]))
    assert await api_auth.authenticate_api_key(_Req("pk_x")) == "t-42"
