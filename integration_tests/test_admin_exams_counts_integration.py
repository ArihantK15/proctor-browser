"""list_exams question/session counts — against REAL Postgres.

Real bug this locks in: the default (non-archived) view showed "0Q, 0
sessions" for every exam until "Show archived" was checked. Root cause was
`_scoped()` in app/routers/admin_exams.py applying `.is_("archived_at",
"null")` to the `questions` and `exam_sessions` tables whenever
include_archived was false — neither table has an archived_at column, so the
query threw UndefinedColumnError, silently swallowed by the router's
`except Exception: logger.debug(...)`, leaving the count dicts empty. Only a
real Postgres schema catches this — a MagicMock has no columns to be
"undefined", so the mocked unit suite could never have seen it.
"""
import uuid

import pytest
import pytest_asyncio

from app.database import async_table
from app.routers.admin_exams import list_exams
from starlette.requests import Request
from unittest.mock import patch

pytestmark = pytest.mark.asyncio

TID = str(uuid.uuid4())
EID = str(uuid.uuid4())


@pytest_asyncio.fixture(autouse=True)
async def _small_pool(monkeypatch):
    from app.postgres_table import close_pool
    monkeypatch.setenv("POSTGRES_POOL_MIN", "1")
    monkeypatch.setenv("POSTGRES_POOL_MAX", "10")
    await close_pool()
    yield
    await close_pool()


async def _seed():
    await async_table("teachers").insert({
        "id": TID, "email": f"{TID}@test.local", "full_name": "Test Teacher",
        "supabase_uid": TID,
    }).execute()
    await async_table("exam_config").insert({
        "teacher_id": TID, "exam_id": EID, "exam_title": "Counts Test Exam",
        "duration_minutes": 60,
    }).execute()
    for i in range(3):
        await async_table("questions").insert({
            "question_id": str(i), "teacher_id": TID, "exam_id": EID,
            "question": f"Q{i}", "options": "a,b", "correct": "a",
        }).execute()


def _fake_request(query_string=b""):
    scope = {
        "type": "http", "method": "GET", "path": "/api/v1/admin/exams",
        "query_string": query_string, "headers": [], "client": ("127.0.0.1", 0),
    }
    return Request(scope)


async def _call_list_exams(*, include_archived=False):
    request = _fake_request(b"include_archived=1" if include_archived else b"")
    with patch("app.routers.admin_exams.require_admin", return_value={"teacher_id": TID}), \
         patch("app.routers.admin_exams.resolve_scope", return_value={"teacher_ids": [TID]}), \
         patch("app.routers.admin_exams.scope_to_teacher_ids", return_value=[TID]):
        return await list_exams(request)


async def test_question_count_correct_on_default_non_archived_view():
    """The exact regression: default view (include_archived not set) must
    still show the real question count, not 0."""
    await _seed()
    result = await _call_list_exams(include_archived=False)
    exams = {e["exam_id"]: e for e in result["exams"]}
    assert EID in exams, "seeded exam missing from default (non-archived) view"
    assert exams[EID]["question_count"] == 3, (
        f"expected 3 questions, got {exams[EID]['question_count']} — "
        "the archived_at-on-questions/exam_sessions bug is back"
    )


async def test_question_count_still_correct_with_include_archived():
    """Sanity check the include_archived=1 path (which never hit the bug,
    since it skips the archived_at filter) still works after the fix."""
    await _seed()
    result = await _call_list_exams(include_archived=True)
    exams = {e["exam_id"]: e for e in result["exams"]}
    assert exams[EID]["question_count"] == 3
