"""Tests for the room camera (phone) feature.

Covers:
  1. Token mint — requires auth, validates session ownership
  2. WS auth — invalid scope rejected, valid token accepted
  3. Singleton — second WS connection kicks the first
  4. Token expiry — expired token rejected at WS handshake
  5. Disconnect — session row flips to offline
  6. Approval flow — approve/reject/status endpoints
"""
import json
import os
import time
import sys
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime, timedelta, timezone

import jwt as _jwt
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")

from tests.conftest import shared_supabase_mock, make_admin_token  # noqa: E402
from app.constants import EXAM_TOKEN_SIGNING_KEY, ROOM_CAM_SIGNING_KEY  # noqa: E402
import app.repositories.sessions as _sess_repo  # noqa: E402


ROOM_CAM_STUDENT_TOKEN = _jwt.encode({
    "roll": "ALICE001", "tid": "teacher-1",
    "scope": "room-cam", "sid": "ALICE001_abc",
    "exp": datetime.now(timezone.utc) + timedelta(hours=2),
}, ROOM_CAM_SIGNING_KEY, algorithm="HS256")

ROOM_CAM_WRONG_SCOPE = _jwt.encode({
    "roll": "ALICE001", "tid": "teacher-1",
    "scope": "live-frame", "sid": "ALICE001_abc",
    "exp": datetime.now(timezone.utc) + timedelta(hours=2),
}, ROOM_CAM_SIGNING_KEY, algorithm="HS256")

ROOM_CAM_EXPIRED = _jwt.encode({
    "roll": "ALICE001", "tid": "teacher-1",
    "scope": "room-cam", "sid": "ALICE001_abc",
    "exp": datetime.now(timezone.utc) - timedelta(hours=1),
}, ROOM_CAM_SIGNING_KEY, algorithm="HS256")


def _mock_ws():
    m = MagicMock()
    m.headers = {"sec-websocket-protocol": f"bearer.{ROOM_CAM_STUDENT_TOKEN}"}
    m.query_params = {}
    return m


@pytest.fixture
def supabase_mock():
    sm = shared_supabase_mock()
    sm.reset_mock()
    return sm


# ═══════════════════════════════════════════════════════════════════
#  1. Token mint
# ═══════════════════════════════════════════════════════════════════

class TestTokenMint:
    def test_requires_auth(self, client):
        resp = client.post("/api/v1/room-cam-token",
                           json={"session_id": "ALICE001_abc"})
        assert resp.status_code == 401  # no Authorization header

    def test_wrong_session_roll_403(self, client):
        token = _jwt.encode({
            "roll": "BOB002", "tid": "teacher-1",
            "exp": datetime.now(timezone.utc) + timedelta(hours=2),
        }, EXAM_TOKEN_SIGNING_KEY, algorithm="HS256")
        resp = client.post("/api/v1/room-cam-token",
                           json={"session_id": "ALICE001_abc"},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403

    def test_success_returns_token(self, client):
        token = _jwt.encode({
            "roll": "ALICE001", "tid": "teacher-1",
            "exp": datetime.now(timezone.utc) + timedelta(hours=2),
        }, EXAM_TOKEN_SIGNING_KEY, algorithm="HS256")
        resp = client.post("/api/v1/room-cam-token",
                           json={"session_id": "ALICE001_abc"},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert data["expires_in_hours"] == 2


# ═══════════════════════════════════════════════════════════════════
#  2. Approval flow
# ═══════════════════════════════════════════════════════════════════

class TestApprovalFlow:
    TEACHER = {"id": "teacher-1", "email": "prof@test.com", "org_id": "org-1",
               "org_role": "admin", "full_name": "Prof T"}

    def _mock_atable(self, data=None):
        """Create a chain mock for _atable queries."""
        chain = MagicMock()
        chain._data = data if data is not None else []
        for attr in ("select", "eq", "limit", "order", "range", "in_"):
            getattr(chain, attr).return_value = chain
        async def _execute():
            r = MagicMock()
            r.data = chain._data
            return r
        chain.execute = _execute
        return chain

    def test_approve_updates_status(self, client):
        with patch("app.auth.admin_auth._get_teacher_by_id", return_value=self.TEACHER), \
             patch("app.routers.admin_liveview.assert_session_accessible", AsyncMock(return_value={})):
            resp = client.post(
                "/api/v1/admin/sessions/ALICE001_abc/room-cam/approve",
                headers={"Authorization": f"Bearer {make_admin_token(teacher_id='teacher-1', email='prof@test.com')}"},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

    def test_reject_updates_status(self, client):
        with patch("app.auth.admin_auth._get_teacher_by_id", return_value=self.TEACHER), \
             patch("app.routers.admin_liveview.assert_session_accessible", AsyncMock(return_value={})):
            resp = client.post(
                "/api/v1/admin/sessions/ALICE001_abc/room-cam/reject",
                headers={"Authorization": f"Bearer {make_admin_token(teacher_id='teacher-1', email='prof@test.com')}"},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"

    def test_approve_co_teacher_session_uses_owner_tid(self, client):
        """Org admin approving a co-teacher's session must key the UPDATE on
        the SESSION OWNER's teacher_id (from assert_session_accessible), not
        the caller's — otherwise it matches zero rows and silently no-ops."""
        captured = {}

        class _UpdChain:
            def update(self, fields):
                captured["fields"] = fields
                return self
            def eq(self, col, val):
                captured.setdefault("eqs", {})[col] = val
                return self
            async def execute(self):
                r = MagicMock()
                r.data = [{"session_key": "ALICE001_abc"}]  # one row updated
                return r

        with patch("app.auth.admin_auth._get_teacher_by_id", return_value=self.TEACHER), \
             patch("app.routers.admin_liveview.assert_session_accessible",
                   AsyncMock(return_value={"teacher_id": "teacher-2"})), \
             patch("app.routers.admin_liveview.resolve_scope",
                   AsyncMock(return_value={"role": "admin", "teacher_id": None, "org_id": "org-1"})), \
             patch("app.database.async_table", side_effect=lambda t: _UpdChain()):
            resp = client.post(
                "/api/v1/admin/sessions/ALICE001_abc/room-cam/approve",
                headers={"Authorization": f"Bearer {make_admin_token(teacher_id='teacher-1', email='prof@test.com')}"},
            )
        assert resp.status_code == 200, resp.text
        assert captured["eqs"]["teacher_id"] == "teacher-2"  # owner, not caller

    def test_approve_zero_rows_updated_404s(self, client):
        """If the UPDATE matches no rows, the endpoint must 404 instead of
        returning ok:true on a no-op."""
        class _EmptyChain:
            def update(self, fields): return self
            def eq(self, col, val): return self
            async def execute(self):
                r = MagicMock()
                r.data = []  # nothing updated
                return r

        with patch("app.auth.admin_auth._get_teacher_by_id", return_value=self.TEACHER), \
             patch("app.routers.admin_liveview.assert_session_accessible",
                   AsyncMock(return_value={"teacher_id": "teacher-2"})), \
             patch("app.routers.admin_liveview.resolve_scope",
                   AsyncMock(return_value={"role": "admin", "teacher_id": None, "org_id": "org-1"})), \
             patch("app.database.async_table", side_effect=lambda t: _EmptyChain()):
            resp = client.post(
                "/api/v1/admin/sessions/ALICE001_abc/room-cam/approve",
                headers={"Authorization": f"Bearer {make_admin_token(teacher_id='teacher-1', email='prof@test.com')}"},
            )
        assert resp.status_code == 404, resp.text

    def test_status_returns_default_disabled(self, client):
        with patch("app.auth.admin_auth._get_teacher_by_id", return_value=self.TEACHER), \
             patch("app.routers.admin_liveview.assert_session_accessible", AsyncMock(return_value={})):
            resp = client.get(
                "/api/v1/admin/sessions/ALICE001_abc/room-cam/status",
                headers={"Authorization": f"Bearer {make_admin_token(teacher_id='teacher-1', email='prof@test.com')}"},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "disabled"


# ═══════════════════════════════════════════════════════════════════
#  3. Approval status endpoint
# ═══════════════════════════════════════════════════════════════════

class TestApprovalStatusEndpoint:
    def test_no_auth_401(self, client):
        resp = client.get("/api/v1/room-cam-approval-status?session_id=ALICE001_abc")
        assert resp.status_code == 401

    def test_valid_request_returns_status(self, client):
        token = _jwt.encode({
            "roll": "ALICE001", "tid": "teacher-1",
            "exp": datetime.now(timezone.utc) + timedelta(hours=2),
        }, EXAM_TOKEN_SIGNING_KEY, algorithm="HS256")
        chain = MagicMock()
        chain._data = [{"room_cam_status": "pending", "room_cam_approved_at": None}]
        for attr in ("select", "eq", "limit"):
            getattr(chain, attr).return_value = chain
        async def _execute():
            r = MagicMock()
            r.data = chain._data
            return r
        chain.execute = _execute
        with patch("app.routers.exam._atable", side_effect=lambda t: chain):
            resp = client.get(
                "/api/v1/room-cam-approval-status?session_id=ALICE001_abc",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"


# ═══════════════════════════════════════════════════════════════════
#  /api/v1/proctor/live-frame — must authenticate (frame-injection guard)
# ═══════════════════════════════════════════════════════════════════

class TestLiveFrameAuth:
    """The legacy HTTP live-frame endpoint must verify the proctor's exam
    bearer token (the client already sends it). Closes an unauthenticated
    camera-frame-injection / forgery hole on any live session."""

    def test_no_token_401(self, client):
        resp = client.post("/api/v1/proctor/live-frame",
                           json={"session_id": "ALICE001_abc", "jpeg_b64": "Zm9v"})
        assert resp.status_code == 401

    def test_invalid_token_401(self, client):
        resp = client.post("/api/v1/proctor/live-frame",
                           json={"session_id": "ALICE001_abc", "jpeg_b64": "Zm9v"},
                           headers={"Authorization": "Bearer not-a-real-token"})
        assert resp.status_code == 401

    def test_token_for_other_session_denied(self, client):
        # Valid exam token, but its roll doesn't match the target session_id.
        token = _jwt.encode({
            "roll": "ALICE001", "tid": "teacher-1",
            "exp": datetime.now(timezone.utc) + timedelta(hours=2),
        }, EXAM_TOKEN_SIGNING_KEY, algorithm="HS256")
        resp = client.post("/api/v1/proctor/live-frame",
                           json={"session_id": "BOB002_xyz", "jpeg_b64": "Zm9v"},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code in (401, 403)
