"""Tests for Gap #49: List-Unsubscribe header + one-click unsubscribe endpoint.

Covers:
  1. POST /api/v1/unsubscribe — valid token flips preference → 200
  2. GET  /api/v1/unsubscribe  — valid token flips preference → 200 HTML
  3. Invalid/expired/tampered token → 400
  4. Idempotent — repeat call still returns 200
  5. send_exam_reminder includes List-Unsubscribe headers
  6. send_invite_email does NOT include unsubscribe headers
"""

import os
import sys
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from httpx import AsyncClient, ASGITransport

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")
os.environ.setdefault("JWT_UNSUBSCRIBE_SIGNING_KEY", "test-unsubscribe-key-32chars!!!!")

from app.main import app
# Import real emailer functions AFTER the _restore_emailer_autouse fixture fires
from app.services.local_auth import issue_unsubscribe_token


STUDENT_ID = "stu-1"
STUDENT_EMAIL = "student@test.com"


def _valid_token() -> str:
    return issue_unsubscribe_token(STUDENT_ID, STUDENT_EMAIL)


def _make_mock_atable(rows=None, update_result=None):
    """Build a mock _atable that returns `rows` on .eq(…).select(…).execute()."""
    mt = MagicMock()
    select_chain = MagicMock()
    select_chain.execute = AsyncMock(return_value=MagicMock(data=rows or []))
    mt.select.return_value = select_chain
    mt.update.return_value = select_chain

    eq_chain = MagicMock()
    eq_chain.execute = AsyncMock(return_value=MagicMock(data=update_result or []))
    mt.eq.return_value = eq_chain
    select_chain.eq.return_value = eq_chain

    return mt


def _load_real_emailer():
    """Return the real app.emailer module, bypassing the conftest mock."""
    saved = sys.modules.get("app.emailer")
    if "app.emailer" in sys.modules:
        del sys.modules["app.emailer"]
    import app.emailer as real
    sys.modules["app.emailer"] = saved or real
    return real


class TestPostUnsubscribe:
    PATH = "/api/v1/unsubscribe"

    @pytest.mark.asyncio
    async def test_valid_token_flips_preference(self):
        mt = _make_mock_atable()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            with patch("app.routers.unsubscribe._atable", return_value=mt):
                r = await ac.post(self.PATH, json={"token": _valid_token()})
        assert r.status_code == 200
        assert r.json() == {"ok": True}
        mt.update.assert_called_once_with({"email_reminders_enabled": False})

    @pytest.mark.asyncio
    async def test_invalid_token_returns_400(self):
        mt = _make_mock_atable()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            with patch("app.routers.unsubscribe._atable", return_value=mt):
                r = await ac.post(self.PATH, json={"token": "invalid-token"})
        assert r.status_code == 400
        assert "Invalid" in r.json()["detail"]

    @pytest.mark.asyncio
    async def test_expired_token_returns_400(self):
        mt = _make_mock_atable()
        from datetime import datetime, timezone, timedelta
        import jwt as _jwt
        from app.constants import UNSUBSCRIBE_SIGNING_KEY
        expired = _jwt.encode({
            "scope": "unsubscribe", "uid": STUDENT_ID, "email": STUDENT_EMAIL,
            "iat": datetime.now(timezone.utc) - timedelta(days=400),
            "exp": datetime.now(timezone.utc) - timedelta(days=35),
        }, UNSUBSCRIBE_SIGNING_KEY, algorithm="HS256")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            with patch("app.routers.unsubscribe._atable", return_value=mt):
                r = await ac.post(self.PATH, json={"token": expired})
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_tampered_token_returns_400(self):
        token = _valid_token() + "x"
        mt = _make_mock_atable()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            with patch("app.routers.unsubscribe._atable", return_value=mt):
                r = await ac.post(self.PATH, json={"token": token})
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_idempotent_on_repeat(self):
        mt = _make_mock_atable()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            with patch("app.routers.unsubscribe._atable", return_value=mt) as mock_atable:
                r1 = await ac.post(self.PATH, json={"token": _valid_token()})
                r2 = await ac.post(self.PATH, json={"token": _valid_token()})
        assert r1.status_code == 200
        assert r2.status_code == 200
        mt.update.assert_called_with({"email_reminders_enabled": False})

    @pytest.mark.asyncio
    async def test_missing_token_returns_422(self):
        mt = _make_mock_atable()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            with patch("app.routers.unsubscribe._atable", return_value=mt):
                r = await ac.post(self.PATH, json={})
        assert r.status_code == 422


class TestGetUnsubscribe:
    PATH = "/api/v1/unsubscribe"

    @pytest.mark.asyncio
    async def test_valid_token_returns_html(self):
        mt = _make_mock_atable()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            with patch("app.routers.unsubscribe._atable", return_value=mt):
                r = await ac.get(self.PATH, params={"token": _valid_token()})
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")
        assert "unsubscribed" in r.text.lower()

    @pytest.mark.asyncio
    async def test_missing_token_param_returns_400(self):
        mt = _make_mock_atable()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            with patch("app.routers.unsubscribe._atable", return_value=mt):
                r = await ac.get(self.PATH)
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_token_returns_400(self):
        mt = _make_mock_atable()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            with patch("app.routers.unsubscribe._atable", return_value=mt):
                r = await ac.get(self.PATH, params={"token": "invalid"})
        assert r.status_code == 400


class TestReminderHeaders:
    @pytest.mark.asyncio
    async def test_send_exam_reminder_includes_list_unsubscribe_headers(self):
        real = _load_real_emailer()
        send_exam_reminder = real.send_exam_reminder
        with patch.object(real, "_pick_backend") as mock_pick:
            mock_backend = MagicMock()
            mock_backend.send.return_value = MagicMock(ok=True, provider_msg_id="mock")
            mock_pick.return_value = mock_backend
            with patch("app.constants.APP_URL", "https://procta.net"):
                result = send_exam_reminder(
                    to_email="test@test.com",
                    to_name="Test",
                    exam_title="Final",
                    invite_url="http://invite",
                    roll_number="R001",
                    hours_until=24,
                    exam_starts_at_display="tomorrow",
                    student_id=STUDENT_ID,
                )
        assert result.ok is True
        call_kwargs = mock_backend.send.call_args[1]
        assert call_kwargs["headers"] is not None
        headers = call_kwargs["headers"]
        assert "List-Unsubscribe" in headers
        assert "https://procta.net/api/v1/unsubscribe?token=" in headers["List-Unsubscribe"]
        assert "mailto:unsubscribe@procta.net" in headers["List-Unsubscribe"]
        assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"

    @pytest.mark.asyncio
    async def test_send_invite_email_does_not_include_unsubscribe_headers(self):
        real = _load_real_emailer()
        send_invite_email = real.send_invite_email
        with patch.object(real, "_pick_backend") as mock_pick:
            mock_backend = MagicMock()
            mock_backend.send.return_value = MagicMock(ok=True, provider_msg_id="mock")
            mock_pick.return_value = mock_backend
            result = send_invite_email(
                to_email="test@test.com",
                to_name="Test",
                exam_title="Final",
                invite_url="http://invite",
                download_url="http://download",
                roll_number="R001",
            )
        assert result.ok is True, f"send failed: {result.error}"
        assert mock_backend.send.call_count == 1
        call_kwargs = mock_backend.send.call_args[1]
        assert call_kwargs.get("headers") is None
