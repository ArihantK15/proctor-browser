"""Fixtures for the auth audit log writer (services/auth_events.py).

record() is best-effort: it must persist the right shape AND never raise
into the auth flow (a failed audit insert can't be allowed to 500 a
login). These tests pin the payload and the swallow-on-error contract.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from tests.conftest import _AsyncTableMock
from app.services import auth_events


class _CapTable(_AsyncTableMock):
    def __init__(self, sink, raise_on_execute=False):
        super().__init__(data=[])
        self._sink = sink
        self._raise = raise_on_execute

    def insert(self, payload, *a, **kw):
        self._sink["payload"] = payload
        return self

    async def execute(self):
        if self._raise:
            raise RuntimeError("db down")
        return await super().execute()


class _Req:
    client = type("c", (), {"host": "203.0.113.5"})()
    headers = {"user-agent": "TestBrowser/1"}


@pytest.mark.asyncio
async def test_records_full_payload(monkeypatch):
    sink = {}
    monkeypatch.setattr(auth_events, "_atable", lambda name: _CapTable(sink))
    await auth_events.record("login_success", request=_Req(), user_kind="teacher",
                             user_id="t1", email="a@b.test", meta={"k": "v"})
    p = sink["payload"]
    assert p["event_type"] == "login_success"
    assert p["user_kind"] == "teacher"
    assert p["ip"] == "203.0.113.5"
    assert p["user_agent"] == "TestBrowser/1"
    assert p["meta"] == {"k": "v"}


@pytest.mark.asyncio
async def test_no_request_leaves_ip_ua_blank(monkeypatch):
    sink = {}
    monkeypatch.setattr(auth_events, "_atable", lambda name: _CapTable(sink))
    await auth_events.record("logout", user_kind="student", user_id="s1")
    assert sink["payload"]["ip"] == ""
    assert sink["payload"]["user_agent"] == ""
    assert sink["payload"]["meta"] == {}   # None coerced to {}


@pytest.mark.asyncio
async def test_insert_failure_is_swallowed(monkeypatch):
    monkeypatch.setattr(auth_events, "_atable",
                        lambda name: _CapTable({}, raise_on_execute=True))
    # Must not raise — auth flow continues even if the audit write fails.
    await auth_events.record("login_success", user_kind="teacher", user_id="t1")
