"""Tests for app/emailer.py — transactional email dispatch.

Test strategy
─────────────
- Template renderers (pure functions) ⇨ real calls, assert output text.
- ``_pick_backend`` ⇨ real calls under patched env vars.
- Backend classes ⇨ real calls with import mocks so we don't actually
  emit HTTP calls or SMTP connections.
- Public send functions ⇨ mock ``_pick_backend`` to return a spy backend
  so we can verify the right arguments are passed without sending real
  email. Webhook verification is tested on its own (pure logic).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# conftest patches sys.modules["app.emailer"] with a MagicMock, so we
# must remove it before importing the real module.
import importlib as _il
if "app.emailer" in sys.modules:
    del sys.modules["app.emailer"]
import app.emailer as emailer
from app.emailer import SendResult
from tests.conftest import mock_cache  # noqa: F401


# ── Helpers ────────────────────────────────────────────────────────────

class SpyBackend(emailer._Backend):
    """Records the last send() call instead of sending real email."""
    def __init__(self):
        self.calls: list[dict] = []

    def send(self, **kw) -> SendResult:
        self.calls.append(kw)
        return SendResult(ok=True, provider_msg_id="spy-42")


@pytest.fixture
def spy_backend():
    sb = SpyBackend()
    with patch.object(emailer, "_pick_backend", return_value=sb):
        yield sb


# ── _pick_backend ─────────────────────────────────────────────────────

class TestPickBackend:
    def test_resend_when_configured(self):
        with patch.dict(os.environ, {"EMAIL_PROVIDER": "resend", "RESEND_API_KEY": "re_xxx"}, clear=True):
            backend = emailer._pick_backend()
            assert isinstance(backend, emailer._ResendBackend)

    def test_noop_when_no_api_key(self):
        with patch.dict(os.environ, {"EMAIL_PROVIDER": "resend"}, clear=True):
            backend = emailer._pick_backend()
            assert isinstance(backend, emailer._NoopBackend)

    def test_explicit_noop(self):
        with patch.dict(os.environ, {"EMAIL_PROVIDER": "noop"}, clear=True):
            backend = emailer._pick_backend()
            assert isinstance(backend, emailer._NoopBackend)

    def test_smtp(self):
        with patch.dict(os.environ, {"EMAIL_PROVIDER": "smtp"}, clear=True):
            backend = emailer._pick_backend()
            assert isinstance(backend, emailer._SmtpBackend)

    def test_unknown_provider_falls_to_resend_check(self):
        with patch.dict(os.environ, {"EMAIL_PROVIDER": "unknown"}, clear=True):
            backend = emailer._pick_backend()
            assert isinstance(backend, emailer._NoopBackend)  # no API key → noop


# ── _NoopBackend ─────────────────────────────────────────────────────

class TestNoopBackend:
    def test_send_returns_noop(self):
        backend = emailer._NoopBackend()
        r = backend.send(to_email="a@b.com", to_name="A", subject="S", html="<p>H</p>", text="H")
        assert r == SendResult(ok=True, provider_msg_id="noop")


# ── _ResendBackend ───────────────────────────────────────────────────

class TestResendBackend:
    @pytest.fixture
    def backend(self):
        return emailer._ResendBackend()

    def test_import_error(self, backend):
        with patch.dict(os.environ, {}, clear=True):
            r = backend.send(to_email="a@b.com", to_name="A", subject="S", html="<p>H</p>", text="H")
        assert r.ok is False
        assert "not installed" in (r.error or "")

    def test_no_api_key(self, backend):
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(emailer, "_Backend",), \
             patch("builtins.__import__", return_value=MagicMock()):
            r = backend.send(to_email="a@b.com", to_name="A", subject="S", html="<p>H</p>", text="H")
        assert r.ok is False
        assert "RESEND_API_KEY" in (r.error or "")

    def test_success(self, backend):
        fake_resend = MagicMock()
        fake_resend.Emails.send.return_value = {"id": "msg-1"}
        with patch.dict(os.environ, {"RESEND_API_KEY": "re_xxx"}, clear=True), \
             patch("builtins.__import__", side_effect=self._fake_import(fake_resend)):
            r = backend.send(to_email="a@b.com", to_name="A", subject="S", html="<p>H</p>", text="H")
        assert r == SendResult(ok=True, provider_msg_id="msg-1")

    def test_success_with_reply_to(self, backend):
        fake_resend = MagicMock()
        fake_resend.Emails.send.return_value = {"id": "msg-2"}
        with patch.dict(os.environ, {"RESEND_API_KEY": "re_xxx", "EMAIL_REPLY_TO": "support@procta.net"}, clear=True), \
             patch("builtins.__import__", side_effect=self._fake_import(fake_resend)):
            r = backend.send(to_email="b@b.com", to_name="B", subject="S", html="<p>H</p>", text="H")
        assert r.ok is True

    def test_with_attachments(self, backend):
        fake_resend = MagicMock()
        fake_resend.Emails.send.return_value = {"id": "msg-3"}
        with patch.dict(os.environ, {"RESEND_API_KEY": "re_xxx"}, clear=True), \
             patch("builtins.__import__", side_effect=self._fake_import(fake_resend)):
            r = backend.send(
                to_email="c@c.com", to_name="C", subject="S", html="<p>H</p>", text="H",
                attachments=[{"filename": "r.pdf", "content": b"PDF data"}],
            )
        assert r.ok is True

    def test_with_headers(self, backend):
        fake_resend = MagicMock()
        fake_resend.Emails.send.return_value = {"id": "msg-4"}
        with patch.dict(os.environ, {"RESEND_API_KEY": "re_xxx"}, clear=True), \
             patch("builtins.__import__", side_effect=self._fake_import(fake_resend)):
            r = backend.send(
                to_email="d@d.com", to_name="D", subject="S", html="<p>H</p>", text="H",
                headers={"List-Unsubscribe": "<https://unsub>"},
            )
        assert r.ok is True

    def test_api_error(self, backend):
        fake_resend = MagicMock()
        fake_resend.Emails.send.side_effect = RuntimeError("API down")
        with patch.dict(os.environ, {"RESEND_API_KEY": "re_xxx"}, clear=True), \
             patch("builtins.__import__", side_effect=self._fake_import(fake_resend)):
            r = backend.send(to_email="e@e.com", to_name="E", subject="S", html="<p>H</p>", text="H")
        assert r.ok is False
        assert "API down" in (r.error or "")

    def test_rate_limit_retry_succeeds(self, backend):
        fake_resend = MagicMock()
        fake_resend.Emails.send.side_effect = [
            RuntimeError("rate limit exceeded"),
            {"id": "msg-retry"},
        ]
        with patch.dict(os.environ, {"RESEND_API_KEY": "re_xxx"}, clear=True), \
             patch("builtins.__import__", side_effect=self._fake_import(fake_resend)), \
             patch("time.sleep"):
            r = backend.send(to_email="f@f.com", to_name="F", subject="S", html="<p>H</p>", text="H")
        assert r.ok is True
        assert r.provider_msg_id == "msg-retry"

    def test_rate_limit_retry_fails(self, backend):
        fake_resend = MagicMock()
        fake_resend.Emails.send.side_effect = [
            RuntimeError("rate limit exceeded"),
            RuntimeError("still failing"),
        ]
        with patch.dict(os.environ, {"RESEND_API_KEY": "re_xxx"}), \
             patch("builtins.__import__", side_effect=self._fake_import(fake_resend)), \
             patch("time.sleep"):
            r = backend.send(to_email="g@g.com", to_name="G", subject="S", html="<p>H</p>", text="H")
        assert r.ok is False
        assert "still failing" in (r.error or "")

    @staticmethod
    def _fake_import(fake_resend):
        def _import(name, *a, **kw):
            return fake_resend
        return _import


# ── _SmtpBackend ─────────────────────────────────────────────────────

class TestSmtpBackend:
    @pytest.fixture
    def backend(self):
        return emailer._SmtpBackend()

    def test_success(self, backend):
        with patch("smtplib.SMTP") as mock_smtp:
            with patch.dict(os.environ, {}, clear=True):
                r = backend.send(to_email="a@b.com", to_name="A", subject="S", html="<p>H</p>", text="H")
        assert r.ok is True
        mock_smtp.return_value.__enter__.return_value.sendmail.assert_called_once()

    def test_with_headers_and_attachments(self, backend):
        with patch("smtplib.SMTP") as mock_smtp:
            with patch.dict(os.environ, {}, clear=True):
                r = backend.send(
                    to_email="b@b.com", to_name="B", subject="S", html="<p>H</p>", text="H",
                    headers={"X-Custom": "val"},
                    attachments=[{"filename": "r.pdf", "content": b"PDF data"}],
                )
        assert r.ok is True

    def test_with_login(self, backend):
        with patch("smtplib.SMTP") as mock_smtp:
            with patch.dict(os.environ, {"SMTP_USER": "user", "SMTP_PASS": "pass"}, clear=True):
                r = backend.send(to_email="c@c.com", to_name="C", subject="S", html="<p>H</p>", text="H")
        assert r.ok is True
        mock_smtp.return_value.__enter__.return_value.login.assert_called_once_with("user", "pass")

    def test_failure(self, backend):
        with patch("smtplib.SMTP") as mock_smtp:
            mock_smtp.return_value.__enter__.return_value.sendmail.side_effect = RuntimeError("SMTP down")
            with patch.dict(os.environ, {}, clear=True):
                r = backend.send(to_email="d@d.com", to_name="D", subject="S", html="<p>H</p>", text="H")
        assert r.ok is False
        assert "SMTP down" in (r.error or "")


# ── _send ─────────────────────────────────────────────────────────────

class TestSend:
    def test_success(self):
        backend = SpyBackend()
        with patch.object(emailer, "_pick_backend", return_value=backend):
            r = emailer._send("a@b.com", "S", "<p>H</p>", "H", to_name="A")
        assert r == SendResult(ok=True, provider_msg_id="spy-42")

    def test_backend_raise(self):
        class BoomBackend(emailer._Backend):
            def send(self, **kw):
                raise RuntimeError("boom")
        with patch.object(emailer, "_pick_backend", return_value=BoomBackend()):
            r = emailer._send("a@b.com", "S", "<p>H</p>", "H")
        assert r.ok is False
        assert "boom" in (r.error or "")


# ── verify_webhook ───────────────────────────────────────────────────

class TestVerifyWebhook:
    def _valid_headers(self, secret: str, raw_body: bytes, ts: int | None = None):
        if ts is None:
            ts = int(time.time())
        key_material = secret[6:] if secret.startswith("whsec_") else secret
        key = base64.b64decode(key_material)
        signed = f"msg_id.{ts}.".encode() + raw_body
        sig = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
        return {"svix-id": "msg_id", "svix-timestamp": str(ts), "svix-signature": f"v1,{sig}"}

    def test_no_signature_returns_false(self):
        assert emailer.verify_webhook(b"{}", {}) is False

    def test_no_secret_returns_false(self):
        with patch.dict(os.environ, {}, clear=True):
            r = emailer.verify_webhook(b"{}", {"svix-signature": "v1,abc"})
        assert r is False

    def test_missing_id_returns_false(self):
        with patch.dict(os.environ, {"RESEND_WEBHOOK_SECRET": "dGVzdA=="}):
            r = emailer.verify_webhook(b"{}", {"svix-signature": "v1,abc", "svix-timestamp": "123"})
        assert r is False

    def test_old_timestamp_returns_false(self):
        with patch.dict(os.environ, {"RESEND_WEBHOOK_SECRET": "dGVzdA=="}):
            r = emailer.verify_webhook(b"{}", {"svix-signature": "v1,abc", "svix-id": "x", "svix-timestamp": "1"})
        assert r is False

    def test_valid_signature_returns_true(self):
        secret = base64.b64encode(b"my-secret-key-32bytes!").decode()
        raw = json.dumps({"event": "email.delivered"}).encode()
        headers = self._valid_headers(secret, raw)
        with patch.dict(os.environ, {"RESEND_WEBHOOK_SECRET": secret}):
            r = emailer.verify_webhook(raw, headers)
        assert r is True

    def test_whsec_prefix_stripped(self):
        raw_secret = base64.b64encode(b"my-secret-key-32bytes!").decode()
        secret = "whsec_" + raw_secret
        raw = json.dumps({"event": "email.delivered"}).encode()
        headers = self._valid_headers(secret, raw)
        with patch.dict(os.environ, {"RESEND_WEBHOOK_SECRET": secret}):
            r = emailer.verify_webhook(raw, headers)
        assert r is True

    def test_exception_returns_false(self):
        with patch.dict(os.environ, {"RESEND_WEBHOOK_SECRET": "dGVzdA=="}):
            headers = {"svix-id": "x", "svix-timestamp": "not-an-int", "svix-signature": "v1,abc"}
            r = emailer.verify_webhook(b"{}", headers)
        assert r is False

    def test_no_matching_version_returns_false(self):
        secret = base64.b64encode(b"my-secret-key-32bytes!").decode()
        raw = json.dumps({"event": "email.delivered"}).encode()
        headers = self._valid_headers(secret, raw)
        # Change the version to v2
        headers["svix-signature"] = "v2," + headers["svix-signature"].split(",", 1)[1]
        with patch.dict(os.environ, {"RESEND_WEBHOOK_SECRET": secret}):
            r = emailer.verify_webhook(raw, headers)
        assert r is False


# ── Template renderers ───────────────────────────────────────────────

class TestRenderInvite:
    def test_minimal(self):
        html, text = emailer._render_invite(invite_url="https://invite")
        assert "https://invite" in html
        assert "https://invite" in text
        assert "Student" in html

    def test_with_all_fields(self):
        html, text = emailer._render_invite(
            to_name="Bob", exam_title="Math Final", invite_url="https://go",
            download_url="https://dl", roll_number="R123",
            registration_url="https://reg", access_code="ABC123",
            exam_starts_at="10:00 AM", exam_ends_at="12:00 PM",
            custom_message="Good luck!", teacher_name="Mr. Smith",
        )
        assert "Bob" in html
        assert "Math Final" in html
        assert "ABC123" in text
        assert "10:00 AM" in text
        assert "12:00 PM" in text
        assert "Good luck!" in html
        assert "https://reg" in html


class TestRenderReminder:
    def test_24h_reminder(self):
        html, text = emailer._render_reminder(
            to_name="Alice", exam_title="Physics", invite_url="https://go",
            roll_number="R1", hours_until=24,
            exam_starts_at_display="Tomorrow at 10 AM",
        )
        assert "tomorrow" in html.lower() or "24" in html
        assert "Alice" in html

    def test_1h_reminder(self):
        html, text = emailer._render_reminder(
            to_name="Alice", exam_title="Physics", invite_url="https://go",
            roll_number="R1", hours_until=1,
            exam_starts_at_display="In 1 hour",
        )
        assert "Alice" in text

    def test_with_access_code(self):
        html, text = emailer._render_reminder(
            to_name="Alice", exam_title="Physics", invite_url="https://go",
            roll_number="R1", hours_until=1,
            exam_starts_at_display="10 AM",
            access_code="XYZ789",
        )
        assert "XYZ789" in text
        assert "XYZ789" in html


class TestRenderScorecard:
    def test_passed(self):
        html, text = emailer._render_scorecard_email(
            to_name="Alice", exam_title="Math", score=8, total=10,
            percentage=80.0, passed=True,
        )
        assert "8" in html
        assert "10" in html
        assert "passed" in html.lower() or "80" in html

    def test_failed(self):
        html, text = emailer._render_scorecard_email(
            to_name="Bob", exam_title="Math", score=4, total=10,
            percentage=40.0, passed=False,
        )
        assert "4" in text

    def test_with_custom_message(self):
        html, text = emailer._render_scorecard_email(
            to_name="Alice", exam_title="Math", score=8, total=10,
            percentage=80.0, passed=True, custom_message="Great job!",
        )
        assert "Great job!" in html


# ── Public send functions (via spy backend) ─────────────────────────

class TestSendInviteEmail:
    def test_success(self, spy_backend):
        r = emailer.send_invite_email(
            to_email="a@b.com", to_name="A", exam_title="Math",
            invite_url="https://invite", download_url="https://dl",
            roll_number="R1", teacher_name="Mr. T",
        )
        assert r == SendResult(ok=True, provider_msg_id="spy-42")
        assert len(spy_backend.calls) == 1
        assert spy_backend.calls[0]["to_email"] == "a@b.com"

    def test_backend_failure(self):
        class FailBackend(emailer._Backend):
            def send(self, **kw):
                raise RuntimeError("send fail")
        with patch.object(emailer, "_pick_backend", return_value=FailBackend()):
            r = emailer.send_invite_email(
                to_email="a@b.com", to_name="A", exam_title="Math",
                invite_url="https://invite", download_url="https://dl",
                roll_number="R1",
            )
        assert r.ok is False


class TestSendCohortLinkEmail:
    def test_success(self, spy_backend):
        r = emailer.send_cohort_link_email(
            to_email="a@b.com", to_name="A", cohort_url="https://cohort",
            download_url="https://dl", batch="Batch 1",
        )
        assert r.ok is True
        assert "Batch 1" in spy_backend.calls[0]["html"]


class TestSendTrialStarted:
    def test_success(self, spy_backend):
        r = emailer.send_trial_started_email(
            to_email="a@b.com", to_name="A", plan="Pro",
            trial_end="2026-07-15", billing_url="https://billing",
        )
        assert r.ok is True


class TestSendExamReminder:
    def test_24h(self, spy_backend):
        r = emailer.send_exam_reminder(
            to_email="a@b.com", to_name="A", exam_title="Math",
            invite_url="https://invite", roll_number="R1",
            hours_until=24, exam_starts_at_display="Tomorrow",
        )
        assert r.ok is True
        assert "tomorrow" in spy_backend.calls[0]["subject"].lower()

    def test_1h(self, spy_backend):
        r = emailer.send_exam_reminder(
            to_email="a@b.com", to_name="A", exam_title="Math",
            invite_url="https://invite", roll_number="R1",
            hours_until=1, exam_starts_at_display="In 1 hour",
        )
        assert r.ok is True
        assert "1 hour" in spy_backend.calls[0]["subject"].lower()


class TestSendScorecardEmail:
    def test_passed(self, spy_backend):
        r = emailer.send_scorecard_email(
            to_email="a@b.com", to_name="A", exam_title="Math",
            score=8, total=10, percentage=80.0, passed=True,
            pdf_bytes=b"PDF", pdf_filename="r.pdf",
        )
        assert r.ok is True
        assert "passed" in spy_backend.calls[0]["subject"]

    def test_failed_no_pdf(self, spy_backend):
        r = emailer.send_scorecard_email(
            to_email="b@b.com", to_name="B", exam_title="Math",
            score=3, total=10, percentage=30.0, passed=False,
            pdf_bytes=b"", pdf_filename="r.pdf",
        )
        assert r.ok is True
        assert "results" in spy_backend.calls[0]["subject"]


class TestSendDemoRequest:
    def test_no_admin_email(self):
        with patch.dict(os.environ, {}, clear=True):
            r = emailer.send_demo_request_notification(
                name="A", email="a@b.com", institution="MIT", role="teacher",
            )
        assert r.ok is False

    def test_success(self, spy_backend):
        with patch.dict(os.environ, {"SUPER_ADMIN_EMAIL": "admin@procta.net"}):
            r = emailer.send_demo_request_notification(
                name="A", email="a@b.com", institution="MIT",
                role="teacher", message="Interested",
            )
        assert r.ok is True

    def test_error(self):
        class FailBackend(emailer._Backend):
            def send(self, **kw):
                raise RuntimeError("fail")
        with patch.dict(os.environ, {"SUPER_ADMIN_EMAIL": "admin@procta.net"}), \
             patch.object(emailer, "_pick_backend", return_value=FailBackend()):
            r = emailer.send_demo_request_notification(
                name="A", email="a@b.com", institution="MIT", role="teacher",
            )
        assert r.ok is False


class TestSendCohortLinkError:
    def test_backend_error(self):
        class FailBackend(emailer._Backend):
            def send(self, **kw):
                raise RuntimeError("cohort fail")
        with patch.object(emailer, "_pick_backend", return_value=FailBackend()):
            r = emailer.send_cohort_link_email(
                to_email="a@b.com", to_name="A", cohort_url="https://cohort",
                download_url="https://dl", batch="B1",
            )
        assert r.ok is False


class TestSendTrialStartedError:
    def test_backend_error(self):
        class FailBackend(emailer._Backend):
            def send(self, **kw):
                raise RuntimeError("trial fail")
        with patch.object(emailer, "_pick_backend", return_value=FailBackend()):
            r = emailer.send_trial_started_email(
                to_email="a@b.com", to_name="A", plan="Pro",
                trial_end="2026-07-15", billing_url="https://billing",
            )
        assert r.ok is False


class TestSendExamReminderError:
    def test_backend_error(self):
        class FailBackend(emailer._Backend):
            def send(self, **kw):
                raise RuntimeError("reminder fail")
        with patch.object(emailer, "_pick_backend", return_value=FailBackend()):
            r = emailer.send_exam_reminder(
                to_email="a@b.com", to_name="A", exam_title="Math",
                invite_url="https://invite", roll_number="R1",
                hours_until=1, exam_starts_at_display="Soon",
            )
        assert r.ok is False

    def test_unsubscribe_header_failure_still_sends(self):
        """If building the unsubscribe header fails, the email still sends."""
        class OkBackend(emailer._Backend):
            def send(self, **kw):
                return SendResult(ok=True, provider_msg_id="ok")
        with patch.object(emailer, "_pick_backend", return_value=OkBackend()):
            with patch("app.services.local_auth.issue_unsubscribe_token", side_effect=RuntimeError("token fail")):
                r = emailer.send_exam_reminder(
                    to_email="a@b.com", to_name="A", exam_title="Math",
                    invite_url="https://invite", roll_number="R1",
                    hours_until=1, exam_starts_at_display="Soon",
                    student_id="s1",
                )
        assert r.ok is True


class TestSendScorecardError:
    def test_backend_error(self):
        class FailBackend(emailer._Backend):
            def send(self, **kw):
                raise RuntimeError("scorecard fail")
        with patch.object(emailer, "_pick_backend", return_value=FailBackend()):
            r = emailer.send_scorecard_email(
                to_email="a@b.com", to_name="A", exam_title="Math",
                score=8, total=10, percentage=80.0, passed=True,
                pdf_bytes=b"", pdf_filename="r.pdf",
            )
        assert r.ok is False


class TestPaymentFailedError:
    def test_backend_error(self):
        """The outer try/except in send_payment_failed_notification catches
        exceptions from _send (defensive — _send normally never raises)."""
        with patch.object(emailer, "_send", side_effect=RuntimeError("payment fail")):
            r = emailer.send_payment_failed_notification("a@b.com", "A")
        assert r.ok is False


class TestNewAccountNotificationError:
    def test_backend_error(self):
        with patch.object(emailer, "_send", side_effect=RuntimeError("account fail")):
            r = emailer.send_new_account_notification(
                account_type="teacher", name="A", email="a@b.com",
            )
        assert r.ok is False


class TestOrgInviteError:
    def test_backend_error(self):
        with patch.object(emailer, "_send", side_effect=RuntimeError("org fail")):
            r = emailer.send_org_invite_email(
                to_email="a@b.com", invite_url="https://invite",
                org_name="Org", invited_by_name="Bob",
            )
        assert r.ok is False


class TestBackendAbstract:
    def test_raises_not_implemented(self):
        b = emailer._Backend()
        with pytest.raises(NotImplementedError):
            b.send(to_email="a@b.com", to_name="A", subject="S", html="", text="")


class TestResetBackendForTests:
    def test_returns_none(self):
        assert emailer._reset_backend_for_tests() is None


class TestSendFunctionsViaSend:
    """These functions delegate to _send, which is tested separately.
    Smoke-test each to confirm argument plumbing works."""

    def test_email_verification(self, spy_backend):
        r = emailer.send_email_verification("a@b.com", "A", "https://verify")
        assert r.ok is True

    def test_password_reset(self, spy_backend):
        r = emailer.send_password_reset_email("a@b.com", "A", "https://reset")
        assert r.ok is True

    def test_2fa_otp(self, spy_backend):
        r = emailer.send_2fa_otp_email("a@b.com", "A", "123456")
        assert r.ok is True

    def test_suspicious_login(self, spy_backend):
        r = emailer.send_suspicious_login_email(
            to_email="a@b.com", to_name="A", ip="1.2.3.4",
            user_agent="Chrome", when="2026-06-30",
        )
        assert r.ok is True

    def test_payment_failed(self, spy_backend):
        r = emailer.send_payment_failed_notification("a@b.com", "A")
        assert r.ok is True

    def test_new_account(self, spy_backend):
        r = emailer.send_new_account_notification(
            account_type="teacher", name="Alice", email="a@b.com",
        )
        assert r.ok is True

    def test_org_invite(self, spy_backend):
        r = emailer.send_org_invite_email(
            to_email="a@b.com", invite_url="https://invite",
            org_name="Test Org", invited_by_name="Bob",
        )
        assert r.ok is True

    def test_student_deleted(self, spy_backend):
        r = emailer.send_student_account_deleted_to_teacher(
            to_email="a@b.com", to_name="T", student_name="S",
            student_email="s@b.com", student_roll="R1",
            deleted_at_str="2026-06-30",
        )
        assert r.ok is True

    def test_email_change_heads_up(self, spy_backend):
        r = emailer.send_student_email_change_heads_up(
            to_email="a@b.com", to_name="T", new_email="new@b.com",
            requested_at_str="2026-06-30",
        )
        assert r.ok is True

    def test_password_changed(self, spy_backend):
        r = emailer.send_student_password_changed_notification(
            to_email="a@b.com", to_name="T", changed_at_str="2026-06-30",
        )
        assert r.ok is True

    def test_controller_breach(self, spy_backend):
        r = emailer.send_controller_breach_notification(
            to_email="a@b.com", to_name="T", org_name="Org",
            description="desc", data_categories="emails",
            discovered_at="2026-06-30",
        )
        assert r.ok is True

    def test_data_subject_breach(self, spy_backend):
        r = emailer.send_data_subject_breach_notification(
            to_email="a@b.com", to_name="T", description="desc",
            data_categories="emails",
        )
        assert r.ok is True

    def test_objection_to_controller(self, spy_backend):
        r = emailer.send_objection_to_controller_notice(
            to_email="a@b.com", to_name="T", org_name="Org",
            user_type="student", grounds="privacy", scope="all",
        )
        assert r.ok is True

    def test_guardian_consent(self, spy_backend):
        r = emailer.send_guardian_consent_request(
            to_email="a@b.com", to_name="T", student_name="Kid",
            consent_url="https://consent",
        )
        assert r.ok is True
