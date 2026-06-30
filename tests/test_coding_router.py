"""Tests for the Edge Compiler server-side execution endpoints.

Covers POST /api/v1/coding/run (sample, server-run), POST /api/v1/coding/judge
(hidden, server-run + graded) and GET /api/v1/coding/testcases. The execution
service is mocked via app.routers.coding.run_one — these tests never touch a
real sandbox.
"""
import base64
import os
import sys
from contextlib import nullcontext
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.conftest import make_student_token
from app.services.exec_client import ExecResult, ExecUnavailable
from app.services import secrets_crypto

_TEST_SECRETS_KEY = base64.b64encode(b"\x07" * 32).decode()


def _exec_result(stdout="", stderr="", exit_code=0, time_ms=10, timed_out=False,
                  oom=False, compile_error=None):
    return ExecResult(stdout=stdout, stderr=stderr, exit_code=exit_code, time_ms=time_ms,
                      timed_out=timed_out, oom=oom, compile_error=compile_error)


def _config(max_attempts=10):
    return {
        "exam_title": "Coding Exam",
        "duration_minutes": 60,
        "coding_max_submit_attempts": max_attempts,
    }


def _make_atable(table_data, recorder=None):
    """Mock _atable: returns per-table data from *table_data* dict keyed by
    table name.  *recorder* (optional) captures every insert payload for
    assertion.

    When a value in *table_data* is a callable (rather than a list), the
    callable receives the mock chain and returns the list to return from
    ``.execute()``.  This lets tests handle queries where the same table
    returns different data depending on filter criteria (e.g. sample vs.
    hidden coding_test_cases).
    """
    def _factory(name):
        chain = MagicMock()
        for attr in ("select", "eq", "is_", "in_", "order", "limit",
                     "single", "range", "gte", "lte", "update", "delete"):
            getattr(chain, attr).return_value = chain

        if recorder is not None:
            def _insert(payload, *a, **k):
                recorder.setdefault(name, []).append(payload)
                return chain
            chain.insert.side_effect = _insert

        raw = table_data.get(name, [])
        if callable(raw):
            rows = raw(chain) or []
        else:
            rows = raw
        async def _execute():
            r = MagicMock()
            r.data = rows
            return r
        chain.execute = _execute
        return chain
    return _factory


def _patches(config, table_data, recorder, run_one_mock=None):
    async def _fake_access(claims, session_id):
        return None
    async def _fake_config(tid=None, exam_id=None):
        return config
    patches = [
        patch("app.routers.coding._assert_student_session_access", side_effect=_fake_access),
        patch("app.routers.coding._load_exam_config", side_effect=_fake_config),
        patch("app.routers.coding._atable", side_effect=_make_atable(table_data, recorder)),
        patch("app.routers.coding.system_context", return_value=nullcontext()),
        patch("app.routers.coding.reserve_idempotency", return_value=(True, None)),
    ]
    if run_one_mock is not None:
        patches.append(patch("app.routers.coding.run_one", run_one_mock))
    return patches


def _hdr(roll="ALICE001", tid="teacher-1", eid="exam-1"):
    return {"Authorization": f"Bearer {make_student_token(roll=roll, tid=tid, eid=eid)}"}


def _judge_body(**overrides):
    body = {
        "session_id": "ALICE001_exam-1",
        "question_id": "coding-q-1",
        "language": "javascript",
        "source": "console.log(5)",
        "telemetry": {"keystroke_rhythm_variance": 0.05, "paste_attempts": 0, "focus_loss_count": 1},
    }
    body.update(overrides)
    return body


def _run_body(**overrides):
    body = {
        "session_id": "ALICE001_exam-1",
        "question_id": "coding-q-1",
        "language": "javascript",
        "source": "console.log(5)",
    }
    body.update(overrides)
    return body


# `questions` table options lookup (_question_time_limit_ms) — empty by
# default so the endpoint falls back to DEFAULT_TIME_LIMIT_MS.
_NO_QUESTION_OPTIONS = {"questions": []}


@pytest.fixture(autouse=True)
def _reset_secrets_key_cache():
    """secrets_crypto caches the parsed CODING_SECRETS_KEY for process
    lifetime; reset around every test so monkeypatch.setenv takes effect."""
    secrets_crypto.reset_key_cache()
    yield
    secrets_crypto.reset_key_cache()


class TestRun:
    """POST /api/v1/coding/run — server executes the SAMPLE cases."""

    def test_run_sample_executes_and_returns_cases(self, client):
        rec = {}
        table_data = {
            **_NO_QUESTION_OPTIONS,
            "coding_test_cases": [
                {"idx": 0, "input": "", "expected_output": "Hello, World!"},
            ],
        }
        mock_run = MagicMock(return_value=_exec_result(stdout="Hello, World!\n", time_ms=5))
        patches = _patches(_config(), table_data, rec, run_one_mock=mock_run)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            resp = client.post("/api/v1/coding/run",
                               json=_run_body(source="print('Hello, World!')", language="python"),
                               headers=_hdr())
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1 and body["passed"] == 1
        assert body["cases"][0]["status"] == "passed"
        assert body["cases"][0]["expected_output"] == "Hello, World!"
        assert body["cases"][0]["output"] == "Hello, World!\n"

    def test_run_executor_never_sent_expected_output(self, client):
        """CRITICAL INVARIANT: run_one() is never called with the expected
        output anywhere in its arguments."""
        rec = {}
        table_data = {
            **_NO_QUESTION_OPTIONS,
            "coding_test_cases": [
                {"idx": 0, "input": "2 3", "expected_output": "SECRET-EXPECTED-5"},
            ],
        }
        mock_run = MagicMock(return_value=_exec_result(stdout="wrong"))
        patches = _patches(_config(), table_data, rec, run_one_mock=mock_run)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            client.post("/api/v1/coding/run", json=_run_body(), headers=_hdr())
        for call in mock_run.call_args_list:
            assert "SECRET-EXPECTED-5" not in str(call)

    def test_run_decrypts_encrypted_expected_output(self, client, monkeypatch):
        """When the stored expected_output is an enc:v1: token (row written
        after CODING_SECRETS_KEY was configured), /coding/run must decrypt it
        before comparing — proving the read path actually decrypts."""
        monkeypatch.setenv("CODING_SECRETS_KEY", _TEST_SECRETS_KEY)
        secrets_crypto.reset_key_cache()
        encrypted_expected = secrets_crypto.encrypt("Hello, World!")
        assert secrets_crypto.is_encrypted(encrypted_expected)
        rec = {}
        table_data = {
            **_NO_QUESTION_OPTIONS,
            "coding_test_cases": [
                {"idx": 0, "input": "", "expected_output": encrypted_expected},
            ],
        }
        mock_run = MagicMock(return_value=_exec_result(stdout="Hello, World!\n", time_ms=5))
        patches = _patches(_config(), table_data, rec, run_one_mock=mock_run)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            resp = client.post("/api/v1/coding/run",
                               json=_run_body(source="print('Hello, World!')", language="python"),
                               headers=_hdr())
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1 and body["passed"] == 1
        assert body["cases"][0]["status"] == "passed"
        # Sample expected_output is returned to the client decrypted (plaintext).
        assert body["cases"][0]["expected_output"] == "Hello, World!"

    def test_run_failing_case_reports_failed_status(self, client):
        rec = {}
        table_data = {
            **_NO_QUESTION_OPTIONS,
            "coding_test_cases": [
                {"idx": 0, "input": "", "expected_output": "expected"},
            ],
        }
        mock_run = MagicMock(return_value=_exec_result(stdout="actual"))
        patches = _patches(_config(), table_data, rec, run_one_mock=mock_run)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            resp = client.post("/api/v1/coding/run", json=_run_body(), headers=_hdr())
        body = resp.json()
        assert body["passed"] == 0 and body["total"] == 1
        assert body["cases"][0]["status"] == "failed"

    def test_run_float_tolerance_used_for_comparison(self, client):
        """A SAMPLE case with float_tolerance must be graded with tolerance on
        Run too (matching /judge) — otherwise a float answer shows a false
        "Wrong Answer" on Run that Submit would Accept."""
        rec = {}
        table_data = {
            **_NO_QUESTION_OPTIONS,
            "coding_test_cases": [
                {"idx": 0, "input": "", "expected_output": "0.30000000004", "float_tolerance": 1e-6},
            ],
        }
        mock_run = MagicMock(return_value=_exec_result(stdout="0.3"))
        patches = _patches(_config(), table_data, rec, run_one_mock=mock_run)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            resp = client.post("/api/v1/coding/run", json=_run_body(), headers=_hdr())
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["passed"] == 1 and body["total"] == 1
        assert body["cases"][0]["status"] == "passed"

    def test_run_no_sample_cases_returns_zero_total(self, client):
        rec = {}
        table_data = {**_NO_QUESTION_OPTIONS, "coding_test_cases": []}
        mock_run = MagicMock()
        patches = _patches(_config(), table_data, rec, run_one_mock=mock_run)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            resp = client.post("/api/v1/coding/run", json=_run_body(), headers=_hdr())
        assert resp.status_code == 200
        assert resp.json() == {"cases": [], "passed": 0, "total": 0}
        mock_run.assert_not_called()

    def test_run_missing_session_id_400(self, client):
        patches = _patches(_config(), {}, {})
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            resp = client.post("/api/v1/coding/run", json=_run_body(session_id=""), headers=_hdr())
        assert resp.status_code == 400

    def test_run_unauthorized_no_token(self, client):
        resp = client.post("/api/v1/coding/run", json=_run_body())
        assert resp.status_code in (401, 403)


class TestJudge:
    """POST /api/v1/coding/judge — server runs HIDDEN cases and grades them."""

    def test_judge_happy_path_returns_passed_total(self, client):
        rec = {}
        table_data = {
            **_NO_QUESTION_OPTIONS,
            "coding_submissions": [],
            "coding_test_cases": [
                {"idx": 0, "input": "", "expected_output": "5", "float_tolerance": None},
                {"idx": 1, "input": "", "expected_output": "10", "float_tolerance": None},
            ],
        }
        mock_run = MagicMock(side_effect=[
            _exec_result(stdout="5", time_ms=3),
            _exec_result(stdout="10", time_ms=5),
        ])
        patches = _patches(_config(), table_data, rec, run_one_mock=mock_run)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            resp = client.post("/api/v1/coding/judge", json=_judge_body(), headers=_hdr())
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["passed"] == 2 and body["total"] == 2
        assert body["average_execution_ms"] == 4

    def test_judge_decrypts_encrypted_hidden_expected_output(self, client, monkeypatch):
        """When the stored hidden expected_output is an enc:v1: token,
        /coding/judge must decrypt it before grading — proving the grading
        read path actually decrypts (and never leaks the decrypted value)."""
        monkeypatch.setenv("CODING_SECRETS_KEY", _TEST_SECRETS_KEY)
        secrets_crypto.reset_key_cache()
        enc_5 = secrets_crypto.encrypt("5")
        enc_10 = secrets_crypto.encrypt("10")
        assert secrets_crypto.is_encrypted(enc_5) and secrets_crypto.is_encrypted(enc_10)
        rec = {}
        table_data = {
            **_NO_QUESTION_OPTIONS,
            "coding_submissions": [],
            "coding_test_cases": [
                {"idx": 0, "input": "", "expected_output": enc_5, "float_tolerance": None},
                {"idx": 1, "input": "", "expected_output": enc_10, "float_tolerance": None},
            ],
        }
        mock_run = MagicMock(side_effect=[
            _exec_result(stdout="5", time_ms=3),
            _exec_result(stdout="10", time_ms=5),
        ])
        patches = _patches(_config(), table_data, rec, run_one_mock=mock_run)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            resp = client.post("/api/v1/coding/judge", json=_judge_body(), headers=_hdr())
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["passed"] == 2 and body["total"] == 2
        # Never leaked: response only has the aggregate counts.
        assert set(body.keys()) == {"passed", "total", "average_execution_ms"}

    def test_judge_runs_hidden_and_never_sends_expected(self, client):
        """CRITICAL INVARIANT: the executor is never sent the secret expected
        output — only language, source, stdin (limits) ever reach run_one().
        The expected output is a distinct marker NOT present in the
        student's source, so this actually proves the secret never leaks."""
        rec = {}
        table_data = {
            **_NO_QUESTION_OPTIONS,
            "coding_submissions": [],
            "coding_test_cases": [
                {"idx": 0, "input": "", "expected_output": "SECRET-EXPECTED-42", "float_tolerance": None},
            ],
        }
        mock_run = MagicMock(return_value=_exec_result(stdout="SECRET-EXPECTED-42", time_ms=7))
        patches = _patches(_config(), table_data, rec, run_one_mock=mock_run)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            resp = client.post("/api/v1/coding/judge",
                               json=_judge_body(language="python", source="print('Hello, World!')"),
                               headers=_hdr())
        b = resp.json()
        assert b["passed"] == b["total"] == 1
        for call in mock_run.call_args_list:
            assert "SECRET-EXPECTED-42" not in str(call)
        # Sanity: run_one WAS called with language/source/stdin positionally.
        args, kwargs = mock_run.call_args
        assert args[0] == "python" and args[1] == "print('Hello, World!')"

    def test_judge_no_per_case_or_expected_leaked(self, client):
        rec = {}
        table_data = {
            **_NO_QUESTION_OPTIONS,
            "coding_submissions": [],
            "coding_test_cases": [
                {"idx": 0, "input": "", "expected_output": "5", "float_tolerance": None},
            ],
        }
        mock_run = MagicMock(return_value=_exec_result(stdout="5"))
        patches = _patches(_config(), table_data, rec, run_one_mock=mock_run)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            resp = client.post("/api/v1/coding/judge", json=_judge_body(), headers=_hdr())
        assert resp.status_code == 200
        assert set(resp.json().keys()) == {"passed", "total", "average_execution_ms"}

    def test_judge_teacher_id_stamped_from_jwt_not_body(self, client):
        rec = {}
        table_data = {
            **_NO_QUESTION_OPTIONS,
            "coding_submissions": [],
            "coding_test_cases": [
                {"idx": 0, "input": "", "expected_output": "5", "float_tolerance": None},
            ],
        }
        mock_run = MagicMock(return_value=_exec_result(stdout="5"))
        patches = _patches(_config(), table_data, rec, run_one_mock=mock_run)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            resp = client.post("/api/v1/coding/judge",
                               json=_judge_body(teacher_id="evil-teacher"),
                               headers=_hdr(tid="teacher-1"))
        assert resp.status_code == 200
        insert_records = rec.get("coding_submissions", [])
        assert len(insert_records) == 1
        assert insert_records[0]["teacher_id"] == "teacher-1"
        assert insert_records[0]["teacher_id"] != "evil-teacher"

    def test_judge_submit_cap_returns_429(self, client):
        rec = {}
        prior = [{"id": f"prior-{i}"} for i in range(10)]
        table_data = {**_NO_QUESTION_OPTIONS, "coding_submissions": prior, "coding_test_cases": []}
        mock_run = MagicMock()
        patches = _patches(_config(max_attempts=10), table_data, rec, run_one_mock=mock_run)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            resp = client.post("/api/v1/coding/judge", json=_judge_body(), headers=_hdr())
        assert resp.status_code == 429
        assert "limit" in resp.text.lower()
        mock_run.assert_not_called()

    def test_judge_submit_under_cap_succeeds(self, client):
        rec = {}
        prior = [{"id": f"prior-{i}"} for i in range(9)]
        table_data = {
            **_NO_QUESTION_OPTIONS,
            "coding_submissions": prior,
            "coding_test_cases": [
                {"idx": 0, "input": "", "expected_output": "5", "float_tolerance": None},
            ],
        }
        mock_run = MagicMock(return_value=_exec_result(stdout="5"))
        patches = _patches(_config(max_attempts=10), table_data, rec, run_one_mock=mock_run)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            resp = client.post("/api/v1/coding/judge", json=_judge_body(), headers=_hdr())
        assert resp.status_code == 200
        assert resp.json()["passed"] == 1

    def test_judge_partial_pass(self, client):
        rec = {}
        table_data = {
            **_NO_QUESTION_OPTIONS,
            "coding_submissions": [],
            "coding_test_cases": [
                {"idx": 0, "input": "", "expected_output": "1", "float_tolerance": None},
                {"idx": 1, "input": "", "expected_output": "2", "float_tolerance": None},
                {"idx": 2, "input": "", "expected_output": "3", "float_tolerance": None},
            ],
        }
        mock_run = MagicMock(side_effect=[
            _exec_result(stdout="1"), _exec_result(stdout="WRONG"), _exec_result(stdout="3"),
        ])
        patches = _patches(_config(), table_data, rec, run_one_mock=mock_run)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            resp = client.post("/api/v1/coding/judge", json=_judge_body(), headers=_hdr())
        assert resp.status_code == 200
        body = resp.json()
        assert body["passed"] == 2 and body["total"] == 3

    def test_judge_missing_session_id_400(self, client):
        patches = _patches(_config(), {}, {})
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            resp = client.post("/api/v1/coding/judge", json=_judge_body(session_id=""), headers=_hdr())
        assert resp.status_code == 400

    def test_judge_missing_question_id_400(self, client):
        patches = _patches(_config(), {}, {})
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            resp = client.post("/api/v1/coding/judge", json=_judge_body(question_id=""), headers=_hdr())
        assert resp.status_code == 400

    def test_judge_unauthorized_no_token(self, client):
        resp = client.post("/api/v1/coding/judge", json=_judge_body())
        assert resp.status_code in (401, 403)

    def test_judge_idempotency_double_fire(self, client):
        """A duplicate idempotency key (same session+question+source) writes
        exactly ONE row and returns the same response on both calls."""
        rec = {}
        table_data = {
            **_NO_QUESTION_OPTIONS,
            "coding_submissions": [],
            "coding_test_cases": [
                {"idx": 0, "input": "", "expected_output": "5", "float_tolerance": None},
                {"idx": 1, "input": "", "expected_output": "10", "float_tolerance": None},
            ],
        }
        mock_run = MagicMock(side_effect=[
            _exec_result(stdout="5"), _exec_result(stdout="10"),
            _exec_result(stdout="5"), _exec_result(stdout="10"),
        ])

        call_count = [0]
        idem_responses = [(True, None), (False, {"passed": 2, "total": 2, "average_execution_ms": 10})]
        async def _fake_reserve(key, ttl=300):
            resp = idem_responses[call_count[0]]
            call_count[0] += 1
            return resp

        patches = _patches(_config(), table_data, rec, run_one_mock=mock_run)
        with patches[0], patches[1], patches[2], patches[3], \
             patch("app.routers.coding.reserve_idempotency", side_effect=_fake_reserve), \
             patches[5]:
            r1 = client.post("/api/v1/coding/judge", json=_judge_body(), headers=_hdr())
            r2 = client.post("/api/v1/coding/judge", json=_judge_body(), headers=_hdr())
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json() == r2.json()
        assert len(rec.get("coding_submissions", [])) == 1

    def test_judge_429_default_cap_when_not_in_config(self, client):
        rec = {}
        prior = [{"id": f"prior-{i}"} for i in range(10)]
        table_data = {**_NO_QUESTION_OPTIONS, "coding_submissions": prior, "coding_test_cases": []}
        mock_run = MagicMock()
        patches = _patches(_config(max_attempts=None), table_data, rec, run_one_mock=mock_run)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            resp = client.post("/api/v1/coding/judge", json=_judge_body(), headers=_hdr())
        assert resp.status_code == 429

    def test_judge_stores_telemetry_and_source(self, client):
        rec = {}
        table_data = {
            **_NO_QUESTION_OPTIONS,
            "coding_submissions": [],
            "coding_test_cases": [
                {"idx": 0, "input": "", "expected_output": "5", "float_tolerance": None},
            ],
        }
        mock_run = MagicMock(return_value=_exec_result(stdout="5"))
        patches = _patches(_config(), table_data, rec, run_one_mock=mock_run)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            resp = client.post("/api/v1/coding/judge", json=_judge_body(), headers=_hdr())
        assert resp.status_code == 200
        row = rec["coding_submissions"][0]
        assert row["source_code"] == _judge_body()["source"]
        assert row["keystroke_rhythm_variance"] == 0.05
        assert row["paste_attempts"] == 0
        assert row["focus_loss_count"] == 1

    def test_judge_stores_exam_id_from_jwt(self, client):
        rec = {}
        table_data = {
            **_NO_QUESTION_OPTIONS,
            "coding_submissions": [],
            "coding_test_cases": [
                {"idx": 0, "input": "", "expected_output": "5", "float_tolerance": None},
            ],
        }
        mock_run = MagicMock(return_value=_exec_result(stdout="5"))
        patches = _patches(_config(), table_data, rec, run_one_mock=mock_run)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            resp = client.post("/api/v1/coding/judge", json=_judge_body(), headers=_hdr(eid="exam-coding-42"))
        assert resp.status_code == 200
        row = rec["coding_submissions"][0]
        assert row["exam_id"] == "exam-coding-42"
        assert row["question_id"] == "coding-q-1"
        assert row["language"] == "javascript"

    def test_judge_float_tolerance_used_for_comparison(self, client):
        rec = {}
        table_data = {
            **_NO_QUESTION_OPTIONS,
            "coding_submissions": [],
            "coding_test_cases": [
                {"idx": 0, "input": "", "expected_output": "0.30000000004", "float_tolerance": 1e-6},
            ],
        }
        mock_run = MagicMock(return_value=_exec_result(stdout="0.3"))
        patches = _patches(_config(), table_data, rec, run_one_mock=mock_run)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            resp = client.post("/api/v1/coding/judge", json=_judge_body(), headers=_hdr())
        assert resp.status_code == 200
        assert resp.json()["passed"] == 1

    def test_judge_compile_error_fails_all_cases_and_is_stored(self, client):
        rec = {}
        table_data = {
            **_NO_QUESTION_OPTIONS,
            "coding_submissions": [],
            "coding_test_cases": [
                {"idx": 0, "input": "", "expected_output": "5", "float_tolerance": None},
                {"idx": 1, "input": "", "expected_output": "10", "float_tolerance": None},
            ],
        }
        mock_run = MagicMock(return_value=_exec_result(compile_error="SyntaxError: bad token"))
        patches = _patches(_config(), table_data, rec, run_one_mock=mock_run)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            resp = client.post("/api/v1/coding/judge", json=_judge_body(), headers=_hdr())
        assert resp.status_code == 200
        body = resp.json()
        assert body["passed"] == 0 and body["total"] == 2
        assert rec["coding_submissions"][0]["compile_output"] == "SyntaxError: bad token"


class TestJudgeFailurePolicy:
    """Task 5.5 — executor unavailability never silently fails or auto-fails;
    it's a retryable 503 and writes no submission row (LeetCode-style wait)."""

    def test_judge_503_on_executor_unavailable(self, client):
        rec = {}
        table_data = {
            **_NO_QUESTION_OPTIONS,
            "coding_submissions": [],
            "coding_test_cases": [
                {"idx": 0, "input": "", "expected_output": "5", "float_tolerance": None},
            ],
        }
        mock_run = MagicMock(side_effect=ExecUnavailable("executor down"))
        patches = _patches(_config(), table_data, rec, run_one_mock=mock_run)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            resp = client.post("/api/v1/coding/judge", json=_judge_body(), headers=_hdr())
        assert resp.status_code == 503
        assert resp.json()["detail"]["retryable"] is True
        assert rec.get("coding_submissions", []) == []

    def test_judge_503_releases_idempotency_for_retry(self, client):
        """A failed (503) attempt must release its idempotency reservation so
        the kiosk's auto-retry isn't wedged behind a stale in-flight marker."""
        rec = {}
        table_data = {
            **_NO_QUESTION_OPTIONS,
            "coding_submissions": [],
            "coding_test_cases": [
                {"idx": 0, "input": "", "expected_output": "5", "float_tolerance": None},
            ],
        }
        mock_run = MagicMock(side_effect=ExecUnavailable("executor down"))
        released = []
        async def _fake_release(key):
            released.append(key)
        patches = _patches(_config(), table_data, rec, run_one_mock=mock_run)
        with patches[0], patches[1], patches[2], patches[3], \
             patch("app.routers.coding.release_idempotency", side_effect=_fake_release), \
             patches[5]:
            resp = client.post("/api/v1/coding/judge", json=_judge_body(), headers=_hdr())
        assert resp.status_code == 503
        assert len(released) >= 1


class TestTestcases:
    """GET /api/v1/coding/testcases — test-case delivery."""

    def test_sample_includes_expected_hidden_omits(self, client):
        """Sample cases return {idx, input, expected_output} but hidden cases
        return {idx, input} WITHOUT expected_output."""
        ALL_CASES = [
            {"idx": 0, "input": "2 3", "expected_output": "5", "visibility": "sample"},
            {"idx": 1, "input": "10 20", "expected_output": "30", "visibility": "sample"},
            {"idx": 2, "input": "100 200", "visibility": "hidden"},
            {"idx": 3, "input": "5 7", "visibility": "hidden"},
        ]

        class _LazyCodingChain:
            """Like _CodingChain but evaluates a callable at execute() time so
            filter chains (.eq() calls) are available for inspection."""
            def __init__(self, data_or_callable):
                self._eq_store = []
                self._source = data_or_callable

            def select(self, *a, **kw): return self
            def eq(self, *a, **kw):
                if len(a) == 2:
                    self._eq_store.append(a)
                return self
            def is_(self, *a, **kw): return self
            def order(self, *a, **kw): return self
            def limit(self, *a, **kw): return self
            def single(self, *a, **kw): return self
            def range(self, *a, **kw): return self
            def gte(self, *a, **kw): return self
            def lte(self, *a, **kw): return self
            def in_(self, *a, **kw): return self

            async def execute(self):
                if callable(self._source):
                    rows = self._source(self) or []
                else:
                    rows = self._source
                r = MagicMock()
                r.data = rows
                return r

        def _make_filtering_atable(table_data):
            def _factory(name):
                raw = table_data.get(name, [])
                return _LazyCodingChain(raw)
            return _factory

        def _filter_cases(chain):
            eq_store = getattr(chain, '_eq_store', [])
            selected = list(ALL_CASES)
            for key, val in eq_store:
                if key == "visibility":
                    selected = [r for r in selected if r.get("visibility") == val]
            return selected

        async def _fake_access(claims, session_id):
            return None
        p_access = patch("app.routers.coding._assert_student_session_access",
                         side_effect=_fake_access)
        p_atable = patch("app.routers.coding._atable",
                         side_effect=_make_filtering_atable(
                             {"coding_test_cases": _filter_cases}))
        p_ctx = patch("app.routers.coding.system_context", return_value=nullcontext())

        with p_access, p_atable, p_ctx:
            resp = client.get("/api/v1/coding/testcases",
                              params={"session_id": "ALICE001_exam-1",
                                      "question_id": "coding-q-1"},
                              headers=_hdr())
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["sample"]) == 2
        assert body["sample"][0] == {"idx": 0, "input": "2 3", "expected_output": "5"}
        assert "expected_output" in body["sample"][0]

        assert len(body["hidden_inputs"]) == 2
        assert body["hidden_inputs"][0] == {"idx": 2, "input": "100 200"}
        assert "expected_output" not in body["hidden_inputs"][0]

    def test_missing_params_400(self, client):
        async def _fake_access(claims, session_id):
            return None
        p_access = patch("app.routers.coding._assert_student_session_access",
                         side_effect=_fake_access)
        with p_access:
            resp = client.get("/api/v1/coding/testcases",
                              headers=_hdr())
        assert resp.status_code == 400

    def test_unauthorized_no_token(self, client):
        resp = client.get("/api/v1/coding/testcases",
                          params={"session_id": "s1", "question_id": "q1"})
        assert resp.status_code in (401, 403)
