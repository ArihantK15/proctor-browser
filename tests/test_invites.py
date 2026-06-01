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
import asyncio
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
        chain._on_conflict = None

        def _select(*a, **k): chain._op = "select"; return chain
        def _eq(c, v): chain._eqs[c] = v; return chain
        def _in(c, vs): chain._ins[c] = list(vs or []); return chain
        def _order(*a, **k): return chain
        def _limit(*a, **k): return chain
        def _update(p): chain._op = "update"; chain._payload = p; return chain
        def _insert(p): chain._op = "insert"; chain._payload = p; return chain
        def _upsert(p, on_conflict=None):
            chain._op = "upsert"
            chain._payload = p
            chain._on_conflict = on_conflict
            return chain
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
                # If on_conflict columns are set, find and update matching rows
                if hasattr(chain, '_on_conflict') and chain._on_conflict:
                    conflict_cols = [c.strip() for c in chain._on_conflict.split(",")]
                    for row in new:
                        matched = False
                        for existing in ds:
                            if all(str(existing.get(c, "")) == str(row.get(c, "")) for c in conflict_cols):
                                existing.update(row)
                                matched = True
                                break
                        if not matched:
                            ds.append(row)
                else:
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
def _atable_async_stub(stub):
    """Wrap an _InviteStub so it works with _atable's async execute()."""
    class Wrapper:
        def __init__(self, s):
            self._stub = s
        def __call__(self, table_name):
            chain = self._stub(table_name)
            orig_execute = chain.execute
            async def _async_exec():
                return orig_execute()
            chain.execute = _async_exec
            return chain
    return Wrapper(stub)


def _patch(stub, cap=None):
    """Patch both supabase.table (sync code paths) and _atable (async code paths)
    so that endpoints in admin.py, public.py, and dependencies.py work regardless
    of which DB wrapper they use.  Also configure supabase.rpc to raise a "function
    not found" error so _claim_and_bump_cap falls back to the _atable-based path."""
    mock_supabase = shared_supabase_mock()
    mock_supabase.rpc.side_effect = Exception(
        "function claim_invite_cap does not exist")
    patches = [
        patch.object(mock_supabase, "table"),
        patch("app.routers.public._atable", side_effect=_atable_async_stub(stub)),
        patch("app.routers.admin._atable", side_effect=_atable_async_stub(stub)),
        patch("app.dependencies._atable", side_effect=_atable_async_stub(stub)),
        patch("app.invites._atable", side_effect=_atable_async_stub(stub)),
    ]
    if cap is not None:
        patches.append(patch("app.invites.INVITE_DAILY_CAP", cap))
    return patches


# ── Tests ──────────────────────────────────────────────────────────
class TestSendInvites:

    def test_happy_path_creates_and_sends(self, client, admin_headers):
        stub = _InviteStub(students=[{
            "roll_number": "ALICE01", "teacher_id": "teacher-1",
            "full_name": "Alice", "email": "alice@school.edu",
        }])
        patches = _patch(stub)
        expires_at = _iso(datetime.now(timezone.utc) + timedelta(days=2))
        with patches[0] as mock_table, patches[1], \
             patch("app.routers.admin_invites.send_invite_email_job",
                   return_value={"ok": True, "provider_msg_id": "msg-alice-1", "error": None}) as mock_send:
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
                    "per_invite_code": True,
                    "expires_at": expires_at,
                })
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["sent"] == 1 and d["failed"] == 0 and d["skipped"] == 0
        # Row persisted with status=sent and a provider_msg_id stamped.
        assert len(stub.invites) == 1
        inv = stub.invites[0]
        assert inv["status"] == "sent"
        assert inv["provider_msg_id"] == "msg-alice-1"
        assert inv["custom_message"] == "Good luck!"
        assert inv["email"] == "alice@school.edu"
        assert inv["expires_at"] == expires_at
        assert inv["access_code"] and len(inv["access_code"]) == 6
        sent_kwargs = mock_send.call_args.kwargs
        assert sent_kwargs["registration_url"] == "https://app.procta.net/register?t=teacher-1&e=exam-1"
        assert sent_kwargs["access_code"] == inv["access_code"]
        assert sent_kwargs["custom_message"] == "Good luck!"

    def test_resend_is_idempotent(self, client, admin_headers):
        """Two sends to the same (teacher, email, exam) must upsert —
        the row count stays at 1 and the token rotates."""
        stub = _InviteStub()
        patches = _patch(stub)
        with patches[0] as mock_table, patches[1], \
             patch("app.routers.admin_invites.send_invite_email_job",
                   return_value={"ok": True, "provider_msg_id": "msg-bob-1", "error": None}):
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

    def test_noop_provider_is_not_reported_as_sent(self, client, admin_headers):
        stub = _InviteStub()
        patches = _patch(stub)
        with patches[0] as mock_table, patches[1], \
             patch("app.routers.admin_invites.send_invite_email_job",
                   return_value={"ok": True, "provider_msg_id": "noop", "error": None}):
            mock_table.side_effect = stub
            r = client.post("/api/v1/admin/invites/send", headers=admin_headers,
                json={"recipients": [{"email": "noop@x.com", "full_name": "Noop",
                      "roll_number": "NOOP1"}], "exam_id": "exam-1"})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["sent"] == 0
        assert d["failed"] == 1
        assert d["failures"][0]["email"] == "noop@x.com"
        assert stub.invites[0]["status"] == "failed"

    def test_daily_cap_rejects_oversized_batch(self, client, admin_headers, monkeypatch):
        # invites._claim_and_bump_cap short-circuits to (allow, full)
        # when EMAIL_PROVIDER=noop OR RESEND_API_KEY is unset, to fix
        # the prod symptom where dry-runs without a real Resend key
        # exhausted the local counter while Resend itself stayed at 0.
        # This test specifically validates the cap-enforcement path so
        # we force both env vars to the "real send" shape for its
        # scope; teardown auto-restores via monkeypatch.
        monkeypatch.setenv("EMAIL_PROVIDER", "resend")
        monkeypatch.setenv("RESEND_API_KEY", "re_test_dummy")
        stub = _InviteStub(counters=[{
            "teacher_id": "teacher-1",
            "day": datetime.now(timezone.utc).date().isoformat(),
            "count": 498,
        }])
        patches = _patch(stub, cap=500)
        with patches[0] as mock_table, patches[1], patches[3], patches[4], patches[5]:
            mock_table.side_effect = stub
            r = client.post("/api/v1/admin/invites/send", headers=admin_headers,
                json={"recipients": [
                    {"email": f"s{i}@x.com", "full_name": f"S{i}",
                     "roll_number": f"R{i}"} for i in range(5)
                ], "exam_id": "exam-1"})
        assert r.status_code == 429, r.text
        assert "cap" in r.text.lower()

    def test_cap_counter_storage_failure_fails_open(self, monkeypatch):
        """Missing invite_send_counters/RPC must not fake a 0-remaining cap."""
        monkeypatch.setenv("EMAIL_PROVIDER", "resend")
        monkeypatch.setenv("RESEND_API_KEY", "re_test_dummy")

        class _BrokenCapTable:
            def select(self, *args, **kwargs):
                return self
            def eq(self, *args, **kwargs):
                return self
            async def execute(self):
                raise RuntimeError("relation invite_send_counters does not exist")

        async def _run():
            from app import invites
            with patch("app.database.is_postgres_backend", return_value=True), \
                 patch("app.invites._atable", return_value=_BrokenCapTable()):
                return await invites._claim_and_bump_cap("teacher-1", 1)

        ok, remaining = asyncio.run(_run())
        assert ok is True
        assert remaining >= 1


class TestInviteLanding:

    def test_404_for_unknown_token(self, client):
        stub = _InviteStub()
        patches = _patch(stub)
        with patches[0] as mock_table, patches[1]:
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
        patches = _patch(stub)
        with patches[0] as mock_table, patches[1]:
            mock_table.side_effect = stub
            r = client.get("/invite/tok-open-1")
        assert r.status_code == 200, r.text
        assert "Alice" in r.text
        assert "ALICE01" in r.text
        assert "HAPPY1" in r.text, "per-invite access code must appear on the landing page"
        assert "/register?t=teacher-1&amp;e=exam-1" in r.text
        assert 'href="procta://invite/tok-open-1"' in r.text
        assert "onclick=" not in r.text
        assert '<script src="/static/invite-landing.js" defer></script>' in r.text
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
        patches = _patch(stub)
        with patches[0] as mock_table, patches[1]:
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
        patches = _patch(stub)
        with patches[0] as mock_table, patches[1]:
            mock_table.side_effect = stub
            r = client.get("/invite/tok-expired")
        assert r.status_code == 410
        assert "expired" in r.text.lower()


class TestInviteExamLaunch:

    def test_validate_student_preserves_invite_exam_id_on_auto_enroll(self, client):
        """Invite-only launch must roster the student into the invited exam."""
        stub = _InviteStub(
            invites=[{
                "id": "i-launch", "token": "tok-launch",
                "teacher_id": "teacher-1", "roll_number": "LAUNCH1",
                "email": "launch@school.edu", "full_name": "Launch Student",
                "exam_id": "exam-demo", "status": "sent",
                "access_code": "DEMO1",
                "expires_at": _iso(datetime.now(timezone.utc) + timedelta(days=1)),
            }],
            exam_configs=[{
                "teacher_id": "teacher-1",
                "exam_id": "exam-demo",
                "access_code": "",
                "starts_at": _iso(datetime.now(timezone.utc) - timedelta(minutes=5)),
                "ends_at": _iso(datetime.now(timezone.utc) + timedelta(hours=2)),
                "duration_minutes": 60,
            }],
        )
        patches = _patch(stub)
        with patches[0] as mock_table, patches[1], \
             patch("app.routers.exam._atable", side_effect=_atable_async_stub(stub)):
            mock_table.side_effect = stub
            r = client.post("/api/v1/validate-student",
                            json={"roll_number": "LAUNCH1", "access_code": "DEMO1"})

        assert r.status_code == 200, r.text
        assert stub.students
        assert stub.students[0]["exam_id"] == "exam-demo"


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
            patches = _patch(stub)
            body = json.dumps({
                "type": "email.bounced",
                "data": {"email_id": "msg-abc-123",
                         "bounce": "mailbox does not exist"},
            }).encode()
            svix_id = "msg_test_01"
            svix_ts = str(int(time.time()))
            signed_payload = f"{svix_id}.{svix_ts}.".encode() + body
            mac = hmac.new(raw_key, signed_payload, hashlib.sha256).digest()
            sig_b64 = base64.b64encode(mac).decode()
            sig = f"v1,{sig_b64}"
            with patches[0] as mock_table, patches[1]:
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
        patches = _patch(stub)
        with patches[0] as mock_table, patches[1]:
            mock_table.side_effect = stub
            r = client.delete("/api/v1/admin/invites/rev1", headers=admin_headers)
        assert r.status_code == 200, r.text
        assert stub.invites[0]["status"] == "revoked"

    def test_revoke_unknown_invite_404(self, client, admin_headers):
        stub = _InviteStub()
        patches = _patch(stub)
        with patches[0] as mock_table, patches[1]:
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
        patches = _patch(stub)
        with patches[0] as mock_table, patches[1]:
            mock_table.side_effect = stub
            r = client.get("/api/v1/admin/invites", headers=admin_headers)
        assert r.status_code == 200, r.text
        d = r.json()
        assert len(d["invites"]) == 2
        for row in d["invites"]:
            assert "token" not in row
            assert row["token_prefix"] == row["invite_url"].rsplit("/", 1)[-1][:8]
            assert row["invite_url"].startswith("https://")
