"""Tests for the coding-question authoring endpoint (Phase 5).

POST/PUT /api/v1/admin/coding-question — the teacher/LLM path that creates a coding
question + its test cases (replacing the SQL-only seeding).
"""
import base64
import json
import os
import sys
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.conftest import make_admin_token  # noqa: E402
from app.services import secrets_crypto  # noqa: E402

_TEST_SECRETS_KEY = base64.b64encode(b"\x0a" * 32).decode()


@pytest.fixture(autouse=True)
def _reset_secrets_key_cache():
    """secrets_crypto caches the parsed CODING_SECRETS_KEY for process
    lifetime; reset around every test so monkeypatch.setenv takes effect."""
    secrets_crypto.reset_key_cache()
    yield
    secrets_crypto.reset_key_cache()


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

    def test_expected_output_is_encrypted_at_rest_when_key_configured(self, client, monkeypatch):
        """With CODING_SECRETS_KEY set, the persisted coding_test_cases row's
        expected_output must be an enc:v1: token, not the raw plaintext —
        proves the write path actually encrypts."""
        monkeypatch.setenv("CODING_SECRETS_KEY", _TEST_SECRETS_KEY)
        secrets_crypto.reset_key_cache()
        rec = {}
        ps = _patches({}, rec)
        with ps[0], ps[1], ps[2], ps[3]:
            r = client.post("/api/v1/admin/coding-question", json=_GOOD, headers=_hdr())
        assert r.status_code == 200, r.text
        stored_cases = rec["coding_test_cases"]
        assert len(stored_cases) == 2
        for stored, original in zip(stored_cases, _GOOD["test_cases"]):
            assert secrets_crypto.is_encrypted(stored["expected_output"]) is True
            assert stored["expected_output"] != original["expected_output"]
            assert secrets_crypto.decrypt(stored["expected_output"]) == original["expected_output"]

    def test_expected_output_not_encrypted_without_key(self, client):
        """No CODING_SECRETS_KEY configured (dev/CI posture) — write path is a
        no-op and stores plaintext, matching legacy behaviour."""
        rec = {}
        ps = _patches({}, rec)
        with ps[0], ps[1], ps[2], ps[3]:
            r = client.post("/api/v1/admin/coding-question", json=_GOOD, headers=_hdr())
        assert r.status_code == 200, r.text
        stored_cases = rec["coding_test_cases"]
        for stored, original in zip(stored_cases, _GOOD["test_cases"]):
            assert secrets_crypto.is_encrypted(stored["expected_output"]) is False
            assert stored["expected_output"] == original["expected_output"]


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

    def test_get_decrypts_encrypted_expected_output_for_teacher_view(self, client, monkeypatch):
        """The authoring editor must show the teacher the real plaintext
        answer key, not the enc:v1: ciphertext stored at rest."""
        monkeypatch.setenv("CODING_SECRETS_KEY", _TEST_SECRETS_KEY)
        secrets_crypto.reset_key_cache()
        encrypted = secrets_crypto.encrypt("42")
        assert secrets_crypto.is_encrypted(encrypted)
        rec = {}
        rows = {
            "questions": [{"question_id": "coding-a", "exam_id": "exam-1",
                           "question": "# Q", "options": json.dumps({"marks": 5})}],
            "coding_test_cases": [{"idx": 0, "input": "1", "expected_output": encrypted,
                                   "visibility": "hidden", "float_tolerance": None}],
        }
        ps = _patches(rows, rec)
        with ps[0], ps[1], ps[2], ps[3]:
            r = client.get("/api/v1/admin/coding-question?question_id=coding-a", headers=_hdr())
        assert r.status_code == 200, r.text
        b = r.json()
        assert b["test_cases"][0]["expected_output"] == "42"


_AI_DRAFT = {
    "question": "# Sum\nRead a b, print a+b",
    "starter_code": "// read & print\n",
    "reference_solution": "const [a,b]=readline().split(' ').map(Number);print(a+b);",
    "test_cases": [
        {"input": "2 3", "expected_output": "5", "visibility": "sample"},
        {"input": "10 20", "expected_output": "30", "visibility": "hidden"},
    ],
}


class TestGenerateCoding:
    def test_generate_endpoint_returns_reviewable_draft(self, client):
        async def _admin(req):
            return {"id": "teacher-1"}
        with patch("app.routers.admin_coding.require_admin", side_effect=_admin), \
             patch("app.routers.admin_coding.assert_can_author"), \
             patch("app.llm.is_configured", return_value=True), \
             patch("app.llm._chat_json", new=AsyncMock(return_value=_AI_DRAFT)):
            r = client.post("/api/v1/admin/coding-question/generate",
                            json={"topic": "sum two ints", "language": "javascript"}, headers=_hdr())
        assert r.status_code == 200, r.text
        b = r.json()
        # AI drafts are never auto-published — flagged for review, reference returned.
        assert b["needs_verification"] is True and b["ai_generated"] is True
        assert b["reference_solution"]
        assert b["options"]["allowed_languages"] == ["javascript"]
        assert any(c["visibility"] == "hidden" for c in b["test_cases"])
        # draft is the exact shape the authoring endpoint accepts
        assert "question" in b and "options" in b and "test_cases" in b

    def test_generate_requires_topic(self, client):
        async def _admin(req):
            return {"id": "teacher-1"}
        with patch("app.routers.admin_coding.require_admin", side_effect=_admin), \
             patch("app.routers.admin_coding.assert_can_author"), \
             patch("app.llm.is_configured", return_value=True):
            r = client.post("/api/v1/admin/coding-question/generate",
                            json={"topic": ""}, headers=_hdr())
        assert r.status_code == 400

    def test_generate_503_when_llm_not_configured(self, client):
        async def _admin(req):
            return {"id": "teacher-1"}
        with patch("app.routers.admin_coding.require_admin", side_effect=_admin), \
             patch("app.routers.admin_coding.assert_can_author"), \
             patch("app.llm.is_configured", return_value=False):
            r = client.post("/api/v1/admin/coding-question/generate",
                            json={"topic": "x"}, headers=_hdr())
        assert r.status_code == 503
