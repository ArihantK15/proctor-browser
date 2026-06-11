"""Heartbeat-reaper abandon transition — against REAL Postgres.

Locks in the Med-High fix: the abandon UPDATE is a compare-and-set on status, so
a real submission that lands between the reaper's status re-check and its UPDATE
can't be reverted to ABANDONED and overwritten by a stale autosave snapshot.
Needs a real DB — the CAS is `UPDATE … WHERE status='in_progress'`, which a mock
can't honour.

(Redis isn't available in the test env, so the autosave/force-submit branch is a
no-op — a marked-ABANDONED session simply stays ABANDONED here, which keeps these
assertions about the *status transition* deterministic.)
"""
import pytest

from app.database import async_table
from app.services.heartbeat_reaper import _mark_abandoned
from app.models import SessionStatus

pytestmark = pytest.mark.asyncio

TID = "22222222-2222-2222-2222-222222222222"


async def _mk(sid: str, status: str) -> None:
    await async_table("exam_sessions").insert({
        "session_key": sid, "teacher_id": TID, "exam_id": "e1",
        "roll_number": "R1", "full_name": "Stu", "status": status,
    }).execute()


def _row(sid: str) -> dict:
    return {"session_key": sid, "teacher_id": TID, "exam_id": "e1", "roll_number": "R1"}


async def _status(sid: str) -> str:
    return (await async_table("exam_sessions").select("status")
            .eq("session_key", sid).execute()).data[0]["status"]


async def _violations(sid: str) -> list[dict]:
    return (await async_table("violations").select("violation_type")
            .eq("session_key", sid).execute()).data or []


async def test_in_progress_session_marked_abandoned():
    await _mk("S_ip", SessionStatus.IN_PROGRESS)
    await _mark_abandoned(_row("S_ip"), async_table)
    assert await _status("S_ip") == SessionStatus.ABANDONED
    assert any(v["violation_type"] == "session_abandoned" for v in await _violations("S_ip"))


async def test_terminal_session_not_clobbered():
    # A session that already submitted must not be touched (re-check guard).
    await _mk("S_done", SessionStatus.COMPLETED)
    await _mark_abandoned(_row("S_done"), async_table)
    assert await _status("S_done") == SessionStatus.COMPLETED   # unchanged
    assert await _violations("S_done") == []                    # bailed, no event


async def test_cas_bails_when_submission_races_in_after_recheck():
    """Submission lands AFTER the status re-check passes but BEFORE the abandon
    UPDATE — the CAS (WHERE status=in_progress) must match 0 rows and bail,
    leaving the real (now COMPLETED) row and its answers untouched."""
    await _mk("S_race", SessionStatus.IN_PROGRESS)
    real = async_table
    state = {"flipped": False}

    class _Chain:
        def __init__(self, inner, table):
            self._inner, self._table = inner, table
            self._status_select = False

        def select(self, *a, **k):
            self._inner = self._inner.select(*a, **k)
            self._status_select = bool(a) and a[0] == "status"
            return self

        def update(self, *a, **k):  self._inner = self._inner.update(*a, **k);  return self
        def insert(self, *a, **k):  self._inner = self._inner.insert(*a, **k);  return self
        def eq(self, *a, **k):      self._inner = self._inner.eq(*a, **k);      return self
        def limit(self, *a, **k):   self._inner = self._inner.limit(*a, **k);   return self

        async def execute(self):
            res = await self._inner.execute()
            # The reaper's re-check just read status (IN_PROGRESS). Simulate a
            # submission landing right now, before the CAS UPDATE fires.
            if self._table == "exam_sessions" and self._status_select and not state["flipped"]:
                state["flipped"] = True
                await real("exam_sessions").update({"status": SessionStatus.COMPLETED})\
                    .eq("session_key", "S_race").execute()
            return res

    def racing_atable(table):
        return _Chain(real(table), table)

    await _mark_abandoned(_row("S_race"), racing_atable)

    assert await _status("S_race") == SessionStatus.COMPLETED   # CAS missed → not reverted
    assert await _violations("S_race") == []                    # bailed before the violation insert
