"""Tests for app/services/exec_client.py — the app→execution-service HTTP
client. The service itself is mocked (no Linux/KVM needed); this only checks
that we POST the right shape, parse the response, and raise ExecUnavailable
on connection failure / 5xx.
"""
import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.services.exec_client import run_one, ExecLimits, ExecUnavailable


def test_run_one_posts_and_parses():
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"stdout": "42\n", "stderr": "", "exit_code": 0, "time_ms": 9,
                              "timed_out": False, "oom": False, "compile_error": None}
    with patch("app.services.exec_client._post", return_value=resp):
        r = run_one("python", "print(42)", "", ExecLimits(2000, 4000, 256, 64))
    assert r.stdout == "42\n" and r.time_ms == 9 and r.timed_out is False
    assert r.exit_code == 0 and r.oom is False and r.compile_error is None


def test_run_one_raises_on_connection_error():
    import httpx
    with patch("app.services.exec_client._post", side_effect=httpx.ConnectError("boom")):
        with pytest.raises(ExecUnavailable):
            run_one("python", "print(1)", "", ExecLimits(2000, 4000, 256, 64))


def test_run_one_raises_on_timeout():
    import httpx
    with patch("app.services.exec_client._post", side_effect=httpx.TimeoutException("boom")):
        with pytest.raises(ExecUnavailable):
            run_one("python", "print(1)", "", ExecLimits(2000, 4000, 256, 64))


def test_run_one_raises_on_5xx():
    resp = MagicMock(status_code=503)
    resp.text = "service unavailable"
    with patch("app.services.exec_client._post", return_value=resp):
        with pytest.raises(ExecUnavailable):
            run_one("python", "print(1)", "", ExecLimits(2000, 4000, 256, 64))


def test_run_one_sends_auth_header_and_no_expected_output():
    """The executor must never receive an 'expected_output' field — only
    language/source/stdin/limits go over the wire."""
    captured = {}
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"stdout": "ok", "stderr": "", "exit_code": 0, "time_ms": 1,
                              "timed_out": False, "oom": False, "compile_error": None}

    def _fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return resp

    with patch("app.services.exec_client._post", side_effect=_fake_post):
        run_one("python", "print('secret-expected-output')", "stdin-data",
                ExecLimits(2000, 4000, 256, 64))

    assert "expected_output" not in captured["json"]
    assert captured["json"]["language"] == "python"
    assert captured["json"]["stdin"] == "stdin-data"
    assert "Authorization" in captured["headers"] or "X-Exec-Auth" in captured["headers"]
