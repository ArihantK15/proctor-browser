"""Tests for the SAR + privacy-export hardening pass.

Covers:
  H1 — credential columns stripped from exported profile
  H2 — SAR gate is satisfied only by the env-pinned SUPER_ADMIN_EMAIL,
       not by a (potentially DB-stored) org_role string
  M1 — SAR student export includes answers + violations
  M2 — post-erasure auth_events stores a masked email, never the raw PII
"""
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.routers.admin_sar import _mask_email
from app.routers.privacy import _redact_profile

client = TestClient(app, raise_server_exceptions=False)

SUPER = "owner@procta.net"


# ── H1 / pure helpers ────────────────────────────────────────────────

def test_redact_profile_strips_credentials_keeps_pii_fields():
    row = {
        "id": "t-1",
        "email": "prof@test.com",
        "full_name": "Prof",
        "password_hash": "argon2$secret",
        "totp_secret": "BASE32SEED",
        "password_reset_token": "abc",
        "email_verify_token": "def",
        "some_api_secret": "shh",
        "created_at": "2026-01-01",
    }
    out = _redact_profile(row)
    # Credential / secret columns gone.
    for k in ("password_hash", "totp_secret", "password_reset_token",
              "email_verify_token", "some_api_secret"):
        assert k not in out, f"{k} should have been redacted"
    # Identity fields the subject is entitled to remain.
    assert out["email"] == "prof@test.com"
    assert out["full_name"] == "Prof"
    assert out["created_at"] == "2026-01-01"
    # Input not mutated in place.
    assert "password_hash" in row


def test_redact_profile_passthrough_non_dict():
    assert _redact_profile(None) is None


def test_mask_email():
    assert _mask_email("student@example.com") == "s***@example.com"
    assert _mask_email("") is None
    assert _mask_email(None) is None
    assert _mask_email("garbage") is None


# ── H2 — gate ────────────────────────────────────────────────────────

def test_sar_export_rejects_non_superadmin_even_with_superadmin_role():
    """A teacher carrying org_role='superadmin' but whose email is NOT
    the env-pinned owner must be rejected — the gate trusts the env
    pin, not the role string."""
    async def fake_require_admin(_request):
        return {"id": "t-9", "email": "orgadmin@other.com", "org_role": "superadmin"}

    with patch("app.routers.admin_sar.require_admin", side_effect=fake_require_admin), \
         patch("app.routers.admin_sar.SUPER_ADMIN_EMAIL", SUPER):
        resp = client.post("/api/v1/admin/sar/export", json={
            "target_user_type": "student",
            "target_email": "victim@x.com",
        }, headers={"Authorization": "Bearer x"})
    assert resp.status_code == 403


# ── H1 + M1 — export integration ─────────────────────────────────────

def _select_chain(data):
    class _C:
        def select(self, *a, **k): return self
        def eq(self, *a, **k): return self
        def limit(self, *a, **k): return self
        async def execute(self): return type("R", (), {"data": data})()
    return _C()


def test_sar_export_student_strips_hash_and_includes_answers_violations():
    async def fake_require_admin(_request):
        return {"id": "op-1", "email": SUPER, "org_role": "teacher"}

    target = {
        "id": "stu-1", "email": "victim@x.com", "full_name": "Victim",
        "password_hash": "argon2$leak",
    }

    # admin_sar._atable only used by _resolve_target here.
    def fake_admin_atable(name):
        return _select_chain([target])

    # privacy._atable backs _safe_fetch — return one session so the
    # session_key walk runs, and answers/violations for that key.
    def fake_privacy_atable(name):
        if name == "exam_sessions":
            return _select_chain([{"session_key": "sk-1", "student_id": "stu-1"}])
        if name == "answers":
            return _select_chain([{"q": 1}])
        if name == "violations":
            return _select_chain([{"v": 1}])
        return _select_chain([])

    with patch("app.routers.admin_sar.require_admin", side_effect=fake_require_admin), \
         patch("app.routers.admin_sar.SUPER_ADMIN_EMAIL", SUPER), \
         patch("app.routers.admin_sar._atable", fake_admin_atable), \
         patch("app.routers.privacy._atable", fake_privacy_atable), \
         patch("app.services.admin_audit.log_admin_action", new=AsyncMock()):
        resp = client.post("/api/v1/admin/sar/export", json={
            "target_user_type": "student",
            "target_user_id": "stu-1",
            "ticket_id": "T-1",
        }, headers={"Authorization": "Bearer x"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "password_hash" not in body["profile"]
    assert body["profile"]["email"] == "victim@x.com"
    # M1 — answers + violations present.
    assert body["answers"] == [{"q": 1}]
    assert body["violations"] == [{"v": 1}]


# ── M2 — delete masks email in retained auth_events ──────────────────

class _RecTable:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self._payload = None
    def select(self, *a, **k): return self
    def update(self, *a, **k): return self
    def delete(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def is_(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def insert(self, payload):
        self._payload = payload
        if self.name == "auth_events":
            self.db.auth_events.append(payload)
        return self
    async def execute(self):
        if self.name in ("teachers", "student_accounts") and self._payload is None:
            return type("R", (), {"data": [self.db.target]})()
        return type("R", (), {"data": []})()


class _RecDb:
    def __init__(self, target):
        self.target = target
        self.auth_events = []
    def __call__(self, name):
        return _RecTable(self, name)


def test_sar_delete_masks_email_in_auth_events():
    async def fake_require_admin(_request):
        return {"id": "op-1", "email": SUPER}

    target = {"id": "tch-1", "email": "real.person@school.edu", "org_id": None}
    db = _RecDb(target)

    with patch("app.routers.admin_sar.require_admin", side_effect=fake_require_admin), \
         patch("app.routers.admin_sar.SUPER_ADMIN_EMAIL", SUPER), \
         patch("app.routers.admin_sar._atable", db), \
         patch("app.services.admin_audit.log_admin_action", new=AsyncMock()):
        resp = client.post("/api/v1/admin/sar/delete", json={
            "target_user_type": "teacher",
            "target_user_id": "tch-1",
            "reason": "Court order ref 12345 — erase subject",
        }, headers={"Authorization": "Bearer x"})

    assert resp.status_code == 200, resp.text
    assert db.auth_events, "an auth_events row should have been written"
    logged_email = db.auth_events[0]["email"]
    assert logged_email == "r***@school.edu"
    assert "real.person" not in (logged_email or "")
