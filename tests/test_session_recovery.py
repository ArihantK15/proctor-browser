"""Session-state recovery for the save paths (forensic audit #3).

ABANDONED is RECOVERABLE: the heartbeat reaper (or a network drop) can close a
session while the student is still mid-exam. submit-exam already recovers
ABANDONED→COMPLETED on a valid late submission; the save paths must be
symmetric — recover ABANDONED→IN_PROGRESS rather than dead-ending the student
at a 409 they can never clear. A genuinely-terminal session
(COMPLETED/FORCE_SUBMITTED/REJECTED) must still 409.
"""
import asyncio

import pytest
from unittest.mock import MagicMock

from fastapi import HTTPException

import app.routers.exam as exam
from app.models import SessionStatus


class _FakeAtable:
    """Records update/insert payloads; select returns a row with `status`."""

    def __init__(self, status):
        self._status = status
        self.ops = []
        self._mode = None

    def __call__(self, table):
        self._table = table
        self._mode = None
        return self

    def select(self, *a, **k):
        self._mode = "select"
        return self

    def update(self, payload):
        self._mode = "update"
        self.ops.append(("update", self._table, payload))
        return self

    def insert(self, payload):
        self.ops.append(("insert", self._table, payload))
        self._mode = "insert"
        return self

    def eq(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    async def execute(self):
        r = MagicMock()
        if self._mode == "select":
            r.data = [{"status": self._status}]
        elif self._mode == "update":
            r.data = [{"session_key": "x"}]  # CAS won
        else:
            r.data = []
        return r


def _run_recover(monkeypatch, status):
    fake = _FakeAtable(status)
    monkeypatch.setattr(exam, "_atable", fake)
    asyncio.run(exam._recover_or_reject_terminal("ALICE001_abc", "teacher-1"))
    return fake


def test_abandoned_session_is_recovered_not_rejected(monkeypatch):
    fake = _run_recover(monkeypatch, SessionStatus.ABANDONED)
    updates = [o for o in fake.ops if o[0] == "update"]
    inserts = [o for o in fake.ops if o[0] == "insert"]
    assert any(o[2].get("status") == SessionStatus.IN_PROGRESS for o in updates), \
        "ABANDONED must be recovered to IN_PROGRESS"
    assert any(o[2].get("violation_type") == "session_recovered" for o in inserts), \
        "recovery must write a session_recovered audit row"


@pytest.mark.parametrize("status", [
    SessionStatus.COMPLETED, SessionStatus.FORCE_SUBMITTED, SessionStatus.REJECTED,
])
def test_truly_terminal_session_still_409s(monkeypatch, status):
    fake = _FakeAtable(status)
    monkeypatch.setattr(exam, "_atable", fake)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(exam._recover_or_reject_terminal("ALICE001_abc", "teacher-1"))
    assert ei.value.status_code == 409


def test_in_progress_session_is_noop(monkeypatch):
    fake = _run_recover(monkeypatch, SessionStatus.IN_PROGRESS)
    assert not [o for o in fake.ops if o[0] in ("update", "insert")], \
        "an active session must not be touched by the recover check"
