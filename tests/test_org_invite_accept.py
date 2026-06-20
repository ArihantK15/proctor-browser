"""Tests for the org-invite ACCEPT half of onboarding.

Invite *creation* (POST /api/v1/org/invite) is covered in test_org_billing.py.
This file covers the two-phase accept flow that actually joins a teacher to an
org:

  1. POST /api/v1/auth/accept-org-invite — creates/links the teacher with
     org_role=teacher and status=pending_verification, but DEFERS the org join
     (org_id stays null) until email ownership is proven. The invite flips to
     `pending_verification`, NOT `accepted`.
  2. GET /verify-email — on a verified teacher with a pending_verification
     invite, sets org_id + org_role + status=active and flips the invite to
     `accepted`. This is the step that confers org membership.

The defer-until-verified design is a security boundary: an admin must not be
able to attach an unproven email to their org, so these tests pin that the join
never happens at accept time and always happens at verify time.
"""
import contextlib
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _iso(dt: datetime) -> str:
    return dt.isoformat()


FUTURE = _iso(datetime.now(timezone.utc) + timedelta(days=3))
PAST = _iso(datetime.now(timezone.utc) - timedelta(days=1))


# ── An operation-aware, recording fake for _atable ────────────────
# The real flow hits the SAME table with different ops (org_invites is
# selected then updated; teachers is selected then inserted/updated), so a
# table->data map alone can't model it. This chain returns select data from a
# map, echoes insert payloads (with an id) so `.data[0]["id"]` works, and
# records every insert/update payload so tests can assert what was written.
class _RecordingChain:
    def __init__(self, table, selects, log, insert_extra):
        self.table = table
        self._selects = selects
        self._log = log
        self._insert_extra = insert_extra
        self._op = "select"
        self._payload = None

    def select(self, *a, **k):
        self._op = "select"
        return self

    def insert(self, *a, **k):
        self._op = "insert"
        self._payload = a[0] if a else {}
        return self

    def update(self, *a, **k):
        self._op = "update"
        self._payload = a[0] if a else {}
        return self

    def delete(self, *a, **k):
        self._op = "delete"
        return self

    # filters / ordering — all no-op passthroughs
    def eq(self, *a, **k): return self
    def neq(self, *a, **k): return self
    def is_(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def lte(self, *a, **k): return self
    def gt(self, *a, **k): return self
    def lt(self, *a, **k): return self
    def like(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def single(self, *a, **k): return self
    def range(self, *a, **k): return self

    async def execute(self):
        r = MagicMock()
        if self._op == "insert":
            row = dict(self._payload or {})
            row.setdefault("id", f"{self.table}-new")
            row.update(self._insert_extra.get(self.table, {}))
            self._log.append((self.table, "insert", dict(self._payload or {})))
            r.data = [row]
        elif self._op == "update":
            self._log.append((self.table, "update", dict(self._payload or {})))
            r.data = []
        else:
            r.data = list(self._selects.get(self.table, []))
        r.count = None
        return r


def _make_atable(selects, log, insert_extra=None):
    def factory(table):
        return _RecordingChain(table, selects, log, insert_extra or {})
    return factory


def _accept_patches(selects, log):
    """Patch _atable + the external helpers accept_org_invite reaches so the
    test exercises only the orchestration/branching logic."""
    factory = _make_atable(selects, log)
    return [
        patch("app.routers.auth._atable", side_effect=factory),
        patch("app.routers.auth.validate_password_async", new=AsyncMock()),
        patch("app.routers.auth.local_password_auth_enabled", return_value=True),
        patch("app.routers.auth.hash_password", new=AsyncMock(return_value="hashed")),
        patch("app.routers.auth.clear_teacher_cache"),
        patch("app.routers.auth.issue_email_verify_token", return_value="vtok"),
        patch("app.routers.auth.enqueue_job"),
    ]


def _post_accept(client, selects, log, *, token="tok123",
                 full_name="New Teacher", password="Str0ng!Passw0rd"):
    with contextlib.ExitStack() as es:
        for p in _accept_patches(selects, log):
            es.enter_context(p)
        return client.post(
            "/api/v1/auth/accept-org-invite",
            json={"token": token, "full_name": full_name, "password": password},
        )


INVITE = {"id": "inv1", "org_id": "org-9", "email": "new@teacher.com",
          "full_name": "Pre-filled", "status": "pending", "expires_at": FUTURE}


# ═══════════════════════════════════════════════════════════════════
#  POST /api/v1/auth/accept-org-invite
# ═══════════════════════════════════════════════════════════════════
class TestAcceptOrgInvite:
    def test_creates_teacher_but_defers_org_join(self, client):
        """Happy path, brand-new teacher: account is created with
        org_role=teacher + pending_verification, the org join is DEFERRED
        (org_id null, invite -> pending_verification not accepted)."""
        log = []
        selects = {"org_invites": [INVITE], "teachers": []}
        resp = _post_accept(client, selects, log)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["org_id"] is None              # join deferred
        assert body["org_role"] == "teacher"
        assert body["email_verification_required"] is True

        # The teacher row was inserted as a teacher, pending verification,
        # WITHOUT an org_id (membership is conferred only after verify).
        inserts = [p for (t, op, p) in log if t == "teachers" and op == "insert"]
        assert len(inserts) == 1
        assert inserts[0]["org_role"] == "teacher"
        assert inserts[0]["status"] == "pending_verification"
        assert "org_id" not in inserts[0]

        # The invite was moved to pending_verification, NEVER accepted here.
        inv_updates = [p for (t, op, p) in log if t == "org_invites" and op == "update"]
        assert {"status": "pending_verification"} in inv_updates
        assert all(u.get("status") != "accepted" for u in inv_updates)

        # No teachers update set an org_id at accept time.
        t_updates = [p for (t, op, p) in log if t == "teachers" and op == "update"]
        assert all("org_id" not in u for u in t_updates)

    def test_links_existing_unverified_teacher_without_org(self, client):
        """An existing teacher with no org and no verified email is linked
        (profile updated) and moved to pending_verification — still no join."""
        log = []
        existing = {"id": "t-existing", "org_id": None, "org_role": "teacher",
                    "email": "new@teacher.com", "email_verified_at": None}
        selects = {"org_invites": [INVITE], "teachers": [existing]}
        resp = _post_accept(client, selects, log)

        assert resp.status_code == 200, resp.text
        assert resp.json()["org_id"] is None
        # No insert — the existing row was updated, not duplicated.
        assert not [1 for (t, op, _) in log if t == "teachers" and op == "insert"]
        t_updates = [p for (t, op, p) in log if t == "teachers" and op == "update"]
        assert t_updates and all("org_id" not in u for u in t_updates)

    def test_rejects_expired_invite(self, client):
        log = []
        selects = {"org_invites": [{**INVITE, "expires_at": PAST}]}
        resp = _post_accept(client, selects, log)
        assert resp.status_code == 410
        # Nothing was written for an expired invite.
        assert not [1 for (_, op, _) in log if op in ("insert", "update")]

    def test_conflicts_when_email_already_in_org(self, client):
        log = []
        existing = {"id": "t1", "org_id": "org-other", "email": "new@teacher.com"}
        selects = {"org_invites": [INVITE], "teachers": [existing]}
        resp = _post_accept(client, selects, log)
        assert resp.status_code == 409

    def test_rejects_invite_missing_email(self, client):
        log = []
        selects = {"org_invites": [{**INVITE, "email": ""}]}
        resp = _post_accept(client, selects, log)
        assert resp.status_code == 400

    def test_rejects_unknown_token(self, client):
        log = []
        selects = {"org_invites": []}   # token hash matches nothing
        resp = _post_accept(client, selects, log)
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════
#  GET /verify-email  (the step that actually performs the org join)
# ═══════════════════════════════════════════════════════════════════
def _verify_patches(selects, log, claims):
    factory = _make_atable(selects, log)
    return [
        patch("app.routers.auth._atable", side_effect=factory),
        patch("app.routers.auth.verify_email_token", return_value=claims),
        patch("app.routers.auth.clear_teacher_cache"),
        patch("app.routers.auth.record_auth_event", new=AsyncMock()),
    ]


def _get_verify(client, selects, log, claims):
    with contextlib.ExitStack() as es:
        for p in _verify_patches(selects, log, claims):
            es.enter_context(p)
        return client.get("/verify-email", params={"token": "x"})


class TestVerifyEmailCompletesOrgJoin:
    CLAIMS = {"uid": "t1", "kind": "teacher", "email": "new@teacher.com"}

    def test_join_is_performed_on_verify(self, client):
        """Verifying a teacher with a pending_verification invite sets
        org_id + org_role=teacher + status=active and flips the invite to
        accepted — this is where org membership is finally conferred."""
        log = []
        selects = {
            "org_invites": [{"id": "inv1", "org_id": "org-9", "expires_at": FUTURE}],
            "teachers": [{"org_id": None}],
        }
        resp = _get_verify(client, selects, log, self.CLAIMS)
        assert resp.status_code == 200, resp.text

        t_updates = [p for (t, op, p) in log if t == "teachers" and op == "update"]
        join = [u for u in t_updates if u.get("org_id") == "org-9"]
        assert join, f"expected a teachers update setting org_id; got {t_updates}"
        assert join[0]["org_role"] == "teacher"
        assert join[0]["status"] == "active"

        inv_updates = [p for (t, op, p) in log if t == "org_invites" and op == "update"]
        assert any(u.get("status") == "accepted" for u in inv_updates)

    def test_already_in_org_retires_invite_without_rejoin(self, client):
        """If the teacher already belongs to an org, the stale invite is
        retired (expired) rather than left stuck, and no second join happens."""
        log = []
        selects = {
            "org_invites": [{"id": "inv1", "org_id": "org-9", "expires_at": FUTURE}],
            "teachers": [{"org_id": "org-already"}],
        }
        resp = _get_verify(client, selects, log, self.CLAIMS)
        assert resp.status_code == 200, resp.text

        t_updates = [p for (t, op, p) in log if t == "teachers" and op == "update"]
        assert all(u.get("org_id") != "org-9" for u in t_updates)  # no re-join
        inv_updates = [p for (t, op, p) in log if t == "org_invites" and op == "update"]
        assert any(u.get("status") == "expired" for u in inv_updates)
        assert all(u.get("status") != "accepted" for u in inv_updates)

    def test_expired_invite_does_not_join(self, client):
        log = []
        selects = {
            "org_invites": [{"id": "inv1", "org_id": "org-9", "expires_at": PAST}],
            "teachers": [{"org_id": None}],
        }
        resp = _get_verify(client, selects, log, self.CLAIMS)
        assert resp.status_code == 200, resp.text
        t_updates = [p for (t, op, p) in log if t == "teachers" and op == "update"]
        assert all("org_id" not in u for u in t_updates)  # never joined
        inv_updates = [p for (t, op, p) in log if t == "org_invites" and op == "update"]
        assert any(u.get("status") == "expired" for u in inv_updates)
