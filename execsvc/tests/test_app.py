import threading
import time

from fastapi.testclient import TestClient
from unittest.mock import patch
from execsvc.app import app
from execsvc.runner import ExecResult

client = TestClient(app)

_VALID = {"language": "python", "source": "x", "stdin": "",
          "cpu_ms": 2000, "wall_ms": 4000, "mem_mb": 256, "output_kb": 64}


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
