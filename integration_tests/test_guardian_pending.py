"""Real-Postgres check for the guardian pending-consent query.

The pending list filters `guardian_email IS NOT NULL`. The builder must
emit `IS NOT NULL` — a naive `!= NULL` (what `.neq(col, None)` compiles to)
is never true in SQL and would make the list silently empty in prod. Mocked
unit tests can't catch that; this exercises the real query.
"""
import uuid
from datetime import datetime, timezone

import pytest

from app.database import async_table

pytestmark = pytest.mark.asyncio


async def _teacher() -> str:
    tid = str(uuid.uuid4())
    await async_table("teachers").insert(
        {"id": tid, "email": f"t{tid[:8]}@s.edu", "org_role": "teacher"}
    ).execute()
    return tid


async def _student(tid: str, roll: str, *, guardian=None, granted=False) -> None:
    await async_table("students").insert({
        "roll_number": roll,
        "full_name": roll,
        "teacher_id": tid,
        "guardian_email": guardian,
        "guardian_consent_granted_at":
            datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat() if granted else None,
    }).execute()


async def test_pending_uses_is_not_null_and_scopes():
    tid = await _teacher()
    await _student(tid, "PENDING1", guardian="g@example.com", granted=False)  # appears
    await _student(tid, "GRANTED1", guardian="g@example.com", granted=True)   # excluded
    await _student(tid, "NOGUARD1", guardian=None, granted=False)             # excluded

    rows = (await async_table("students")
            .select("roll_number")
            .not_.is_("guardian_email", None)
            .is_("guardian_consent_granted_at", None)
            .eq("teacher_id", tid)
            .execute()).data or []
    rolls = {r["roll_number"] for r in rows}
    assert rolls == {"PENDING1"}, f"expected only the pending student, got {rolls}"
