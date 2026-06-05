"""
Regression tests for /sessions — the Live tab payload.

What broke and why we care:
  Before this test pinned the behaviour, any session with
  status='in_progress' was rendered as "Live" on the dashboard,
  regardless of when the student's client last heartbeat'd.
  That left abandoned sessions (student closed laptop, crashed,
  network died) frozen on the Live tab for hours. Teachers
  would see "ACTIVE NOW: 5" that was entirely stale rows.

  Fix: the server now classifies each session into
  live_state ∈ {"live","stale","submitted"} based on the age of
  last_heartbeat vs _CLEAR_ACTIVE_WINDOW (120s).

These tests lock in:
  1. Fresh heartbeat → live_state == "live", counted in `sessions`.
  2. Old heartbeat → live_state == "stale", NOT counted in `sessions`
     (but still appears in all_sessions).
  3. No heartbeat at all → live_state == "stale".
  4. status=="completed" → live_state == "submitted".
  5. Exam scoping still filters as before.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.conftest import shared_supabase_mock,  make_admin_token  # noqa: E402


@pytest.fixture
def admin_headers():
    return {"Authorization": f"Bearer {make_admin_token()}"}


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _viol(sk, vtype="heartbeat", severity="low",
          ts=None, teacher_id="teacher-1"):
    return {
        "session_key":    sk,
        "violation_type": vtype,
        "severity":       severity,
        "created_at":     ts or _iso(datetime.now(timezone.utc)),
        "details":        "{}",
        "teacher_id":     teacher_id,
    }


class _SessionsStub:
    """Fluent-builder stub that dispatches on table + chained eq filters.

    Mirrors the shape of the Supabase Python client the route uses:
        supabase.table("foo").select(...).eq(...).gte(...).order(...).execute()
    """

    def __init__(self, sessions=None, violations=None, teachers=None):
        self.sessions = sessions or []
        self.violations = violations or []
        self.teachers = teachers or [{"id": "teacher-1", "email": "p@t.com"}]

    def __call__(self, table):
        chain = MagicMock()
        chain._table = table
        chain._eqs = {}

        def _select(*a, **k): return chain
        def _eq(c, v): chain._eqs[c] = v; return chain
        def _gte(*a, **k): return chain
        def _order(*a, **k): return chain
        def _limit(*a, **k): return chain

        def _execute():
            if table == "teachers":
                return MagicMock(data=self.teachers)
            if table == "exam_sessions":
                tid = chain._eqs.get("teacher_id")
                eid = chain._eqs.get("exam_id")
                rows = list(self.sessions)
                if tid is not None:
                    rows = [r for r in rows if str(r.get("teacher_id")) == str(tid)]
                if eid is not None:
                    rows = [r for r in rows if str(r.get("exam_id")) == str(eid)]
                return MagicMock(data=rows)
            if table == "violations":
                tid = chain._eqs.get("teacher_id")
                rows = list(self.violations)
                if tid is not None:
                    rows = [r for r in rows if str(r.get("teacher_id")) == str(tid)]
                return MagicMock(data=rows)
            return MagicMock(data=[])

        chain.select.side_effect = _select
        chain.eq.side_effect = _eq
        chain.gte.side_effect = _gte
        chain.order.side_effect = _order
        chain.limit.side_effect = _limit
        chain.execute.side_effect = _execute
        return chain


class TestLiveSessions:

    def test_fresh_heartbeat_is_live(self, client, admin_headers):
        """Heartbeat 10s ago → live_state=='live', counted in Active."""
        now = datetime.now(timezone.utc)
        stub = _SessionsStub(
            sessions=[{
                "session_key":    "sess_alice_1",
                "teacher_id":     "teacher-1",
                "exam_id":        "exam-1",
                "status":         "in_progress",
                "risk_score":     None,
                "last_heartbeat": _iso(now - timedelta(seconds=10)),
                "started_at":     _iso(now - timedelta(minutes=5)),
                "submitted_at":   None,
            }],
            violations=[_viol("sess_alice_1", vtype="face_missing")],
        )
        with patch.object(shared_supabase_mock(), "table") as mock_table, \
             patch("app.routers.exam.compute_risk_score",
                          return_value={"risk_score": 12}):
            mock_table.side_effect = stub
            r = client.get("/api/v1/admin/sessions", headers=admin_headers)
        assert r.status_code == 200, r.text
        d = r.json()
        assert len(d["all_sessions"]) == 1
        s = d["all_sessions"][0]
        assert s["live_state"] == "live"
        assert s["submitted"] is False
        assert s["heartbeat_age_sec"] is not None
        assert s["heartbeat_age_sec"] < 120
        # Active counter uses live_state=="live"
        assert len(d["sessions"]) == 1

    def test_stale_heartbeat_is_stale_not_live(self, client, admin_headers):
        """Heartbeat 10 min ago → live_state=='stale' and NOT counted Active.

        This is the exact bug the user reported: sessions frozen at
        in_progress for hours because the student client died without
        submitting. The Live badge must downgrade.
        """
        now = datetime.now(timezone.utc)
        stub = _SessionsStub(
            sessions=[{
                "session_key":    "sess_bob_1",
                "teacher_id":     "teacher-1",
                "exam_id":        "exam-1",
                "status":         "in_progress",
                "risk_score":     None,
                "last_heartbeat": _iso(now - timedelta(minutes=10)),
                "started_at":     _iso(now - timedelta(minutes=15)),
                "submitted_at":   None,
            }],
            violations=[_viol("sess_bob_1", vtype="vpn_detected",
                              severity="high",
                              ts=_iso(now - timedelta(minutes=10)))],
        )
        with patch.object(shared_supabase_mock(), "table") as mock_table, \
             patch("app.routers.exam.compute_risk_score",
                          return_value={"risk_score": 50}):
            mock_table.side_effect = stub
            r = client.get("/api/v1/admin/sessions", headers=admin_headers)
        assert r.status_code == 200, r.text
        d = r.json()
        assert len(d["all_sessions"]) == 1
        s = d["all_sessions"][0]
        assert s["live_state"] == "stale", (
            f"Session 10 min stale must be classified 'stale', got "
            f"{s['live_state']!r}. The dashboard relies on this to stop "
            "showing abandoned sessions as Live."
        )
        # Still listed in all_sessions so the teacher can see it — but
        # absent from `sessions` which drives the 'Active Now' counter.
        assert len(d["sessions"]) == 0

    def test_missing_heartbeat_is_stale(self, client, admin_headers):
        """in_progress row with no last_heartbeat at all → stale.

        A session can land in this state when the row was written but
        the client died before the first heartbeat tick. It must not
        be counted as Live forever.
        """
        stub = _SessionsStub(
            sessions=[{
                "session_key":    "sess_ghost_1",
                "teacher_id":     "teacher-1",
                "exam_id":        "exam-1",
                "status":         "in_progress",
                "risk_score":     None,
                "last_heartbeat": None,
                "started_at":     None,
                "submitted_at":   None,
            }],
            violations=[_viol("sess_ghost_1")],
        )
        with patch.object(shared_supabase_mock(), "table") as mock_table, \
             patch("app.routers.exam.compute_risk_score",
                          return_value={"risk_score": 0}):
            mock_table.side_effect = stub
            r = client.get("/api/v1/admin/sessions", headers=admin_headers)
        assert r.status_code == 200, r.text
        d = r.json()
        s = d["all_sessions"][0]
        assert s["live_state"] == "stale"
        assert s["heartbeat_age_sec"] is None

    def test_in_progress_session_without_violations_still_appears(self, client, admin_headers):
        """Live table is backed by exam_sessions, not only violations.

        A clean student may have no violation rows yet. The teacher must still
        see that candidate in Live Sessions instead of watching them vanish
        between SSE init and the next polling refresh.
        """
        now = datetime.now(timezone.utc)
        stub = _SessionsStub(
            sessions=[{
                "session_key":    "sess_clean_1",
                "teacher_id":     "teacher-1",
                "exam_id":        "exam-1",
                "status":         "in_progress",
                "risk_score":     0,
                "last_heartbeat": _iso(now - timedelta(seconds=8)),
                "started_at":     _iso(now - timedelta(minutes=1)),
                "submitted_at":   None,
                "room_cam_status": "disabled",
            }],
            violations=[],
        )
        with patch.object(shared_supabase_mock(), "table") as mock_table:
            mock_table.side_effect = stub
            r = client.get("/api/v1/admin/sessions", headers=admin_headers)
        assert r.status_code == 200, r.text
        d = r.json()
        assert len(d["all_sessions"]) == 1
        s = d["all_sessions"][0]
        assert s["session_id"] == "sess_clean_1"
        assert s["live_state"] == "live"
        assert s["last_event"] == "heartbeat"
        assert len(d["sessions"]) == 1

    def test_completed_session_is_submitted(self, client, admin_headers):
        """status=='completed' → live_state=='submitted', submitted=True."""
        now = datetime.now(timezone.utc)
        stub = _SessionsStub(
            sessions=[{
                "session_key":    "sess_carol_1",
                "teacher_id":     "teacher-1",
                "exam_id":        "exam-1",
                "status":         "completed",
                "risk_score":     18,
                "last_heartbeat": _iso(now - timedelta(minutes=30)),
                "started_at":     _iso(now - timedelta(hours=1)),
                "submitted_at":   _iso(now - timedelta(minutes=30)),
            }],
            violations=[_viol("sess_carol_1", vtype="window_focus_lost",
                              severity="medium")],
        )
        with patch.object(shared_supabase_mock(), "table") as mock_table, \
             patch("app.routers.exam.compute_risk_score",
                          return_value={"risk_score": 18}):
            mock_table.side_effect = stub
            r = client.get("/api/v1/admin/sessions", headers=admin_headers)
        assert r.status_code == 200, r.text
        d = r.json()
        s = d["all_sessions"][0]
        assert s["live_state"] == "submitted"
        assert s["submitted"] is True
        assert len(d["sessions"]) == 0

    def test_live_view_shows_all_active_sessions_across_exams(self, client, admin_headers):
        """The LIVE view must show ALL of a teacher's active sessions
        regardless of the dashboard's selected exam — a proctor can't miss
        a student testing in a sibling exam (or a session with no exam_id).
        The exam_id query param does NOT filter the live list; tenant
        isolation stays via teacher_id. (Per-exam scoping lives on the
        results/history endpoints, not here.)"""
        now = datetime.now(timezone.utc)
        stub = _SessionsStub(
            sessions=[
                {
                    "session_key":    "sess_in_exam_1",
                    "teacher_id":     "teacher-1",
                    "exam_id":        "exam-1",
                    "status":         "in_progress",
                    "risk_score":     None,
                    "last_heartbeat": _iso(now - timedelta(seconds=5)),
                    "started_at":     _iso(now - timedelta(minutes=2)),
                    "submitted_at":   None,
                },
                {
                    "session_key":    "sess_other_exam_1",
                    "teacher_id":     "teacher-1",
                    "exam_id":        "exam-2",
                    "status":         "in_progress",
                    "risk_score":     None,
                    "last_heartbeat": _iso(now - timedelta(seconds=5)),
                    "started_at":     _iso(now - timedelta(minutes=2)),
                    "submitted_at":   None,
                },
            ],
            violations=[
                _viol("sess_in_exam_1"),
                _viol("sess_other_exam_1"),
            ],
        )
        with patch.object(shared_supabase_mock(), "table") as mock_table, \
             patch("app.routers.exam.compute_risk_score",
                          return_value={"risk_score": 5}):
            mock_table.side_effect = stub
            # Even with ?exam_id=exam-1 selected, the live view shows BOTH
            # of teacher-1's active sessions (exam-1 AND exam-2).
            r = client.get("/api/v1/admin/sessions?exam_id=exam-1", headers=admin_headers)
        assert r.status_code == 200, r.text
        d = r.json()
        keys = {s["session_id"] for s in d["all_sessions"]}
        assert keys == {"sess_in_exam_1", "sess_other_exam_1"}, (
            f"live view must show all active sessions regardless of exam, got {keys}"
        )


# ── Proctor readiness derivation (dashboard "AI degraded" badge) ──────────────
# Locks in _derive_proctor_readiness: the live payload now folds each session's
# proctoring_tier / proctor_camera_failed events into proctor_tier + missing, so
# a teacher sees a student whose exam is proctored at reduced capacity (or not at
# all) instead of assuming full coverage. Events are newest-first (created_at DESC).
from app.services.sessions import _derive_proctor_readiness  # noqa: E402


def test_proctor_readiness_reads_latest_tier():
    evs = [{"violation_type": "proctoring_tier",
            "details": '{"tier": "reduced", "missing": ["yolo", "gaze"]}'}]
    assert _derive_proctor_readiness(evs) == ("reduced", ["yolo", "gaze"])


def test_proctor_readiness_camera_failed_wins_when_newest():
    # newest-first: a camera failure after an earlier boot is the live state
    evs = [{"violation_type": "proctor_camera_failed", "details": "no camera"},
           {"violation_type": "proctoring_tier", "details": '{"tier": "full", "missing": []}'}]
    assert _derive_proctor_readiness(evs) == ("camera_failed", [])


def test_proctor_readiness_newest_boot_overrides_older_failure():
    # a successful re-boot (newest) supersedes a prior camera failure
    evs = [{"violation_type": "proctoring_tier", "details": '{"tier": "full", "missing": []}'},
           {"violation_type": "proctor_camera_failed", "details": "no camera"}]
    assert _derive_proctor_readiness(evs) == ("full", [])


def test_proctor_readiness_none_without_proctor_events():
    assert _derive_proctor_readiness([]) == (None, [])
    assert _derive_proctor_readiness([{"violation_type": "tab_switch"}]) == (None, [])


def test_proctor_readiness_tolerates_bad_details_json():
    evs = [{"violation_type": "proctoring_tier", "details": "not json{"}]
    assert _derive_proctor_readiness(evs) == (None, [])


# ── derive_live_state: paused must surface so the dashboard shows Resume ──────
from app.services.sessions import derive_live_state  # noqa: E402


def test_derive_live_state_surfaces_paused():
    # The Resume button only renders when live_state=='paused'; before this,
    # a paused session fell through to live/stale and was un-resumable in the UI.
    assert derive_live_state({"status": "paused"})[0] == "paused"


def test_derive_live_state_terminal_and_stale_unchanged():
    assert derive_live_state({"status": "completed"})[0] == "submitted"
    assert derive_live_state({"status": "in_progress"})[0] == "stale"  # no heartbeat
