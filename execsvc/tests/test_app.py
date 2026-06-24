import threading
import time

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch
from execsvc.app import app
from execsvc.runner import ExecResult

client = TestClient(app)

_VALID = {"language": "python", "source": "x", "stdin": "",
          "cpu_ms": 2000, "wall_ms": 4000, "mem_mb": 256, "output_kb": 64}


@pytest.fixture(autouse=True)
def _no_ambient_auth(monkeypatch):
    """Make these tests independent of the host's EXEC_SERVICE_AUTH env. The
    deploy box sets it, which would 401 every un-tokened request here. The
    auth-specific test re-patches it to a real value inside its own `with`."""
    monkeypatch.setattr("execsvc.app.EXEC_SERVICE_AUTH", "")
    yield


def test_run_returns_envelope():
    fake = ExecResult("Hello, World!\n", "", 0, 12, False, False, None)
    with patch("execsvc.app.run_in_isolate", return_value=fake):
        r = client.post("/run", json={"language": "python", "source": "x", "stdin": "",
                                       "cpu_ms": 2000, "wall_ms": 4000, "mem_mb": 256, "output_kb": 64})
    assert r.status_code == 200
    b = r.json()
    assert b["stdout"] == "Hello, World!\n" and b["timed_out"] is False and b["exit_code"] == 0


def test_run_rejects_unknown_language():
    r = client.post("/run", json={"language": "cobol", "source": "x", "stdin": "",
                                   "cpu_ms": 1, "wall_ms": 1, "mem_mb": 1, "output_kb": 1})
    assert r.status_code == 400


def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_auth_enforced_when_configured():
    """When EXEC_SERVICE_AUTH is set, /run requires the matching bearer token."""
    fake = ExecResult("ok\n", "", 0, 1, False, False, None)
    with patch("execsvc.app.EXEC_SERVICE_AUTH", "s3cret"), \
         patch("execsvc.app.run_in_isolate", return_value=fake):
        # missing token → 401
        assert client.post("/run", json=_VALID).status_code == 401
        # wrong token → 401
        assert client.post("/run", json=_VALID,
                           headers={"Authorization": "Bearer nope"}).status_code == 401
        # correct token → 200
        assert client.post("/run", json=_VALID,
                           headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_auth_runs_before_body_validation():
    """Auth-first: an un-tokened request is 401'd by the middleware before the
    body is even validated — a garbage body returns 401, not 422."""
    with patch("execsvc.app.EXEC_SERVICE_AUTH", "s3cret"):
        assert client.post("/run", json={}).status_code == 401          # no token
        assert client.post("/run", json={},
                           headers={"Authorization": "Bearer s3cret"}).status_code == 422  # token ok → body checked


def test_oversized_source_rejected():
    """A source above the cap is rejected before any execution (422)."""
    big = {**_VALID, "source": "a" * (256 * 1024 + 1)}
    assert client.post("/run", json=big).status_code == 422


def test_concurrent_runs_use_distinct_boxes():
    """Regression for the box-id collision: concurrent runs must each get a
    UNIQUE isolate box, or they clobber each other's sandbox (silent wrong
    scores). Holds each box briefly to force overlap; asserts all distinct.
    (Also can't pass against the old code, which never passed a box_id.)"""
    seen: list[int] = []
    lock = threading.Lock()

    def fake_run(language, source, stdin, limits, box_id):
        with lock:
            seen.append(box_id)
        time.sleep(0.15)  # hold the box so concurrent runs can't reuse it
        return ExecResult("ok\n", "", 0, 1, False, False, None)

    with patch("execsvc.app.run_in_isolate", side_effect=fake_run):
        threads = [threading.Thread(target=lambda: client.post("/run", json=_VALID))
                   for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert len(seen) == 4
    assert len(set(seen)) == 4  # every concurrent run got its own box


def test_box_not_returned_until_sandbox_thread_finishes_on_cancel():
    """If the orchestrator times out / disconnects mid-run, the endpoint
    coroutine is cancelled — but the un-cancellable to_thread keeps running
    isolate on its box. The box must NOT go back to the pool until that thread
    truly finishes, or a new run grabs the same box and collides (the silent-
    wrong-score class the pool exists to prevent)."""
    import asyncio
    from execsvc import app as A

    started = threading.Event()
    release = threading.Event()

    def blocking(language, source, stdin, limits, box_id):
        started.set()
        release.wait(2)
        return ExecResult("ok\n", "", 0, 1, False, False, None)

    async def scenario():
        free0 = A._box_ids.qsize()
        with patch("execsvc.app.run_in_isolate", side_effect=blocking):
            task = asyncio.ensure_future(
                A.run(A.RunRequest(**_VALID), authorization=None))
            for _ in range(200):           # wait until the box is checked out
                if started.is_set():
                    break
                await asyncio.sleep(0.01)
            assert started.is_set()
            assert A._box_ids.qsize() == free0 - 1

            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            await asyncio.sleep(0.05)
            # The sandbox thread is still blocked → the box must still be OUT.
            assert A._box_ids.qsize() == free0 - 1, \
                "box returned to pool while the sandbox thread is still running"

            release.set()
            for _ in range(200):           # box returns once the thread ends
                if A._box_ids.qsize() == free0:
                    break
                await asyncio.sleep(0.01)
            assert A._box_ids.qsize() == free0

    asyncio.run(scenario())
