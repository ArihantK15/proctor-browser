"""Tests for app/reminders.py.

Covers _reminder_window, _send_reminder_for_invite,
_student_allows_email_reminders, _lookup_student_id_by_email,
_reminder_tick, and _reminder_loop.

The existing test_student_reminder_preferences.py covers the API layer
and one preference-off tick scenario.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from app import reminders


# ── Test double (mirrors the one in test_student_reminder_preferences.py) ─

class _ReminderDB:
    def __init__(self, tables: dict[str, list[dict]]):
        self.tables = {k: [dict(r) for r in v] for k, v in tables.items()}

    def __call__(self, table_name):
        return _Chain(self, table_name)


class _Chain:
    def __init__(self, db: _ReminderDB, table_name: str):
        self.db = db
        self.table_name = table_name
        self.eqs: dict[str, object] = {}
        self.ins: dict[str, set] = {}
        self.nulls: set[str] = set()
        self.payload = None
        self.op = "select"
        self._limit = None
        self._min_col = self._min_val = None
        self._max_col = self._max_val = None

    def select(self, *a, **kw): self.op = "select"; return self
    def eq(self, col, val): self.eqs[col] = val; return self
    def in_(self, col, vals): self.ins[col] = set(vals or []); return self
    def is_(self, col, val):
        if val == "null":
            self.nulls.add(col)
        return self
    def gte(self, col, val): self._min_col, self._min_val = col, val; return self
    def lte(self, col, val): self._max_col, self._max_val = col, val; return self
    def limit(self, n): self._limit = n; return self
    def update(self, payload): self.op = "update"; self.payload = dict(payload); return self

    def _rows(self):
        rows = list(self.db.tables.get(self.table_name, []))
        out = []
        for row in rows:
            if any(str(row.get(k) or "") != str(v or "") for k, v in self.eqs.items()):
                continue
            if any(row.get(k) not in vals for k, vals in self.ins.items()):
                continue
            if any(row.get(k) is not None for k in self.nulls):
                continue
            if self._min_col and (row.get(self._min_col) or "") < (self._min_val or ""):
                continue
            if self._max_col and (row.get(self._max_col) or "") > (self._max_val or ""):
                continue
            out.append(row)
        if self._limit is not None:
            out = out[:self._limit]
        return out

    async def execute(self):
        rows = self._rows()
        if self.op == "update":
            for row in rows:
                row.update(self.payload or {})
        return MagicMock(data=rows, count=len(rows))


# ── _reminder_window ────────────────────────────────────────────────────

class TestReminderWindow:
    def test_returns_tuple(self):
        lo, hi = reminders._reminder_window(60, 5)
        assert lo < hi
        assert (hi - lo).total_seconds() == pytest.approx(600, abs=2)


# ── _send_reminder_for_invite ───────────────────────────────────────────
# The function does `from .emailer import send_exam_reminder` lazily.
# We patch app.emailer.send_exam_reminder (the ultimate target).

class TestSendReminderForInvite:
    def _call(self, **overrides):
        inv = {"token": "tok1", "email": "a@b.com", "full_name": "Alice", "roll_number": "R1"}
        inv.update(overrides)
        return reminders._send_reminder_for_invite(inv, {"exam_title": "Midterm"}, 1)

    def test_success_returns_true(self):
        with patch("app.emailer.send_exam_reminder", return_value=MagicMock(ok=True)):
            assert self._call() is True

    def test_exception_returns_false(self):
        with patch("app.emailer.send_exam_reminder", side_effect=RuntimeError("SMTP down")):
            assert self._call() is False

    def test_not_ok_returns_false(self):
        with patch("app.emailer.send_exam_reminder", return_value=MagicMock(ok=False, error="bounce")):
            assert self._call() is False

    def test_none_result_returns_false(self):
        with patch("app.emailer.send_exam_reminder", return_value=None):
            assert self._call() is False


# ── _student_allows_email_reminders ─────────────────────────────────────

class TestStudentAllowsEmailReminders:
    @pytest.mark.asyncio
    async def test_empty_email_returns_false(self):
        assert await reminders._student_allows_email_reminders("") is False
        assert await reminders._student_allows_email_reminders("  ") is False

    @pytest.mark.asyncio
    async def test_no_account_returns_true(self):
        db = _ReminderDB({"student_accounts": []})
        with patch.object(reminders, "_atable", side_effect=db):
            assert await reminders._student_allows_email_reminders("no-account@test.com") is True

    @pytest.mark.asyncio
    async def test_preference_true_returns_true(self):
        db = _ReminderDB({"student_accounts": [{"email": "alice@test.com", "email_reminders_enabled": True}]})
        with patch.object(reminders, "_atable", side_effect=db):
            assert await reminders._student_allows_email_reminders("alice@test.com") is True

    @pytest.mark.asyncio
    async def test_preference_false_returns_false(self):
        db = _ReminderDB({"student_accounts": [{"email": "bob@test.com", "email_reminders_enabled": False}]})
        with patch.object(reminders, "_atable", side_effect=db):
            assert await reminders._student_allows_email_reminders("bob@test.com") is False

    @pytest.mark.asyncio
    async def test_null_preference_returns_true(self):
        db = _ReminderDB({"student_accounts": [{"email": "carol@test.com", "email_reminders_enabled": None}]})
        with patch.object(reminders, "_atable", side_effect=db):
            assert await reminders._student_allows_email_reminders("carol@test.com") is True

    @pytest.mark.asyncio
    async def test_column_missing_graceful_degradation(self):
        err = Exception("column \"email_reminders_enabled\" does not exist")
        with patch.object(reminders, "_atable", side_effect=err):
            assert await reminders._student_allows_email_reminders("dave@test.com") is True

    @pytest.mark.asyncio
    async def test_other_db_exception_returns_true_with_warning(self):
        err = Exception("connection timeout")
        with patch.object(reminders, "_atable", side_effect=err):
            assert await reminders._student_allows_email_reminders("eve@test.com") is True
        reminders._dep_log.warning.assert_called()


# ── _lookup_student_id_by_email ─────────────────────────────────────────

class TestLookupStudentIdByEmail:
    @pytest.mark.asyncio
    async def test_found_returns_id(self):
        db = _ReminderDB({"student_accounts": [{"id": "s-42", "email": "alice@test.com"}]})
        with patch.object(reminders, "_atable", side_effect=db):
            assert await reminders._lookup_student_id_by_email("alice@test.com") == "s-42"

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        db = _ReminderDB({"student_accounts": []})
        with patch.object(reminders, "_atable", side_effect=db):
            assert await reminders._lookup_student_id_by_email("nobody@test.com") is None

    @pytest.mark.asyncio
    async def test_empty_email_returns_none(self):
        assert await reminders._lookup_student_id_by_email("") is None
        assert await reminders._lookup_student_id_by_email("  ") is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        with patch.object(reminders, "_atable", side_effect=RuntimeError("DB down")):
            assert await reminders._lookup_student_id_by_email("alice@test.com") is None


# ── _reminder_tick ──────────────────────────────────────────────────────

_now = datetime.now(timezone.utc)


def _exam(overrides=None):
    base = {
        "exam_id": "exam-1", "teacher_id": "t-1", "exam_title": "Midterm",
        "starts_at": _now + timedelta(minutes=60), "access_code": "AC1",
    }
    if overrides:
        base.update(overrides)
    return base


def _invite(overrides=None):
    base = {
        "token": "tok-1", "email": "alice@test.com", "full_name": "Alice",
        "roll_number": "R1", "exam_id": "exam-1", "status": "sent",
        "reminder_1h_at": None, "reminder_24h_at": None,
    }
    if overrides:
        base.update(overrides)
    return base


class TestReminderTick:
    @pytest.mark.asyncio
    async def test_full_happy_path_sends_reminder(self):
        db = _ReminderDB({
            "exam_config": [_exam()],
            "student_invites": [_invite()],
            "student_accounts": [{"email": "alice@test.com", "id": "s-1", "email_reminders_enabled": True}],
        })
        with patch.object(reminders, "_atable", side_effect=db), \
             patch.object(reminders, "_send_reminder_for_invite", return_value=True) as send:
            await reminders._reminder_tick()
        # Only the 1h bucket matches (exam starts 60 min from now)
        send.assert_called_once()
        args = send.call_args
        assert args[0][2] == 1  # hours_until = 1

    @pytest.mark.asyncio
    async def test_preference_off_skips_and_sets_timestamp(self):
        db = _ReminderDB({
            "exam_config": [_exam()],
            "student_invites": [_invite()],
            "student_accounts": [{"email": "alice@test.com", "email_reminders_enabled": False}],
        })
        with patch.object(reminders, "_atable", side_effect=db), \
             patch.object(reminders, "_send_reminder_for_invite", return_value=True) as send:
            await reminders._reminder_tick()
        send.assert_not_called()
        assert db.tables["student_invites"][0]["reminder_1h_at"] is not None

    @pytest.mark.asyncio
    async def test_claim_fails_skips(self):
        db = _ReminderDB({
            "exam_config": [_exam()],
            "student_invites": [_invite()],
            "student_accounts": [{"email": "alice@test.com", "email_reminders_enabled": True}],
        })
        # First update that's an actual claim (not preference-off skip)
        # should return empty to simulate another worker
        original_update_rows = _Chain._rows
        update_count = [0]

        def _rows_with_first_update_empty(self_chain):
            if self_chain.op == "update":
                update_count[0] += 1
                if update_count[0] == 1:
                    return []
            return original_update_rows(self_chain)

        with patch.object(reminders, "_atable", side_effect=db), \
             patch.object(_Chain, "_rows", _rows_with_first_update_empty), \
             patch.object(reminders, "_send_reminder_for_invite", return_value=True) as send:
            await reminders._reminder_tick()
        send.assert_not_called()

    @pytest.mark.asyncio
    async def test_email_failure_rolls_back_timestamp(self):
        db = _ReminderDB({
            "exam_config": [_exam()],
            "student_invites": [_invite()],
            "student_accounts": [{"email": "alice@test.com", "id": "s-1", "email_reminders_enabled": True}],
        })
        with patch.object(reminders, "_atable", side_effect=db), \
             patch.object(reminders, "_send_reminder_for_invite", return_value=False):
            await reminders._reminder_tick()
        assert db.tables["student_invites"][0]["reminder_1h_at"] is None

    @pytest.mark.asyncio
    async def test_exam_query_exception_caught(self):
        def failing_table(name):
            raise RuntimeError("exam query crash")
        with patch.object(reminders, "_atable", side_effect=failing_table):
            await reminders._reminder_tick()
        reminders._dep_log.warning.assert_called()

    @pytest.mark.asyncio
    async def test_invite_query_exception_caught(self):
        called = [0]

        def db_with_failing_invites(name):
            called[0] += 1
            if called[0] >= 2:
                raise RuntimeError("invites query crash")
            return _ReminderDB({
                "exam_config": [_exam()],
                "student_invites": [_invite()],
            })(name)

        with patch.object(reminders, "_atable", side_effect=db_with_failing_invites):
            await reminders._reminder_tick()
        reminders._dep_log.warning.assert_called()

    @pytest.mark.asyncio
    async def test_invite_with_no_email_skipped(self):
        db = _ReminderDB({
            "exam_config": [_exam()],
            "student_invites": [_invite({"email": ""})],
            "student_accounts": [],
        })
        with patch.object(reminders, "_atable", side_effect=db), \
             patch.object(reminders, "_send_reminder_for_invite", return_value=True) as send:
            await reminders._reminder_tick()
        send.assert_not_called()

    @pytest.mark.asyncio
    async def test_exam_with_no_id_skipped(self):
        db = _ReminderDB({
            "exam_config": [_exam({"exam_id": None})],
            "student_invites": [_invite()],
            "student_accounts": [],
        })
        with patch.object(reminders, "_atable", side_effect=db), \
             patch.object(reminders, "_send_reminder_for_invite", return_value=True) as send:
            await reminders._reminder_tick()
        send.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_exams_in_window_does_nothing(self):
        db = _ReminderDB({"exam_config": [], "student_invites": []})
        with patch.object(reminders, "_atable", side_effect=db), \
             patch.object(reminders, "_send_reminder_for_invite") as send:
            await reminders._reminder_tick()
        send.assert_not_called()

    @pytest.mark.asyncio
    async def test_student_id_lookup_exception_caught(self):
        db = _ReminderDB({
            "exam_config": [_exam()],
            "student_invites": [_invite()],
            "student_accounts": [{"email": "alice@test.com", "id": "s-1", "email_reminders_enabled": True}],
        })
        with patch.object(reminders, "_atable", side_effect=db), \
             patch.object(reminders, "_send_reminder_for_invite", return_value=True) as send, \
             patch.object(reminders, "_lookup_student_id_by_email",
                         AsyncMock(side_effect=RuntimeError("lookup crashed"))):
            await reminders._reminder_tick()
        send.assert_not_called()


# ── _reminder_loop ──────────────────────────────────────────────────────

class TestReminderLoop:
    @pytest.mark.asyncio
    async def test_tick_then_sleep(self, monkeypatch):
        original_sleep = asyncio.sleep
        sleeps = []

        async def mock_sleep(s):
            sleeps.append(s)
            await original_sleep(0.001)  # yield to event loop

        monkeypatch.setattr(asyncio, "sleep", mock_sleep)

        tick_count = [0]
        async def single_tick():
            tick_count[0] += 1
        monkeypatch.setattr(reminders, "_reminder_tick", single_tick)

        task = asyncio.create_task(reminders._reminder_loop())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert tick_count[0] >= 1
        assert len(sleeps) >= 1

    @pytest.mark.asyncio
    async def test_tick_crash_logged_and_sleeps(self, monkeypatch):
        original_sleep = asyncio.sleep
        sleeps = []

        async def mock_sleep(s):
            sleeps.append(s)
            await original_sleep(0.001)

        monkeypatch.setattr(asyncio, "sleep", mock_sleep)

        tick_count = [0]
        async def crashing_tick():
            tick_count[0] += 1
            if tick_count[0] == 1:
                raise RuntimeError("tick crash!")
        monkeypatch.setattr(reminders, "_reminder_tick", crashing_tick)

        task = asyncio.create_task(reminders._reminder_loop())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert tick_count[0] >= 1
        assert len(sleeps) >= 1
        reminders._dep_log.error.assert_called()

    @pytest.mark.asyncio
    async def test_sleep_failure_falls_back(self, monkeypatch):
        original_sleep = asyncio.sleep
        sleeps = []
        sleep_attempts = [0]

        async def sleep_that_fails_once(s):
            sleep_attempts[0] += 1
            sleeps.append(s)
            if sleep_attempts[0] == 1:
                raise RuntimeError("sleep failed")
            await original_sleep(0.001)

        monkeypatch.setattr(asyncio, "sleep", sleep_that_fails_once)

        tick_count = [0]
        async def single_tick():
            tick_count[0] += 1
        monkeypatch.setattr(reminders, "_reminder_tick", single_tick)

        task = asyncio.create_task(reminders._reminder_loop())
        await original_sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert len(sleeps) >= 1
        reminders._dep_log.error.assert_called()
