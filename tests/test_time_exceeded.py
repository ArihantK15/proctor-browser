"""time_exceeded must use a SERVER-side start reference, never the client's
time_taken_secs, when the session row's started_at is missing (forensic #21).
Otherwise a cheater submits a low time_taken_secs to dodge the flag."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import app.routers.exam as exam


def test_elapsed_uses_server_start_reference_over_client_value():
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    started_one_hour_ago = (now - timedelta(hours=1)).isoformat()
    # Client claims only 60s elapsed; server reference says 3600s. Server wins.
    elapsed = exam._resolve_server_elapsed(now, started_one_hour_ago, 60, 0)
    assert abs(elapsed - 3600) < 1


def test_elapsed_subtracts_paused_time():
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    started = (now - timedelta(seconds=3600)).isoformat()
    elapsed = exam._resolve_server_elapsed(now, started, 60, 600)
    assert abs(elapsed - 3000) < 1


def test_elapsed_falls_back_to_client_only_when_no_server_ref():
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert exam._resolve_server_elapsed(now, None, 1234, 0) == 1234
    # Unparseable ref → still falls back to client (never crashes).
    assert exam._resolve_server_elapsed(now, "not-a-date", 1234, 0) == 1234


class _FakeAtable:
    def __init__(self, rows):
        self._rows = rows
        self.filters = []

    def __call__(self, table):
        self._table = table
        return self

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    async def execute(self):
        r = MagicMock()
        r.data = self._rows
        return r


def test_server_start_reference_reads_exam_started_event(monkeypatch):
    fake = _FakeAtable([{"created_at": "2026-01-01T11:00:00+00:00"}])
    monkeypatch.setattr(exam, "_atable", fake)
    ref = asyncio.run(exam._server_start_reference("ALICE001_abc", "teacher-1"))
    assert ref == "2026-01-01T11:00:00+00:00"
    # It must filter to the exam_started event, scoped to the session.
    assert ("violation_type", "exam_started") in fake.filters
    assert ("session_key", "ALICE001_abc") in fake.filters


def test_server_start_reference_none_when_no_event(monkeypatch):
    monkeypatch.setattr(exam, "_atable", _FakeAtable([]))
    assert asyncio.run(exam._server_start_reference("ALICE001_abc", "teacher-1")) is None
