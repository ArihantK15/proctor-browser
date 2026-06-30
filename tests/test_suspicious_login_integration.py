"""Tests for the async DB + notification path of suspicious_login.py.

The pure-function tests (_ip_to_subnet, _is_new_device) live in
test_suspicious_login.py.  This file covers _recent_logins and the
orchestrator check_and_notify.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from app.services import suspicious_login


# ── _recent_logins ──────────────────────────────────────────────────────

class TestRecentLogins:
    @pytest.mark.asyncio
    async def test_returns_data_from_db(self):
        chain = MagicMock()
        chain.execute = AsyncMock(return_value=MagicMock(
            data=[{"ip": "1.2.3.4", "user_agent": "Chrome/120", "created_at": "2025-01-01T00:00:00"}],
        ))
        for m in ("select", "eq", "gte", "lte", "limit"):
            getattr(chain, m).return_value = chain
        with patch.object(suspicious_login, "_atable", MagicMock(return_value=chain)):
            result = await suspicious_login._recent_logins("teacher", "t-1")
        assert result == [{"ip": "1.2.3.4", "user_agent": "Chrome/120", "created_at": "2025-01-01T00:00:00"}]

    @pytest.mark.asyncio
    async def test_empty_data_returns_empty_list(self):
        chain = MagicMock()
        chain.execute = AsyncMock(return_value=MagicMock(data=[]))
        for m in ("select", "eq", "gte", "lte", "limit"):
            getattr(chain, m).return_value = chain
        with patch.object(suspicious_login, "_atable", MagicMock(return_value=chain)):
            result = await suspicious_login._recent_logins("student", "s-2")
        assert result == []

    @pytest.mark.asyncio
    async def test_db_exception_returns_empty_and_logs(self, caplog):
        chain = MagicMock()
        chain.execute = AsyncMock(side_effect=RuntimeError("DB timeout"))
        for m in ("select", "eq", "gte", "lte", "limit"):
            getattr(chain, m).return_value = chain
        with patch.object(suspicious_login, "_atable", MagicMock(return_value=chain)):
            result = await suspicious_login._recent_logins("student", "s-3")
        assert result == []
        assert "auth_events lookup failed" in caplog.text


# ── check_and_notify ────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _email_send():
    with patch("app.emailer.send_suspicious_login_email") as send:
        yield send


@pytest.mark.asyncio
async def test_no_history_skips_email(_email_send):
    with patch.object(suspicious_login, "_recent_logins", AsyncMock(return_value=[])):
        await suspicious_login.check_and_notify(
            user_kind="teacher", user_id="t-1", user_email="a@b.com",
            user_name="A", request_ip="1.2.3.4", user_agent="Chrome/120",
        )
    _email_send.assert_not_called()


@pytest.mark.asyncio
async def test_not_new_device_skips_email(_email_send):
    data = [
        {"ip": "1.2.3.4", "user_agent": "Chrome/120"},
        {"ip": "1.2.3.9", "user_agent": "Chrome/119"},
    ]
    with patch.object(suspicious_login, "_recent_logins", AsyncMock(return_value=data)):
        await suspicious_login.check_and_notify(
            user_kind="student", user_id="s-1", user_email="s@b.com",
            user_name="S", request_ip="1.2.3.99", user_agent="Chrome/120",
        )
    _email_send.assert_not_called()


@pytest.mark.asyncio
async def test_teacher_with_prefs_on_sends_email(_email_send):
    with patch("app.services.notification_prefs.teacher_wants", AsyncMock(return_value=True)), \
         patch.object(suspicious_login, "_recent_logins", AsyncMock(return_value=[
             {"ip": "9.9.9.9", "user_agent": "Old/1"},
         ])):
        await suspicious_login.check_and_notify(
            user_kind="teacher", user_id="t-2", user_email="t@b.com",
            user_name="Teacher", request_ip="1.2.3.4", user_agent="New/1",
        )
    _email_send.assert_called_once()


@pytest.mark.asyncio
async def test_teacher_with_prefs_off_skips_email(_email_send):
    with patch("app.services.notification_prefs.teacher_wants", AsyncMock(return_value=False)), \
         patch.object(suspicious_login, "_recent_logins", AsyncMock(return_value=[
             {"ip": "9.9.9.9", "user_agent": "Old/1"},
         ])):
        await suspicious_login.check_and_notify(
            user_kind="teacher", user_id="t-3", user_email="t@b.com",
            user_name="Teacher", request_ip="1.2.3.4", user_agent="New/1",
        )
    _email_send.assert_not_called()


@pytest.mark.asyncio
async def test_student_always_sends_no_prefs_check(_email_send):
    with patch.object(suspicious_login, "_recent_logins", AsyncMock(return_value=[
        {"ip": "9.9.9.9", "user_agent": "Old/1"},
    ])):
        await suspicious_login.check_and_notify(
            user_kind="student", user_id="s-2", user_email="s@b.com",
            user_name="Student", request_ip="1.2.3.4", user_agent="New/1",
        )
    _email_send.assert_called_once()


@pytest.mark.asyncio
async def test_email_exception_is_swallowed(_email_send):
    with patch.object(suspicious_login, "_recent_logins", AsyncMock(return_value=[
        {"ip": "9.9.9.9", "user_agent": "Old/1"},
    ])):
        _email_send.side_effect = RuntimeError("SMTP down")
        await suspicious_login.check_and_notify(
            user_kind="student", user_id="s-3", user_email="s@b.com",
            user_name="S", request_ip="1.2.3.4", user_agent="New/1",
        )


@pytest.mark.asyncio
async def test_db_exception_is_swallowed(_email_send):
    with patch.object(suspicious_login, "_recent_logins", AsyncMock(return_value=[])):
        await suspicious_login.check_and_notify(
            user_kind="teacher", user_id="t-4", user_email="t@b.com",
            user_name="T", request_ip="1.2.3.4", user_agent="New/1",
        )
    _email_send.assert_not_called()
