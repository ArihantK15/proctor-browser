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

import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("procta.email")


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


def verify_webhook(raw_body: bytes, signature_header: str) -> bool:
    """True if the webhook signature is valid. Resend signs with HMAC-SHA256
    over the raw body keyed by RESEND_WEBHOOK_SECRET.

    Returns False if the secret isn't configured (fail closed) or the
    header is missing/malformed — never raises."""
    secret = os.environ.get("RESEND_WEBHOOK_SECRET", "")
    if not secret or not signature_header:
        return False
    try:
        # Resend format: "t=<ts>,v1=<hex>"
        parts = dict(p.split("=", 1) for p in signature_header.split(","))
        expected = parts.get("v1", "")
        if not expected:
            return False
        mac = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(mac, expected)
    except Exception:
        return False


# ─── Backends ──────────────────────────────────────────────────────

class _Backend:
    def send(self, *, to_email, to_name, subject, html, text) -> SendResult:
        raise NotImplementedError


class _NoopBackend(_Backend):
    """Logs only. Used in tests and when no provider is configured."""

    def send(self, *, to_email, to_name, subject, html, text) -> SendResult:
        log.info(
            "[emailer:noop] would send to=%s subject=%s bytes=%d",
            to_email, subject, len(html),
        )
        # Deterministic fake id so tests can assert on it.
        fake_id = "noop-" + hashlib.sha1(
            f"{to_email}|{subject}|{int(time.time())}".encode()
        ).hexdigest()[:16]
        return SendResult(ok=True, provider_msg_id=fake_id)


class _ResendBackend(_Backend):
    """Resend HTTP API. We call the REST endpoint directly instead of
    pulling in the `resend` SDK — one fewer dep, same behaviour."""

    API_URL = "https://api.resend.com/emails"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.from_addr = os.environ.get("EMAIL_FROM", "invites@procta.net")
        self.from_name = os.environ.get("EMAIL_FROM_NAME", "Procta")
        self.reply_to = os.environ.get("EMAIL_REPLY_TO") or None

    def send(self, *, to_email, to_name, subject, html, text) -> SendResult:
        # Lazy import so the noop backend doesn't need httpx.
        import httpx

        payload = {
            "from": f"{self.from_name} <{self.from_addr}>",
            "to": [f"{to_name} <{to_email}>" if to_name else to_email],
            "subject": subject,
            "html": html,
            "text": text,
        }
        if self.reply_to:
            payload["reply_to"] = self.reply_to

        try:
            r = httpx.post(
                self.API_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=10.0,
            )
        except httpx.HTTPError as e:
            return SendResult(ok=False, error=f"transport: {e}")

        if r.status_code >= 400:
            return SendResult(
                ok=False,
                error=f"resend {r.status_code}: {r.text[:200]}",
            )
        try:
            msg_id = r.json().get("id")
        except Exception:
            msg_id = None
        return SendResult(ok=True, provider_msg_id=msg_id)


_cached_backend: Optional[_Backend] = None


def _pick_backend() -> _Backend:
    """Resolve the backend from env once per process."""
    global _cached_backend
    if _cached_backend is not None:
        return _cached_backend
    provider = os.environ.get("EMAIL_PROVIDER", "resend").lower().strip()
    if provider == "noop":
        _cached_backend = _NoopBackend()
    elif provider == "resend":
        key = os.environ.get("RESEND_API_KEY", "").strip()
        if not key:
            log.warning("RESEND_API_KEY unset — falling back to noop backend")
            _cached_backend = _NoopBackend()
        else:
            _cached_backend = _ResendBackend(api_key=key)
    else:
        log.warning("Unknown EMAIL_PROVIDER=%r — falling back to noop", provider)
        _cached_backend = _NoopBackend()
    return _cached_backend


def _reset_backend_for_tests():
    """Tests patch the cache between cases."""
    global _cached_backend
    _cached_backend = None


# ─── Template ──────────────────────────────────────────────────────

def _render_invite(**ctx) -> tuple[str, str]:
    """Return (html, text). Deliberately inline styles because Gmail/
    Outlook strip <style> blocks in many contexts."""

    to_name        = ctx.get("to_name") or "Student"
    exam_title     = ctx.get("exam_title") or "Your exam"
    invite_url     = ctx["invite_url"]
    download_url   = ctx["download_url"]
    roll_number    = ctx.get("roll_number") or ""
    access_code    = ctx.get("access_code")
    starts_at      = ctx.get("exam_starts_at")
    ends_at        = ctx.get("exam_ends_at")
    custom_message = ctx.get("custom_message")
    teacher_name   = ctx.get("teacher_name") or "your teacher"

    # ── Plaintext ──
    text_lines = [
        f"Hi {to_name},",
        "",
        f"{teacher_name} has invited you to take: {exam_title}",
        "",
        f"Your roll number: {roll_number}",
    ]
    if access_code:
        text_lines.append(f"Your access code: {access_code}")
    if starts_at:
        text_lines.append(f"Exam starts: {starts_at}")
    if ends_at:
        text_lines.append(f"Exam window closes: {ends_at}")
    text_lines += [
        "",
        "Step 1 — Open your personal invite page:",
        f"  {invite_url}",
        "",
        "Step 2 — Download the Procta exam browser for your OS:",
        f"  {download_url}",
        "",
        "Step 3 — When the exam starts, launch Procta and sign in with the",
        "roll number and access code above. The app will guide you the rest",
        "of the way.",
    ]
    if custom_message:
        text_lines += ["", "— Message from your teacher —", custom_message]
    text_lines += [
        "",
        "If you didn't expect this email you can safely ignore it.",
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
    time_block = ""
    if starts_at:
        time_block += (f'<div style="color:#475569;margin-top:4px;">'
                       f'<b>Starts:</b> {_esc(starts_at)}</div>')
    if ends_at:
        time_block += (f'<div style="color:#475569;margin-top:4px;">'
                       f'<b>Closes:</b> {_esc(ends_at)}</div>')

    custom_block = ""
    if custom_message:
        custom_block = (
            f'<div style="background:#fff7ed;border-left:3px solid #f59e0b;'
            f'padding:12px 16px;margin:20px 0;border-radius:6px;color:#78350f;'
            f'font-size:14px;line-height:1.5;">'
            f'<div style="font-weight:600;margin-bottom:4px;color:#92400e;">'
            f'Message from {_esc(teacher_name)}</div>'
            f'{_esc(custom_message).replace(chr(10), "<br>")}'
            f'</div>'
        )

    html = f"""\
<!doctype html>
<html><head><meta charset="utf-8"><title>{_esc(exam_title)} — Procta</title></head>
<body style="margin:0;padding:0;background:#0f172a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
         style="background:#0f172a;padding:32px 16px;">
    <tr><td align="center">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="560"
             style="background:#ffffff;border-radius:16px;overflow:hidden;max-width:560px;">
        <tr><td style="background:linear-gradient(135deg,#10b981,#3b82f6);padding:28px 32px;">
          <div style="color:#ffffff;font-size:13px;letter-spacing:2px;font-weight:600;opacity:.9;">PROCTA</div>
          <div style="color:#ffffff;font-size:22px;font-weight:700;margin-top:6px;">You're invited to an exam</div>
        </td></tr>
        <tr><td style="padding:32px;color:#0f172a;">
          <p style="margin:0 0 16px 0;font-size:16px;">Hi {_esc(to_name)},</p>
          <p style="margin:0 0 20px 0;font-size:15px;line-height:1.55;color:#334155;">
            <b>{_esc(teacher_name)}</b> has invited you to take
            <b>{_esc(exam_title)}</b> via the Procta proctored browser.
          </p>

          <div style="background:#f8fafc;border-radius:10px;padding:16px 18px;margin:20px 0;border:1px solid #e2e8f0;">
            <div style="color:#334155;"><b>Roll number:</b>
              <code style="background:#f1f5f9;padding:2px 8px;border-radius:4px;font-family:monospace;font-size:14px;">
                {_esc(roll_number)}
              </code>
            </div>
            {access_block}
            {time_block}
          </div>

          {custom_block}

          <p style="margin:24px 0 12px 0;font-weight:600;color:#0f172a;">Getting started</p>

          <div style="margin:16px 0;">
            <a href="{_esc(invite_url)}"
               style="display:inline-block;background:#10b981;color:#ffffff;text-decoration:none;
                      padding:12px 24px;border-radius:8px;font-weight:600;font-size:15px;">
              Open my invite page
            </a>
          </div>
          <p style="color:#64748b;font-size:13px;margin:8px 0 24px 0;">
            Your personal link — opens a page with the exact download for your OS.
          </p>

          <div style="margin:12px 0 24px 0;padding:16px;background:#f8fafc;border-radius:10px;">
            <div style="font-size:13px;color:#64748b;margin-bottom:8px;">Prefer a direct download?</div>
            <a href="{_esc(download_url)}"
               style="color:#3b82f6;text-decoration:none;font-weight:600;word-break:break-all;">
              {_esc(download_url)}
            </a>
          </div>

          <p style="margin:20px 0 0 0;color:#94a3b8;font-size:12px;line-height:1.55;">
            If you weren't expecting this email you can safely ignore it.
            Your invite link is personal — please don't forward it.
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


def _esc(s) -> str:
    """HTML escape for template interpolation."""
    if s is None:
        return ""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))
