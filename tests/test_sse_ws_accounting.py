"""Regression test for live-frame WS connection accounting (sse.py).

_ws_conn_count gates MAX_WS_PER_SESSION. It is incremented once per
connection in the handler and decremented once in _ws_unsubscribe (always
run in the handler's finally). _ws_broadcast must NOT also decrement it when
a send fails on a dead socket: that socket's own finally will unsubscribe
too, so decrementing in both places double-counts and drifts the per-session
count below reality — leaking the connection cap while a peer is still live.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.routers import sse


class _WS:
    def __init__(self, fail=False):
        self.fail = fail
        self.sent = []

    async def send_bytes(self, b):
        if self.fail:
            raise RuntimeError("socket dead")
        self.sent.append(b)


@pytest.fixture(autouse=True)
def _clean():
    sse._ws_clients.clear()
    sse._ws_conn_count.clear()
    yield
    sse._ws_clients.clear()
    sse._ws_conn_count.clear()


@pytest.mark.asyncio
async def test_broadcast_does_not_double_decrement_conn_count():
    sid = "sess-1"
    alive, dead = _WS(), _WS(fail=True)
    # Two connected sockets (handler increments the counter, then subscribes).
    sse._ws_conn_count[sid] = 2
    await sse._ws_subscribe(sid, alive)
    await sse._ws_subscribe(sid, dead)

    # Producer broadcasts a frame; the dead socket's send fails.
    await sse._ws_broadcast(sid, b"frame")

    # Broadcast prunes the dead socket from the send list but must leave the
    # counter alone (the dead socket's own finally owns the decrement).
    assert sse._ws_conn_count[sid] == 2
    assert dead not in sse._ws_clients[sid]
    assert alive in sse._ws_clients[sid]
    assert alive.sent == [b"frame"]

    # The dead socket's handler finally runs → exactly one decrement, leaving
    # the count equal to the one still-live connection.
    await sse._ws_unsubscribe(sid, dead)
    assert sse._ws_conn_count[sid] == 1

    # And the live socket eventually closes too → count fully cleared.
    await sse._ws_unsubscribe(sid, alive)
    assert sid not in sse._ws_conn_count
    assert sid not in sse._ws_clients


@pytest.mark.asyncio
async def test_subscribe_unsubscribe_balanced_single():
    sid = "sess-2"
    ws = _WS()
    sse._ws_conn_count[sid] = 1
    await sse._ws_subscribe(sid, ws)
    await sse._ws_unsubscribe(sid, ws)
    assert sid not in sse._ws_conn_count
    assert sid not in sse._ws_clients
