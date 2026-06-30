"""Tests for chat service: ChatHub message routing and session management.

WebSocket close-code testing is skipped because the TestClient's
async event loop handling makes them unreliable.  Those belong in
integration / e2e tests.
"""

import json
import os
import sys
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")

from tests.conftest import make_admin_token, make_student_token, shared_supabase_mock


@pytest.fixture(autouse=True)
def _clean_chathub():
    from app.routers.chat import chat_hub
    chat_hub.threads.clear()
    chat_hub.student_conns.clear()
    chat_hub.teacher_conns.clear()
    chat_hub.student_meta.clear()
    chat_hub.teacher_last_seen.clear()
    chat_hub._last_pong.clear()
    yield


# ═══════════════════════════════════════════════════════════════════
#  ChatHub Service
# ═══════════════════════════════════════════════════════════════════

class TestChatHubService:

    def test_student_send_adds_to_thread(self):
        from app.routers.chat import chat_hub
        import asyncio
        ws = AsyncMock()
        asyncio.run(chat_hub.register_student(
            session_id="sess-1", teacher_id="t1",
            roll="ALICE", name="Alice", ws=ws,
        ))
        asyncio.run(chat_hub.student_send("sess-1", "Hello"))
        hist = chat_hub._thread("t1", "sess-1")
        assert len(hist) > 0
        assert hist[-1]["text"] == "Hello"

    def test_student_send_without_meta_returns_none(self):
        from app.routers.chat import chat_hub
        import asyncio
        result = asyncio.run(chat_hub.student_send("orphan-session", "Hi"))
        assert result is None

    def test_teacher_send_without_meta_no_crash(self):
        from app.routers.chat import chat_hub
        import asyncio
        asyncio.run(chat_hub.teacher_send("t1", "ghost-session", "Hello"))
        assert True

    def test_register_student_creates_meta(self):
        from app.routers.chat import chat_hub
        import asyncio
        ws = AsyncMock()
        asyncio.run(chat_hub.register_student(
            session_id="sess-1", teacher_id="t1",
            roll="ALICE", name="Alice", ws=ws,
        ))
        assert "sess-1" in chat_hub.student_conns
        assert chat_hub.student_meta.get("sess-1", {}).get("roll") == "ALICE"

    def test_register_teacher_creates_conn(self):
        from app.routers.chat import chat_hub
        import asyncio
        ws = AsyncMock()
        asyncio.run(chat_hub.register_teacher("t1", ws))
        assert ws in chat_hub.teacher_conns.get("t1", set())

    def test_unregister_student_removes_conn(self):
        from app.routers.chat import chat_hub
        import asyncio
        ws = AsyncMock()
        asyncio.run(chat_hub.register_student(
            session_id="sess-1", teacher_id="t1",
            roll="ALICE", name="Alice", ws=ws,
        ))
        asyncio.run(chat_hub.unregister_student("sess-1"))
        assert "sess-1" not in chat_hub.student_conns

    def test_unregister_teacher_removes_socket(self):
        from app.routers.chat import chat_hub
        import asyncio
        ws1, ws2 = AsyncMock(), AsyncMock()
        asyncio.run(chat_hub.register_teacher("t1", ws1))
        asyncio.run(chat_hub.register_teacher("t1", ws2))
        asyncio.run(chat_hub.unregister_teacher("t1", ws1))
        assert ws1 not in chat_hub.teacher_conns.get("t1", set())
        assert ws2 in chat_hub.teacher_conns.get("t1", set())

    def test_text_passthrough_no_truncation(self):
        """ChatHub passes text through — router truncates."""
        from app.routers.chat import chat_hub
        import asyncio
        ws = AsyncMock()
        asyncio.run(chat_hub.register_student(
            session_id="sess-long", teacher_id="t1",
            roll="ALICE", name="Alice", ws=ws,
        ))
        long = "x" * 5000
        asyncio.run(chat_hub.student_send("sess-long", long))
        msgs = chat_hub._thread("t1", "sess-long")
        assert len(msgs) > 0
        assert len(msgs[-1]["text"]) == 5000

    def test_message_has_required_fields(self):
        from app.routers.chat import chat_hub
        import asyncio
        ws = AsyncMock()
        asyncio.run(chat_hub.register_student(
            session_id="sess-1", teacher_id="t1",
            roll="ALICE", name="Alice", ws=ws,
        ))
        asyncio.run(chat_hub.student_send("sess-1", "Hello"))
        msgs = chat_hub._thread("t1", "sess-1")
        assert len(msgs) > 0
        msg = msgs[-1]
        assert "type" in msg
        assert "text" in msg
        assert "sender" in msg
        assert "ts" in msg
        assert "session_id" in msg
        assert msg["type"] == "msg"
        assert msg["sender"] == "student"

    def test_student_send_empty_text_is_stored(self):
        """ChatHub does not filter empty text — router does."""
        from app.routers.chat import chat_hub
        import asyncio
        ws = AsyncMock()
        asyncio.run(chat_hub.register_student(
            session_id="sess-1", teacher_id="t1",
            roll="ALICE", name="Alice", ws=ws,
        ))
        asyncio.run(chat_hub.student_send("sess-1", ""))
        msgs = chat_hub._thread("t1", "sess-1")
        assert len(msgs) == 1  # empty string is still stored

    def test_teacher_broadcast_no_students_no_crash(self):
        from app.routers.chat import chat_hub
        import asyncio
        asyncio.run(chat_hub.teacher_broadcast("t1", "Hi everyone"))
        assert True

    def test_student_send_fanout_to_teacher(self):
        from app.routers.chat import chat_hub
        import asyncio
        teacher_ws = AsyncMock()
        teacher_ws.send_json = AsyncMock()
        asyncio.run(chat_hub.register_teacher("t1", teacher_ws))
        asyncio.run(chat_hub.register_student(
            session_id="sess-1", teacher_id="t1",
            roll="ALICE", name="Alice", ws=AsyncMock(),
        ))
        asyncio.run(chat_hub.student_send("sess-1", "Hello teacher"))
        assert teacher_ws.send_json.called
        call_args = teacher_ws.send_json.call_args[0][0]
        assert call_args["text"] == "Hello teacher"

    def test_presence_and_message_pipeline_teacher_sees_student(self):
        """End-to-end: student registers, teacher receives presence + roster,
        student sends message, teacher receives it."""
        from app.routers.chat import chat_hub
        import asyncio
        teacher_ws = AsyncMock()
        teacher_ws.send_json = AsyncMock()
        # Teacher connects first
        asyncio.run(chat_hub.register_teacher("t1", teacher_ws))
        # Clear the roster sent during register_teacher
        teacher_ws.send_json.reset_mock()
        # Student registers — teacher should get presence
        s_ws = AsyncMock()
        s_ws.send_json = AsyncMock()
        asyncio.run(chat_hub.register_student(
            session_id="sess-1", teacher_id="t1",
            roll="ALICE", name="Alice", ws=s_ws,
        ))
        presence_calls = [c for c in teacher_ws.send_json.call_args_list
                          if c[0][0].get("type") == "presence"]
        assert presence_calls, "teacher must receive presence on student register"
        assert presence_calls[0][0][0]["online"] is True
        assert presence_calls[0][0][0]["session_id"] == "sess-1"
        # Student sends a message — teacher should receive it
        teacher_ws.send_json.reset_mock()
        s_ws.send_json.reset_mock()
        asyncio.run(chat_hub.student_send("sess-1", "Hello from student"))
        teacher_msg_calls = [c for c in teacher_ws.send_json.call_args_list
                             if c[0][0].get("type") == "msg"]
        assert teacher_msg_calls, "teacher must receive student message"
        assert teacher_msg_calls[0][0][0]["text"] == "Hello from student"
        assert teacher_msg_calls[0][0][0]["sender"] == "student"
        assert teacher_msg_calls[0][0][0]["roll"] == "ALICE"
        assert teacher_msg_calls[0][0][0]["name"] == "Alice"
        # Student's own socket gets the echo
        s_student_msgs = [c for c in s_ws.send_json.call_args_list
                          if c[0][0].get("type") == "msg"]
        assert s_student_msgs, "student must receive echo of own message"
        # Teacher reconnecting later gets the student in the roster
        teacher_ws2 = AsyncMock()
        teacher_ws2.send_json = AsyncMock()
        asyncio.run(chat_hub.register_teacher("t1", teacher_ws2))
        roster_calls = [c for c in teacher_ws2.send_json.call_args_list
                        if c[0][0].get("type") == "roster"]
        assert roster_calls, "reconnecting teacher must receive roster"
        roster_sids = [s["session_id"] for s in roster_calls[0][0][0].get("sessions", [])]
        assert "sess-1" in roster_sids, "student must be in roster for same teacher"
        # Different teacher must NOT see this student
        teacher_ws3 = AsyncMock()
        teacher_ws3.send_json = AsyncMock()
        asyncio.run(chat_hub.register_teacher("t2", teacher_ws3))
        roster_calls_t2 = [c for c in teacher_ws3.send_json.call_args_list
                           if c[0][0].get("type") == "roster"]
        t2_sids = [s["session_id"] for s in (roster_calls_t2[0][0][0].get("sessions", []) if roster_calls_t2 else [])]
        assert "sess-1" not in t2_sids, "student must NOT appear in another teacher's roster"


# ═══════════════════════════════════════════════════════════════════
#  Heartbeat / liveness  (regression for the chat-reaped-after-30s bug)
# ═══════════════════════════════════════════════════════════════════

class TestChatHubHeartbeat:

    def test_heartbeat_does_not_reap_fresh_connections(self):
        """register_* seeds liveness so the first heartbeat sweep can't reap a
        brand-new socket. Before the fix _last_pong defaulted to 0.0, which is
        < deadline once the server is up 60s, so EVERY chat connection was
        closed ~30s after connecting (record_pong was never called)."""
        from app.routers.chat import chat_hub
        import asyncio
        s_ws, t_ws = AsyncMock(), AsyncMock()
        asyncio.run(chat_hub.register_student(
            session_id="sess-1", teacher_id="t1",
            roll="ALICE", name="Alice", ws=s_ws))
        asyncio.run(chat_hub.register_teacher("t1", t_ws))
        asyncio.run(chat_hub._send_heartbeats())
        assert "sess-1" in chat_hub.student_conns
        assert t_ws in chat_hub.teacher_conns.get("t1", set())
        assert not s_ws.close.called

    def test_heartbeat_reaps_stale_connection(self):
        """The reaper still works: a socket whose last pong is older than
        HEARTBEAT_TIMEOUT is closed and removed."""
        from app.routers.chat import chat_hub
        import asyncio
        import time
        s_ws = AsyncMock()
        asyncio.run(chat_hub.register_student(
            session_id="sess-stale", teacher_id="t1",
            roll="ALICE", name="Alice", ws=s_ws))
        chat_hub._last_pong[s_ws] = time.monotonic() - (chat_hub.HEARTBEAT_TIMEOUT + 10)
        asyncio.run(chat_hub._send_heartbeats())
        assert "sess-stale" not in chat_hub.student_conns
        assert s_ws.close.called

    def test_record_pong_refreshes_liveness(self):
        """An inbound pong refreshes liveness, keeping an idle socket alive
        across heartbeat sweeps."""
        from app.routers.chat import chat_hub
        import asyncio
        import time
        s_ws = AsyncMock()
        asyncio.run(chat_hub.register_student(
            session_id="sess-1", teacher_id="t1",
            roll="ALICE", name="Alice", ws=s_ws))
        chat_hub._last_pong[s_ws] = time.monotonic() - 1000
        asyncio.run(chat_hub.record_pong(s_ws))
        asyncio.run(chat_hub._send_heartbeats())
        assert "sess-1" in chat_hub.student_conns
        assert not s_ws.close.called
