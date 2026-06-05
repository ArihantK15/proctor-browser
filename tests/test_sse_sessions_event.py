"""Regression: the SSE sessions 'refresh' tick must carry the full snapshot.

The 5s refresh once sent only {"ts": ...}; the dashboard's refresh handler
renders straight from data.sessions / data.all_sessions, so the live table
blanked every 5 seconds (live sessions "vanish after 5s"). _sessions_event_data
centralises the init/refresh payload shape so the two events can't drift apart
into that contract mismatch again.
"""
from app.routers.sse import _sessions_event_data

SNAP = {
    "sessions": {"s1": {"session_id": "s1"}},
    "all_sessions": [{"session_id": "s1"}],
}


def test_refresh_carries_sessions_and_all_sessions():
    data = _sessions_event_data(SNAP, with_ts=True)
    assert data["sessions"] == SNAP["sessions"]
    assert data["all_sessions"] == SNAP["all_sessions"]
    assert "ts" in data                       # refresh tick is timestamped
    assert data["realtime"] in ("live", "degraded")


def test_init_matches_refresh_session_payload_minus_ts():
    init = _sessions_event_data(SNAP)
    refresh = _sessions_event_data(SNAP, with_ts=True)
    assert init["sessions"] == refresh["sessions"]
    assert init["all_sessions"] == refresh["all_sessions"]
    assert "ts" not in init                   # only refresh adds ts


def test_missing_keys_default_safely_not_dropped():
    data = _sessions_event_data({}, with_ts=True)
    assert data["sessions"] == {}
    assert data["all_sessions"] == []
    assert data["realtime"] in ("live", "degraded")
