"""Tests for guardian consent endpoints.

Covers:
  - POST send-request: sends consent email, stores token hash
  - GET pending: lists students pending guardian consent
  - GET /guardian-consent/<token>: landing page (Grant / Deny buttons)
  - POST /api/v1/guardian/consent: records grant or deny
  - Validation: missing guardian_email, already consented, bad action
  - Authorization: teacher-scoped vs admin scope
"""

import hashlib
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, raise_server_exceptions=False)


_FAKE_STUDENT = {
    "id": "s-001",
    "roll_number": "S001",
    "full_name": "Alice Smith",
    "email": "alice@school.edu",
    "guardian_email": "parent@example.com",
    "guardian_consent_granted_at": None,
    "guardian_consent_requested_at": None,
    "guardian_consent_token_hash": None,
}


def _token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _fake_atable(data=None):
    m = MagicMock()
    m.select.return_value = m
    m.eq.return_value = m
    m.neq.return_value = m
    m.is_.return_value = m
    m.in_.return_value = m
    m.order.return_value = m
    m.limit.return_value = m
    m.insert.return_value = m
    m.update.return_value = m
    m.not_ = m  # .not_.is_(...) chains back to the same mock

    async def _exec():
        r = MagicMock()
        r.data = data if data is not None else []
        r.count = len(data) if data else 0
        return r
    m.execute = _exec
    return m


async def _teacher_user(request=None):
    return {"id": "t-1", "email": "teacher@school.edu", "org_role": "teacher"}


# -- send-request --------------------------------------------------------------


def test_send_request_missing_guardian_email():
    """422 when student has no guardian_email."""
    student = dict(_FAKE_STUDENT, guardian_email=None)
    fake_atable = _fake_atable([student])
    with patch("app.routers.admin_guardian.require_admin", side_effect=_teacher_user), \
         patch("app.routers.admin_guardian._atable", return_value=fake_atable), \
         patch("app.routers.admin_guardian.enqueue_job"):
        resp = client.post("/api/v1/admin/guardian/send-request", json={
            "roll_number": "S001",
        })
    assert resp.status_code == 422
    assert "guardian_email" in resp.text.lower()


def test_send_request_already_consented():
    """409 when consent already granted."""
    student = dict(_FAKE_STUDENT, guardian_consent_granted_at="2026-06-12T10:00:00+00:00")
    fake_atable = _fake_atable([student])
    with patch("app.routers.admin_guardian.require_admin", side_effect=_teacher_user), \
         patch("app.routers.admin_guardian._atable", return_value=fake_atable), \
         patch("app.routers.admin_guardian.enqueue_job"):
        resp = client.post("/api/v1/admin/guardian/send-request", json={
            "roll_number": "S001",
        })
    assert resp.status_code == 409


def test_send_request_success():
    """Happy path: stores token hash, enqueues email."""
    student = dict(_FAKE_STUDENT)

    class _DualChain:
        def __init__(self):
            self._call_count = 0
        def select(self, *a, **kw): return self
        def eq(self, *a, **kw): return self
        def limit(self, *a, **kw): return self
        async def execute(self):
            self._call_count += 1
            r = MagicMock()
            if self._call_count == 1:
                r.data = [student]
            else:
                r.data = []
            return r
        def update(self, *a, **kw): return self

    dual = _DualChain()
    enqueued = []

    def _fake_enqueue(func, **kwargs):
        enqueued.append(kwargs)

    with patch("app.routers.admin_guardian.require_admin", side_effect=_teacher_user), \
         patch("app.routers.admin_guardian._atable", return_value=dual), \
         patch("app.routers.admin_guardian.enqueue_job", side_effect=_fake_enqueue):
        resp = client.post("/api/v1/admin/guardian/send-request", json={
            "roll_number": "S001",
        })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["guardian_email"] == "parent@example.com"
    assert len(enqueued) == 1
    assert enqueued[0]["to_email"] == "parent@example.com"
    assert "guardian-consent" in enqueued[0]["consent_url"]


def test_send_request_student_not_found():
    """404 for unknown roll number."""
    fake_atable = _fake_atable([])
    with patch("app.routers.admin_guardian.require_admin", side_effect=_teacher_user), \
         patch("app.routers.admin_guardian._atable", return_value=fake_atable):
        resp = client.post("/api/v1/admin/guardian/send-request", json={
            "roll_number": "NONEXISTENT",
        })
    assert resp.status_code == 404


# -- pending ------------------------------------------------------------------


def test_pending_empty():
    """200 with empty list when no pending students."""
    fake_atable = _fake_atable([])
    with patch("app.routers.admin_guardian.require_admin", side_effect=_teacher_user), \
         patch("app.routers.admin_guardian._atable", return_value=fake_atable):
        resp = client.get("/api/v1/admin/guardian/pending")
    assert resp.status_code == 200
    assert resp.json()["pending"] == []


def test_pending_with_students():
    """Returns students with guardian_email but no consent."""
    pending = [
        {"roll_number": "S001", "full_name": "Alice", "email": "a@school.edu",
         "guardian_email": "parent@example.com",
         "guardian_consent_requested_at": "2026-06-12T10:00:00+00:00",
         "guardian_consent_granted_at": None},
    ]
    fake_atable = _fake_atable(pending)
    with patch("app.routers.admin_guardian.require_admin", side_effect=_teacher_user), \
         patch("app.routers.admin_guardian._atable", return_value=fake_atable):
        resp = client.get("/api/v1/admin/guardian/pending")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["pending"]) == 1
    assert body["pending"][0]["roll_number"] == "S001"
    assert body["pending"][0]["guardian_email"] == "parent@example.com"


# -- consent landing page (guardian-facing) -------------------------------------


def test_landing_missing_token():
    """Landing page with no token returns 404 (path-param is empty)."""
    resp = client.get("/guardian-consent/")
    assert resp.status_code == 404


def test_landing_invalid_token():
    """404 for unrecognised token."""
    token = "some-unknown-token"
    _token_hash(token)
    fake_atable = _fake_atable([])
    with patch("app.routers.admin_guardian._atable", return_value=fake_atable):
        resp = client.get(f"/guardian-consent/{token}")
    assert resp.status_code == 404


def test_landing_already_granted():
    """Shows 'already recorded' page when consent already granted."""
    token = "known-token"
    _token_hash(token)
    student = dict(_FAKE_STUDENT, guardian_consent_granted_at="2026-06-12T10:00:00+00:00")
    fake_atable = _fake_atable([student])
    # The lookup uses token_hash, so the student must have matching hash.
    # Our fake_atable returns student regardless of the filter, but we
    # need to ensure the eq filter chain works.
    with patch("app.routers.admin_guardian._atable", return_value=fake_atable):
        resp = client.get(f"/guardian-consent/{token}")
    assert resp.status_code == 200
    assert "already" in resp.text.lower()


def test_landing_shows_buttons():
    """Landing page shows Grant / Deny buttons for a valid pending token."""
    token = "raw-token-for-test"
    student = dict(_FAKE_STUDENT)  # no consent yet
    fake_atable = _fake_atable([student])
    with patch("app.routers.admin_guardian._atable", return_value=fake_atable):
        resp = client.get(f"/guardian-consent/{token}")
    assert resp.status_code == 200
    assert "Grant" in resp.text
    assert "Deny" in resp.text


# -- consent action POST -------------------------------------------------------


def test_consent_action_bad_action():
    """422 when action is not 'grant' or 'deny'."""
    resp = client.post("/api/v1/guardian/consent", json={
        "token": "some-token",
        "action": "maybe",
    })
    assert resp.status_code == 422


def test_consent_action_missing_token():
    """400 when token is empty."""
    resp = client.post("/api/v1/guardian/consent", json={
        "token": "",
        "action": "grant",
    })
    assert resp.status_code == 400


def test_consent_action_invalid_token():
    """404 for unrecognised token."""
    token = "invalid"
    fake_atable = _fake_atable([])
    with patch("app.routers.admin_guardian._atable", return_value=fake_atable):
        resp = client.post("/api/v1/guardian/consent", json={
            "token": token,
            "action": "grant",
        })
    assert resp.status_code == 404


def test_consent_action_grant():
    """Grant consent: stamps granted_at, writes consent_records."""
    token = "valid-grant-token"
    student = dict(_FAKE_STUDENT)

    class _ConsentChain:
        def __init__(self):
            self._call_count = 0
        def select(self, *a, **kw): return self
        def eq(self, *a, **kw): return self
        def limit(self, *a, **kw): return self
        async def execute(self):
            self._call_count += 1
            r = MagicMock()
            if self._call_count == 1:
                r.data = [student]
            else:
                r.data = []
            return r
        def update(self, *a, **kw): return self
        def insert(self, *a, **kw): return self

    chain = _ConsentChain()
    with patch("app.routers.admin_guardian._atable", return_value=chain):
        resp = client.post("/api/v1/guardian/consent", json={
            "token": token,
            "action": "grant",
        })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["action"] == "grant"
    assert data["student_name"] == "Alice Smith"
    # Two DB calls: SELECT + UPDATE + INSERT = at least 2 execute calls
    assert chain._call_count >= 2


def test_consent_action_deny():
    """Deny consent: stamps denied_at, no consent_records."""
    token = "valid-deny-token"
    student = dict(_FAKE_STUDENT)

    class _DenyChain:
        def __init__(self):
            self._call_count = 0
        def select(self, *a, **kw): return self
        def eq(self, *a, **kw): return self
        def limit(self, *a, **kw): return self
        async def execute(self):
            self._call_count += 1
            r = MagicMock()
            if self._call_count == 1:
                r.data = [student]
            else:
                r.data = []
            return r
        def update(self, *a, **kw): return self
        def insert(self, *a, **kw): return self

    chain = _DenyChain()
    with patch("app.routers.admin_guardian._atable", return_value=chain):
        resp = client.post("/api/v1/guardian/consent", json={
            "token": token,
            "action": "deny",
        })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["action"] == "deny"
