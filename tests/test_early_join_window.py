"""Early-join pre-exam verification window — the two time gates.

_check_lobby_window  = entry gate (validate_student): opens at
                       starts_at - early_join_minutes so students verify early.
_check_exam_started  = hard gate (GET /api/v1/questions): locked until starts_at.

Both are pure functions over a config dict, so we drive them with relative
times. See docs/superpowers/specs/2026-06-20-early-join-verification-window-design.md
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.routers.exam import _check_lobby_window, _check_exam_started  # noqa: E402


def _iso(minutes_from_now: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes_from_now)).isoformat()


def _raises_403(fn, config) -> int | None:
    try:
        fn(config)
        return None
    except HTTPException as e:
        return e.status_code


# ── Lobby (entry) gate ────────────────────────────────────────────
class TestLobbyWindow:
    def test_open_during_early_window(self):
        # starts in 10 min, 15-min early window → lobby already open.
        assert _check_lobby_window({"starts_at": _iso(10), "early_join_minutes": 15}) is None

    def test_closed_before_lobby_opens(self):
        # starts in 30 min, only 15-min early window → 25 min too early.
        assert _raises_403(_check_lobby_window,
                           {"starts_at": _iso(30), "early_join_minutes": 15}) == 403

    def test_late_join_always_allowed(self):
        # started 5 min ago — the window only gates EARLY entry, never late.
        assert _check_lobby_window({"starts_at": _iso(-5), "early_join_minutes": 15}) is None

    def test_no_early_window_matches_start(self):
        # early=0 → lobby opens exactly at starts_at, so 10 min before is 403.
        assert _raises_403(_check_lobby_window,
                           {"starts_at": _iso(10), "early_join_minutes": 0}) == 403

    def test_unscheduled_exam_always_open(self):
        assert _check_lobby_window({}) is None
        assert _check_lobby_window({"starts_at": None}) is None

    def test_closed_after_ends_at(self):
        assert _raises_403(_check_lobby_window,
                           {"starts_at": _iso(-60), "ends_at": _iso(-5),
                            "early_join_minutes": 15}) == 403

    def test_early_minutes_clamped(self):
        # Absurd 9999 → clamped to 240; starts in 100 min is within 240 → open.
        assert _check_lobby_window({"starts_at": _iso(100), "early_join_minutes": 9999}) is None


# ── Exam-started (questions) gate ─────────────────────────────────
class TestExamStarted:
    def test_locked_before_start_even_when_lobby_open(self):
        # In the early window the lobby is open, but questions stay locked.
        cfg = {"starts_at": _iso(10), "early_join_minutes": 15}
        assert _check_lobby_window(cfg) is None          # lobby OPEN
        assert _raises_403(_check_exam_started, cfg) == 403  # questions LOCKED

    def test_open_at_or_after_start(self):
        assert _check_exam_started({"starts_at": _iso(-1)}) is None

    def test_unscheduled_always_open(self):
        assert _check_exam_started({}) is None

    def test_start_only_does_not_block_after_ends_at(self):
        # Start-only by design: a session running up to/after ends_at can still
        # refetch questions on reconnect (ends_at is enforced at entry/submit).
        assert _check_exam_started({"starts_at": _iso(-60), "ends_at": _iso(-5)}) is None
