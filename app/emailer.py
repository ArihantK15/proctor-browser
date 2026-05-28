"""
Transactional email abstraction.

Procta ships with a Resend backend because Resend has the cleanest DX
and a generous free tier, but every public function here is
provider-agnostic. To swap providers:

  1. Implement another `_Backend` subclass.
  2. Point `_pick_backend()` at it based on EMAIL_PROVIDER env var.
  3. Leave the rest of the codebase alone — callers only touch
     ``send_invite_email()`` / ``verify_webhook()``.

Environment variables consumed:

  EMAIL_PROVIDER          resend | smtp | noop     (default: resend)
  RESEND_API_KEY          re_xxx... token (required for resend)
  RESEND_WEBHOOK_SECRET   for bounce webhook HMAC verification
  EMAIL_FROM              invites@procta.net       (default)
  EMAIL_FROM_NAME         Procta                    (default)
  EMAIL_REPLY_TO          support@procta.net        (default None)
  INVITE_BASE_URL         https://app.procta.net    (default — landing page lives at /invite/<token>)

In dev / CI we fall back to the 'noop' backend which logs instead of
sending so tests can run offline and local dev doesn't burn free-tier
quota. Set EMAIL_PROVIDER=noop explicitly if you want that behaviour
with the key present.
"""
from __future__ import annotations

from .log_safe import safe
import base64
import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

from .utils import _html_escape as _esc

log = logging.getLogger(__name__)


# ─── Public surface ─────────────────────────────────────────────────

@dataclass
class SendResult:
    """What callers get back from send_invite_email."""
    ok: bool
    provider_msg_id: Optional[str] = None
    error: Optional[str] = None


def send_invite_email(
    *,
    to_email: str,
    to_name: str,
    exam_title: str,
    invite_url: str,
    download_url: str,
    roll_number: str,
    access_code: Optional[str] = None,
    exam_starts_at: Optional[str] = None,
    exam_ends_at: Optional[str] = None,
    custom_message: Optional[str] = None,
    teacher_name: Optional[str] = None,
) -> SendResult:
    """Send a single invite. Never raises — returns SendResult(ok=False)
    so callers can mark the invite as 'failed' and move on.
    """
    html, text = _render_invite(
        to_name=to_name,
        exam_title=exam_title,
        invite_url=invite_url,
        download_url=download_url,
        roll_number=roll_number,
        access_code=access_code,
        exam_starts_at=exam_starts_at,
        exam_ends_at=exam_ends_at,
        custom_message=custom_message,
        teacher_name=teacher_name,
    )
    subject = f"{exam_title} — your Procta exam invite"
    try:
        backend = _pick_backend()
        return backend.send(
            to_email=to_email,
            to_name=to_name,
            subject=subject,
            html=html,
            text=text,
        )
    except Exception as e:  # never leak exceptions to caller
        log.exception("send_invite_email failed: %s", e)
        return SendResult(ok=False, error=str(e))


def _reset_backend_for_tests() -> None:
    """Compatibility hook for tests that swap email/webhook env vars."""
    return None


def verify_webhook(raw_body: bytes, headers) -> bool:
    """Verify Resend/Svix webhook signatures.

    In development without ``RESEND_WEBHOOK_SECRET`` configured, require a
    non-empty signature header so unsigned calls still fail closed.
    """
    svix_id = headers.get("svix-id") if hasattr(headers, "get") else ""
    svix_ts = headers.get("svix-timestamp") if hasattr(headers, "get") else ""
    svix_sig = headers.get("svix-signature") if hasattr(headers, "get") else ""
    if not svix_sig:
        return False

    secret = os.environ.get("RESEND_WEBHOOK_SECRET", "")
    if not secret:
        return bool(svix_sig)
    if not svix_id or not svix_ts:
        return False
    try:
        ts = int(svix_ts)
        if abs(int(time.time()) - ts) > 5 * 60:
            return False
        key_material = secret[6:] if secret.startswith("whsec_") else secret
        key = base64.b64decode(key_material)
        signed = f"{svix_id}.{svix_ts}.".encode() + raw_body
        expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
        for part in svix_sig.split():
            version, _, candidate = part.partition(",")
            if version == "v1" and hmac.compare_digest(candidate, expected):
                return True
    except Exception:
        return False
    return False


def send_exam_reminder(
    *,
    to_email: str,
    to_name: str,
    exam_title: str,
    invite_url: str,
    roll_number: str,
    hours_until: int,             # 1 or 24 — drives copy
    exam_starts_at_display: str,  # already-formatted IST string
    access_code: Optional[str] = None,
    teacher_name: Optional[str] = None,
) -> SendResult:
    """Send a "your exam starts in N hours" reminder.

    Same contract as ``send_invite_email`` — never raises, returns
    SendResult(ok=False) on provider failure so the reminder loop can
    retry on the next tick (the ``reminder_XX_at`` timestamp is only
    written AFTER this returns ok, so a failed send leaves the row
    claimable again)."""
    html, text = _render_reminder(
        to_name=to_name,
        exam_title=exam_title,
        invite_url=invite_url,
        roll_number=roll_number,
        hours_until=hours_until,
        exam_starts_at_display=exam_starts_at_display,
        access_code=access_code,
        teacher_name=teacher_name,
    )
    if hours_until >= 24:
        subject = f"{exam_title} — starts tomorrow"
    else:
        subject = f"{exam_title} — starts in 1 hour"
    try:
        backend = _pick_backend()
        return backend.send(
            to_email=to_email,
            to_name=to_name,
            subject=subject,
            html=html,
            text=text,
        )
    except Exception as e:
        log.exception("send_exam_reminder failed: %s", e)
        return SendResult(ok=False, error=str(e))


def send_scorecard_email(
    *,
    to_email: str,
    to_name: str,
    exam_title: str,
    score: int,
    total: int,
    percentage: float,
    passed: bool,
    pdf_bytes: bytes,
    pdf_filename: str,
    teacher_name: Optional[str] = None,
    custom_message: Optional[str] = None,
) -> SendResult:
    """Email a student their graded scorecard with the PDF attached.

    Triggered by the teacher pressing "Email all scorecards" after
    results are published. Same no-raise contract as the other
    senders — the caller (a bulk loop over completed sessions) needs
    a partial-failure-tolerant API so one bad address doesn't kill
    the whole batch. The endpoint records ``scorecard_emailed_at``
    only when this returns ok=True."""
    html, text = _render_scorecard_email(
        to_name=to_name,
        exam_title=exam_title,
        score=score,
        total=total,
        percentage=percentage,
        passed=passed,
        teacher_name=teacher_name,
        custom_message=custom_message,
    )
    verdict = "passed" if passed else "results"
    subject = f"{exam_title} — your {verdict}"
    try:
        backend = _pick_backend()
        return backend.send(
            to_email=to_email,
            to_name=to_name,
            subject=subject,
            html=html,
            text=text,
            attachments=[{
                "filename": pdf_filename,
                "content": pdf_bytes,
            }] if pdf_bytes else None,
        )
    except Exception as e:
        log.exception("send_scorecard_email failed: %s", e)
        return SendResult(ok=False, error=str(e))


# ─── Admin notifications ─────────────────────────────────────────────

def send_demo_request_notification(
    *,
    name: str,
    email: str,
    institution: str,
    role: str,
    message: str = "",
) -> SendResult:
    """Email the super admin with full demo request details.

    Never raises — returns SendResult(ok=False) so the form response
    isn't blocked by a notification failure."""
    admin_email = os.environ.get("SUPER_ADMIN_EMAIL", "").strip().lower()
    if not admin_email:
        return SendResult(ok=False, error="SUPER_ADMIN_EMAIL not set")

    subject = f"[Procta] Demo request from {name}"

    role_labels = {
        "faculty": "Faculty / Professor",
        "admin": "Exam Administrator",
        "it": "IT Department",
        "management": "Management",
        "hr": "HR / Recruitment",
        "other": "Other",
    }
    role_display = role_labels.get(role, role)

    text_lines = [
        "New demo request received:",
        "",
        f"Name:        {name}",
        f"Email:       {email}",
        f"Institution: {institution}",
        f"Role:        {role_display}",
    ]
    if message:
        text_lines += ["", "Message:", message, ""]
    text = "\n".join(text_lines)

    message_block = (
        f'<div style="background:#fff7ed;border-left:3px solid #f59e0b;'
        f'padding:12px 16px;margin:16px 0;border-radius:6px;color:#78350f;'
        f'font-size:14px;line-height:1.5;">'
        f'<div style="font-weight:600;margin-bottom:4px;color:#92400e;">'
        f'Message</div>'
        f'{_esc(message).replace(chr(10), "<br>")}'
        f'</div>'
        if message else ""
    )

    html = f"""\
<!doctype html>
<html><head><meta charset="utf-8"><title>Demo request — Procta</title></head>
<body style="margin:0;padding:0;background:#0f172a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
         style="background:#0f172a;padding:32px 16px;">
    <tr><td align="center">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="560"
             style="background:#ffffff;border-radius:16px;overflow:hidden;max-width:560px;">
        <tr><td style="background:linear-gradient(135deg,#3b82f6,#8b5cf6);padding:28px 32px;">
          <div style="color:#ffffff;font-size:12px;letter-spacing:2px;font-weight:600;opacity:.9;">PROCTA · DEMO REQUEST</div>
          <div style="color:#ffffff;font-size:22px;font-weight:700;margin-top:6px;">New demo request</div>
        </td></tr>
        <tr><td style="padding:32px;color:#0f172a;">
          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
                 style="background:#f8fafc;border-radius:10px;padding:16px 18px;margin:0 0 16px 0;border:1px solid #e2e8f0;">
            <tr><td style="padding:6px 0;font-size:15px;color:#334155;"><b>Name</b><br>{_esc(name)}</td></tr>
            <tr><td style="padding:6px 0;font-size:15px;color:#334155;"><b>Email</b><br>{_esc(email)}</td></tr>
            <tr><td style="padding:6px 0;font-size:15px;color:#334155;"><b>Institution</b><br>{_esc(institution)}</td></tr>
            <tr><td style="padding:6px 0;font-size:15px;color:#334155;"><b>Role</b><br>{_esc(role_display)}</td></tr>
          </table>
          {message_block}
          <p style="margin:16px 0 0 0;color:#94a3b8;font-size:12px;">
            View all demo requests in the
            <a href="https://app.procta.net/dashboard" style="color:#3b82f6;">admin dashboard</a>.
          </p>
        </td></tr>
        <tr><td style="padding:24px 0 0;text-align:center;font-size:11px;color:#94a3b8;border-top:1px solid #e2e8f0">
          Sent via <a href="https://procta.net" style="color:#64748b;text-decoration:none;font-weight:600">Procta</a>
          &nbsp;·&nbsp;
          Proctored exams for Indian institutions
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>
"""
    try:
        backend = _pick_backend()
        return backend.send(
            to_email=admin_email,
            to_name="Procta Admin",
            subject=subject,
            html=html,
            text=text,
        )
    except Exception as e:
        log.exception("send_demo_request_notification failed: %s", e)
        return SendResult(ok=False, error=str(e))


def send_email_verification(to_email: str, to_name: str, verify_url: str) -> SendResult:
    """Send an email verification link to a new teacher or student."""
    subject = "Verify your email address — Procta"
    text = f"""Hello {to_name},

Please verify your email address by clicking the link below:

{verify_url}

This link expires in 24 hours. If you did not sign up for Procta, you can ignore this email.

— The Procta Team
"""
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Verify your email — Procta</title></head>
<body style="margin:0;padding:0;background:#0f172a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
         style="background:#0f172a;padding:32px 16px;">
    <tr><td align="center">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="480"
             style="background:#ffffff;border-radius:12px;overflow:hidden;max-width:480px;">
        <tr><td style="background:linear-gradient(135deg,#5b8af0,#4a78dc);padding:24px 28px;text-align:center;">
          <div style="color:#ffffff;font-size:18px;font-weight:700;">Verify your email</div>
        </td></tr>
        <tr><td style="padding:28px;color:#0f172a;">
          <p style="margin:0 0 16px;font-size:15px;line-height:1.5;">Hello <strong>{to_name}</strong>,</p>
          <p style="margin:0 0 16px;font-size:15px;line-height:1.5;">Please verify your email address to continue using Procta. Click the button below:</p>
          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"><tr><td align="center" style="padding:8px 0 20px;">
            <a href="{verify_url}" target="_blank" style="display:inline-block;padding:12px 32px;border-radius:6px;background:#5b8af0;color:#ffffff;font-size:15px;font-weight:600;text-decoration:none;">Verify Email</a>
          </td></tr></table>
          <p style="margin:0 0 12px;font-size:13px;color:#555;line-height:1.4;">Or copy this link: <span style="font-size:12px;color:#5b8af0;word-break:break-all;font-family:monospace;">{verify_url}</span></p>
          <p style="margin:0;font-size:12px;color:#999;line-height:1.4;">This link expires in 24 hours. If you did not sign up for Procta, please ignore this email.</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""
    return _send(to_email, subject, html, text)


def send_password_reset_email(to_email: str, to_name: str, reset_url: str) -> SendResult:
    """Send a local-auth password reset link."""
    subject = "Reset your Procta password"
    display_name = to_name or "there"
    text = f"""Hello {display_name},

We received a request to reset your Procta password.

{reset_url}

This link expires in 30 minutes. If you did not request this, you can ignore this email.

— The Procta Team
"""
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Reset your password — Procta</title></head>
<body style="margin:0;padding:0;background:#0f172a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
         style="background:#0f172a;padding:32px 16px;">
    <tr><td align="center">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="480"
             style="background:#ffffff;border-radius:12px;overflow:hidden;max-width:480px;">
        <tr><td style="background:#3b82f6;padding:24px 28px;text-align:center;">
          <div style="color:#ffffff;font-size:18px;font-weight:700;">Reset your password</div>
        </td></tr>
        <tr><td style="padding:28px;color:#0f172a;">
          <p style="margin:0 0 16px;font-size:15px;line-height:1.5;">Hello <strong>{_esc(display_name)}</strong>,</p>
          <p style="margin:0 0 16px;font-size:15px;line-height:1.5;">We received a request to reset your Procta password. Click the button below to choose a new password.</p>
          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"><tr><td align="center" style="padding:8px 0 20px;">
            <a href="{reset_url}" target="_blank" style="display:inline-block;padding:12px 32px;border-radius:6px;background:#3b82f6;color:#ffffff;font-size:15px;font-weight:600;text-decoration:none;">Reset Password</a>
          </td></tr></table>
          <p style="margin:0 0 12px;font-size:13px;color:#555;line-height:1.4;">Or copy this link: <span style="font-size:12px;color:#3b82f6;word-break:break-all;font-family:monospace;">{reset_url}</span></p>
          <p style="margin:0;font-size:12px;color:#999;line-height:1.4;">This link expires in 30 minutes. If you did not request this, please ignore this email.</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""
    return _send(to_email, subject, html, text)


def send_2fa_otp_email(to_email: str, to_name: str, code: str) -> SendResult:
    """Email a 6-digit 2FA code during the login flow.

    Triggered when a user with email_2fa_enabled_at attempts to log
    in — server generates an email_otps row via email_otp.issue() and
    calls this helper to deliver the raw code.

    Code TTL is enforced server-side (email_otp.OTP_TTL_MINUTES=10);
    we just mention it in the message so the user knows they have a
    bounded window to use it.
    """
    subject = "Your Procta login code"
    display_name = to_name or "there"
    text = f"""Hello {display_name},

Your two-factor authentication code is:

    {code}

This code expires in 10 minutes. Enter it on the Procta login page to finish signing in.

If you did not try to log in, please ignore this email and consider changing your password.

— The Procta Team
"""
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Your Procta login code</title></head>
<body style="margin:0;padding:0;background:#0f172a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
         style="background:#0f172a;padding:32px 16px;">
    <tr><td align="center">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="480"
             style="background:#ffffff;border-radius:12px;overflow:hidden;max-width:480px;">
        <tr><td style="background:linear-gradient(135deg,#5b8af0,#4a78dc);padding:24px 28px;text-align:center;">
          <div style="color:#ffffff;font-size:18px;font-weight:700;">Your login code</div>
        </td></tr>
        <tr><td style="padding:28px;color:#0f172a;">
          <p style="margin:0 0 16px;font-size:15px;line-height:1.5;">Hello <strong>{display_name}</strong>,</p>
          <p style="margin:0 0 16px;font-size:15px;line-height:1.5;">Use this code to finish signing in to Procta:</p>
          <div style="text-align:center;margin:24px 0;">
            <div style="display:inline-block;padding:18px 32px;background:#f1f5f9;border-radius:8px;font-size:32px;font-weight:700;letter-spacing:8px;font-family:'SFMono-Regular',Menlo,Consolas,monospace;color:#0f172a;">{code}</div>
          </div>
          <p style="margin:0 0 12px;font-size:13px;color:#555;line-height:1.4;">This code expires in 10 minutes.</p>
          <p style="margin:0;font-size:12px;color:#999;line-height:1.4;">If you did not try to log in, please ignore this email and consider changing your password.</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""
    return _send(to_email, subject, html, text)


def send_suspicious_login_email(
    *,
    to_email: str,
    to_name: str,
    ip: str,
    user_agent: str,
    when,
) -> SendResult:
    """Heads-up email: someone logged in from a new device/location.

    Not a block — informational. If it was the user, they ignore it.
    If it wasn't, they reset their password + check active sessions.
    """
    # Format the timestamp in a human-readable way; the email client
    # will localise it again via the timestamp itself for screen readers
    # but we provide both for plain-text fallbacks.
    when_str = when.strftime("%d %b %Y, %I:%M %p UTC") if hasattr(when, "strftime") else str(when)
    # Truncate UA — full strings are 200+ chars and unreadable.
    ua_short = (user_agent or "Unknown browser")[:120]
    subject = "New sign-in to your Procta account"
    text = f"""Hello {to_name},

We noticed a new sign-in to your Procta account from a device or
location we haven't seen before.

  When:    {when_str}
  IP:      {ip or 'unknown'}
  Browser: {ua_short}

If this was you, no action is needed.

If it was NOT you:
  1. Sign in to Procta and change your password immediately.
  2. Review active sessions at app.procta.net/account/security
     and revoke any you don't recognise.
  3. Enable two-factor authentication if you haven't already.

— The Procta Security Team
"""
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>New sign-in — Procta</title></head>
<body style="margin:0;padding:0;background:#0f172a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
         style="background:#0f172a;padding:32px 16px;">
    <tr><td align="center">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="480"
             style="background:#ffffff;border-radius:12px;overflow:hidden;max-width:480px;">
        <tr><td style="background:linear-gradient(135deg,#f59e0b,#d97706);padding:24px 28px;text-align:center;">
          <div style="color:#ffffff;font-size:18px;font-weight:700;">New sign-in detected</div>
        </td></tr>
        <tr><td style="padding:28px;color:#0f172a;">
          <p style="margin:0 0 16px;font-size:15px;line-height:1.5;">Hello <strong>{to_name}</strong>,</p>
          <p style="margin:0 0 16px;font-size:15px;line-height:1.5;">We noticed a new sign-in to your Procta account from a device or location we haven't seen before:</p>
          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
                 style="background:#f8fafc;border-radius:8px;margin:0 0 20px;font-size:13px;">
            <tr><td style="padding:8px 12px;color:#64748b;width:80px;">When</td>
                <td style="padding:8px 12px;color:#0f172a;"><strong>{when_str}</strong></td></tr>
            <tr><td style="padding:8px 12px;color:#64748b;">IP address</td>
                <td style="padding:8px 12px;color:#0f172a;font-family:monospace;font-size:12px;">{ip or 'unknown'}</td></tr>
            <tr><td style="padding:8px 12px;color:#64748b;">Browser</td>
                <td style="padding:8px 12px;color:#0f172a;font-size:12px;">{ua_short}</td></tr>
          </table>
          <p style="margin:0 0 12px;font-size:14px;line-height:1.5;"><strong>If this was you</strong>, no action is needed.</p>
          <p style="margin:0 0 8px;font-size:14px;line-height:1.5;"><strong>If it was NOT you:</strong></p>
          <ol style="margin:0 0 20px 20px;padding:0;font-size:13px;line-height:1.6;color:#334155;">
            <li>Sign in to Procta and change your password immediately.</li>
            <li>Review and revoke unfamiliar sessions in account settings.</li>
            <li>Enable two-factor authentication.</li>
          </ol>
          <p style="margin:0;font-size:12px;color:#999;line-height:1.4;">If you have any questions, reply to this email and we'll help.</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""
    return _send(to_email, subject, html, text)


def _render_reminder(**ctx) -> tuple[str, str]:
    """Return (html, text) for a "your exam starts in N hours" reminder.

    Intentionally terser than the full invite email: the student has
    already opened the original invite, so the only jobs here are
    (a) remind them it's happening and (b) give them a one-click
    entrypoint. No download links unless they ask — if they've got
    this far without installing Procta the 1-hour reminder is the
    wrong moment to suggest they start now."""
    to_name                = ctx.get("to_name") or "Student"
    exam_title             = ctx.get("exam_title") or "Your exam"
    invite_url             = ctx["invite_url"]
    roll_number            = ctx.get("roll_number") or ""
    hours_until            = int(ctx.get("hours_until") or 1)
    starts_at_display      = ctx.get("exam_starts_at_display") or ""
    access_code            = ctx.get("access_code")
    teacher_name           = ctx.get("teacher_name") or "your teacher"

    if hours_until >= 24:
        headline_short = "Your exam is tomorrow"
        lead = f"A quick heads-up — <b>{_esc(exam_title)}</b> opens tomorrow."
        hero_tag = "24-HOUR REMINDER"
    else:
        headline_short = "Your exam starts in 1 hour"
        lead = (f"Just a reminder — <b>{_esc(exam_title)}</b> opens in about "
                f"one hour. Make sure Procta is already installed and you're "
                f"in a quiet spot with stable internet.")
        hero_tag = "1-HOUR REMINDER"

    # ── Plaintext ──
    text_lines = [
        f"Hi {to_name},",
        "",
    ]
    if hours_until >= 24:
        text_lines.append(f"Your exam '{exam_title}' is scheduled for tomorrow.")
    else:
        text_lines.append(f"Your exam '{exam_title}' starts in about 1 hour.")
    if starts_at_display:
        text_lines.append(f"Starts: {starts_at_display}")
    text_lines.append(f"Roll number: {roll_number}")
    if access_code:
        text_lines.append(f"Access code: {access_code}")
    text_lines += [
        "",
        "Open your invite page:",
        f"  {invite_url}",
        "",
        "If Procta isn't already installed on your computer, install it now —",
        "the invite page has the right download for your operating system.",
        "",
        "— Procta",
    ]
    text = "\n".join(text_lines)

    # ── HTML ──
    access_block = (
        f'<div style="margin-top:6px;color:#334155;"><b>Access code:</b> '
        f'<code style="background:#f1f5f9;padding:2px 8px;border-radius:4px;'
        f'font-family:monospace;font-size:14px;">{_esc(access_code)}</code></div>'
        if access_code else ""
    )
    starts_block = ""
    if starts_at_display:
        starts_block = (f'<div style="color:#475569;margin-top:4px;">'
                        f'<b>Starts:</b> {_esc(starts_at_display)}</div>')

    html = f"""\
<!doctype html>
<html><head><meta charset="utf-8"><title>{_esc(headline_short)} — Procta</title></head>
<body style="margin:0;padding:0;background:#0f172a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
         style="background:#0f172a;padding:32px 16px;">
    <tr><td align="center">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="560"
             style="background:#ffffff;border-radius:16px;overflow:hidden;max-width:560px;">
        <tr><td style="background:linear-gradient(135deg,#f59e0b,#ef4444);padding:28px 32px;">
          <div style="color:#ffffff;font-size:12px;letter-spacing:2px;font-weight:600;opacity:.9;">PROCTA · {hero_tag}</div>
          <div style="color:#ffffff;font-size:22px;font-weight:700;margin-top:6px;">{_esc(headline_short)}</div>
        </td></tr>
        <tr><td style="padding:32px;color:#0f172a;">
          <p style="margin:0 0 16px 0;font-size:16px;">Hi {_esc(to_name)},</p>
          <p style="margin:0 0 20px 0;font-size:15px;line-height:1.55;color:#334155;">{lead}</p>

          <div style="background:#f8fafc;border-radius:10px;padding:16px 18px;margin:20px 0;border:1px solid #e2e8f0;">
            <div style="color:#334155;"><b>Roll number:</b>
              <code style="background:#f1f5f9;padding:2px 8px;border-radius:4px;font-family:monospace;font-size:14px;">
                {_esc(roll_number)}
              </code>
            </div>
            {access_block}
            {starts_block}
          </div>

          <div style="margin:16px 0;">
            <a href="{_esc(invite_url)}"
               style="display:inline-block;background:#10b981;color:#ffffff;text-decoration:none;
                      padding:12px 24px;border-radius:8px;font-weight:600;font-size:15px;">
              Open my invite page
            </a>
          </div>

          <p style="margin:20px 0 0 0;color:#94a3b8;font-size:12px;line-height:1.55;">
            Good luck! If anything's unclear, reply to this email and your
            teacher will get back to you.
          </p>
        </td></tr>
        <tr><td style="padding:24px 0 0;text-align:center;font-size:11px;color:#94a3b8;border-top:1px solid #e2e8f0">
          Sent via <a href="https://procta.net" style="color:#64748b;text-decoration:none;font-weight:600">Procta</a>
          &nbsp;·&nbsp;
          Proctored exams for Indian institutions
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>
"""
    return html, text


def _render_scorecard_email(**ctx) -> tuple[str, str]:
    """Return (html, text) for the "here are your results" email.

    Colour scheme: emerald for pass, slate for fail — deliberately
    restrained on the fail path so a borderline student doesn't feel
    kicked while down. The PDF has the full per-question breakdown;
    this email is a summary + "open the attachment for details"."""
    to_name        = ctx.get("to_name") or "Student"
    exam_title     = ctx.get("exam_title") or "Your exam"
    score          = int(ctx.get("score") or 0)
    total          = int(ctx.get("total") or 0)
    percentage     = float(ctx.get("percentage") or 0.0)
    passed         = bool(ctx.get("passed"))
    teacher_name   = ctx.get("teacher_name") or "your teacher"
    custom_message = ctx.get("custom_message")

    verdict_label = "Passed" if passed else "Results available"
    # Greens for pass, slate-blue for non-pass — keeps the visual
    # language familiar (same palette family as invite/reminder emails)
    # but distinct enough that students can tell at a glance which
    # email this is in their inbox.
    if passed:
        gradient = "linear-gradient(135deg,#10b981,#059669)"
        hero_tag = "RESULT · PASSED"
    else:
        gradient = "linear-gradient(135deg,#64748b,#334155)"
        hero_tag = "RESULT"

    pct_display = f"{percentage:.1f}%"

    # ── Plaintext ──
    text_lines = [
        f"Hi {to_name},",
        "",
        f"Your results for '{exam_title}' are ready.",
        "",
        f"Score:      {score} / {total}",
        f"Percentage: {pct_display}",
        f"Verdict:    {verdict_label}",
        "",
        "The full scorecard with per-question breakdown is attached as a PDF.",
    ]
    if custom_message:
        text_lines += ["", f"— Note from {teacher_name} —", custom_message]
    text_lines += [
        "",
        "If you have questions about any specific question, reply to this",
        "email and your teacher will get back to you.",
        "",
        "— Procta",
    ]
    text = "\n".join(text_lines)

    # ── HTML ──
    custom_block = ""
    if custom_message:
        custom_block = (
            f'<div style="background:#fff7ed;border-left:3px solid #f59e0b;'
            f'padding:12px 16px;margin:20px 0;border-radius:6px;color:#78350f;'
            f'font-size:14px;line-height:1.5;">'
            f'<div style="font-weight:600;margin-bottom:4px;color:#92400e;">'
            f'Note from {_esc(teacher_name)}</div>'
            f'{_esc(custom_message).replace(chr(10), "<br>")}'
            f'</div>'
        )

    html = f"""\
<!doctype html>
<html><head><meta charset="utf-8"><title>{_esc(exam_title)} — Results</title></head>
<body style="margin:0;padding:0;background:#0f172a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
         style="background:#0f172a;padding:32px 16px;">
    <tr><td align="center">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="560"
             style="background:#ffffff;border-radius:16px;overflow:hidden;max-width:560px;">
        <tr><td style="background:{gradient};padding:28px 32px;">
          <div style="color:#ffffff;font-size:12px;letter-spacing:2px;font-weight:600;opacity:.9;">PROCTA · {hero_tag}</div>
          <div style="color:#ffffff;font-size:22px;font-weight:700;margin-top:6px;">Your results are in</div>
        </td></tr>
        <tr><td style="padding:32px;color:#0f172a;">
          <p style="margin:0 0 16px 0;font-size:16px;">Hi {_esc(to_name)},</p>
          <p style="margin:0 0 20px 0;font-size:15px;line-height:1.55;color:#334155;">
            Your scorecard for <b>{_esc(exam_title)}</b> is ready.
          </p>

          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
                 style="background:#f8fafc;border-radius:12px;margin:20px 0;border:1px solid #e2e8f0;">
            <tr>
              <td style="padding:18px 20px;border-right:1px solid #e2e8f0;text-align:center;">
                <div style="color:#64748b;font-size:11px;letter-spacing:1.5px;font-weight:600;">SCORE</div>
                <div style="color:#0f172a;font-size:26px;font-weight:700;margin-top:4px;">{score}<span style="color:#94a3b8;font-size:16px;font-weight:500;"> / {total}</span></div>
              </td>
              <td style="padding:18px 20px;border-right:1px solid #e2e8f0;text-align:center;">
                <div style="color:#64748b;font-size:11px;letter-spacing:1.5px;font-weight:600;">PERCENTAGE</div>
                <div style="color:#0f172a;font-size:26px;font-weight:700;margin-top:4px;">{pct_display}</div>
              </td>
              <td style="padding:18px 20px;text-align:center;">
                <div style="color:#64748b;font-size:11px;letter-spacing:1.5px;font-weight:600;">VERDICT</div>
                <div style="color:{'#059669' if passed else '#475569'};font-size:18px;font-weight:700;margin-top:6px;">{verdict_label}</div>
              </td>
            </tr>
          </table>

          {custom_block}

          <div style="margin:20px 0;padding:14px 16px;background:#eff6ff;border-radius:8px;color:#1e3a8a;font-size:14px;">
            📄 <b>Full scorecard attached</b> — open the PDF for per-question
            results (your answer, the correct answer, and whether it was right).
          </div>

          <p style="margin:20px 0 0 0;color:#94a3b8;font-size:12px;line-height:1.55;">
            Questions about a specific answer? Reply to this email and
            {_esc(teacher_name)} will get back to you.
          </p>
        </td></tr>
        <tr><td style="padding:24px 0 0;text-align:center;font-size:11px;color:#94a3b8;border-top:1px solid #e2e8f0">
          Sent via <a href="https://procta.net" style="color:#64748b;text-decoration:none;font-weight:600">Procta</a>
          &nbsp;·&nbsp;
          Proctored exams for Indian institutions
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>
"""
    return html, text


# ─── Backend infrastructure ──────────────────────────────────────────

class _Backend:
    """Abstract base for email provider backends."""
    def send(
        self, *, to_email: str, to_name: str, subject: str,
        html: str, text: str, attachments=None,
    ) -> SendResult:
        raise NotImplementedError


class _ResendBackend(_Backend):
    def send(self, *, to_email: str, to_name: str, subject: str,
             html: str, text: str, attachments=None) -> SendResult:
        try:
            import resend  # type: ignore
        except ImportError:
            return SendResult(ok=False, error="resend package not installed")
        api_key = os.environ.get("RESEND_API_KEY", "")
        if not api_key:
            return SendResult(ok=False, error="RESEND_API_KEY not configured")
        resend.api_key = api_key
        from_addr = os.environ.get("EMAIL_FROM", "invites@procta.net")
        from_name = os.environ.get("EMAIL_FROM_NAME", "Procta")
        reply_to = os.environ.get("EMAIL_REPLY_TO", "")
        params: dict = {
            "from": f"{from_name} <{from_addr}>",
            "to": [f"{to_name} <{to_email}>"] if to_name else [to_email],
            "subject": subject,
            "html": html,
            "text": text,
        }
        if reply_to:
            params["reply_to"] = reply_to
        if attachments:
            import base64 as _b64
            params["attachments"] = [
                {"filename": a["filename"], "content": _b64.b64encode(a["content"]).decode("ascii")}
                for a in attachments
            ]
        try:
            resp = resend.Emails.send(params)
            msg_id = resp.get("id") if isinstance(resp, dict) else getattr(resp, "id", None)
            return SendResult(ok=True, provider_msg_id=msg_id)
        except Exception as e:
            log.error("Resend API error: %s", e)
            return SendResult(ok=False, error=str(e))


class _SmtpBackend(_Backend):
    def send(self, *, to_email: str, to_name: str, subject: str,
             html: str, text: str, attachments=None) -> SendResult:
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.mime.base import MIMEBase
        from email import encoders
        host = os.environ.get("SMTP_HOST", "localhost")
        port = int(os.environ.get("SMTP_PORT", "587"))
        user = os.environ.get("SMTP_USER", "")
        password = os.environ.get("SMTP_PASS", "")
        from_addr = os.environ.get("EMAIL_FROM", "invites@procta.net")
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = f"{to_name} <{to_email}>" if to_name else to_email
        msg.attach(MIMEText(text, "plain"))
        msg.attach(MIMEText(html, "html"))
        if attachments:
            for a in attachments:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(a["content"])
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", f'attachment; filename="{a["filename"]}"')
                msg.attach(part)
        try:
            with smtplib.SMTP(host, port) as smtp:
                smtp.starttls()
                if user:
                    smtp.login(user, password)
                smtp.sendmail(from_addr, [to_email], msg.as_string())
            return SendResult(ok=True)
        except Exception as e:
            log.error("SMTP error: %s", e)
            return SendResult(ok=False, error=str(e))


class _NoopBackend(_Backend):
    def send(self, *, to_email: str, to_name: str, subject: str,
             html: str, text: str, attachments=None) -> SendResult:
        log.info("[noop-email] to=%s subject=%r", safe(to_email), safe(subject))
        return SendResult(ok=True, provider_msg_id="noop")


def _pick_backend() -> _Backend:
    provider = os.environ.get("EMAIL_PROVIDER", "resend").lower().strip()
    if provider == "smtp":
        return _SmtpBackend()
    if provider == "noop":
        return _NoopBackend()
    if not os.environ.get("RESEND_API_KEY"):
        log.debug("[emailer] RESEND_API_KEY not set — using noop backend")
        return _NoopBackend()
    return _ResendBackend()


def _send(to_email: str, subject: str, html: str, text: str,
          to_name: str = "") -> SendResult:
    """Convenience wrapper: pick backend and send in one call."""
    try:
        return _pick_backend().send(
            to_email=to_email, to_name=to_name,
            subject=subject, html=html, text=text,
        )
    except Exception as e:
        log.exception("_send failed: %s", e)
        return SendResult(ok=False, error=str(e))


def _render_invite(**ctx) -> tuple[str, str]:
    """Return (html, text) for a new student exam invite email."""
    to_name        = ctx.get("to_name") or "Student"
    exam_title     = ctx.get("exam_title") or "Your exam"
    invite_url     = ctx["invite_url"]
    download_url   = ctx.get("download_url") or "https://procta.net/download"
    roll_number    = ctx.get("roll_number") or ""
    access_code    = ctx.get("access_code")
    exam_starts_at = ctx.get("exam_starts_at") or ""
    exam_ends_at   = ctx.get("exam_ends_at") or ""
    custom_message = ctx.get("custom_message") or ""
    teacher_name   = ctx.get("teacher_name") or "your teacher"

    # ── Plaintext ──
    text_lines = [
        f"Hi {to_name},",
        "",
        f"You've been invited to take '{exam_title}' on Procta.",
        "",
        f"Roll number: {roll_number}",
    ]
    if access_code:
        text_lines.append(f"Access code: {access_code}")
    if exam_starts_at:
        text_lines.append(f"Starts: {exam_starts_at}")
    if exam_ends_at:
        text_lines.append(f"Ends:   {exam_ends_at}")
    text_lines += [
        "",
        "Open your invite page to join:",
        f"  {invite_url}",
        "",
        "You'll need the Procta desktop app to take this exam. Download it at:",
        f"  {download_url}",
        "",
    ]
    if custom_message:
        text_lines += [f"Note from {teacher_name}:", custom_message, ""]
    text_lines.append("— The Procta Team")
    text = "\n".join(text_lines)

    # ── HTML ──
    access_block = (
        f'<div style="margin-top:6px;color:#334155;"><b>Access code:</b> '
        f'<code style="background:#f1f5f9;padding:2px 8px;border-radius:4px;'
        f'font-family:monospace;font-size:14px;">{_esc(access_code)}</code></div>'
        if access_code else ""
    )
    starts_block = (
        f'<div style="color:#475569;margin-top:4px;"><b>Starts:</b> {_esc(exam_starts_at)}</div>'
        if exam_starts_at else ""
    )
    ends_block = (
        f'<div style="color:#475569;margin-top:4px;"><b>Ends:</b> {_esc(exam_ends_at)}</div>'
        if exam_ends_at else ""
    )
    message_block = (
        f'<div style="background:#fff7ed;border-left:3px solid #f59e0b;padding:12px 16px;'
        f'margin:16px 0;border-radius:6px;color:#78350f;font-size:14px;line-height:1.5;">'
        f'<div style="font-weight:600;margin-bottom:4px;color:#92400e;">'
        f'Note from {_esc(teacher_name)}</div>'
        f'{_esc(custom_message).replace(chr(10), "<br>")}'
        f'</div>'
        if custom_message else ""
    )

    html = f"""\
<!doctype html>
<html><head><meta charset="utf-8"><title>You're invited — {_esc(exam_title)}</title></head>
<body style="margin:0;padding:0;background:#0f172a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
         style="background:#0f172a;padding:32px 16px;">
    <tr><td align="center">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="560"
             style="background:#ffffff;border-radius:16px;overflow:hidden;max-width:560px;">
        <tr><td style="background:linear-gradient(135deg,#3dd9a8,#3b82f6);padding:28px 32px;">
          <div style="color:#ffffff;font-size:12px;letter-spacing:2px;font-weight:600;opacity:.9;">PROCTA · EXAM INVITE</div>
          <div style="color:#ffffff;font-size:22px;font-weight:700;margin-top:6px;">You're invited to take an exam</div>
        </td></tr>
        <tr><td style="padding:32px;color:#0f172a;">
          <p style="margin:0 0 16px 0;font-size:16px;">Hi {_esc(to_name)},</p>
          <p style="margin:0 0 20px 0;font-size:15px;line-height:1.55;color:#334155;">
            You've been invited to take <b>{_esc(exam_title)}</b> on Procta.
          </p>
          <div style="background:#f8fafc;border-radius:10px;padding:16px 18px;margin:20px 0;border:1px solid #e2e8f0;">
            <div style="color:#334155;"><b>Roll number:</b>
              <code style="background:#f1f5f9;padding:2px 8px;border-radius:4px;font-family:monospace;font-size:14px;">{_esc(roll_number)}</code>
            </div>
            {access_block}
            {starts_block}
            {ends_block}
          </div>
          {message_block}
          <div style="margin:20px 0;">
            <a href="{_esc(invite_url)}"
               style="display:inline-block;background:#10b981;color:#ffffff;text-decoration:none;
                      padding:12px 24px;border-radius:8px;font-weight:600;font-size:15px;margin-right:10px;">
              Open invite page
            </a>
            <a href="{_esc(download_url)}"
               style="display:inline-block;background:#1e293b;color:#ffffff;text-decoration:none;
                      padding:12px 24px;border-radius:8px;font-weight:600;font-size:15px;">
              Download Procta
            </a>
          </div>
          <p style="margin:20px 0 0 0;color:#94a3b8;font-size:12px;line-height:1.55;">
            You'll need the Procta desktop app installed before your exam.
            Questions? Reply to this email and {_esc(teacher_name)} will get back to you.
          </p>
        </td></tr>
        <tr><td style="padding:24px 0 0;text-align:center;font-size:11px;color:#94a3b8;border-top:1px solid #e2e8f0">
          Sent via <a href="https://procta.net" style="color:#64748b;text-decoration:none;font-weight:600">Procta</a>
          &nbsp;·&nbsp;
          Proctored exams for Indian institutions
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>
"""
    return html, text


def send_payment_failed_notification(to_email: str, to_name: str) -> SendResult:
    """Notify the org admin that their Razorpay payment failed and they need to update their payment method."""
    subject = "[Procta] Your payment failed — action required"
    name_esc = _esc(to_name or "there")
    html = f"""\
<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:Inter,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:40px 16px;">
    <table width="540" cellpadding="0" cellspacing="0"
           style="background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e2e8f0;max-width:540px;">
      <tr><td style="background:#ef4444;padding:28px 32px;">
        <div style="font-size:22px;font-weight:700;color:#ffffff;letter-spacing:-0.3px;">Payment failed</div>
        <div style="color:#fecaca;font-size:13px;margin-top:4px;">Action required to keep your Procta subscription active</div>
      </td></tr>
      <tr><td style="padding:32px;">
        <p style="margin:0 0 16px 0;font-size:16px;">Hi {name_esc},</p>
        <p style="margin:0 0 20px 0;font-size:15px;line-height:1.55;color:#334155;">
          We were unable to process your most recent Razorpay payment for your Procta subscription.
          Your access may be restricted if the payment is not resolved promptly.
        </p>
        <p style="margin:0 0 20px 0;font-size:15px;line-height:1.55;color:#334155;">
          Please update your payment method or retry the payment to keep your team's exams running without interruption.
        </p>
        <div style="margin:24px 0;">
          <a href="https://app.procta.net/dashboard#billing"
             style="display:inline-block;background:#ef4444;color:#ffffff;text-decoration:none;
                    padding:12px 28px;border-radius:8px;font-weight:600;font-size:15px;">
            Update payment method &rarr;
          </a>
        </div>
        <p style="margin:20px 0 0 0;color:#94a3b8;font-size:12px;line-height:1.55;">
          If you believe this is an error or need assistance, reply to this email and we'll help you right away.
        </p>
      </td></tr>
      <tr><td style="background:#f8fafc;padding:14px 32px;color:#94a3b8;font-size:11px;text-align:center;border-top:1px solid #e2e8f0;">
        Procta — proctored exams, made simple.
      </td></tr>
    </table>
  </td></tr></table>
</body></html>
"""
    text = (
        f"Hi {to_name or 'there'},\n\n"
        "We were unable to process your most recent Razorpay payment for your Procta subscription.\n"
        "Please update your payment method to keep your team's exams running without interruption.\n\n"
        "Update payment method: https://app.procta.net/dashboard#billing\n\n"
        "If you need help, just reply to this email.\n\n"
        "— The Procta team"
    )
    try:
        return _send(to_email, subject, html, text)
    except Exception as e:
        log.exception("send_payment_failed_notification failed: %s", e)
        return SendResult(ok=False, error=str(e))


def send_new_account_notification(*, account_type: str, name: str, email: str) -> SendResult:
    """Notify internal ops that a new account was created."""
    subject = f"[Procta] New {account_type} account: {name}"
    name_esc = _esc(name)
    html = f"""\
<!doctype html>
<html><head><meta charset="utf-8"><title>New account — Procta</title></head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:40px 16px;">
<table width="480" cellpadding="0" cellspacing="0"
       style="background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e2e8f0;">
<tr><td style="background:#10b981;padding:24px 28px;">
<div style="font-size:18px;font-weight:700;color:#ffffff;">New signup</div>
</td></tr>
<tr><td style="padding:28px;">
<p style="margin:0 0 12px;font-size:15px;color:#334155;">
A new <b>{_esc(account_type)}</b> account was created on Procta.
</p>
<p style="margin:0 0 4px;font-size:14px;color:#475569;"><b>Name:</b> {name_esc}</p>
<p style="margin:0 0 4px;font-size:14px;color:#475569;"><b>Email:</b> {_esc(email)}</p>
</td></tr>
<tr><td style="padding:14px 28px;font-size:11px;color:#94a3b8;text-align:center;border-top:1px solid #e2e8f0;">
Procta
</td></tr>
</table>
</td></tr></table>
</body></html>"""
    text = f"A new {account_type} account was created on Procta.\n\nName: {name}\nEmail: {email}"
    try:
        return _send(email, subject, html, text)
    except Exception as e:
        log.exception("send_new_account_notification failed: %s", e)
        return SendResult(ok=False, error=str(e))


def send_org_invite_email(*, to_email: str, invite_url: str, org_name: str, invited_by_name: str) -> SendResult:
    """Send an org invite email to a new admin."""
    subject = f"You've been invited to join {org_name} on Procta"
    name_esc = _esc(invited_by_name)
    org_esc = _esc(org_name)
    html = f"""\
<!doctype html>
<html><head><meta charset="utf-8"><title>Org invite — Procta</title></head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:40px 16px;">
<table width="480" cellpadding="0" cellspacing="0"
       style="background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e2e8f0;">
<tr><td style="background:linear-gradient(135deg,#3dd9a8,#3b82f6);padding:24px 28px;">
<div style="font-size:18px;font-weight:700;color:#ffffff;">Organization invite</div>
</td></tr>
<tr><td style="padding:28px;">
<p style="margin:0 0 16px;font-size:15px;color:#334155;">
{name_esc} has invited you to join <b>{org_esc}</b> on Procta.
</p>
<a href="{_esc(invite_url)}"
   style="display:inline-block;background:#10b981;color:#ffffff;text-decoration:none;padding:12px 28px;border-radius:8px;font-weight:600;font-size:15px;">
Accept invite
</a>
</td></tr>
<tr><td style="padding:14px 28px;font-size:11px;color:#94a3b8;text-align:center;border-top:1px solid #e2e8f0;">
Procta — proctored exams for Indian institutions
</td></tr>
</table>
</td></tr></table>
</body></html>"""
    text = f"{invited_by_name} has invited you to join {org_name} on Procta.\n\nAccept your invite: {invite_url}"
    try:
        return _send(to_email, subject, html, text)
    except Exception as e:
        log.exception("send_org_invite_email failed: %s", e)
        return SendResult(ok=False, error=str(e))
