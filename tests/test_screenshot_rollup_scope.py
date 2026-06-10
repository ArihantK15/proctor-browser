"""Tests for the admin screenshot roll-up scoping (admin_media.get_screenshot).

Screenshots are stored under SCREENSHOTS_DIR/{owning_teacher_id}/{roll}/{file},
where the owner is the teacher who set the exam — not necessarily the caller.
An org admin doing a per-teacher roll-up of an org-member's data must be able to
retrieve that member's screenshots; a plain teacher must stay locked to their
own; cross-tenant access must 404.

The endpoint derives the owner tid from the supplied ?session_id via the scope
spine (assert_session_accessible 404s anything out of tenant). Absent a
session_id it falls back to the caller's own tid (legacy / own-scoped links).
"""
import os
import sys
from unittest.mock import patch, AsyncMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")

from tests.conftest import make_admin_token  # noqa: E402

CALLER = {"id": "teacher-1", "email": "admin@test.com", "org_id": "org-1",
          "org_role": "admin", "full_name": "Admin", "status": "active"}


def _headers():
    return {"Authorization": f"Bearer {make_admin_token(teacher_id='teacher-1')}"}


def _make_file(root, tid, roll, fname):
    d = root / tid / roll
    d.mkdir(parents=True, exist_ok=True)
    p = d / fname
    p.write_bytes(b"\xff\xd8\xff\x00fakejpeg")
    return p


def test_session_id_routes_to_owner_tid(client, tmp_path):
    """With a session_id the path is keyed to the SESSION's owner tid
    (teacher-2), not the caller's (teacher-1) — that's the roll-up fix."""
    _make_file(tmp_path, "teacher-2", "alice", "face_missing_1.jpg")
    with patch("app.auth.admin_auth._get_teacher_by_id", return_value=CALLER), \
         patch("app.routers.admin_media.SCREENSHOTS_DIR", str(tmp_path)), \
         patch("app.routers.admin_media.resolve_scope",
               AsyncMock(return_value={"role": "admin", "teacher_id": None,
                                       "org_id": "org-1"})), \
         patch("app.routers.admin_media.assert_session_accessible",
               AsyncMock(return_value={"teacher_id": "teacher-2"})):
        r = client.get(
            "/api/v1/admin/screenshot/alice/face_missing_1.jpg",
            params={"session_id": "alice_sess_xyz"},
            headers=_headers(),
        )
    assert r.status_code == 200, r.text
    assert r.content == b"\xff\xd8\xff\x00fakejpeg"


def test_no_session_id_falls_back_to_caller_tid(client, tmp_path):
    """Legacy / own-scoped links with no session_id resolve under the
    caller's own tid (backward compatibility)."""
    _make_file(tmp_path, "teacher-1", "alice", "id_card_1.jpg")
    with patch("app.auth.admin_auth._get_teacher_by_id", return_value=CALLER), \
         patch("app.routers.admin_media.SCREENSHOTS_DIR", str(tmp_path)):
        r = client.get(
            "/api/v1/admin/screenshot/alice/id_card_1.jpg",
            headers=_headers(),
        )
    assert r.status_code == 200, r.text


def test_cross_tenant_session_404s(client, tmp_path):
    """If the session is outside the caller's tenant, assert_session_accessible
    404s and no file is served — the owner tid is never reached."""
    _make_file(tmp_path, "teacher-OTHER", "mallory", "face_missing_1.jpg")
    from fastapi import HTTPException
    with patch("app.auth.admin_auth._get_teacher_by_id", return_value=CALLER), \
         patch("app.routers.admin_media.SCREENSHOTS_DIR", str(tmp_path)), \
         patch("app.routers.admin_media.resolve_scope",
               AsyncMock(return_value={"role": "admin", "teacher_id": None,
                                       "org_id": "org-1"})), \
         patch("app.routers.admin_media.assert_session_accessible",
               AsyncMock(side_effect=HTTPException(status_code=404,
                                                   detail="Session not found"))):
        r = client.get(
            "/api/v1/admin/screenshot/mallory/face_missing_1.jpg",
            params={"session_id": "mallory_sess_victim"},
            headers=_headers(),
        )
    assert r.status_code == 404, r.text


def test_session_with_no_owner_tid_404s(client, tmp_path):
    """When a session_id is supplied but the resolved session row carries no
    teacher_id (orphan), the endpoint must 404 rather than widen the search
    to the root SCREENSHOTS_DIR (which would span tenants)."""
    # A file directly under the root would be served if the fallback leaked.
    (tmp_path / "alice").mkdir(parents=True, exist_ok=True)
    (tmp_path / "alice" / "face_missing_1.jpg").write_bytes(b"\xff\xd8\xff\x00x")
    with patch("app.auth.admin_auth._get_teacher_by_id", return_value=CALLER), \
         patch("app.routers.admin_media.SCREENSHOTS_DIR", str(tmp_path)), \
         patch("app.routers.admin_media.resolve_scope",
               AsyncMock(return_value={"role": "superadmin", "teacher_id": None,
                                       "org_id": None})), \
         patch("app.routers.admin_media.assert_session_accessible",
               AsyncMock(return_value={"teacher_id": ""})):
        r = client.get(
            "/api/v1/admin/screenshot/alice/face_missing_1.jpg",
            params={"session_id": "alice_orphan"},
            headers=_headers(),
        )
    assert r.status_code == 404, r.text


def test_owner_tid_traversal_is_neutralised(client, tmp_path):
    """A malicious teacher_id from the session row cannot escape SCREENSHOTS_DIR
    — _safe_path_component strips path separators before it becomes a segment."""
    _make_file(tmp_path, "teacher-2", "alice", "face_missing_1.jpg")
    with patch("app.auth.admin_auth._get_teacher_by_id", return_value=CALLER), \
         patch("app.routers.admin_media.SCREENSHOTS_DIR", str(tmp_path)), \
         patch("app.routers.admin_media.resolve_scope",
               AsyncMock(return_value={"role": "superadmin", "teacher_id": None,
                                       "org_id": None})), \
         patch("app.routers.admin_media.assert_session_accessible",
               AsyncMock(return_value={"teacher_id": "../../etc"})):
        r = client.get(
            "/api/v1/admin/screenshot/alice/face_missing_1.jpg",
            params={"session_id": "alice_sess_xyz"},
            headers=_headers(),
        )
    # Sanitised owner tid won't match teacher-2's real dir → 404, never a leak.
    assert r.status_code == 404, r.text
