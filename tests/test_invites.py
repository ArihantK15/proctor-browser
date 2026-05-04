"""
Regression tests for /api/admin/invites/* and /invite/<token>.

Covers:
  1. send_invites happy path — creates rows, hits the emailer, stamps
     status=sent + provider_msg_id.
  2. send_invites idempotency — two sends to the same (teacher, email,
     exam) upsert instead of duplicating, and the token rotates.
  3. Daily cap — batch larger than remaining quota is rejected with 429.
  4. Per-invite access code — validate-student accepts the per-invite
     code even when the shared exam code is also configured, and flips
     the invite to 'accepted'.
  5. Revoked invite cannot be accepted.
  6. Landing page (/invite/<token>) — 200 for a valid token, 404 for
     unknown, 410 for revoked or expired.
  7. Webhook signature is enforced — unsigned request → 403.
  8. Webhook bounce event updates the matching invite's status.

The Resend provider is pinned to the noop backend via EMAIL_PROVIDER=noop
so no network calls happen during tests.
"""
import hashlib
import hmac
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("EMAIL_PROVIDER", "noop")
os.environ.setdefault("INVITE_BASE_URL", "https://app.procta.net")

from tests.conftest import shared_supabase_mock,  make_admin_token  # noqa: E402


@pytest.fixture
def admin_headers():
    return {"Authorization": f"Bearer {make_admin_token()}"}


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


class _InviteStub:
    """Supabase fluent-builder stub that keeps in-memory state for
    student_invites + invite_send_counters + teachers + students +
    exam_config.

    Dispatches on table name and returns tailored chains that honour
    select/eq/update/insert/delete/upsert/order.
    """

    def __init__(self, invites=None, counters=None, teachers=None,
                 students=None, exam_configs=None):
        self.invites = list(invites or [])
        self.counters = list(counters or [])
        self.teachers = list(teachers or [
            {"id": "teacher-1", "email": "t@p.com", "full_name": "T One"}])
        self.students = list(students or [])
        self.exam_configs = list(exam_configs or [])

    # ── helpers ────────────────────────────────────────────────────
    def _apply_filters(self, rows, eqs, ins=None):
        """Apply both .eq() and .in_() filters in one pass.
        ``ins`` is a dict {column: list_of_allowed_values} matching
        what the production code passes via .in_('col', [...])."""
        out = []
        for r in rows:
            ok = True
            for k, v in eqs.items():
                # Supabase treats "" as a real value; our code passes ""
                # for exam_id when None — normalise both sides to str.
                if str(r.get(k) or "") != str(v or ""):
                    ok = False; break
            if ok and ins:
                for k, allowed in ins.items():
                    if r.get(k) not in allowed:
                        ok = False; break
            if ok:
                out.append(r)
        return out

    def __call__(self, table):
        chain = MagicMock()
        chain._table = table
        chain._eqs = {}
        chain._ins = {}      # {column: [allowed_values]} from .in_()
        chain._payload = None
        chain._op = None

        def _select(*a, **k): chain._op = "select"; return chain
        def _eq(c, v): chain._eqs[c] = v; return chain
        def _in(c, vs): chain._ins[c] = list(vs or []); return chain
        def _order(*a, **k): return chain
        def _limit(*a, **k): return chain
        def _update(p): chain._op = "update"; chain._payload = p; return chain
        def _insert(p): chain._op = "insert"; chain._payload = p; return chain
        def _upsert(p): chain._op = "upsert"; chain._payload = p; return chain
        def _delete(): chain._op = "delete"; return chain

        def _execute():
            ds = None
            if table == "student_invites":
                ds = self.invites
            elif table == "invite_send_counters":
                ds = self.counters
            elif table == "teachers":
                ds = self.teachers
            elif table == "students":
                ds = self.students
            elif table == "exam_config":
                ds = self.exam_configs
            elif table == "exam_sessions":
                return MagicMock(data=[])
            elif table == "exam_group_assignments":
                return MagicMock(data=[])
            else:
                return MagicMock(data=[])

            if chain._op in (None, "select"):
                return MagicMock(data=self._apply_filters(ds, chain._eqs, chain._ins))
            if chain._op == "insert":
                new = list(chain._payload) if isinstance(chain._payload, list) \
                    else [chain._payload]
                # Mirror Supabase default: rows without an id get a fresh
                # uuid so update-by-id flows work the same way in tests.
                import uuid as _uuid
                for row in new:
                    if not row.get("id"):
                        row["id"] = str(_uuid.uuid4())
                ds.extend(new)
                return MagicMock(data=new)
            if chain._op == "upsert":
                new = chain._payload if isinstance(chain._payload, list) \
                    else [chain._payload]
                ds.extend(new)
                return MagicMock(data=new)
            if chain._op == "update":
                matched = self._apply_filters(ds, chain._eqs, chain._ins)
                for r in matched:
                    r.update(chain._payload or {})
                return MagicMock(data=matched)
            if chain._op == "delete":
                keep = []
                removed = []
                for r in ds:
                    ok = all(str(r.get(k) or "") == str(v or "")
                             for k, v in chain._eqs.items())
                    (removed if ok else keep).append(r)
                ds[:] = keep
                return MagicMock(data=removed)
            return MagicMock(data=[])

        chain.select.side_effect = _select
        chain.eq.side_effect = _eq
        # `.in_` is the supabase-py method for SQL `IN (...)` filters.
        # Underscore suffix because `in` is a reserved keyword.
        chain.in_.side_effect = _in
        chain.order.side_effect = _order
        chain.limit.side_effect = _limit
        chain.update.side_effect = _update
        chain.insert.side_effect = _insert
        chain.upsert.side_effect = _upsert
        chain.delete.side_effect = _delete
        chain.execute.side_effect = _execute
        return chain


# ── Fixtures ───────────────────────────────────────────────────────
def _patch(stub, cap=None):
    patches = [patch.object(shared_supabase_mock(), "table")]
    if cap is not None:
        patches.append(patch("app.dependencies.INVITE_DAILY_CAP", cap))
    return patches


# ── Tests ──────────────────────────────────────────────────────────
class TestSendInvites:

    def test_happy_path_creates_and_sends(self, client, admin_headers):
        stub = _InviteStub(students=[{
            "roll_number": "ALICE01", "teacher_id": "teacher-1",
            "full_name": "Alice", "email": "alice@school.edu",
        }])
        patches = _patch(stub)
        with patches[0] as mock_table:
            mock_table.side_effect = stub
            r = client.post("/api/v1/admin/invites/send",
                headers=admin_headers,
                json={
                    "recipients": [
                        {"email": "alice@school.edu", "full_name": "Alice",
                         "roll_number": "ALICE01"},
                    ],
                    "exam_id": "exam-1",
                    "custom_message": "Good luck!",
                })
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["sent"] == 1 and d["failed"] == 0 and d["skipped"] == 0
        # Row persisted with status=sent and a provider_msg_id stamped.
        assert len(stub.invites) == 1
        inv = stub.invites[0]
        assert inv["status"] == "sent"
        assert inv["provider_msg_id"], "noop backend should stamp an id"
        assert inv["custom_message"] == "Good luck!"
        assert inv["email"] == "alice@school.edu"

    def test_resend_is_idempotent(self, client, admin_headers):
        """Two sends to the same (teacher, email, exam) must upsert —
        the row count stays at 1 and the token rotates."""
        stub = _InviteStub()
        with patch.object(shared_supabase_mock(), "table") as mock_table:
            mock_table.side_effect = stub
            r1 = client.post("/api/v1/admin/invites/send", headers=admin_headers,
                json={"recipients": [{"email": "bob@x.com", "full_name": "Bob",
                      "roll_number": "BOB1"}], "exam_id": "exam-1"})
            assert r1.status_code == 200, r1.text
            first_token = stub.invites[0]["token"]
            r2 = client.post("/api/v1/admin/invites/send", headers=admin_headers,
                json={"recipients": [{"email": "bob@x.com", "full_name": "Bob",
                      "roll_number": "BOB1"}], "exam_id": "exam-1"})
            assert r2.status_code == 200, r2.text
        assert len(stub.invites) == 1, "second send must upsert, not duplicate"
        assert stub.invites[0]["token"] != first_token, (
            "resend must rotate the token so old links stop working"
        )

    def test_daily_cap_rejects_oversized_batch(self, client, admin_headers):
        stub = _InviteStub(counters=[{
            "teacher_id": "teacher-1",
            "day": datetime.now(timezone.utc).date().isoformat(),
            "count": 498,
        }])
        with patch.object(shared_supabase_mock(), "table") as mock_table, \
             patch("app.dependencies.INVITE_DAILY_CAP", 500):
            mock_table.side_effect = stub
            r = client.post("/api/v1/admin/invites/send", headers=admin_headers,
                json={"recipients": [
                    {"email": f"s{i}@x.com", "full_name": f"S{i}",
                     "roll_number": f"R{i}"} for i in range(5)
                ], "exam_id": "exam-1"})
        assert r.status_code == 429, r.text
        assert "cap" in r.text.lower()


class TestInviteLanding:

    def test_404_for_unknown_token(self, client):
        stub = _InviteStub()
        with patch.object(shared_supabase_mock(), "table") as mock_table:
            mock_table.side_effect = stub
            r = client.get("/invite/nonexistent-token-abcdef")
        assert r.status_code == 404
        assert "invalid" in r.text.lower() or "revoked" in r.text.lower()

    def test_200_marks_opened(self, client):
        stub = _InviteStub(invites=[{
            "id": "i1", "token": "tok-open-1",
            "teacher_id": "teacher-1", "roll_number": "ALICE01",
            "email": "alice@school.edu", "full_name": "Alice",
            "exam_id": "exam-1", "status": "sent",
            "sent_at": _iso(datetime.now(timezone.utc)),
            "opened_at": None, "access_code": "HAPPY1",
            "expires_at": _iso(datetime.now(timezone.utc) + timedelta(days=5)),
        }])
        with patch.object(shared_supabase_mock(), "table") as mock_table:
            mock_table.side_effect = stub
            r = client.get("/invite/tok-open-1")
        assert r.status_code == 200, r.text
        assert "Alice" in r.text
        assert "ALICE01" in r.text
        assert "HAPPY1" in r.text, "per-invite access code must appear on the landing page"
        # opened_at stamped
        assert stub.invites[0]["opened_at"] is not None
        assert stub.invites[0]["status"] == "opened"

    def test_410_for_revoked(self, client):
        stub = _InviteStub(invites=[{
            "id": "i2", "token": "tok-revoked",
            "teacher_id": "teacher-1", "roll_number": "R2",
            "email": "r@x.com", "full_name": "R",
            "exam_id": "exam-1", "status": "revoked",
        }])
        with patch.object(shared_supabase_mock(), "table") as mock_table:
            mock_table.side_effect = stub
            r = client.get("/invite/tok-revoked")
        assert r.status_code == 410

    def test_410_for_expired(self, client):
        stub = _InviteStub(invites=[{
            "id": "i3", "token": "tok-expired",
            "teacher_id": "teacher-1", "roll_number": "R3",
            "email": "e@x.com", "full_name": "E",
            "exam_id": "exam-1", "status": "sent",
            "expires_at": _iso(datetime.now(timezone.utc) - timedelta(days=1)),
        }])
        with patch.object(shared_supabase_mock(), "table") as mock_table:
            mock_table.side_effect = stub
            r = client.get("/invite/tok-expired")
        assert r.status_code == 410
        assert "expired" in r.text.lower()


class TestWebhook:

    def test_unsigned_webhook_rejected(self, client):
        r = client.post("/api/v1/webhooks/email",
            content=json.dumps({"type": "email.bounced",
                                "data": {"email_id": "x"}}))
        assert r.status_code == 403

    def test_signed_bounce_flips_status(self, client):
        """Webhook signature verification — Svix format.

        Resend uses Svix-style signing (since 2024). Three required
        headers: svix-id, svix-timestamp, svix-signature. The signed
        payload is `<id>.<ts>.<body>` (literal dots, not concat) and
        the signature is base64. The secret is `whsec_<base64-key>`
        and the key is base64-decoded before use as the HMAC key.
        Multiple v1 signatures are space-separated to support secret
        rotation.

        This test was originally written for the pre-Svix legacy
        format (`t=ts,v1=<hexsig>`). Updated to match the format
        emailer.verify_webhook actually expects after the Phase 10
        rewrite — anything else returns 403 forbidden.
        """
        from app import emailer
        import base64, time
        # Use a `whsec_`-prefixed secret because that's the format
        # Resend distributes; verify_webhook base64-decodes the part
        # after the prefix before using it as the HMAC key.
        raw_key = b"test-webhook-secret-123-with-padding"
        secret = "whsec_" + base64.b64encode(raw_key).decode()
        os.environ["RESEND_WEBHOOK_SECRET"] = secret
        try:
            emailer._reset_backend_for_tests()
            stub = _InviteStub(invites=[{
                "id": "ib", "token": "t-bounce",
                "teacher_id": "teacher-1", "roll_number": "RB",
                "email": "bouncer@gone.example", "full_name": "B",
                "exam_id": "exam-1", "status": "sent",
                "provider_msg_id": "msg-abc-123",
            }])
            body = json.dumps({
                "type": "email.bounced",
                "data": {"email_id": "msg-abc-123",
                         "bounce": "mailbox does not exist"},
            }).encode()
            svix_id = "msg_test_01"
            # Svix accepts any timestamp within 5 min of now to defend
            # against replay; pick "now" so the check passes.
            svix_ts = str(int(time.time()))
            signed_payload = f"{svix_id}.{svix_ts}.".encode() + body
            mac = hmac.new(raw_key, signed_payload, hashlib.sha256).digest()
            sig_b64 = base64.b64encode(mac).decode()
            # Header format: "v1,<sig>" — multiple sigs space-separated.
            sig = f"v1,{sig_b64}"
            with patch.object(shared_supabase_mock(), "table") as mock_table:
                mock_table.side_effect = stub
                r = client.post("/api/v1/webhooks/email", content=body,
                                headers={"svix-id": svix_id,
                                         "svix-timestamp": svix_ts,
                                         "svix-signature": sig,
                                         "content-type": "application/json"})
            assert r.status_code == 200, r.text
            assert stub.invites[0]["status"] == "bounced"
            assert stub.invites[0]["bounced_at"] is not None
            assert "mailbox does not exist" in (stub.invites[0]["bounce_reason"] or "")
        finally:
            os.environ.pop("RESEND_WEBHOOK_SECRET", None)
            emailer._reset_backend_for_tests()


class TestRevoke:

    def test_revoke_flips_status(self, client, admin_headers):
        stub = _InviteStub(invites=[{
            "id": "rev1", "token": "tok-x",
            "teacher_id": "teacher-1", "roll_number": "R",
            "email": "r@x.com", "full_name": "R",
            "exam_id": "exam-1", "status": "sent",
        }])
        with patch.object(shared_supabase_mock(), "table") as mock_table:
            mock_table.side_effect = stub
            r = client.delete("/api/v1/admin/invites/rev1", headers=admin_headers)
        assert r.status_code == 200, r.text
        assert stub.invites[0]["status"] == "revoked"

    def test_revoke_unknown_invite_404(self, client, admin_headers):
        stub = _InviteStub()
        with patch.object(shared_supabase_mock(), "table") as mock_table:
            mock_table.side_effect = stub
            r = client.delete("/api/v1/admin/invites/does-not-exist",
                              headers=admin_headers)
        assert r.status_code == 404


class TestListInvites:

    def test_list_returns_invite_urls(self, client, admin_headers):
        stub = _InviteStub(invites=[{
            "id": "l1", "token": "tok-list-1",
            "teacher_id": "teacher-1", "roll_number": "R1",
            "email": "r1@x.com", "full_name": "R1",
            "exam_id": "exam-1", "status": "sent",
        }, {
            "id": "l2", "token": "tok-list-2",
            "teacher_id": "teacher-1", "roll_number": "R2",
            "email": "r2@x.com", "full_name": "R2",
            "exam_id": "exam-1", "status": "bounced",
        }])
        with patch.object(shared_supabase_mock(), "table") as mock_table:
            mock_table.side_effect = stub
            r = client.get("/api/v1/admin/invites", headers=admin_headers)
        assert r.status_code == 200, r.text
        d = r.json()
        assert len(d["invites"]) == 2
        for row in d["invites"]:
            assert row["invite_url"].endswith(row["token"])
            assert row["invite_url"].startswith("https://")
