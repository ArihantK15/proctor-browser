"""Reschedule-with-attempts: the schedule endpoint reports how many students
already submitted, and the bulk reset re-opens exactly those (and only those)."""

from __future__ import annotations

from unittest.mock import patch

from tests.conftest import make_admin_token, shared_supabase_mock
from tests.test_student_tenancy_boundaries import _TenantDB


def _admin_headers():
    return {"Authorization": f"Bearer {make_admin_token(teacher_id='teacher-1', email='prof@test.com')}"}


def _db(sessions):
    return _TenantDB({
        "auth_sessions": [],
        "teachers": [{"id": "teacher-1", "email": "prof@test.com", "full_name": "Prof T"}],
        "exam_config": [{"teacher_id": "teacher-1", "exam_id": "exam-1", "exam_title": "Maths"}],
        "exam_sessions": sessions,
        "violations": [],
    })


def _sess(roll, status):
    return {
        "session_key": f"{roll}_exam-1", "exam_id": "exam-1", "teacher_id": "teacher-1",
        "roll_number": roll, "status": status, "score": 5, "total": 10,
        "percentage": 50.0, "submitted_at": "2026-06-15T09:00:00Z",
    }


# ── reschedule reports the attempted count ────────────────────────────────

def test_reschedule_reports_attempted_count(client):
    db = _db([_sess("A1", "completed"), _sess("A2", "submitted"),
              _sess("A3", "in_progress")])  # active one must NOT count
    sm = shared_supabase_mock()
    with patch.object(sm, "table", side_effect=db):
        resp = client.post("/api/v1/admin/exam-schedule", headers=_admin_headers(),
                           json={"exam_id": "exam-1", "starts_at": "2026-07-01T09:00:00Z"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["attempted_count"] == 2


def test_reschedule_no_attempts_count_zero(client):
    db = _db([_sess("A3", "in_progress")])
    sm = shared_supabase_mock()
    with patch.object(sm, "table", side_effect=db):
        resp = client.post("/api/v1/admin/exam-schedule", headers=_admin_headers(),
                           json={"exam_id": "exam-1", "ends_at": "2026-07-02T09:00:00Z"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["attempted_count"] == 0


# ── bulk reset re-opens the finished attempts only ────────────────────────

def test_reset_attempts_reopens_done_sessions(client):
    sessions = [_sess("A1", "completed"), _sess("A2", "force_submitted"),
                _sess("A3", "in_progress")]
    db = _db(sessions)
    sm = shared_supabase_mock()
    with patch.object(sm, "table", side_effect=db):
        resp = client.post("/api/v1/admin/exam/exam-1/reset-attempts",
                           headers=_admin_headers(), json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["reset_count"] == 2
    by_roll = {s["roll_number"]: s["status"] for s in db.tables["exam_sessions"]}
    assert by_roll["A1"] == "in_progress"
    assert by_roll["A2"] == "in_progress"
    assert by_roll["A3"] == "in_progress"  # was already active, untouched
    # the two finished attempts had their stamped score cleared
    cleared = [s for s in db.tables["exam_sessions"]
               if s["roll_number"] in ("A1", "A2")]
    assert all(s["submitted_at"] is None and s["score"] is None for s in cleared)


def test_reset_attempts_none_to_reset(client):
    db = _db([_sess("A3", "in_progress")])
    sm = shared_supabase_mock()
    with patch.object(sm, "table", side_effect=db):
        resp = client.post("/api/v1/admin/exam/exam-1/reset-attempts",
                           headers=_admin_headers(), json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["reset_count"] == 0
