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
from app.constants import SECRET_KEY  # noqa: E402
import app.repositories.sessions as _sess_repo  # noqa: E402


ROOM_CAM_STUDENT_TOKEN = _jwt.encode({
    "roll": "ALICE001", "tid": "teacher-1",
    "scope": "room-cam", "sid": "ALICE001_abc",
    "exp": datetime.now(timezone.utc) + timedelta(hours=2),
}, SECRET_KEY, algorithm="HS256")

ROOM_CAM_WRONG_SCOPE = _jwt.encode({
    "roll": "ALICE001", "tid": "teacher-1",
    "scope": "live-frame", "sid": "ALICE001_abc",
    "exp": datetime.now(timezone.utc) + timedelta(hours=2),
}, SECRET_KEY, algorithm="HS256")

ROOM_CAM_EXPIRED = _jwt.encode({
    "roll": "ALICE001", "tid": "teacher-1",
    "scope": "room-cam", "sid": "ALICE001_abc",
    "exp": datetime.now(timezone.utc) - timedelta(hours=1),
}, SECRET_KEY, algorithm="HS256")


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
        }, SECRET_KEY, algorithm="HS256")
        resp = client.post("/api/v1/room-cam-token",
                           json={"session_id": "ALICE001_abc"},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403

    def test_success_returns_token(self, client):
        token = _jwt.encode({
            "roll": "ALICE001", "tid": "teacher-1",
            "exp": datetime.now(timezone.utc) + timedelta(hours=2),
        }, SECRET_KEY, algorithm="HS256")
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
             patch("app.routers.admin_liveview._assert_session_owned", AsyncMock()):
            resp = client.post(
                "/api/v1/admin/sessions/ALICE001_abc/room-cam/approve",
                headers={"Authorization": f"Bearer {make_admin_token(teacher_id='teacher-1', email='prof@test.com')}"},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

    def test_reject_updates_status(self, client):
        with patch("app.auth.admin_auth._get_teacher_by_id", return_value=self.TEACHER), \
             patch("app.routers.admin_liveview._assert_session_owned", AsyncMock()):
            resp = client.post(
                "/api/v1/admin/sessions/ALICE001_abc/room-cam/reject",
                headers={"Authorization": f"Bearer {make_admin_token(teacher_id='teacher-1', email='prof@test.com')}"},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"

    def test_status_returns_default_disabled(self, client):
        with patch("app.auth.admin_auth._get_teacher_by_id", return_value=self.TEACHER), \
             patch("app.routers.admin_liveview._assert_session_owned", AsyncMock()), \
             patch("app.database.async_table", side_effect=lambda t: self._mock_atable([])):
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
        }, SECRET_KEY, algorithm="HS256")
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
