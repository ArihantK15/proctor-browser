"""Regressions for the student dashboard: history payload field name and
exams-list status classification (the 'everything shows Upcoming' bug)."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from tests.conftest import shared_supabase_mock
from tests.test_student_tenancy_boundaries import _TenantDB, _student_account_headers


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _completed_session_db(starts_at, ends_at, status="completed"):
    now = datetime.now(timezone.utc)
    return _TenantDB({
        "auth_sessions": [],
        "student_accounts": [{"id": "student-1", "email": "alice@test.com", "full_name": "Alice"}],
        "students": [{
            "roll_number": "A1", "teacher_id": "teacher-1",
            "email": "alice@test.com", "account_id": "student-1",
        }],
        "student_invites": [
            {"teacher_id": "teacher-1", "roll_number": "A1", "exam_id": "exam-1", "status": "accepted"},
        ],
        "teachers": [{"id": "teacher-1", "full_name": "Teacher One"}],
        "exam_config": [{
            "teacher_id": "teacher-1", "exam_id": "exam-1", "exam_title": "Maths",
            "starts_at": starts_at, "ends_at": ends_at, "duration_minutes": 60,
        }],
        "exam_sessions": [{
            "session_key": "A1_exam-1", "exam_id": "exam-1", "teacher_id": "teacher-1",
            "roll_number": "A1", "full_name": "Alice", "email": "alice@test.com",
            "status": status, "score": 8, "total": 10, "percentage": 80.0,
            "time_taken_secs": 1800, "started_at": _iso(now - timedelta(hours=2)),
            "submitted_at": _iso(now - timedelta(hours=1)), "risk_score": 10,
        }],
        "violations": [],
    })


# ── Bug #2 & #3 root cause: history must expose `session_key` ──────────────
# The "Why flagged?" and "Appeal" buttons read h.session_key. The endpoint
# returned it as `session_id`, so both buttons fired with undefined → null,
# yielding a 404 ("couldn't fetch evidence") and a strict-validation 422
# ("[object Object]") respectively.

def test_student_history_item_exposes_session_key(client):
    now = datetime.now(timezone.utc)
    db = _completed_session_db(_iso(now - timedelta(days=2)), _iso(now - timedelta(days=1)))
    sm = shared_supabase_mock()
    with patch.object(sm, "table", side_effect=db):
        resp = client.get("/api/student/history", headers=_student_account_headers())
    assert resp.status_code == 200, resp.text
    hist = resp.json()["history"]
    assert len(hist) == 1, hist
    assert hist[0]["session_key"] == "A1_exam-1"


# ── Bug #1: a submitted exam must read as completed, not Upcoming ──────────

def test_submitted_exam_with_closed_window_shows_completed(client):
    now = datetime.now(timezone.utc)
    db = _completed_session_db(_iso(now - timedelta(days=2)), _iso(now - timedelta(days=1)))
    sm = shared_supabase_mock()
    with patch.object(sm, "table", side_effect=db):
        resp = client.get("/api/student/exams", headers=_student_account_headers())
    assert resp.status_code == 200, resp.text
    exam = resp.json()["exams"][0]
    assert exam["status"] == "completed", exam


def test_submitted_exam_with_future_window_shows_completed(client):
    # The student already submitted (submitted_at is in the past). Even if the
    # configured window opens in the FUTURE (re-scheduled / mis-set schedule),
    # their own completed session is ground truth — it must not be relabelled
    # 'upcoming'. This is the user-reported "everything shows Upcoming" bug.
    now = datetime.now(timezone.utc)
    db = _completed_session_db(_iso(now + timedelta(days=1)), _iso(now + timedelta(days=2)))
    sm = shared_supabase_mock()
    with patch.object(sm, "table", side_effect=db):
        resp = client.get("/api/student/exams", headers=_student_account_headers())
    assert resp.status_code == 200, resp.text
    exam = resp.json()["exams"][0]
    assert exam["status"] == "completed", exam
