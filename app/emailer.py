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
        <tr><td style="background:#f8fafc;padding:14px 32px;color:#94a3b8;font-size:11px;text-align:center;border-top:1px solid #e2e8f0;">
          Procta — proctored exams, made simple.
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>
"""
    return html, text


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
        <tr><td style="background:#f8fafc;padding:14px 32px;color:#94a3b8;font-size:11px;text-align:center;border-top:1px solid #e2e8f0;">
          Procta — proctored exams, made simple.
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
        <tr><td style="background:#f8fafc;padding:14px 32px;color:#94a3b8;font-size:11px;text-align:center;border-top:1px solid #e2e8f0;">
          Procta — proctored exams, made simple.
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>
"""
    return html, text
