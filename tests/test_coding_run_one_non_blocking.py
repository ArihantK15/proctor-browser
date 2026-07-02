"""run_one() must not block the shared asyncio event loop.

run_one() is a SYNCHRONOUS httpx.Client call to execsvc (up to
EXEC_CLIENT_TIMEOUT=20s). Production runs only 4 uvicorn workers total for
the ENTIRE API (see entrypoint.sh / docker-compose.yml) — a single worker's
event loop is shared by every request that lands on it: other students'
exam heartbeats, logins, submissions, everything. Calling a blocking
function directly from inside an `async def` route handler (instead of via
asyncio.to_thread) freezes that whole worker for the call's duration, not
just the coding request that triggered it. With hidden test suites running
several cases sequentially and only 4 workers total, a handful of
concurrent /coding/judge calls during a live exam could stall the entire
platform.

This proves the fix holds: a slow (mocked) run_one call running in one
concurrent request must NOT prevent a concurrent, unrelated /health request
on the same event loop from completing well before it.
"""
import asyncio
import contextlib
import os
import sys
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.conftest import make_student_token
from app.services.exec_client import ExecResult

pytestmark = pytest.mark.asyncio


def _slow_exec_result(delay_s):
    """A synchronous stand-in for run_one() that blocks for `delay_s` —
    exactly what a real execsvc HTTP round-trip does, just without the
    network. If run_one() is invoked directly (not via asyncio.to_thread),
    this time.sleep() blocks the whole event loop."""
    def _run(*_args, **_kwargs):
        time.sleep(delay_s)
        return ExecResult(stdout="5", stderr="", exit_code=0, time_ms=int(delay_s * 1000),
                           timed_out=False, oom=False, compile_error=None)
    return MagicMock(side_effect=_run)


def _chain(data=None):
    m = MagicMock()
    m._data = data if data is not None else []
    for a in ("select", "eq", "is_", "in_", "order", "limit", "insert", "update"):
        getattr(m, a).return_value = m
    async def _execute():
        r = MagicMock()
        r.data = m._data
        return r
    m.execute = _execute
    return m


def _make_atable(table_data):
    def _factory(name):
        return _chain(table_data.get(name, []))
    return _factory


async def test_slow_coding_run_does_not_block_concurrent_health_request():
    from app.main import app

    table_data = {
        "coding_test_cases": [
            {"idx": 0, "input": "", "expected_output": "5", "float_tolerance": None},
        ],
    }

    async def _fake_access(claims, session_id):
        return None

    patches = [
        patch("app.routers.coding._assert_student_session_access", side_effect=_fake_access),
        patch("app.routers.coding._atable", side_effect=_make_atable(table_data)),
        patch("app.routers.coding.system_context", return_value=contextlib.nullcontext()),
        patch("app.routers.coding.run_one", _slow_exec_result(0.6)),
    ]

    hdr = {"Authorization": f"Bearer {make_student_token(roll='ALICE001', tid='teacher-1', eid='exam-1')}"}
    body = {
        "session_id": "ALICE001_exam-1",
        "question_id": "coding-q-1",
        "language": "javascript",
        "source": "console.log(5)",
    }

    with contextlib.ExitStack() as es:
        for p in patches:
            es.enter_context(p)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            health_done_at = []

            async def _slow_coding_call():
                return await client.post("/api/v1/coding/run", json=body, headers=hdr)

            async def _fast_health_probe():
                # Give the slow call a head start so it's genuinely in-flight
                # (blocked inside time.sleep) when this fires.
                await asyncio.sleep(0.1)
                resp = await client.get("/health")
                health_done_at.append(time.monotonic())
                return resp

            start = time.monotonic()
            coding_resp, health_resp = await asyncio.gather(_slow_coding_call(), _fast_health_probe())

    assert coding_resp.status_code == 200, coding_resp.text
    # /health's own DB check may report unhealthy in this mocked environment
    # (unrelated to what's being tested here) — only its RESPONSE TIMING
    # matters: whether the event loop could schedule and complete it at all
    # while the coding call was still sleeping.
    assert health_resp.status_code in (200, 503)
    # The health probe was fired ~0.1s in, while the coding call was still
    # sleeping for another ~0.5s. If run_one() blocked the event loop, the
    # health request couldn't be scheduled until the coding call finished —
    # it would land at ~0.6s+, not ~0.1-0.3s.
    health_elapsed = health_done_at[0] - start
    assert health_elapsed < 0.45, (
        f"/health took {health_elapsed:.3f}s to complete while a slow coding "
        f"run was in flight — the event loop was blocked (run_one() must be "
        f"called via asyncio.to_thread, not directly)"
    )
