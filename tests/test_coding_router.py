"""Tests for the Edge Compiler judge + test-case delivery endpoints.

Covers POST /api/v1/coding/judge and GET /api/v1/coding/testcases — the two
server-public endpoints for the coding-assessment engine. Mirrors the mocking
style from tests/test_rough_sheet_endpoints.py.
"""
import json
import os
import sys
from contextlib import nullcontext
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.conftest import make_student_token


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


def _patches(config, table_data, recorder):
    async def _fake_access(claims, session_id):
        return None
    async def _fake_config(tid=None, exam_id=None):
        return config
    return (
        patch("app.routers.coding._assert_student_session_access", side_effect=_fake_access),
        patch("app.routers.coding._load_exam_config", side_effect=_fake_config),
        patch("app.routers.coding._atable", side_effect=_make_atable(table_data, recorder)),
        patch("app.routers.coding.system_context", return_value=nullcontext()),
        patch("app.routers.coding.reserve_idempotency", return_value=(True, None)),
    )


def _hdr(roll="ALICE001", tid="teacher-1", eid="exam-1"):
    return {"Authorization": f"Bearer {make_student_token(roll=roll, tid=tid, eid=eid)}"}


def _judge_body(**overrides):
    body = {
        "session_id": "ALICE001_exam-1",
        "question_id": "coding-q-1",
        "language": "javascript",
        "source": "console.log(require('fs').readFileSync('/dev/stdin','utf8').trim().split(' ').reduce((a,b)=>+a+(+b)))",
        "outputs": ["5", "10"],
        "metrics": {"average_execution_ms": 12, "memory_consumed_kb": 4096},
        "telemetry": {"keystroke_rhythm_variance": 0.05, "paste_attempts": 0, "focus_loss_count": 1},
    }
    body.update(overrides)
    return body


class TestJudge:
    """POST /api/v1/coding/judge — server-authoritative grading."""

    def test_judge_happy_path_returns_passed_total(self, client):
        """Correct outputs produce passed=total with a 200."""
        rec = {}
        table_data = {
            "coding_submissions": [],
            "coding_test_cases": [
                {"idx": 0, "expected_output": "5", "float_tolerance": None},
                {"idx": 1, "expected_output": "10", "float_tolerance": None},
            ],
        }
        p1, p2, p3, p4, p5 = _patches(_config(), table_data, rec)
        with p1, p2, p3, p4, p5:
            resp = client.post("/api/v1/coding/judge",
                               json=_judge_body(),
                               headers=_hdr())
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body == {"passed": 2, "total": 2}

    def test_judge_no_per_case_or_expected_leaked(self, client):
        """Response MUST contain only {passed, total} — never per_case or expected."""
        rec = {}
        table_data = {
            "coding_submissions": [],
            "coding_test_cases": [
                {"idx": 0, "expected_output": "5", "float_tolerance": None},
            ],
        }
        p1, p2, p3, p4, p5 = _patches(_config(), table_data, rec)
        with p1, p2, p3, p4, p5:
            resp = client.post("/api/v1/coding/judge",
                               json=_judge_body(outputs=["5"]),
                               headers=_hdr())
        assert resp.status_code == 200
        assert set(resp.json().keys()) == {"passed", "total"}

    def test_judge_teacher_id_stamped_from_jwt_not_body(self, client):
        """teacher_id on the inserted row must come from the JWT (tid claim),
        even when a bogus teacher_id is present in the request body."""
        rec = {}
        table_data = {
            "coding_submissions": [],
            "coding_test_cases": [
                {"idx": 0, "expected_output": "5", "float_tolerance": None},
                {"idx": 1, "expected_output": "10", "float_tolerance": None},
            ],
        }
        p1, p2, p3, p4, p5 = _patches(_config(), table_data, rec)
        with p1, p2, p3, p4, p5:
            resp = client.post("/api/v1/coding/judge",
                               json=_judge_body(teacher_id="evil-teacher"),
                               headers=_hdr(tid="teacher-1"))
        assert resp.status_code == 200
        insert_records = rec.get("coding_submissions", [])
        assert len(insert_records) == 1
        assert insert_records[0]["teacher_id"] == "teacher-1"
        assert insert_records[0]["teacher_id"] != "evil-teacher"

    def test_judge_submit_cap_returns_429(self, client):
        """When prior submission count >= coding_max_submit_attempts, return 429."""
        rec = {}
        # 10 prior rows = at the cap of 10
        prior = [{"id": f"prior-{i}"} for i in range(10)]
        table_data = {
            "coding_submissions": prior,
            "coding_test_cases": [],
        }
        p1, p2, p3, p4, p5 = _patches(_config(max_attempts=10), table_data, rec)
        with p1, p2, p3, p4, p5:
            resp = client.post("/api/v1/coding/judge",
                               json=_judge_body(outputs=["5"]),
                               headers=_hdr())
        assert resp.status_code == 429
        assert "limit" in resp.text.lower()

    def test_judge_submit_under_cap_succeeds(self, client):
        """At cap-1, the submission is accepted."""
        rec = {}
        prior = [{"id": f"prior-{i}"} for i in range(9)]  # 9 < 10
        table_data = {
            "coding_submissions": prior,
            "coding_test_cases": [
                {"idx": 0, "expected_output": "5", "float_tolerance": None},
            ],
        }
        p1, p2, p3, p4, p5 = _patches(_config(max_attempts=10), table_data, rec)
        with p1, p2, p3, p4, p5:
            resp = client.post("/api/v1/coding/judge",
                               json=_judge_body(outputs=["5"]),
                               headers=_hdr())
        assert resp.status_code == 200
        assert resp.json()["passed"] == 1

    def test_judge_partial_pass(self, client):
        """Mixed correct/wrong outputs yield the right fraction."""
        rec = {}
        table_data = {
            "coding_submissions": [],
            "coding_test_cases": [
                {"idx": 0, "expected_output": "1", "float_tolerance": None},
                {"idx": 1, "expected_output": "2", "float_tolerance": None},
                {"idx": 2, "expected_output": "3", "float_tolerance": None},
            ],
        }
        p1, p2, p3, p4, p5 = _patches(_config(), table_data, rec)
        with p1, p2, p3, p4, p5:
            resp = client.post("/api/v1/coding/judge",
                               json=_judge_body(outputs=["1", "WRONG", "3"]),
                               headers=_hdr())
        assert resp.status_code == 200
        assert resp.json() == {"passed": 2, "total": 3}

    def test_judge_missing_session_id_400(self, client):
        p1, p2, p3, p4, p5 = _patches(_config(), {}, {})
        with p1, p2, p3, p4, p5:
            resp = client.post("/api/v1/coding/judge",
                               json=_judge_body(session_id=""),
                               headers=_hdr())
        assert resp.status_code == 400

    def test_judge_missing_question_id_400(self, client):
        p1, p2, p3, p4, p5 = _patches(_config(), {}, {})
        with p1, p2, p3, p4, p5:
            resp = client.post("/api/v1/coding/judge",
                               json=_judge_body(question_id=""),
                               headers=_hdr())
        assert resp.status_code == 400

    def test_judge_outputs_not_list_400(self, client):
        rec = {}
        p1, p2, p3, p4, p5 = _patches(_config(), {}, rec)
        with p1, p2, p3, p4, p5:
            resp = client.post("/api/v1/coding/judge",
                               json=_judge_body(outputs="not-a-list"),
                               headers=_hdr())
        assert resp.status_code == 400

    def test_judge_unauthorized_no_token(self, client):
        resp = client.post("/api/v1/coding/judge",
                           json=_judge_body())
        assert resp.status_code in (401, 403)

    def test_judge_idempotency_double_fire(self, client):
        """A duplicate idempotency key (same session+question+outputs) writes
        exactly ONE row and returns the same response on both calls."""
        rec = {}
        table_data = {
            "coding_submissions": [],
            "coding_test_cases": [
                {"idx": 0, "expected_output": "5", "float_tolerance": None},
                {"idx": 1, "expected_output": "10", "float_tolerance": None},
            ],
        }

        # Override reserve_idempotency: first call acquires, second call is cached
        call_count = [0]
        idem_responses = [(True, None), (False, {"passed": 2, "total": 2})]
        async def _fake_reserve(key, ttl=300):
            resp = idem_responses[call_count[0]]
            call_count[0] += 1
            return resp

        p1, p2, p3, p4, p5 = _patches(_config(), table_data, rec)
        with p1, p2, p3, p4 as _, \
             patch("app.routers.coding.reserve_idempotency", side_effect=_fake_reserve):
            r1 = client.post("/api/v1/coding/judge",
                             json=_judge_body(),
                             headers=_hdr())
            r2 = client.post("/api/v1/coding/judge",
                             json=_judge_body(),
                             headers=_hdr())
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json() == r2.json()
        assert len(rec.get("coding_submissions", [])) == 1

    def test_judge_429_default_cap_when_not_in_config(self, client):
        """When no coding_max_submit_attempts in config, default is 10."""
        rec = {}
        # cap defaults to 10; 10 prior rows should trigger 429
        prior = [{"id": f"prior-{i}"} for i in range(10)]
        table_data = {"coding_submissions": prior, "coding_test_cases": []}
        p1, p2, p3, p4, p5 = _patches(_config(max_attempts=None), table_data, rec)
        with p1, p2, p3, p4, p5:
            resp = client.post("/api/v1/coding/judge",
                               json=_judge_body(outputs=["5"]),
                               headers=_hdr())
        assert resp.status_code == 429

    def test_judge_stores_telemetry_and_source(self, client):
        """The coding_submissions row includes source_code and telemetry."""
        rec = {}
        table_data = {
            "coding_submissions": [],
            "coding_test_cases": [
                {"idx": 0, "expected_output": "5", "float_tolerance": None},
            ],
        }
        p1, p2, p3, p4, p5 = _patches(_config(), table_data, rec)
        with p1, p2, p3, p4, p5:
            resp = client.post("/api/v1/coding/judge",
                               json=_judge_body(outputs=["5"]),
                               headers=_hdr())
        assert resp.status_code == 200
        row = rec["coding_submissions"][0]
        assert row["source_code"] == _judge_body()["source"]
        assert row["keystroke_rhythm_variance"] == 0.05
        assert row["paste_attempts"] == 0
        assert row["focus_loss_count"] == 1
        assert row["average_execution_ms"] == 12
        assert row["memory_consumed_kb"] == 4096

    def test_judge_stores_exam_id_from_jwt(self, client):
        """exam_id and student_id in the row come from JWT claims."""
        rec = {}
        table_data = {
            "coding_submissions": [],
            "coding_test_cases": [
                {"idx": 0, "expected_output": "5", "float_tolerance": None},
            ],
        }
        p1, p2, p3, p4, p5 = _patches(_config(), table_data, rec)
        with p1, p2, p3, p4, p5:
            resp = client.post("/api/v1/coding/judge",
                               json=_judge_body(outputs=["5"]),
                               headers=_hdr(eid="exam-coding-42"))
        assert resp.status_code == 200
        row = rec["coding_submissions"][0]
        assert row["exam_id"] == "exam-coding-42"
        assert row["question_id"] == "coding-q-1"
        assert row["language"] == "javascript"

    def test_judge_float_tolerance_passed_to_judge(self, client):
        """float_tolerance from coding_test_cases flows into judge_outputs
        (tested via the judge comparison; we just verify the endpoint doesn't
        drop the tolerance before calling judge_outputs)."""
        rec = {}
        table_data = {
            "coding_submissions": [],
            "coding_test_cases": [
                {"idx": 0, "expected_output": "0.30000000004", "float_tolerance": 1e-6},
            ],
        }
        p1, p2, p3, p4, p5 = _patches(_config(), table_data, rec)
        with p1, p2, p3, p4, p5:
            resp = client.post("/api/v1/coding/judge",
                               json=_judge_body(outputs=["0.3"]),
                               headers=_hdr())
        assert resp.status_code == 200
        assert resp.json()["passed"] == 1


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

        class _CodingChain:
            """Mock chain that records .eq() calls and returns self for every
            builder method, so the fluent API from the router works."""
            def __init__(self, eq_store=None):
                self._eq_store = eq_store if eq_store is not None else []
                self._data = []

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
            def insert(self, *a, **kw): return self
            def update(self, *a, **kw): return self
            def delete(self, *a, **kw): return self

            async def execute(self):
                r = MagicMock()
                r.data = self._data
                return r

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
