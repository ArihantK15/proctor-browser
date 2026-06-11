"""Student-axis tenant isolation — against REAL Postgres.

Complements the admin cross-org coverage in test_tenant_scope_integration: a
student must never be able to act on ANOTHER student's session. submit_appeal is
the canonical boundary — it loads the session and 403s on a student_id/email
mismatch before touching anything else, so this exercises the real ownership
check against real rows (the tenancy guard the static check_tenant_scoping.py
allow-lists by the student axis).
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.database import async_table
from app.routers import appeals as ap
from app.routers.appeals import submit_appeal, AppealIn
from app.limiter import limiter

pytestmark = pytest.mark.asyncio

STUDENT_A = {"id": "aaaaaaaa-0000-0000-0000-000000000001", "email": "a@example.com"}
STUDENT_B = {"id": "bbbbbbbb-0000-0000-0000-000000000002", "email": "b@example.com"}
OWNER_TID = "cccccccc-0000-0000-0000-000000000003"


async def _seed_session(sid: str, student: dict) -> None:
    await async_table("exam_sessions").insert({
        "session_key": sid, "teacher_id": OWNER_TID, "exam_id": "e1",
        "roll_number": "R1", "status": "completed",
        "student_id": student["id"], "email": student["email"],
    }).execute()


async def _submit_as(account: dict, session_key: str):
    body = AppealIn(session_key=session_key, appeal_type="violation", description="dispute")
    prev = limiter.enabled
    limiter.enabled = False
    try:
        with patch.object(ap, "require_student_account", AsyncMock(return_value=account)):
            return await submit_appeal(body, MagicMock())
    finally:
        limiter.enabled = prev


async def test_student_cannot_appeal_another_students_session():
    await _seed_session("S_owned_by_A", STUDENT_A)
    # Student B tries to appeal A's session — must be refused, not leak A's data.
    with pytest.raises(HTTPException) as ei:
        await _submit_as(STUDENT_B, "S_owned_by_A")
    assert ei.value.status_code == 403


async def test_student_appeal_on_missing_session_is_404_not_403():
    # Distinguishes "not yours" (403) from "doesn't exist" (404) — proves the
    # ownership check actually runs rather than blanket-denying.
    with pytest.raises(HTTPException) as ei:
        await _submit_as(STUDENT_A, "S_does_not_exist")
    assert ei.value.status_code == 404
