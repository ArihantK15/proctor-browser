"""Regression tests for emailer HTML-escaping and the webhook logger.

Covers two audit findings:
  1. send_suspicious_login_email rendered the User-Agent header (attacker-
     controlled) and the user's name into the alert email's HTML without
     escaping — HTML/script injection into the victim's security email.
  2. verify_webhook called a non-existent `_log` (NameError) when
     RESEND_WEBHOOK_SECRET was unset, crashing the bounce-webhook path
     instead of failing closed with a warning.
"""
import os
import sys
import importlib

os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")
os.environ.setdefault("SUPABASE_URL", "https://fake.local")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake")

from unittest.mock import patch
from datetime import datetime, timezone

# tests/conftest.py replaces app.emailer with a MagicMock in sys.modules so
# the unit suite never sends real mail. To exercise the REAL escaping and
# webhook logic, swap the mock out, import the genuine module (which keeps
# its proper `app` package context for relative imports), then restore the
# mock so the rest of the suite is unaffected.
_saved_mock = sys.modules.pop("app.emailer", None)
try:
    emailer = importlib.import_module("app.emailer")
finally:
    if _saved_mock is not None:
        sys.modules["app.emailer"] = _saved_mock


class TestSuspiciousLoginEscaping:
    def _capture_html(self, **kwargs):
        captured = {}

        class _FakeBackend:
            def send(self, *, to_email, to_name, subject, html, text):
                captured["html"] = html
                captured["text"] = text
                return emailer.SendResult(ok=True, provider_msg_id="x")

        with patch.object(emailer, "_pick_backend", return_value=_FakeBackend()):
            emailer.send_suspicious_login_email(**kwargs)
        return captured

    def test_malicious_user_agent_is_escaped(self):
        cap = self._capture_html(
            to_email="victim@example.com",
            to_name="Victim",
            ip="1.2.3.4",
            user_agent="<script>alert(document.cookie)</script>",
            when=datetime(2026, 6, 11, tzinfo=timezone.utc),
        )
        assert "<script>alert" not in cap["html"]
        assert "&lt;script&gt;" in cap["html"]

    def test_malicious_name_is_escaped(self):
        cap = self._capture_html(
            to_email="victim@example.com",
            to_name="<img src=x onerror=alert(1)>",
            ip="1.2.3.4",
            user_agent="Mozilla/5.0",
            when=datetime(2026, 6, 11, tzinfo=timezone.utc),
        )
        assert "<img src=x onerror" not in cap["html"]
        assert "&lt;img" in cap["html"]


class TestVerifyWebhookNoSecret:
    def test_unsigned_fails_closed_without_crashing(self, monkeypatch):
        monkeypatch.delenv("RESEND_WEBHOOK_SECRET", raising=False)
        headers = {"svix-id": "id", "svix-timestamp": "1", "svix-signature": "v1,abc"}
        # Must return False (fail closed), not raise NameError on `_log`.
        assert emailer.verify_webhook(b"{}", headers) is False
