"""Tests for the admin audit log writer (app/services/admin_audit.py).

log_admin_action() is best-effort: it must persist the right shape AND
never raise into the calling endpoint (a failed audit insert can't 500
a data mutation). These tests pin the payload and the swallow-on-error
contract.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from contextlib import contextmanager
from app.services import admin_audit
from tests.conftest import mock_database


class _Req:
    client = type("c", (), {"host": "203.0.113.5"})()
    headers = {"user-agent": "TestBrowser/1"}


class _NoClientReq:
    client = None
    headers = {}


@contextmanager
def _noop_system_context():
    yield


# ── log_admin_action ────────────────────────────────────────────────


class _CaptureTable:
    def __init__(self, sink, raise_on_execute=False):
        self._sink = sink
        self._raise = raise_on_execute

    def insert(self, payload, *a, **kw):
        self._sink["payload"] = payload
        return self

    async def execute(self):
        if self._raise:
            raise RuntimeError("db down")


@pytest.mark.asyncio
async def test_logs_full_payload(monkeypatch):
    sink = {}
    monkeypatch.setattr(mock_database, "async_table",
                        lambda name: _CaptureTable(sink))
    monkeypatch.setattr("app.db_context.system_context", _noop_system_context)

    await admin_audit.log_admin_action(
        teacher_id="t1",
        action="delete_exam",
        target_type="exam",
        target_id="exam-1",
        before_data={"status": "active", "score": 85},
        after_data={"status": "archived"},
        details={"reason": "cleanup", "count": 1},
        request=_Req(),
    )
    p = sink["payload"]
    assert p["teacher_id"] == "t1"
    assert p["action"] == "delete_exam"
    assert p["target_type"] == "exam"
    assert p["target_id"] == "exam-1"
    assert p["ip"] == "203.0.113.5"
    assert p["user_agent"] == "TestBrowser/1"
    assert p["before_data"] == '{"status": "active", "score": 85}'
    assert p["after_data"] == '{"status": "archived"}'
    assert p["details"] == '{"reason": "cleanup", "count": 1}'


@pytest.mark.asyncio
async def test_no_request_leaves_ip_ua_none(monkeypatch):
    sink = {}
    monkeypatch.setattr(mock_database, "async_table",
                        lambda name: _CaptureTable(sink))
    monkeypatch.setattr("app.db_context.system_context", _noop_system_context)

    await admin_audit.log_admin_action(
        teacher_id="t1",
        action="force_submit",
        target_type="session",
        target_id="sess-1",
    )
    p = sink["payload"]
    assert p["ip"] is None
    assert p["user_agent"] is None


@pytest.mark.asyncio
async def test_before_after_data_none_by_default(monkeypatch):
    sink = {}
    monkeypatch.setattr(mock_database, "async_table",
                        lambda name: _CaptureTable(sink))
    monkeypatch.setattr("app.db_context.system_context", _noop_system_context)

    await admin_audit.log_admin_action(
        teacher_id="t1",
        action="bulk_dismiss",
        target_type="violations",
    )
    p = sink["payload"]
    assert p["before_data"] is None
    assert p["after_data"] is None
    assert p["details"] == "{}"
    assert p["target_id"] is None


@pytest.mark.asyncio
async def test_insert_failure_is_swallowed(monkeypatch):
    monkeypatch.setattr(mock_database, "async_table",
                        lambda name: _CaptureTable({}, raise_on_execute=True))
    monkeypatch.setattr("app.db_context.system_context", _noop_system_context)

    # Must not raise — audit failure must not break the admin action.
    await admin_audit.log_admin_action(
        teacher_id="t1",
        action="delete_student",
        target_type="student",
        target_id="s1",
    )


@pytest.mark.asyncio
async def test_client_without_host_does_not_crash(monkeypatch):
    sink = {}
    monkeypatch.setattr(mock_database, "async_table",
                        lambda name: _CaptureTable(sink))
    monkeypatch.setattr("app.db_context.system_context", _noop_system_context)

    req = type("", (), {"client": type("", (), {"host": None})(), "headers": {}})()
    await admin_audit.log_admin_action(
        teacher_id="t1",
        action="delete_exam",
        target_type="exam",
        target_id="e1",
        request=req,
    )
    assert sink["payload"]["ip"] is None


# ── _json_default ───────────────────────────────────────────────────


def test_json_default_datetime():
    from datetime import datetime
    assert admin_audit._json_default(datetime(2025, 6, 1, 12, 30)) == "2025-06-01T12:30:00"


def test_json_default_date():
    from datetime import date
    assert admin_audit._json_default(date(2025, 6, 1)) == "2025-06-01"


def test_json_default_uuid():
    from uuid import UUID
    assert admin_audit._json_default(UUID("12345678-1234-5678-1234-567812345678")) == "12345678-1234-5678-1234-567812345678"


def test_json_default_bytes():
    assert admin_audit._json_default(b"hello") == "hello"


def test_json_default_fallback():
    assert admin_audit._json_default(42) == "42"


# ── _to_jsonb ────────────────────────────────────────────────────────


def test_to_jsonb_none():
    assert admin_audit._to_jsonb(None) is None


def test_to_jsonb_dict():
    result = admin_audit._to_jsonb({"a": 1, "b": "two"})
    import json
    assert json.loads(result) == {"a": 1, "b": "two"}


def test_to_jsonb_with_uuid():
    from uuid import UUID
    d = {"id": UUID("00000000-0000-0000-0000-000000000001")}
    result = admin_audit._to_jsonb(d)
    assert '"00000000-0000-0000-0000-000000000001"' in result
