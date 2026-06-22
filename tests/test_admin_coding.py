"""Tests for the coding-question authoring endpoint (Phase 5).

POST/PUT /api/v1/admin/coding-question — the teacher/LLM path that creates a coding
question + its test cases (replacing the SQL-only seeding).
"""
import json
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.conftest import make_admin_token  # noqa: E402


def _hdr():
    return {"Authorization": f"Bearer {make_admin_token(teacher_id='teacher-1', email='p@t.com')}"}


def _atable_factory(rows_by_table, recorder):
    def _factory(name):
        chain = MagicMock()
        for a in ("select", "eq", "in_", "is_", "order", "limit", "delete", "update"):
            getattr(chain, a).return_value = chain

        def _insert(payload, *a, **k):
            recorder.setdefault(name, []).append(payload)
            return chain
        chain.insert.side_effect = _insert

        async def _execute():
            return MagicMock(data=rows_by_table.get(name, []))
        chain.execute = _execute
        return chain
    return _factory


def _patches(rows, rec):
    async def _admin(req):
        return {"id": "teacher-1"}
    return (
        patch("app.routers.admin_coding.require_admin", side_effect=_admin),
        patch("app.routers.admin_coding.assert_can_author"),
        patch("app.routers.admin_coding._cache"),
        patch("app.routers.admin_coding._atable", side_effect=_atable_factory(rows, rec)),
    )


_GOOD = {
    "exam_id": "exam-1",
    "question": "# Sum\nPrint a+b",
    "options": {"allowed_languages": ["javascript", "python"], "marks": 10,
                "marks_policy": "all_or_nothing", "time_limit_ms": 4000,
                "starter_code": "// go\n"},
    "test_cases": [
        {"input": "2 3", "expected_output": "5", "visibility": "sample"},
        {"input": "10 20", "expected_output": "30", "visibility": "hidden"},
    ],
}


class TestCreateCodingQuestion:
    def test_happy_path_creates_question_and_cases(self, client):
        rec = {}
        ps = _patches({}, rec)
        with ps[0], ps[1], ps[2], ps[3]:
            r = client.post("/api/v1/admin/coding-question", json=_GOOD, headers=_hdr())
        assert r.status_code == 200, r.text
        b = r.json()
        assert b["question_id"].startswith("coding-")
        assert b["test_cases"] == 2 and b["hidden"] == 1 and b["sample"] == 1
        assert b["replaced"] is False
        # questions row keyed on the minted label; options stored as JSON string
        qrow = rec["questions"][0]
        assert qrow["question_type"] == "coding"
        assert qrow["question_id"] == b["question_id"]
        opts = json.loads(qrow["options"])
        assert opts["marks"] == 10 and opts["allowed_languages"] == ["javascript", "python"]
        # coding_test_cases keyed on the SAME label
        assert all(c["question_id"] == b["question_id"] for c in rec["coding_test_cases"])

    def test_missing_exam_id(self, client):
        rec = {}; ps = _patches({}, rec)
        with ps[0], ps[1], ps[2], ps[3]:
            r = client.post("/api/v1/admin/coding-question",
                            json={**_GOOD, "exam_id": ""}, headers=_hdr())
        assert r.status_code == 400

    def test_requires_a_hidden_case(self, client):
        rec = {}; ps = _patches({}, rec)
        body = {**_GOOD, "test_cases": [{"input": "1", "expected_output": "1", "visibility": "sample"}]}
        with ps[0], ps[1], ps[2], ps[3]:
            r = client.post("/api/v1/admin/coding-question", json=body, headers=_hdr())
        assert r.status_code == 400
        assert "hidden" in r.json()["detail"].lower()

    def test_rejects_unsupported_language(self, client):
        rec = {}; ps = _patches({}, rec)
        body = {**_GOOD, "options": {**_GOOD["options"], "allowed_languages": ["rust"]}}
        with ps[0], ps[1], ps[2], ps[3]:
            r = client.post("/api/v1/admin/coding-question", json=body, headers=_hdr())
        assert r.status_code == 400

    def test_requires_expected_output(self, client):
        rec = {}; ps = _patches({}, rec)
        body = {**_GOOD, "test_cases": [{"input": "1", "visibility": "hidden"}]}
        with ps[0], ps[1], ps[2], ps[3]:
            r = client.post("/api/v1/admin/coding-question", json=body, headers=_hdr())
        assert r.status_code == 400

    def test_replace_requires_ownership(self, client):
        rec = {}; ps = _patches({"questions": []}, rec)  # not owned → empty
        body = {**_GOOD, "question_id": "coding-xyz"}
        with ps[0], ps[1], ps[2], ps[3]:
            r = client.post("/api/v1/admin/coding-question", json=body, headers=_hdr())
        assert r.status_code == 404


class TestGetCodingQuestion:
    def test_get_returns_question_and_cases(self, client):
        rec = {}
        rows = {
            "questions": [{"question_id": "coding-a", "exam_id": "exam-1",
                           "question": "# Q", "options": json.dumps({"marks": 5})}],
            "coding_test_cases": [{"idx": 0, "input": "1", "expected_output": "1",
                                   "visibility": "hidden", "float_tolerance": None}],
        }
        ps = _patches(rows, rec)
        with ps[0], ps[1], ps[2], ps[3]:
            r = client.get("/api/v1/admin/coding-question?question_id=coding-a", headers=_hdr())
        assert r.status_code == 200, r.text
        b = r.json()
        assert b["question_id"] == "coding-a" and b["options"]["marks"] == 5
        assert len(b["test_cases"]) == 1
