from fastapi.testclient import TestClient
from unittest.mock import patch
from execsvc.app import app
from execsvc.runner import ExecResult

client = TestClient(app)


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
