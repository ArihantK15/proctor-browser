"""Fixtures for the Google Classroom service (services/google_classroom.py).

Importing this module is itself the regression guard for the missing
google-api-python-client dependency — if googleapiclient isn't installed,
this test file fails to import and CI goes red, instead of the bug only
surfacing as a 500 on a live Classroom endpoint.

Beyond that we pin: is_configured env gating, the course/student response
shaping, push_grade's already-graded idempotency short-circuit, and the
fail-soft HttpError handling (return [] / False, never raise).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError

from app.services import google_classroom as gc


def _http_error(status=403):
    resp = type("R", (), {"status": status, "reason": "err"})()
    return HttpError(resp, b'{"error": {"message": "nope"}}')


# ── is_configured ────────────────────────────────────────────────────

def test_is_configured_true_when_both_set(monkeypatch):
    monkeypatch.setattr(gc, "_GOOGLE_CLIENT_ID", "id")
    monkeypatch.setattr(gc, "_GOOGLE_CLIENT_SECRET", "secret")
    assert gc.is_configured() is True


def test_is_configured_false_when_missing(monkeypatch):
    monkeypatch.setattr(gc, "_GOOGLE_CLIENT_ID", "")
    monkeypatch.setattr(gc, "_GOOGLE_CLIENT_SECRET", "secret")
    assert gc.is_configured() is False


# ── list_courses / list_students shaping ─────────────────────────────

@pytest.mark.asyncio
async def test_list_courses_shapes_rows(monkeypatch):
    svc = MagicMock()
    svc.courses.return_value.list.return_value.execute.return_value = {
        "courses": [{"id": "c1", "name": "Math", "section": "A", "enrollmentCode": "xyz"}]
    }
    monkeypatch.setattr(gc, "build", lambda *a, **k: svc)
    rows = await gc.list_courses(creds=object())
    assert rows == [{"id": "c1", "name": "Math", "section": "A", "enrollment_code": "xyz"}]


@pytest.mark.asyncio
async def test_list_courses_http_error_returns_empty(monkeypatch):
    svc = MagicMock()
    svc.courses.return_value.list.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(gc, "build", lambda *a, **k: svc)
    assert await gc.list_courses(creds=object()) == []


@pytest.mark.asyncio
async def test_list_students_shapes_rows(monkeypatch):
    svc = MagicMock()
    svc.courses.return_value.students.return_value.list.return_value.execute.return_value = {
        "students": [{"userId": "u1",
                      "profile": {"emailAddress": "s@x.test", "name": {"fullName": "Sam"}}}]
    }
    monkeypatch.setattr(gc, "build", lambda *a, **k: svc)
    rows = await gc.list_students(creds=object(), course_id="c1")
    assert rows == [{"user_id": "u1", "email": "s@x.test", "name": "Sam"}]


@pytest.mark.asyncio
async def test_list_students_tolerates_missing_email(monkeypatch):
    # rosters.readonly without classroom.profile.emails omits emailAddress —
    # must NOT KeyError (was a hard 500 on sync-roster). Blank email instead.
    svc = MagicMock()
    svc.courses.return_value.students.return_value.list.return_value.execute.return_value = {
        "students": [
            {"userId": "u1", "profile": {"name": {"fullName": "No Email"}}},
            {"userId": "u2", "profile": {"emailAddress": "s@x.test", "name": {"fullName": "Sam"}}},
        ]
    }
    monkeypatch.setattr(gc, "build", lambda *a, **k: svc)
    rows = await gc.list_students(creds=object(), course_id="c1")
    assert rows == [
        {"user_id": "u1", "email": "", "name": "No Email"},
        {"user_id": "u2", "email": "s@x.test", "name": "Sam"},
    ]


@pytest.mark.asyncio
async def test_get_course_name_returns_name(monkeypatch):
    svc = MagicMock()
    svc.courses.return_value.get.return_value.execute.return_value = {"id": "c1", "name": "  Physics 101  "}
    monkeypatch.setattr(gc, "build", lambda *a, **k: svc)
    assert await gc.get_course_name(creds=object(), course_id="c1") == "Physics 101"


@pytest.mark.asyncio
async def test_get_course_name_failsoft(monkeypatch):
    svc = MagicMock()
    svc.courses.return_value.get.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(gc, "build", lambda *a, **k: svc)
    assert await gc.get_course_name(creds=object(), course_id="c1") == ""


# ── push_grade ───────────────────────────────────────────────────────

def _submissions_mock(svc):
    return svc.courses.return_value.courseWork.return_value.studentSubmissions.return_value


@pytest.mark.asyncio
async def test_push_grade_patches_and_returns(monkeypatch):
    svc = MagicMock()
    subs = _submissions_mock(svc)
    subs.get.return_value.execute.return_value = {"assignedGrade": 5}  # lower than new
    monkeypatch.setattr(gc, "build", lambda *a, **k: svc)
    ok = await gc.push_grade(object(), "c1", "cw1", "u1", score=8, max_score=10)
    assert ok is True
    subs.patch.assert_called_once()
    subs.return_.assert_called_once()


@pytest.mark.asyncio
async def test_push_grade_skips_when_already_equal_or_higher(monkeypatch):
    svc = MagicMock()
    subs = _submissions_mock(svc)
    subs.get.return_value.execute.return_value = {"assignedGrade": 9}  # >= new score
    monkeypatch.setattr(gc, "build", lambda *a, **k: svc)
    ok = await gc.push_grade(object(), "c1", "cw1", "u1", score=8, max_score=10)
    assert ok is True
    subs.patch.assert_not_called()   # idempotent: no downgrade / rewrite
    subs.return_.assert_not_called()


@pytest.mark.asyncio
async def test_push_grade_http_error_returns_false(monkeypatch):
    svc = MagicMock()
    _submissions_mock(svc).get.return_value.execute.side_effect = _http_error(500)
    monkeypatch.setattr(gc, "build", lambda *a, **k: svc)
    assert await gc.push_grade(object(), "c1", "cw1", "u1", 8, 10) is False
