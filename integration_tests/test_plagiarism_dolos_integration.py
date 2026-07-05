"""Integration test against a REAL running dolos-svc — not mocked. This is
the actual detection logic that matters; a unit test with a mocked HTTP
response would only prove the Python code calls httpx.post correctly, not
that Dolos actually detects similarity.

Prerequisite: `docker compose up -d dolos-svc` (or run dolos-svc locally on
port 8801) before running this test.

Also needs DATABASE_URL set to ANY reachable Postgres, even though this
specific test never touches it — integration_tests/conftest.py's
pytest_collection_modifyitems unconditionally skips every test under this
directory when DATABASE_URL is unset (it can't tell this test doesn't need
a DB). Verified this is the real cause, not a dolos-svc problem: the
skipif's own condition (_dolos_svc_available()) evaluates correctly even
without DATABASE_URL — the directory-wide gate fires independently and
wins. Not worth changing that shared gate just for this one test.
"""
import os
import httpx
import pytest

DOLOS_SVC_URL = os.environ.get("DOLOS_SVC_URL", "http://localhost:8801")


def _dolos_svc_available() -> bool:
    try:
        return httpx.get(f"{DOLOS_SVC_URL}/health", timeout=2).is_success
    except Exception:
        return False


@pytest.mark.skipif(not _dolos_svc_available(), reason="dolos-svc not running")
def test_identical_python_submissions_flagged():
    resp = httpx.post(f"{DOLOS_SVC_URL}/compare", json={
        "language": "python",
        "submissions": [
            {"id": "a", "source_code": "def factorial(n):\n    if n <= 1:\n        return 1\n    return n * factorial(n - 1)\n"},
            {"id": "b", "source_code": "def factorial(n):\n    if n <= 1:\n        return 1\n    return n * factorial(n - 1)\n"},
        ],
    }, timeout=30)
    assert resp.is_success
    pairs = resp.json()["pairs"]
    assert len(pairs) == 1
    assert pairs[0]["similarity_score"] > 0.9


@pytest.mark.skipif(not _dolos_svc_available(), reason="dolos-svc not running")
def test_genuinely_different_submissions_not_flagged():
    resp = httpx.post(f"{DOLOS_SVC_URL}/compare", json={
        "language": "python",
        "submissions": [
            {"id": "a", "source_code": "def factorial(n):\n    if n <= 1:\n        return 1\n    return n * factorial(n - 1)\n"},
            {"id": "b", "source_code": "class Stack:\n    def __init__(self):\n        self.items = []\n    def push(self, x):\n        self.items.append(x)\n    def pop(self):\n        return self.items.pop()\n"},
        ],
    }, timeout=30)
    assert resp.is_success
    pairs = resp.json()["pairs"]
    assert len(pairs) == 0 or pairs[0]["similarity_score"] < 0.3
