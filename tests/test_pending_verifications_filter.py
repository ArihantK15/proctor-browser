"""
Regression tests for the multi-exam pending-verifications filter.

Bug history:
  • The student-facing writer (/api/id-verification) did not stamp exam_id
    into violations.details, so /api/admin/pending-verifications?exam_id=X
    could not filter correctly until a session row existed in exam_sessions.
    Students waiting for ID approval therefore never appeared under their
    exam's Live Sessions tab when the teacher had 2+ exams.

  • Fix: writer now stamps `exam_id` in details; reader filters on the
    stamped value first, with a legacy fallback via exam_sessions lookup
    so rows created before the fix still surface.

These tests mock Supabase (see conftest.py) so they run offline in ~ms.
They lock in both halves of the contract — writer stamps, reader filters —
so neither can silently regress in isolation.
"""
import base64
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.dirname(__file__))

from conftest import make_student_token, make_admin_token  # noqa: E402


# Smallest valid JPEG (1x1 pixel) so the base64 guard and file-write
# path are exercised without needing real image bytes in the fixture.
_TINY_JPEG_B64 = (
    "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
    "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIy"
    "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIA"
    "AhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQA"
    "AAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3"
    "ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWm"
    "p6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/9oADAMB"
    "AAIRAxEAPwD3+iiigD//2Q=="
)


def _mk_viol_row(details: dict, session_key="S1", row_id=1, teacher_id="teacher-1"):
    """Helper: shape a violations-table row exactly like Supabase returns."""
    return {
        "id":             row_id,
        "session_key":    session_key,
        "violation_type": "id_verification",
        "severity":       "low",
        "teacher_id":     teacher_id,
        "details":        json.dumps(details),
        "created_at":     "2026-04-20T12:00:00+00:00",
    }


# ─── WRITER: /api/id-verification must stamp exam_id ─────────────────

class TestIdVerificationWriter:
    """The writer half of the contract: the student's JWT 'eid' claim must
    flow into violations.details.exam_id so the reader can filter."""

    def test_writer_stamps_exam_id_from_student_jwt(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("SCREENSHOTS_DIR", str(tmp_path))
        import main  # noqa: WPS433 — import after env patch

        token = make_student_token(roll="ALICE001", tid="teacher-1", eid="exam-A")

        with patch.object(main, "supabase") as mock_sb, \
             patch.object(main, "_check_session_ownership"):
            # Capture the insert payload so we can inspect the stamped details.
            captured = {}

            def _insert(row):
                captured["row"] = row
                res = MagicMock()
                res.execute.return_value = MagicMock(data=[row])
                return res

            mock_sb.table.return_value.insert.side_effect = _insert

            resp = client.post(
                "/api/id-verification",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "session_id":   "ALICE001_1",
                    "roll_number":  "ALICE001",
                    "selfie_frame": _TINY_JPEG_B64,
                    "id_frame":     _TINY_JPEG_B64,
                    "full_name":    "Alice Example",
                },
            )

        assert resp.status_code == 200, resp.text
        assert "row" in captured, "violations.insert was never called"
        details = json.loads(captured["row"]["details"])
        assert details["exam_id"] == "exam-A", (
            f"writer failed to stamp exam_id — got {details!r}. "
            "This is the regression that hid pending IDs for multi-exam teachers."
        )
        assert details["status"] == "pending"
        assert details["roll_number"] == "ALICE001"

    def test_writer_stamps_empty_string_when_eid_claim_missing(self, client, tmp_path, monkeypatch):
        """Back-compat: single-exam teachers have no 'eid' claim. Writer
        should stamp '' rather than omit the key, so the reader's .get()
        logic treats it as 'legacy row' and falls back to session lookup."""
        monkeypatch.setenv("SCREENSHOTS_DIR", str(tmp_path))
        import main

        # Hand-build a token without 'eid' to simulate legacy single-exam clients.
        from jose import jwt as jose_jwt
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        token = jose_jwt.encode(
            {"roll": "BOB002", "tid": "teacher-1",
             "iat": now, "exp": now + timedelta(hours=1)},
            os.environ["SUPABASE_JWT_SECRET"], algorithm="HS256",
        )

        with patch.object(main, "supabase") as mock_sb, \
             patch.object(main, "_check_session_ownership"):
            captured = {}
            mock_sb.table.return_value.insert.side_effect = lambda row: (
                captured.setdefault("row", row),
                MagicMock(execute=lambda: MagicMock(data=[row]))
            )[1]

            resp = client.post(
                "/api/id-verification",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "session_id":   "BOB002_1",
                    "roll_number":  "BOB002",
                    "selfie_frame": _TINY_JPEG_B64,
                    "id_frame":     _TINY_JPEG_B64,
                    "full_name":    "Bob Example",
                },
            )

        assert resp.status_code == 200, resp.text
        details = json.loads(captured["row"]["details"])
        assert details.get("exam_id") == "", details


# ─── READER: /api/admin/pending-verifications must filter ─────────────

class TestPendingVerificationsReader:
    """The reader half: given mixed rows under one teacher, filtering by
    exam_id must return only the matching rows. This is the user-visible
    bug symptom from the original report."""

    def _stub_violations_chain(self, mock_sb, rows):
        """Build the chained-call mock Supabase fluent API returns for
        .table('violations').select('*').eq(...).eq(...).order(...).execute()."""
        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.order.return_value = chain
        chain.limit.return_value = chain
        chain.execute.return_value = MagicMock(data=rows)
        return chain

    def _stub_exam_sessions_chain(self, mock_sb, sessions):
        """exam_sessions lookup used by the legacy fallback path."""
        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.execute.return_value = MagicMock(data=sessions)
        return chain

    def _stub_teachers_chain(self, teacher_id="teacher-1"):
        """require_admin → _get_teacher_by_id → .table('teachers').select.eq.execute"""
        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.execute.return_value = MagicMock(data=[{
            "id":        teacher_id,
            "email":     "prof@test.com",
            "full_name": "Prof Test",
        }])
        return chain

    def _router(self, mock_sb, viol_rows, sessions):
        """Build a .table() side_effect that answers every table the
        endpoint touches: teachers (auth), violations (read), exam_sessions
        (fallback)."""
        def _route(name):
            if name == "teachers":
                return self._stub_teachers_chain()
            if name == "violations":
                return self._stub_violations_chain(mock_sb, viol_rows)
            return self._stub_exam_sessions_chain(mock_sb, sessions)
        return _route

    def test_filter_returns_only_matching_exam(self, client, admin_headers):
        """Two pending rows, different stamped exam_ids — ?exam_id=A must
        return only row A."""
        import main

        rows = [
            _mk_viol_row({
                "status": "pending", "roll_number": "ALICE001",
                "selfie_file": "s1.jpg", "id_file": "i1.jpg",
                "full_name": "Alice", "exam_id": "exam-A",
            }, session_key="S_A", row_id=1),
            _mk_viol_row({
                "status": "pending", "roll_number": "BOB002",
                "selfie_file": "s2.jpg", "id_file": "i2.jpg",
                "full_name": "Bob", "exam_id": "exam-B",
            }, session_key="S_B", row_id=2),
        ]

        with patch.object(main, "supabase") as mock_sb, \
             patch.object(main, "_get_teacher_by_id",
                          return_value={"id": "teacher-1", "email": "p@t.com"}):
            mock_sb.table.side_effect = self._router(mock_sb, rows, [])
            resp = client.get(
                "/api/admin/pending-verifications?exam_id=exam-A",
                headers=admin_headers,
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        rolls = [p["roll_number"] for p in body["pending"]]
        assert rolls == ["ALICE001"], (
            f"expected only exam-A row, got {rolls!r} — "
            "filter is not honoring stamped exam_id"
        )

    def test_no_filter_returns_both(self, client, admin_headers):
        """Sanity: without ?exam_id, every pending row comes back.
        This guards against an over-eager filter regressing the default view."""
        import main

        rows = [
            _mk_viol_row({
                "status": "pending", "roll_number": "ALICE001",
                "selfie_file": "s.jpg", "id_file": "i.jpg",
                "full_name": "Alice", "exam_id": "exam-A",
            }, session_key="S_A", row_id=1),
            _mk_viol_row({
                "status": "pending", "roll_number": "BOB002",
                "selfie_file": "s.jpg", "id_file": "i.jpg",
                "full_name": "Bob", "exam_id": "exam-B",
            }, session_key="S_B", row_id=2),
        ]

        with patch.object(main, "supabase") as mock_sb, \
             patch.object(main, "_get_teacher_by_id",
                          return_value={"id": "teacher-1", "email": "p@t.com"}):
            mock_sb.table.side_effect = self._router(mock_sb, rows, [])
            resp = client.get(
                "/api/admin/pending-verifications",
                headers=admin_headers,
            )

        assert resp.status_code == 200, resp.text
        rolls = {p["roll_number"] for p in resp.json()["pending"]}
        assert rolls == {"ALICE001", "BOB002"}

    def test_legacy_row_without_stamped_eid_falls_back_to_session_lookup(
            self, client, admin_headers):
        """Rows created before the writer started stamping exam_id have
        details.exam_id == ''. The reader must fall back to cross-referencing
        exam_sessions.session_key to decide whether they belong to the
        requested exam. Otherwise those legacy students disappear after
        the fix ships."""
        import main

        rows = [
            _mk_viol_row({
                "status": "pending", "roll_number": "LEGACY001",
                "selfie_file": "s.jpg", "id_file": "i.jpg",
                "full_name": "Legacy Student", "exam_id": "",
            }, session_key="SESS_LEGACY", row_id=9),
        ]
        # exam_sessions has this session_key under exam-A — legacy row
        # belongs to exam-A via cross-reference.
        legacy_sessions = [{"session_key": "SESS_LEGACY"}]

        with patch.object(main, "supabase") as mock_sb, \
             patch.object(main, "_get_teacher_by_id",
                          return_value={"id": "teacher-1", "email": "p@t.com"}):
            mock_sb.table.side_effect = self._router(mock_sb, rows, legacy_sessions)
            resp = client.get(
                "/api/admin/pending-verifications?exam_id=exam-A",
                headers=admin_headers,
            )

        assert resp.status_code == 200, resp.text
        rolls = [p["roll_number"] for p in resp.json()["pending"]]
        assert rolls == ["LEGACY001"], (
            "legacy-row fallback path broken — rows submitted before the "
            "exam_id stamp was added must still appear via exam_sessions lookup"
        )

    def test_legacy_row_with_no_matching_session_is_filtered_out(
            self, client, admin_headers):
        """Mirror of the above: legacy row whose session_key is NOT in
        exam_sessions for the requested exam must be suppressed."""
        import main

        rows = [
            _mk_viol_row({
                "status": "pending", "roll_number": "LEGACY_OTHER",
                "selfie_file": "s.jpg", "id_file": "i.jpg",
                "full_name": "Other Legacy", "exam_id": "",
            }, session_key="SESS_ORPHAN", row_id=10),
        ]
        # Fallback lookup returns no sessions for exam-A.
        legacy_sessions = []

        with patch.object(main, "supabase") as mock_sb, \
             patch.object(main, "_get_teacher_by_id",
                          return_value={"id": "teacher-1", "email": "p@t.com"}):
            mock_sb.table.side_effect = self._router(mock_sb, rows, legacy_sessions)
            resp = client.get(
                "/api/admin/pending-verifications?exam_id=exam-A",
                headers=admin_headers,
            )

        assert resp.status_code == 200, resp.text
        assert resp.json()["pending"] == []

    def test_non_pending_rows_ignored(self, client, admin_headers):
        """Approved/rejected rows share the table. The reader must skip
        anything whose details.status != 'pending'."""
        import main

        rows = [
            _mk_viol_row({
                "status": "approved", "roll_number": "OLDER001",
                "selfie_file": "s.jpg", "id_file": "i.jpg",
                "full_name": "Already Approved", "exam_id": "exam-A",
            }, session_key="S1", row_id=1),
            _mk_viol_row({
                "status": "pending", "roll_number": "CURRENT001",
                "selfie_file": "s.jpg", "id_file": "i.jpg",
                "full_name": "Waiting", "exam_id": "exam-A",
            }, session_key="S2", row_id=2),
        ]

        with patch.object(main, "supabase") as mock_sb, \
             patch.object(main, "_get_teacher_by_id",
                          return_value={"id": "teacher-1", "email": "p@t.com"}):
            mock_sb.table.side_effect = self._router(mock_sb, rows, [])
            resp = client.get(
                "/api/admin/pending-verifications?exam_id=exam-A",
                headers=admin_headers,
            )

        assert resp.status_code == 200, resp.text
        rolls = [p["roll_number"] for p in resp.json()["pending"]]
        assert rolls == ["CURRENT001"]
