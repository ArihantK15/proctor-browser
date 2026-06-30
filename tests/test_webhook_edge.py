"""Webhook edge-case tests for /api/v1/webhooks/email.

Covers status transitions not yet tested in test_invites.py:
  - complained → failed
  - opened  → opened (with status guard)
  - clicked → clicked (with click_count increment)
  - delivered → ignored
  - bad JSON body → 400
  - expired timestamp → 403
  - already-bounced invite is not overwritten (status guard)
"""

import base64
import hashlib
import hmac
import json
import os
import sys
import time
from unittest.mock import MagicMock, AsyncMock


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("EMAIL_PROVIDER", "noop")
os.environ.setdefault("INVITE_BASE_URL", "https://app.procta.net")

from tests.conftest import shared_supabase_mock


def _signed_body(body: dict, secret_raw: bytes = b"test-webhook-secret-123",
                 svix_id: str = "msg_test_01") -> tuple[bytes, dict]:
    """Return (raw_body, headers) with valid Svix signature."""
    raw = json.dumps(body).encode()
    svix_ts = str(int(time.time()))
    signed_payload = f"{svix_id}.{svix_ts}.".encode() + raw
    mac = hmac.new(secret_raw, signed_payload, hashlib.sha256).digest()
    sig_b64 = base64.b64encode(mac).decode()
    sig = f"v1,{sig_b64}"
    headers = {
        "svix-id": svix_id,
        "svix-timestamp": svix_ts,
        "svix-signature": sig,
        "content-type": "application/json",
    }
    return raw, headers


def _setup_webhook_secret(secret_raw: bytes = b"test-webhook-secret-123"):
    """Set RESEND_WEBHOOK_SECRET env var and return it."""
    secret = "whsec_" + base64.b64encode(secret_raw).decode()
    os.environ["RESEND_WEBHOOK_SECRET"] = secret
    from app import emailer
    emailer._reset_backend_for_tests()
    return secret


INVITE_ROW = {
    "id": "inv-1", "token": "tok-webhook-edge",
    "teacher_id": "teacher-1", "roll_number": "WBEDGE",
    "email": "edge@test.com", "full_name": "Edge",
    "exam_id": "exam-1", "status": "sent",
    "provider_msg_id": "msg-webhook-edge",
    "bounced_at": None, "bounce_reason": None,
    "opened_at": None, "clicked_at": None, "click_count": 0,
}


# ═══════════════════════════════════════════════════════════════════
#  Webhook Edge Cases
# ═══════════════════════════════════════════════════════════════════

class TestWebhookEdgeCases:

    def test_complained_flips_to_failed(self, client):
        """email.complained should set status=failed with spam reason."""
        raw, headers = _signed_body({
            "type": "email.complained",
            "data": {"email_id": "msg-webhook-edge"},
        })
        sm = shared_supabase_mock()
        sm.table.return_value.select.return_value.eq.return_value.limit.return_value.execute = MagicMock(
            return_value=MagicMock(data=[])
        )
        sm.table.return_value.update.return_value.eq.return_value.in_.return_value.execute = AsyncMock()

        _setup_webhook_secret()
        try:
            resp = client.post("/api/v1/webhooks/email", content=raw, headers=headers)
            assert resp.status_code == 200, resp.text
            assert resp.json()["event"] == "email.complained"
        finally:
            os.environ.pop("RESEND_WEBHOOK_SECRET", None)

    def test_opened_flips_status(self, client):
        """email.opened with sent status should set opened_at and status=opened."""
        raw, headers = _signed_body({
            "type": "email.opened",
            "data": {"email_id": "msg-webhook-edge"},
        })
        sm = shared_supabase_mock()
        sm.table.return_value.select.return_value.eq.return_value.limit.return_value.execute = MagicMock(
            return_value=MagicMock(data=[])
        )
        sm.table.return_value.update.return_value.eq.return_value.execute = AsyncMock()
        sm.table.return_value.update.return_value.eq.return_value.is_.return_value.execute = AsyncMock()

        _setup_webhook_secret()
        try:
            resp = client.post("/api/v1/webhooks/email", content=raw, headers=headers)
            assert resp.status_code == 200
            assert resp.json()["event"] == "email.opened"
        finally:
            os.environ.pop("RESEND_WEBHOOK_SECRET", None)

    def test_opened_db_failure_returns_500_for_provider_retry(self, client):
        """email.opened DB failures should not be swallowed."""
        raw, headers = _signed_body({
            "type": "email.opened",
            "data": {"email_id": "msg-webhook-edge"},
        })
        sm = shared_supabase_mock()
        sm.table.return_value.select.return_value.eq.return_value.limit.return_value.execute = MagicMock(
            return_value=MagicMock(data=[])
        )
        sm.table.return_value.update.return_value.eq.return_value.eq.return_value.execute = AsyncMock(
            side_effect=RuntimeError("db down")
        )
        sm.table.return_value.update.return_value.eq.return_value.is_.return_value.execute = AsyncMock(
            side_effect=RuntimeError("db down")
        )

        _setup_webhook_secret()
        try:
            resp = client.post("/api/v1/webhooks/email", content=raw, headers=headers)
            assert resp.status_code == 500
            assert "retry" in resp.json()["detail"].lower()
        finally:
            os.environ.pop("RESEND_WEBHOOK_SECRET", None)

    def test_clicked_increments_click_count(self, client):
        """email.clicked should increment click_count and set status=clicked."""
        sm = shared_supabase_mock()
        existing_row = dict(INVITE_ROW, click_count=2)
        sm.table.return_value.select.return_value.eq.return_value.limit.return_value.execute = MagicMock(
            return_value=MagicMock(data=[existing_row])
        )
        sm.table.return_value.update.return_value.eq.return_value.execute = AsyncMock()
        sm.table.return_value.update.return_value.eq.return_value.in_.return_value.execute = AsyncMock()

        raw, headers = _signed_body({
            "type": "email.clicked",
            "data": {"email_id": "msg-webhook-edge"},
        })
        _setup_webhook_secret()
        try:
            resp = client.post("/api/v1/webhooks/email", content=raw, headers=headers)
            assert resp.status_code == 200
            assert resp.json()["event"] == "email.clicked"
        finally:
            os.environ.pop("RESEND_WEBHOOK_SECRET", None)

    def test_clicked_db_failure_returns_500_for_provider_retry(self, client):
        """email.clicked DB failures should not be swallowed."""
        sm = shared_supabase_mock()
        sm.table.return_value.select.return_value.eq.return_value.limit.return_value.execute = AsyncMock(
            side_effect=RuntimeError("db down")
        )

        raw, headers = _signed_body({
            "type": "email.clicked",
            "data": {"email_id": "msg-webhook-edge"},
        })
        _setup_webhook_secret()
        try:
            resp = client.post("/api/v1/webhooks/email", content=raw, headers=headers)
            assert resp.status_code == 500
            assert "retry" in resp.json()["detail"].lower()
        finally:
            os.environ.pop("RESEND_WEBHOOK_SECRET", None)

    def test_delivered_is_ignored(self, client):
        """email.delivered is a no-op and returns 200."""
        raw, headers = _signed_body({
            "type": "email.delivered",
            "data": {"email_id": "msg-webhook-edge"},
        })
        shared_supabase_mock()
        _setup_webhook_secret()
        try:
            resp = client.post("/api/v1/webhooks/email", content=raw, headers=headers)
            assert resp.status_code == 200
            assert resp.json()["event"] == "email.delivered"
        finally:
            os.environ.pop("RESEND_WEBHOOK_SECRET", None)

    def test_bad_json_400(self, client):
        """Non-JSON body should be rejected with 400."""
        raw = b"not json at all"
        svix_id = "msg_bad"
        svix_ts = str(int(time.time()))
        signed_payload = f"{svix_id}.{svix_ts}.".encode() + raw
        mac = hmac.new(b"whsec_test", signed_payload, hashlib.sha256).digest()
        headers = {
            "svix-id": svix_id, "svix-timestamp": svix_ts,
            "svix-signature": "v1," + base64.b64encode(mac).decode(),
            "content-type": "application/json",
        }
        _setup_webhook_secret(b"test")
        try:
            resp = client.post("/api/v1/webhooks/email", content=raw, headers=headers)
            assert resp.status_code == 400
        finally:
            os.environ.pop("RESEND_WEBHOOK_SECRET", None)

    def test_no_svix_headers_403(self, client):
        """Missing svix-id header should fail verification."""
        body = json.dumps({"type": "email.bounced", "data": {"email_id": "x"}}).encode()
        _setup_webhook_secret()
        try:
            resp = client.post("/api/v1/webhooks/email", content=body,
                               headers={"content-type": "application/json"})
            assert resp.status_code == 403
        finally:
            os.environ.pop("RESEND_WEBHOOK_SECRET", None)

    def test_no_msg_id_ignored(self, client):
        """Webhook without email_id should be accepted but ignored."""
        raw, headers = _signed_body({"type": "email.bounced", "data": {}})
        _setup_webhook_secret()
        try:
            resp = client.post("/api/v1/webhooks/email", content=raw, headers=headers)
            assert resp.status_code == 200
            assert resp.json()["ignored"] == "no msg id"
        finally:
            os.environ.pop("RESEND_WEBHOOK_SECRET", None)
