"""Fixtures for the in-memory chat hub (services/chat.py).

The hub relays proctor↔student messages. The security-critical invariants
are tenant isolation (a teacher can only message/broadcast to their own
students) and message integrity (caller-supplied `extra` fields can't
overwrite core fields like sender/type — otherwise a directive could be
spoofed). We also pin delivery/fanout, presence, history cap, and
socket-replacement. A FakeWebSocket captures sends; no real I/O.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.constants import CHAT_HISTORY_LIMIT
from app.services.chat import ChatHub


class FakeWS:
    def __init__(self):
        self.sent = []
        self.closed = None

    async def send_json(self, payload):
        self.sent.append(payload)

    async def close(self, code=None, reason=None):
        self.closed = (code, reason)


@pytest.fixture
def hub():
    h = ChatHub()
    # Suppress the background cleanup task: the guard no-ops when this is
    # already set, so no asyncio task is spawned during the test.
    h._cleanup_task = "noop"
    return h


def _msgs(ws, kind=None):
    return [m for m in ws.sent if kind is None or m.get("type") == kind]


@pytest.mark.asyncio
async def test_make_msg_shape(hub):
    m = hub._make_msg(sender="teacher", session_id="s1", text="hi")
    assert m["type"] == "msg" and m["sender"] == "teacher"
    assert m["session_id"] == "s1" and m["text"] == "hi"
    assert m["id"] and m["ts"]


@pytest.mark.asyncio
async def test_extra_cannot_overwrite_core_fields(hub):
    sws, tws = FakeWS(), FakeWS()
    await hub.register_teacher("t1", tws)
    await hub.register_student(session_id="s1", teacher_id="t1", roll="R1", name="A", ws=sws)
    msg = await hub.teacher_send("t1", "s1", "stop", kind="terminate_directive",
                                 extra={"sender": "HACK", "type": "msg", "reason_code": "rc1"})
    assert msg["sender"] == "teacher"          # not overwritten
    assert msg["type"] == "terminate_directive"  # not overwritten
    assert msg["reason_code"] == "rc1"          # genuinely-new extra applied


@pytest.mark.asyncio
async def test_teacher_send_cross_tenant_blocked(hub):
    sws = FakeWS()
    await hub.register_student(session_id="s1", teacher_id="t1", roll="R1", name="A", ws=sws)
    # A different teacher must not be able to message t1's student.
    assert await hub.teacher_send("t2", "s1", "hi") is None
    assert _msgs(sws, "msg") == []


@pytest.mark.asyncio
async def test_student_send_unknown_session_returns_none(hub):
    assert await hub.student_send("ghost", "hi") is None


@pytest.mark.asyncio
async def test_student_message_fans_out_to_own_teacher(hub):
    sws, tws = FakeWS(), FakeWS()
    await hub.register_teacher("t1", tws)
    await hub.register_student(session_id="s1", teacher_id="t1", roll="R1", name="A", ws=sws)
    await hub.student_send("s1", "hello")
    teacher_chat = _msgs(tws, "msg")
    assert any(m["text"] == "hello" and m["sender"] == "student" for m in teacher_chat)
    assert any(m["text"] == "hello" for m in _msgs(sws, "msg"))  # echoed to student too


@pytest.mark.asyncio
async def test_broadcast_only_reaches_own_tenant(hub):
    s1, s2 = FakeWS(), FakeWS()
    await hub.register_student(session_id="a", teacher_id="t1", roll="R1", name="A", ws=s1)
    await hub.register_student(session_id="b", teacher_id="t2", roll="R2", name="B", ws=s2)
    delivered = await hub.teacher_broadcast("t1", "exam ends in 5m")
    assert delivered == 1
    assert _msgs(s1, "broadcast") and not _msgs(s2, "broadcast")


@pytest.mark.asyncio
async def test_presence_notified_on_register_and_unregister(hub):
    tws = FakeWS()
    await hub.register_teacher("t1", tws)
    sws = FakeWS()
    await hub.register_student(session_id="s1", teacher_id="t1", roll="R1", name="A", ws=sws)
    await hub.unregister_student("s1")
    presence = _msgs(tws, "presence")
    assert any(p["online"] is True for p in presence)
    assert any(p["online"] is False for p in presence)


@pytest.mark.asyncio
async def test_history_capped_at_limit(hub):
    sws = FakeWS()
    await hub.register_student(session_id="s1", teacher_id="t1", roll="R1", name="A", ws=sws)
    for i in range(CHAT_HISTORY_LIMIT + 10):
        await hub.student_send("s1", f"m{i}")
    thread = hub._thread("t1", "s1")
    assert len(thread) == CHAT_HISTORY_LIMIT


@pytest.mark.asyncio
async def test_reconnect_replaces_and_closes_old_socket(hub):
    old, new = FakeWS(), FakeWS()
    await hub.register_student(session_id="s1", teacher_id="t1", roll="R1", name="A", ws=old)
    await hub.register_student(session_id="s1", teacher_id="t1", roll="R1", name="A", ws=new)
    assert old.closed is not None and old.closed[0] == 4000  # old socket closed
    assert hub.student_conns["s1"] is new
