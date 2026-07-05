"""Tests for the teacher-facing plagiarism-check routes in app/routers/coding.py:
POST /api/v1/admin/exams/{exam_id}/plagiarism-check
GET  /api/v1/admin/exams/{exam_id}/plagiarism-matches
POST /api/v1/admin/plagiarism-matches/{match_id}/review

Same MagicMock-chain _atable pattern already established in
tests/test_admin_coding.py for this same require_admin-gated style of route.
"""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.conftest import make_admin_token  # noqa: E402


def _hdr():
    return {"Authorization": f"Bearer {make_admin_token(teacher_id='teacher-1', email='p@t.com')}"}


def _atable_factory(rows_by_table, recorder=None):
    def _factory(name):
        chain = MagicMock()
        for a in ("select", "eq", "in_", "is_", "order", "limit", "delete", "update"):
            getattr(chain, a).return_value = chain

        if recorder is not None:
            def _update(payload, *a, **k):
                recorder.setdefault(name, []).append(payload)
                return chain
            chain.update.side_effect = _update

        async def _execute():
            return MagicMock(data=rows_by_table.get(name, []))
        chain.execute = _execute
        return chain
    return _factory


def _admin_patch():
    async def _admin(req):
        return {"id": "teacher-1"}
    return patch("app.routers.coding.require_admin", side_effect=_admin)


def test_trigger_plagiarism_check_enqueues_job(client):
    with _admin_patch(), \
         patch("app.jobs.enqueue_job") as mock_enqueue, \
         patch("app.jobs.check_plagiarism_job"):
        r = client.post("/api/v1/admin/exams/exam-1/plagiarism-check", headers=_hdr())
        assert r.status_code == 200
        assert r.json() == {"status": "enqueued"}
        assert mock_enqueue.called
        _, kwargs = mock_enqueue.call_args
        assert kwargs["exam_id"] == "exam-1"
        assert kwargs["teacher_id"] == "teacher-1"


def test_list_plagiarism_matches_joins_source_code(client):
    rows = {
        "coding_plagiarism_matches": [
            {"id": "m1", "exam_id": "exam-1", "question_id": "q1",
             "submission_a_id": "sub-a", "submission_b_id": "sub-b",
             "similarity_score": 0.9, "status": "unreviewed"},
        ],
        "coding_submissions": [
            {"id": "sub-a", "source_code": "print('a')"},
            {"id": "sub-b", "source_code": "print('a')"},
        ],
    }
    with _admin_patch(), \
         patch("app.routers.coding._atable", side_effect=_atable_factory(rows)):
        r = client.get("/api/v1/admin/exams/exam-1/plagiarism-matches", headers=_hdr())
        assert r.status_code == 200
        matches = r.json()["matches"]
        assert len(matches) == 1
        assert matches[0]["source_code_a"] == "print('a')"
        assert matches[0]["source_code_b"] == "print('a')"


def test_review_match_rejects_invalid_status(client):
    with _admin_patch():
        r = client.post("/api/v1/admin/plagiarism-matches/m1/review",
                         json={"status": "maybe"}, headers=_hdr())
        assert r.status_code == 400


def test_review_match_updates_status(client):
    recorder = {}
    rows = {"coding_plagiarism_matches": [{"id": "m1", "status": "confirmed"}]}
    with _admin_patch(), \
         patch("app.routers.coding._atable", side_effect=_atable_factory(rows, recorder)):
        r = client.post("/api/v1/admin/plagiarism-matches/m1/review",
                         json={"status": "confirmed"}, headers=_hdr())
        assert r.status_code == 200
        assert r.json() == {"updated": True}
        assert recorder["coding_plagiarism_matches"][0]["status"] == "confirmed"
        assert recorder["coding_plagiarism_matches"][0]["reviewed_by"] == "teacher-1"
