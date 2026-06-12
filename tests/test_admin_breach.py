"""Tests for superadmin breach-incident lifecycle management.

Covers:
  - CRUD: create, list, get, update (status transition + metadata)
  - Authorization: non-superadmin gets 403 even with org_role='superadmin'
  - Notify controller: resolves org -> billing_email / admin teacher fallback
  - Notify subjects: dispatches bulk data-subject notification jobs
  - Validation: invalid risk_level, role, status transitions rejected
"""

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, raise_server_exceptions=False)

SUPER = "owner@procta.net"

# Shared fake breach row returned by DB mocks.
_FAKE_BREACH = {
    "id": "00000000-0000-0000-0000-000000000001",
    "discovered_at": "2026-06-12T10:00:00+00:00",
    "description": "Unauthorized access to exam session recordings",
    "data_categories": "exam recordings, facial images, keystroke patterns",
    "affected_scope": '{"org_ids": ["org-1"], "exam_ids": ["exam-99"]}',
    "risk_level": "high",
    "role": "processor",
    "status": "open",
    "authority_notified_at": None,
    "controllers_notified_at": None,
    "subjects_notified_at": None,
    "created_by": "t-super",
    "created_at": "2026-06-12T10:00:00+00:00",
}


# -- helpers ------------------------------------------------------------------


def _fake_atable(data=None):
    """Return a mocked _atable that yields the given data on .execute()."""
    m = MagicMock()
    m.select.return_value = m
    m.eq.return_value = m
    m.neq.return_value = m
    m.is_.return_value = m
    m.in_.return_value = m
    m.gte.return_value = m
    m.lte.return_value = m
    m.order.return_value = m
    m.limit.return_value = m
    m.range.return_value = m
    m.insert.return_value = m
    m.update.return_value = m
    m.delete.return_value = m

    async def _exec():
        r = MagicMock()
        r.data = data if data is not None else []
        r.count = len(data) if data else 0
        return r
    m.execute = _exec
    return m


async def _superadmin_user(request=None):
    """Return a superadmin user dict (compatible with require_admin signature)."""
    return {"id": "t-super", "email": SUPER}


async def _regular_admin_user(request=None):
    """Return a non-superadmin user (compatible with require_admin signature)."""
    return {"id": "t-org", "email": "orgadmin@other.com", "org_role": "superadmin"}


# -- authorization ------------------------------------------------------------


def test_create_rejects_non_superadmin():
    """A teacher with org_role='superadmin' but non-matching email is rejected."""
    patches = [
        patch("app.routers.admin_breach.require_admin", side_effect=_regular_admin_user),
        patch("app.routers.admin_breach.SUPER_ADMIN_EMAIL", SUPER),
    ]
    for p in patches:
        p.start()
    try:
        resp = client.post("/api/v1/admin/breach", json={
            "description": "Unauthorized access to exam recordings",
            "risk_level": "high",
        })
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 403


# -- create -------------------------------------------------------------------


def test_create_breach():
    """Happy path: create a breach incident."""
    fake_atable = _fake_atable([_FAKE_BREACH])
    patches = [
        patch("app.routers.admin_breach.require_admin", side_effect=_superadmin_user),
        patch("app.routers.admin_breach._atable", return_value=fake_atable),
        patch("app.routers.admin_breach.SUPER_ADMIN_EMAIL", SUPER),
        patch("app.routers.admin_breach.enqueue_job"),
    ]
    for p in patches:
        p.start()
    try:
        resp = client.post("/api/v1/admin/breach", json={
            "description": "Unauthorized access to exam recordings",
            "data_categories": "exam recordings, facial images",
            "risk_level": "high",
            "role": "processor",
        })
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["risk_level"] == "high"
    assert data["status"] == "open"


def test_create_breach_invalid_risk():
    with patch("app.routers.admin_breach.require_admin", side_effect=_superadmin_user), \
         patch("app.routers.admin_breach.SUPER_ADMIN_EMAIL", SUPER):
        resp = client.post("/api/v1/admin/breach", json={
            "description": "Test breach with invalid risk level here",
            "risk_level": "critical",
        })
    assert resp.status_code == 422


def test_create_breach_invalid_role():
    with patch("app.routers.admin_breach.require_admin", side_effect=_superadmin_user), \
         patch("app.routers.admin_breach.SUPER_ADMIN_EMAIL", SUPER):
        resp = client.post("/api/v1/admin/breach", json={
            "description": "Test breach with invalid role param here",
            "risk_level": "medium",
            "role": "subprocessor",
        })
    assert resp.status_code == 422


# -- list ---------------------------------------------------------------------


def test_list_breaches():
    fake_atable = _fake_atable([_FAKE_BREACH])
    with patch("app.routers.admin_breach.require_admin", side_effect=_superadmin_user), \
         patch("app.routers.admin_breach._atable", return_value=fake_atable), \
         patch("app.routers.admin_breach.SUPER_ADMIN_EMAIL", SUPER):
        resp = client.get("/api/v1/admin/breach")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 1


# -- get ----------------------------------------------------------------------


def test_get_breach():
    fake_atable = _fake_atable([_FAKE_BREACH])
    with patch("app.routers.admin_breach.require_admin", side_effect=_superadmin_user), \
         patch("app.routers.admin_breach._atable", return_value=fake_atable), \
         patch("app.routers.admin_breach.SUPER_ADMIN_EMAIL", SUPER):
        resp = client.get(f"/api/v1/admin/breach/{_FAKE_BREACH['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == _FAKE_BREACH["id"]


def test_get_breach_not_found():
    fake_atable = _fake_atable([])
    with patch("app.routers.admin_breach.require_admin", side_effect=_superadmin_user), \
         patch("app.routers.admin_breach._atable", return_value=fake_atable), \
         patch("app.routers.admin_breach.SUPER_ADMIN_EMAIL", SUPER):
        resp = client.get("/api/v1/admin/breach/00000000-0000-0000-0000-000000009999")
    assert resp.status_code == 404


# -- update / status transitions ----------------------------------------------


def test_update_breach_valid_transition():
    open_row = dict(_FAKE_BREACH)
    contained_row = dict(_FAKE_BREACH, status="contained")

    call_count = 0

    async def fake_execute():
        nonlocal call_count
        call_count += 1
        r = MagicMock()
        # 1 = fetch current, 2 = update, 3 = re-fetch for the response.
        # (Audit logging uses the canonical best-effort helper, which does
        # not touch this patched handle.)
        if call_count == 1:
            r.data = [open_row]
        elif call_count == 3:
            r.data = [contained_row]
        else:
            r.data = []
        r.count = 1
        return r

    class _Chain:
        def __getattr__(self, name):
            if name == 'execute':
                return fake_execute
            return self
        def __call__(self, *a, **kw):
            return self

    chain = _Chain()

    with patch("app.routers.admin_breach.require_admin", side_effect=_superadmin_user), \
         patch("app.routers.admin_breach._atable", return_value=chain), \
         patch("app.routers.admin_breach.SUPER_ADMIN_EMAIL", SUPER), \
         patch("app.routers.admin_breach.enqueue_job"):
        resp = client.patch(f"/api/v1/admin/breach/{_FAKE_BREACH['id']}", json={
            "status": "contained",
        })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "contained"


def test_update_breach_invalid_transition():
    row = dict(_FAKE_BREACH, status="open")
    fake_atable = _fake_atable([row])
    with patch("app.routers.admin_breach.require_admin", side_effect=_superadmin_user), \
         patch("app.routers.admin_breach._atable", return_value=fake_atable), \
         patch("app.routers.admin_breach.SUPER_ADMIN_EMAIL", SUPER), \
         patch("app.routers.admin_breach.enqueue_job"):
        resp = client.patch(f"/api/v1/admin/breach/{_FAKE_BREACH['id']}", json={
            "status": "closed",
        })
    assert resp.status_code == 422


# -- notify controller --------------------------------------------------------


def test_notify_controller_billing_email():
    """Uses organizations.billing_email when available."""
    breach_row = dict(_FAKE_BREACH)
    org_row = {"id": "org-1", "billing_email": "billing@org.com"}

    class _FakeChain:
        def __init__(self):
            self._call_count = 0
        def select(self, *a, **kw): return self
        def eq(self, *a, **kw): return self
        def in_(self, *a, **kw): return self
        def limit(self, *a, **kw): return self
        def range(self, *a, **kw): return self
        def order(self, *a, **kw): return self
        def update(self, *a, **kw): return self
        def insert(self, *a, **kw): return self
        async def execute(self):
            r = MagicMock()
            self._call_count += 1
            if self._call_count == 1:
                r.data = [breach_row]
            elif self._call_count == 2:
                r.data = [org_row]
            else:
                r.data = []
            r.count = 1
            return r

    fake_atable = _FakeChain()
    enqueued = []

    def fake_enqueue(job_name, **kwargs):
        enqueued.append((job_name, kwargs))

    with patch("app.routers.admin_breach.require_admin", side_effect=_superadmin_user), \
         patch("app.routers.admin_breach._atable", return_value=fake_atable), \
         patch("app.routers.admin_breach.SUPER_ADMIN_EMAIL", SUPER), \
         patch("app.routers.admin_breach.enqueue_job", side_effect=fake_enqueue):
        resp = client.post(f"/api/v1/admin/breach/{_FAKE_BREACH['id']}/notify-controller", json={
            "org_name": "Test Org",
        })
    assert resp.status_code == 200, resp.text
    assert resp.json()["to"] == "billing@org.com"
    assert len(enqueued) == 1
    assert enqueued[0][0] == "send_controller_breach_notification_job"
    assert enqueued[0][1]["to_email"] == "billing@org.com"


def test_notify_controller_fallback_to_admin():
    """Falls back to first admin teacher when billing_email is null."""
    breach_row = dict(_FAKE_BREACH)
    org_row = {"id": "org-1", "billing_email": None}
    admin_row = {"email": "admin@org.com", "full_name": "Org Admin"}

    class _FakeChain:
        def __init__(self):
            self._call_count = 0
        def select(self, *a, **kw): return self
        def eq(self, *a, **kw): return self
        def in_(self, *a, **kw): return self
        def limit(self, *a, **kw): return self
        def range(self, *a, **kw): return self
        def order(self, *a, **kw): return self
        def update(self, *a, **kw): return self
        def insert(self, *a, **kw): return self
        async def execute(self):
            r = MagicMock()
            self._call_count += 1
            if self._call_count == 1:
                r.data = [breach_row]
            elif self._call_count == 2:
                r.data = [org_row]
            elif self._call_count == 3:
                r.data = [admin_row]
            else:
                r.data = []
            r.count = 1
            return r

    fake_atable = _FakeChain()
    enqueued = []

    def fake_enqueue(job_name, **kwargs):
        enqueued.append((job_name, kwargs))

    with patch("app.routers.admin_breach.require_admin", side_effect=_superadmin_user), \
         patch("app.routers.admin_breach._atable", return_value=fake_atable), \
         patch("app.routers.admin_breach.SUPER_ADMIN_EMAIL", SUPER), \
         patch("app.routers.admin_breach.enqueue_job", side_effect=fake_enqueue):
        resp = client.post(f"/api/v1/admin/breach/{_FAKE_BREACH['id']}/notify-controller", json={
            "org_name": "Test Org",
        })
    assert resp.status_code == 200, resp.text
    assert resp.json()["to"] == "admin@org.com"
    assert enqueued[0][1]["to_email"] == "admin@org.com"
    assert enqueued[0][1]["to_name"] == "Org Admin"


def test_notify_controller_org_not_found():
    breach_row = dict(_FAKE_BREACH)

    class _FakeChain:
        def __init__(self):
            self._call_count = 0
        def select(self, *a, **kw): return self
        def eq(self, *a, **kw): return self
        def in_(self, *a, **kw): return self
        def limit(self, *a, **kw): return self
        def range(self, *a, **kw): return self
        def order(self, *a, **kw): return self
        def update(self, *a, **kw): return self
        def insert(self, *a, **kw): return self
        async def execute(self):
            r = MagicMock()
            self._call_count += 1
            if self._call_count == 1:
                r.data = [breach_row]
            else:
                r.data = []
            r.count = 0
            return r

    fake_atable = _FakeChain()

    with patch("app.routers.admin_breach.require_admin", side_effect=_superadmin_user), \
         patch("app.routers.admin_breach._atable", return_value=fake_atable), \
         patch("app.routers.admin_breach.SUPER_ADMIN_EMAIL", SUPER):
        resp = client.post(f"/api/v1/admin/breach/{_FAKE_BREACH['id']}/notify-controller", json={
            "org_name": "Nonexistent Org",
        })
    assert resp.status_code == 404


def test_notify_controller_no_billing_no_admin():
    """404 when org has no billing_email and no admin teachers."""
    breach_row = dict(_FAKE_BREACH)
    org_row = {"id": "org-1", "billing_email": None}

    class _FakeChain:
        def __init__(self):
            self._call_count = 0
        def select(self, *a, **kw): return self
        def eq(self, *a, **kw): return self
        def in_(self, *a, **kw): return self
        def limit(self, *a, **kw): return self
        def range(self, *a, **kw): return self
        def order(self, *a, **kw): return self
        def update(self, *a, **kw): return self
        def insert(self, *a, **kw): return self
        async def execute(self):
            r = MagicMock()
            self._call_count += 1
            if self._call_count == 1:
                r.data = [breach_row]
            elif self._call_count == 2:
                r.data = [org_row]
            elif self._call_count == 3:
                r.data = []
            else:
                r.data = []
            r.count = 0
            return r

    fake_atable = _FakeChain()

    with patch("app.routers.admin_breach.require_admin", side_effect=_superadmin_user), \
         patch("app.routers.admin_breach._atable", return_value=fake_atable), \
         patch("app.routers.admin_breach.SUPER_ADMIN_EMAIL", SUPER):
        resp = client.post(f"/api/v1/admin/breach/{_FAKE_BREACH['id']}/notify-controller", json={
            "org_name": "Test Org",
        })
    assert resp.status_code == 404


# -- notify subjects ----------------------------------------------------------


def test_notify_subjects():
    breach_row = dict(_FAKE_BREACH)
    fake_atable = _fake_atable([breach_row])

    enqueued = []

    def fake_enqueue(job_name, **kwargs):
        enqueued.append((job_name, kwargs))

    with patch("app.routers.admin_breach.require_admin", side_effect=_superadmin_user), \
         patch("app.routers.admin_breach._atable", return_value=fake_atable), \
         patch("app.routers.admin_breach.SUPER_ADMIN_EMAIL", SUPER), \
         patch("app.routers.admin_breach.enqueue_job", side_effect=fake_enqueue):
        resp = client.post(f"/api/v1/admin/breach/{_FAKE_BREACH['id']}/notify-subjects", json={
            "to_emails": ["alice@example.com", "bob@example.com"],
        })
    assert resp.status_code == 200, resp.text
    assert resp.json()["sent_to_count"] == 2
    assert len(enqueued) == 2
    job_names = {e[0] for e in enqueued}
    assert job_names == {"send_data_subject_breach_notification_job"}
