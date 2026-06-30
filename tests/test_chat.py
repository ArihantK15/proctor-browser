"""Tests for app/routers/chat.py — WebSocket chat endpoints.

Covers _chat_verify_session_owned and the two WebSocket handler
entry points (ws_chat_student, ws_chat_teacher).
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from fastapi import HTTPException
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.conftest import mock_database


# ── _chat_verify_session_owned ──────────────────────────────────────────

class TestChatVerifySessionOwned:
    """Pure-logic tests for the session-ownership check."""

    _BASE = {
        "session_key": "sess-1",
        "roll_number": "ALICE001",
        "status": "in_progress",
        "teacher_id": "teacher-1",
    }

    def _mock_chain(self, data: list[dict]) -> MagicMock:
        """Build a chained _atable mock returning the given data."""
        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain

        async def _execute():
            r = MagicMock()
            r.data = data
            return r

        chain.execute = _execute
        return chain

    @pytest.mark.asyncio
    async def test_returns_row_when_owned(self):
        row = {**self._BASE}
        chain = self._mock_chain([row])
        with patch.object(chain, "limit", return_value=chain):
            with patch("app.routers.chat._atable", return_value=chain):
                from app.routers.chat import _chat_verify_session_owned
                result = await _chat_verify_session_owned("sess-1", "teacher-1", "ALICE001")
                assert result == row

    @pytest.mark.asyncio
    async def test_returns_none_when_no_rows(self):
        chain = self._mock_chain([])
        with patch.object(chain, "limit", return_value=chain):
            with patch("app.routers.chat._atable", return_value=chain):
                from app.routers.chat import _chat_verify_session_owned
                result = await _chat_verify_session_owned("sess-1", "teacher-1", "ALICE001")
                assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_teacher_mismatch(self):
        row = {**self._BASE, "teacher_id": "other-teacher"}
        chain = self._mock_chain([row])
        with patch.object(chain, "limit", return_value=chain):
            with patch("app.routers.chat._atable", return_value=chain):
                from app.routers.chat import _chat_verify_session_owned
                result = await _chat_verify_session_owned("sess-1", "teacher-1", "ALICE001")
                assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_roll_mismatch(self):
        row = {**self._BASE, "roll_number": "BOB002"}
        chain = self._mock_chain([row])
        with patch.object(chain, "limit", return_value=chain):
            with patch("app.routers.chat._atable", return_value=chain):
                from app.routers.chat import _chat_verify_session_owned
                result = await _chat_verify_session_owned("sess-1", "teacher-1", "ALICE001")
                assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_status_is_result(self):
        from app.models import RESULT_STATUSES
        for status in RESULT_STATUSES:
            row = {**self._BASE, "status": status}
            chain = self._mock_chain([row])
            with patch.object(chain, "limit", return_value=chain):
                with patch("app.routers.chat._atable", return_value=chain):
                    from app.routers.chat import _chat_verify_session_owned
                    result = await _chat_verify_session_owned("sess-1", "teacher-1", "ALICE001")
                    assert result is None, f"expected None for status={status}"

    @pytest.mark.asyncio
    async def test_case_insensitive_roll_match(self):
        row = {**self._BASE, "roll_number": "alice001"}
        chain = self._mock_chain([row])
        with patch.object(chain, "limit", return_value=chain):
            with patch("app.routers.chat._atable", return_value=chain):
                from app.routers.chat import _chat_verify_session_owned
                result = await _chat_verify_session_owned("sess-1", "teacher-1", "ALICE001")
                assert result is not None

    @pytest.mark.asyncio
    async def test_missing_teacher_id_in_row(self):
        row = {**self._BASE, "teacher_id": None}
        chain = self._mock_chain([row])
        with patch.object(chain, "limit", return_value=chain):
            with patch("app.routers.chat._atable", return_value=chain):
                from app.routers.chat import _chat_verify_session_owned
                result = await _chat_verify_session_owned("sess-1", "teacher-1", "ALICE001")
                assert result is None


# ── ws_chat_student ─────────────────────────────────────────────────────

def _make_student_ws(
    session_id: str = "sess-1",
    token: str | None = "valid-token",
    receive_messages: list[str] | None = None,
) -> AsyncMock:
    """Build a mocked WebSocket that simulates a student connection."""
    ws = AsyncMock()
    ws.headers = {"sec-websocket-protocol": token or ""}
    ws.query_params = {"session_id": session_id}
    ws.cookies = {}
    ws.accept = AsyncMock()
    ws.close = AsyncMock()

    messages = list(receive_messages or [])
    messages.append(WebSocketDisconnectSignal())  # terminates the loop

    async def _receive_text():
        msg = messages.pop(0)
        if isinstance(msg, WebSocketDisconnectSignal):
            raise type("WebSocketDisconnect", (Exception,), {})(code=1000)
        return msg

    ws.receive_text = _receive_text
    ws.send_json = AsyncMock()
    ws.send_bytes = AsyncMock()
    return ws


class WebSocketDisconnectSignal:
    """Sentinel to signal WebSocket disconnect in mock receive sequences."""


@pytest.fixture(autouse=True)
def _patch_student_auth():
    """Patch verify_student_token to accept 'valid-token' (sync)."""
    def _verify(token: str):
        if token == "valid-token":
            return {"roll": "ALICE001", "tid": "teacher-1", "eid": "exam-1"}
        raise HTTPException(status_code=401)
    with patch("app.routers.chat.verify_student_token", side_effect=_verify):
        yield


@pytest.fixture(autouse=True)
def _patch_rate_limiter():
    """Make ws_rate_limiter always allow."""
    limiter = MagicMock()
    limiter.check_and_increment = AsyncMock(return_value=True)
    limiter.decrement = AsyncMock()
    with patch("app.routers.chat.ws_rate_limiter", limiter):
        yield


@pytest.fixture
def _mock_session_owner():
    """Make _chat_verify_session_owned return a valid row."""
    row = {"session_key": "sess-1", "roll_number": "ALICE001", "status": "in_progress", "teacher_id": "teacher-1"}
    with patch("app.routers.chat._chat_verify_session_owned", AsyncMock(return_value=row)):
        yield


@pytest.mark.asyncio
async def test_student_rate_limited_closes_4400():
    ws = _make_student_ws()
    limiter = MagicMock()
    limiter.check_and_increment = AsyncMock(return_value=False)  # rate limited
    limiter.decrement = AsyncMock()
    with patch("app.routers.chat.ws_rate_limiter", limiter):
        from app.routers.chat import ws_chat_student
        await ws_chat_student(ws)
    ws.close.assert_called_with(code=4400, reason="rate_limited")


@pytest.mark.asyncio
async def test_student_no_session_id_closes_4400():
    ws = _make_student_ws(session_id="")
    from app.routers.chat import ws_chat_student
    await ws_chat_student(ws)
    ws.close.assert_called_with(code=4400)


@pytest.mark.asyncio
async def test_student_invalid_token_closes_4401():
    ws = _make_student_ws(token="bad-token")
    def _bad_verify(token: str):
        raise HTTPException(401)
    with patch("app.routers.chat.verify_student_token", side_effect=_bad_verify):
        from app.routers.chat import ws_chat_student
        await ws_chat_student(ws)
    ws.close.assert_called_with(code=4401)


@pytest.mark.asyncio
async def test_student_no_roll_or_tid_closes_4401():
    def _verify_no_roll(token: str):
        return {"roll": "", "tid": ""}
    ws = _make_student_ws()
    with patch("app.routers.chat.verify_student_token", side_effect=_verify_no_roll):
        from app.routers.chat import ws_chat_student
        await ws_chat_student(ws)
    ws.close.assert_called_with(code=4401)


@pytest.mark.asyncio
async def test_student_session_not_found_closes_4403():
    ws = _make_student_ws()
    with patch("app.routers.chat._chat_verify_session_owned", AsyncMock(return_value=None)):
        from app.routers.chat import ws_chat_student
        await ws_chat_student(ws)
    ws.close.assert_called_with(code=4403)


@pytest.mark.asyncio
async def test_student_happy_path_accepts_and_sends_history(_mock_session_owner):
    ws = _make_student_ws()
    ws.accept = AsyncMock()
    from app.routers.chat import ws_chat_student
    await ws_chat_student(ws)
    ws.accept.assert_called_once()
    # Should have sent history message
    history_calls = [c for c in ws.send_json.call_args_list
                     if c[0][0].get("type") == "history"]
    assert len(history_calls) > 0
    assert history_calls[0][0][0]["session_id"] == "sess-1"


@pytest.mark.asyncio
async def test_student_reauth_flow(_mock_session_owner):
    valid_reauth = json.dumps({"type": "reauth", "token": "reauth-token"})
    ws = _make_student_ws(receive_messages=[valid_reauth])
    ws.accept = AsyncMock()

    def _reauth_verify(token: str):
        return {"roll": "ALICE001", "tid": "teacher-1", "exp": 9999999999}
    with patch("app.routers.chat.verify_student_token", side_effect=_reauth_verify):
        from app.routers.chat import ws_chat_student
        await ws_chat_student(ws)
    reauth_ok_calls = [c for c in ws.send_json.call_args_list
                       if c[0][0].get("type") == "reauth_ok"]
    assert len(reauth_ok_calls) > 0


@pytest.mark.asyncio
async def test_student_pong_does_not_send_response(_mock_session_owner):
    ws = _make_student_ws(receive_messages=[json.dumps({"type": "pong"})])
    ws.accept = AsyncMock()
    from app.routers.chat import ws_chat_student
    await ws_chat_student(ws)
    # No separate "pong" response message should have been sent
    pong_responses = [c for c in ws.send_json.call_args_list
                      if c[0][0].get("type") == "pong"]
    assert len(pong_responses) == 0


@pytest.mark.asyncio
async def test_student_bad_json_ignored(_mock_session_owner):
    ws = _make_student_ws(receive_messages=["not json at all"])
    ws.accept = AsyncMock()
    from app.routers.chat import ws_chat_student
    await ws_chat_student(ws)
    assert True  # no crash


@pytest.mark.asyncio
async def test_student_empty_text_ignored(_mock_session_owner):
    ws = _make_student_ws(receive_messages=[json.dumps({"type": "msg", "text": "  "})])
    ws.accept = AsyncMock()
    from app.routers.chat import ws_chat_student
    await ws_chat_student(ws)
    assert True


@pytest.mark.asyncio
async def test_student_long_text_truncated(_mock_session_owner):
    from app.constants import CHAT_MAX_TEXT_LEN
    long_text = "x" * (CHAT_MAX_TEXT_LEN + 100)
    ws = _make_student_ws(receive_messages=[json.dumps({"type": "msg", "text": long_text})])
    ws.accept = AsyncMock()
    with patch("app.routers.chat.chat_hub.student_send") as mock_send:
        mock_send.return_value = None
        from app.routers.chat import ws_chat_student
        await ws_chat_student(ws)
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        assert len(args[1]) == CHAT_MAX_TEXT_LEN


# ── ws_chat_teacher ─────────────────────────────────────────────────────

def _make_teacher_ws(
    subproto: str = "admin-token",
    cookie: str | None = None,
    receive_messages: list[str] | None = None,
) -> AsyncMock:
    ws = AsyncMock()
    ws.headers = {"sec-websocket-protocol": subproto or ""}
    ws.query_params = {}
    ws.cookies = {"procta_access": cookie} if cookie else {}
    ws.accept = AsyncMock()
    ws.close = AsyncMock()

    messages = list(receive_messages or [])
    messages.append(WebSocketDisconnectSignal())

    async def _receive_text():
        msg = messages.pop(0)
        if isinstance(msg, WebSocketDisconnectSignal):
            raise type("WebSocketDisconnect", (Exception,), {})(code=1000)
        return msg

    ws.receive_text = _receive_text
    ws.send_json = AsyncMock()
    ws.send_bytes = AsyncMock()
    return ws


@pytest.fixture(autouse=True)
def _patch_admin_auth():
    """Patch verify_admin_token to accept 'admin-token' (sync)."""
    def _verify(token: str):
        if token == "admin-token":
            return {"id": "teacher-1", "email": "t@test.com", "org_role": "teacher"}
        raise HTTPException(401)
    with patch("app.routers.chat.verify_admin_token", side_effect=_verify):
        yield


@pytest.mark.asyncio
async def test_teacher_rate_limited_closes_4400():
    ws = _make_teacher_ws()
    limiter = MagicMock()
    limiter.check_and_increment = AsyncMock(return_value=False)
    limiter.decrement = AsyncMock()
    with patch("app.routers.chat.ws_rate_limiter", limiter):
        from app.routers.chat import ws_chat_teacher
        await ws_chat_teacher(ws)
    ws.close.assert_called_with(code=4400, reason="rate_limited")


@pytest.mark.asyncio
async def test_teacher_no_auth_closes_4401():
    ws = _make_teacher_ws(subproto="")
    from app.routers.chat import ws_chat_teacher
    await ws_chat_teacher(ws)
    ws.close.assert_called_with(code=4401)


@pytest.mark.asyncio
async def test_teacher_superadmin_closes_4403():
    def _superadmin(token: str):
        return {"id": "super-1", "email": "s@test.com", "org_role": "superadmin"}
    ws = _make_teacher_ws()
    with patch("app.routers.chat.verify_admin_token", side_effect=_superadmin):
        from app.routers.chat import ws_chat_teacher
        await ws_chat_teacher(ws)
    ws.close.assert_called_with(code=4403, reason="monitor_only")


@pytest.mark.asyncio
async def test_teacher_cookie_auth_fallback():
    ws = _make_teacher_ws(subproto="", cookie="cookie-token")

    def _cookie_verify(token: str):
        if token == "cookie-token":
            return {"id": "teacher-1", "email": "t@test.com", "org_role": "teacher"}
        raise HTTPException(401)

    with patch("app.routers.chat.verify_admin_token", side_effect=_cookie_verify):
        from app.routers.chat import ws_chat_teacher
        await ws_chat_teacher(ws)
    ws.accept.assert_called_once()


@pytest.mark.asyncio
async def test_teacher_happy_path_accepts():
    ws = _make_teacher_ws(receive_messages=[
        json.dumps({"type": "msg", "text": "Hello", "session_id": "sess-1"})
    ])
    from app.routers.chat import ws_chat_teacher
    await ws_chat_teacher(ws)
    ws.accept.assert_called_once()


@pytest.mark.asyncio
async def test_teacher_broadcast():
    ws = _make_teacher_ws(receive_messages=[
        json.dumps({"type": "broadcast", "text": "Hi everyone"})
    ])
    with patch("app.routers.chat.chat_hub.teacher_broadcast") as mock_bc:
        from app.routers.chat import ws_chat_teacher
        await ws_chat_teacher(ws)
        mock_bc.assert_called_once_with("teacher-1", "Hi everyone")


@pytest.mark.asyncio
async def test_teacher_msg_no_target_skipped():
    ws = _make_teacher_ws(receive_messages=[
        json.dumps({"type": "msg", "text": "Hello", "session_id": ""})
    ])
    with patch("app.routers.chat.chat_hub.teacher_send") as mock_send:
        from app.routers.chat import ws_chat_teacher
        await ws_chat_teacher(ws)
        mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_teacher_bad_json_ignored():
    ws = _make_teacher_ws(receive_messages=["not json"])
    from app.routers.chat import ws_chat_teacher
    await ws_chat_teacher(ws)
    assert True  # no crash


@pytest.mark.asyncio
async def test_teacher_reauth_mismatch():
    def _reauth_verify(token: str):
        if token == "admin-token":
            return {"id": "teacher-1", "email": "t@test.com", "org_role": "teacher"}
        if token == "reauth-token":
            return {"id": "other-teacher", "email": "o@test.com"}
        raise HTTPException(status_code=401)

    ws = _make_teacher_ws(receive_messages=[
        json.dumps({"type": "reauth", "token": "reauth-token"})
    ])
    with patch("app.routers.chat.verify_admin_token", side_effect=_reauth_verify):
        from app.routers.chat import ws_chat_teacher
        await ws_chat_teacher(ws)
    ws.send_json.assert_any_call({"type": "reauth_failed", "reason": "mismatch"})


@pytest.mark.asyncio
async def test_teacher_long_text_truncated():
    from app.constants import CHAT_MAX_TEXT_LEN
    long_text = "x" * (CHAT_MAX_TEXT_LEN + 100)
    ws = _make_teacher_ws(receive_messages=[
        json.dumps({"type": "msg", "text": long_text, "session_id": "sess-1"})
    ])
    with patch("app.routers.chat.chat_hub.teacher_send") as mock_send:
        from app.routers.chat import ws_chat_teacher
        await ws_chat_teacher(ws)
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        assert len(args[2]) == CHAT_MAX_TEXT_LEN
