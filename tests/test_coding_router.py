"""Tests for the Edge Compiler judge + test-case delivery endpoints (P1-T3/T4).

Asserts the four security invariants: hidden expected never serialized,
teacher_id from JWT not body, submit-attempt cap, idempotent insert.
"""
import contextlib
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.conftest import make_student_token  # noqa: E402


def _hdr():
    return {"Authorization": f"Bearer {make_student_token(roll='ALICE001', tid='teacher-1', eid='exam-A')}"}


def _make_atable(by_table, recorder):
    """Mock _atable. ``by_table`` maps table name -> the list returned by SELECT
    .execute(); inserts are recorded into ``recorder[name]``."""
    def _factory(name):
        chain = MagicMock()
        for attr in ("select", "eq", "is_", "in_", "order", "limit", "single", "range"):
            getattr(chain, attr).return_value = chain

        def _insert(payload, *a, **k):
            recorder.setdefault(name, []).append(payload)
            return chain
        chain.insert.side_effect = _insert

        async def _execute():
            return MagicMock(data=by_table.get(name, []))
        chain.execute = _execute
        return chain
    return _factory


def _patches(by_table, recorder, config=None, reserve=(True, None)):
    async def _access(claims, session_id):
        return None
    async def _cfg(tid=None, exam_id=None):
        return config or {}
    async def _reserve(key, ttl=None):
        return reserve
    async def _mark(key, resp):
        return None
    async def _release(key):
        return None
    return (
        patch("app.routers.coding._assert_student_session_access", side_effect=_access),
        patch("app.routers.coding._load_exam_config", side_effect=_cfg),
        patch("app.routers.coding._atable", side_effect=_make_atable(by_table, recorder)),
        patch("app.routers.coding.system_context", lambda: contextlib.nullcontext()),
        patch("app.routers.coding.reserve_idempotency", side_effect=_reserve),
        patch("app.routers.coding.mark_idempotent", side_effect=_mark),
        patch("app.routers.coding.release_idempotency", side_effect=_release),
    )


HIDDEN = [
    {"idx": 0, "expected_output": "3", "float_tolerance": None},
    {"idx": 1, "expected_output": "7", "float_tolerance": None},
]


class TestJudge:
    def test_happy_path_returns_counts_only(self, client):
        rec = {}
        ps = _patches({"coding_test_cases": HIDDEN, "coding_submissions": []}, rec)
        with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6]:
            resp = client.post("/api/v1/coding/judge", headers=_hdr(), json={
                "session_id": "ALICE001_1", "question_id": "q1", "language": "javascript",
                "source": "x", "outputs": ["3", "7"]})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body == {"passed": 2, "total": 2}        # counts ONLY
        assert "per_case" not in body and "expected_output" not in resp.text

    def test_teacher_id_from_jwt_not_body(self, client):
        rec = {}
        ps = _patches({"coding_test_cases": HIDDEN, "coding_submissions": []}, rec)
        with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6]:
            client.post("/api/v1/coding/judge", headers=_hdr(), json={
                "session_id": "ALICE001_1", "question_id": "q1", "language": "javascript",
                "source": "x", "outputs": ["3", "7"], "teacher_id": "HACKER-999"})
        row = rec["coding_submissions"][0]
        assert row["teacher_id"] == "teacher-1"          # JWT claim, not the body value

    def test_partial_pass_counts(self, client):
        rec = {}
        ps = _patches({"coding_test_cases": HIDDEN, "coding_submissions": []}, rec)
        with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6]:
            resp = client.post("/api/v1/coding/judge", headers=_hdr(), json={
                "session_id": "ALICE001_1", "question_id": "q1", "language": "javascript",
                "source": "x", "outputs": ["3", "WRONG"]})
        assert resp.json() == {"passed": 1, "total": 2}

    def test_submit_cap_returns_429(self, client):
        rec = {}
        # 10 prior submissions already → at the default cap.
        prior = [{"id": str(i)} for i in range(10)]
        ps = _patches({"coding_test_cases": HIDDEN, "coding_submissions": prior}, rec)
        with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6]:
            resp = client.post("/api/v1/coding/judge", headers=_hdr(), json={
                "session_id": "ALICE001_1", "question_id": "q1", "language": "javascript",
                "source": "x", "outputs": ["3", "7"]})
        assert resp.status_code == 429
        assert "coding_submissions" not in rec          # no new write at the cap

    def test_idempotent_replay_returns_cached_no_write(self, client):
        rec = {}
        ps = _patches({"coding_test_cases": HIDDEN, "coding_submissions": []}, rec,
                      reserve=(False, {"passed": 2, "total": 2}))
        with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6]:
            resp = client.post("/api/v1/coding/judge", headers=_hdr(), json={
                "session_id": "ALICE001_1", "question_id": "q1", "language": "javascript",
                "source": "x", "outputs": ["3", "7"]})
        assert resp.status_code == 200
        assert resp.json() == {"passed": 2, "total": 2}
        assert "coding_submissions" not in rec          # replay did not re-insert

    def test_outputs_must_be_list(self, client):
        rec = {}
        ps = _patches({"coding_test_cases": HIDDEN, "coding_submissions": []}, rec)
        with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6]:
            resp = client.post("/api/v1/coding/judge", headers=_hdr(), json={
                "session_id": "ALICE001_1", "question_id": "q1", "outputs": "notalist"})
        assert resp.status_code == 400


class TestTestcaseDelivery:
    def test_hidden_expected_never_serialized(self, client):
        rec = {}
        by_table = {"coding_test_cases": [
            {"idx": 0, "input": "1 2", "expected_output": "3"},   # SELECT decides what's present
        ]}
        ps = _patches(by_table, rec)
        with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6]:
            resp = client.get("/api/v1/coding/testcases?session_id=ALICE001_1&question_id=q1",
                              headers=_hdr())
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # hidden_inputs entries must never carry expected_output
        for h in body["hidden_inputs"]:
            assert "expected_output" not in h
        assert set(body.keys()) == {"sample", "hidden_inputs"}
