"""
Tests for the optional reason fields on the ID-decision flow.

Contract:
  • POST /api/v1/admin/id-decision accepts optional reason_code (must be
    one of ID_REJECT_REASON_CODES) + reason_text (capped at 500 chars).
  • Both are persisted into the existing violations.details JSON next to
    decided_by / decided_at.
  • GET /api/v1/id-verification/status surfaces both fields to the
    student so the rejected / retake screens can show "Why".

Mocks Supabase via the existing conftest pattern, lifted from
tests/test_admin_exams_coverage.py.
"""
import json
import os
import sys
from unittest.mock import MagicMock, patch


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.conftest import (  # noqa: E402
    shared_supabase_mock, make_admin_token, make_student_token,
)


TEACHER = {"id": "teacher-1", "email": "prof@test.com", "full_name": "Prof T"}


def _admin_headers():
    return {"Authorization": f"Bearer {make_admin_token(teacher_id='teacher-1', email='prof@test.com')}"}


def _viol_row(details: dict, row_id=42, teacher_id="teacher-1",
              session_key="S1") -> dict:
    return {
        "id":             row_id,
        "session_key":    session_key,
        "violation_type": "id_verification",
        "severity":       "low",
        "teacher_id":     teacher_id,
        "details":        json.dumps(details),
        "created_at":     "2026-05-29T10:00:00+00:00",
    }


def _table_side_effect(mapping, captured: dict):
    """Build a Supabase mock that returns table-name-keyed data and
    captures any update() / insert() row for later inspection."""
    def _build_chain(name, data):
        m = MagicMock()
        for attr in ("select", "eq", "neq", "is_", "in_", "order",
                     "limit", "single", "range", "gte", "lte"):
            getattr(m, attr).return_value = m

        def _capture_update(row):
            captured.setdefault(f"{name}_update", row)
            return m
        def _capture_insert(row):
            captured.setdefault(f"{name}_insert", row)
            return m
        m.update.side_effect = _capture_update
        m.insert.side_effect = _capture_insert
        m.delete.return_value = m

        async def _execute():
            return MagicMock(data=data)
        m.execute = _execute
        return m

    def _side(name):
        return _build_chain(name, mapping.get(name, []))
    return _side


# ─── DECISION WRITER: persists reason fields ─────────────────────────

class TestIdDecisionReasonWrite:
    def test_chip_reason_persists_into_details_json(self, client):
        existing = {
            "status": "pending",
            "selfie_file": "x.jpg", "id_file": "y.jpg",
            "roll_number": "ALICE001", "exam_id": "exam-A",
        }
        captured: dict = {}
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
                "teachers": [TEACHER],
                "violations": [_viol_row(existing)],
             }, captured)), \
             patch("app.routers.admin_verification._cache", None):
            resp = client.post(
                "/api/v1/admin/id-decision",
                headers=_admin_headers(),
                json={
                    "violation_id": 42,
                    "session_key": "S1",
                    "decision": "retake",
                    "reason_code": "selfie_blurry",
                    "reason_text": "Please sit closer to the light",
                },
            )
        assert resp.status_code == 200, resp.text
        assert "violations_update" in captured, "no violations.update happened"
        new_details = json.loads(captured["violations_update"]["details"])
        assert new_details["status"] == "retake"
        assert new_details["reason_code"] == "selfie_blurry"
        assert new_details["reason_text"] == "Please sit closer to the light"
        # Pre-existing fields preserved.
        assert new_details["roll_number"] == "ALICE001"
        assert new_details["exam_id"] == "exam-A"

    def test_unknown_reason_code_returns_400(self, client):
        captured: dict = {}
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
                "teachers": [TEACHER],
                "violations": [],
             }, captured)):
            resp = client.post(
                "/api/v1/admin/id-decision",
                headers=_admin_headers(),
                json={
                    "violation_id": 42,
                    "session_key": "S1",
                    "decision": "rejected",
                    "reason_code": "totally_made_up_code",
                    "reason_text": "",
                },
            )
        assert resp.status_code == 400, resp.text
        assert "reason_code" in resp.text.lower()
        assert "violations_update" not in captured, "should reject before any DB write"

    def test_reason_text_capped_at_500_chars(self, client):
        existing = {"status": "pending", "roll_number": "BOB",
                    "exam_id": "exam-A",
                    "selfie_file": "x.jpg", "id_file": "y.jpg"}
        captured: dict = {}
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
                "teachers": [TEACHER],
                "violations": [_viol_row(existing)],
             }, captured)), \
             patch("app.routers.admin_verification._cache", None):
            resp = client.post(
                "/api/v1/admin/id-decision",
                headers=_admin_headers(),
                json={
                    "violation_id": 42,
                    "session_key": "S1",
                    "decision": "retake",
                    "reason_code": "",
                    "reason_text": "x" * 800,
                },
            )
        assert resp.status_code == 200, resp.text
        saved = json.loads(captured["violations_update"]["details"])
        assert len(saved["reason_text"]) == 500


# ─── STATUS READER: surfaces reason fields to student ────────────────

class TestIdVerificationStatusReason:
    def test_status_returns_reason_fields(self, client):
        token = make_student_token(roll="ALICE001", tid="teacher-1", eid="exam-A")
        rows = [_viol_row({
            "status": "rejected",
            "reason_code": "face_mismatch",
            "reason_text": "Selfie and ID look like different people.",
        }, session_key="ALICE001_1")]

        captured: dict = {}
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
                "violations": rows,
             }, captured)), \
             patch("app.dependencies._check_session_ownership"):
            resp = client.get(
                "/api/v1/id-verification/status?session_id=ALICE001_1",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "rejected"
        assert body["reason_code"] == "face_mismatch"
        assert body["reason_text"] == "Selfie and ID look like different people."

    def test_status_back_compat_when_reason_fields_missing(self, client):
        """Old rows submitted before this change have no reason fields in
        their details JSON. The status endpoint must still respond with
        empty strings so the student renderer's _idReasonInline() short-
        circuits to the generic copy."""
        token = make_student_token(roll="BOB", tid="teacher-1", eid="exam-A")
        rows = [_viol_row({"status": "approved"}, session_key="BOB_1")]

        captured: dict = {}
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
                "violations": rows,
             }, captured)), \
             patch("app.dependencies._check_session_ownership"):
            resp = client.get(
                "/api/v1/id-verification/status?session_id=BOB_1",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "approved"
        assert body["reason_code"] == ""
        assert body["reason_text"] == ""
