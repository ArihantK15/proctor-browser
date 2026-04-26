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


def verify_webhook(raw_body: bytes, headers) -> bool:
    """True if the webhook signature is valid.

    Resend uses Svix under the hood, so signatures follow the Svix
    spec:

      svix-id          unique event id
      svix-timestamp   unix seconds — reject if drift > 5 min (replay
                       protection; Svix's reference impl uses the same)
      svix-signature   "v1,<base64>" — possibly several space-separated
                       (rotation: an old + new sig appear together
                       during key rollover; any one matching is fine)

    Signed payload: ``<svix-id>.<svix-timestamp>.<raw-body>``. HMAC
    key is the base64-decoded portion of the secret after the
    ``whsec_`` prefix.

    For backward compatibility, callers may pass either a dict-like
    of headers OR the raw signature string (legacy single-arg call).
    The latter path can never verify a Svix signature (it lacks id +
    timestamp) so it always returns False — but it won't raise,
    which keeps existing tests green.

    Returns False on any failure mode — missing secret, missing
    header, bad base64, expired timestamp, mismatched HMAC. Never
    raises."""
    secret = os.environ.get("RESEND_WEBHOOK_SECRET", "")
    if not secret:
        return False

    # Header lookup — accept dict-like, or fall back to legacy single-string mode.
    if isinstance(headers, str):
        # Legacy single-arg call. Can't reconstruct the Svix signed
        # payload from just the signature, so fail closed.
        return False
    if not headers:
        return False
    def _hget(name: str) -> str:
        # Headers may be a Starlette Headers (case-insensitive) or a
        # plain dict. Try lowercase, then exact, to support both.
        try:
            return headers.get(name) or headers.get(name.lower()) or ""
        except Exception:
            return ""

    svix_id = _hget("svix-id")
    svix_ts = _hget("svix-timestamp")
    svix_sig = _hget("svix-signature")
    if not (svix_id and svix_ts and svix_sig):
        return False

    # Replay protection — 5-minute tolerance both ways.
    try:
        ts_int = int(svix_ts)
    except ValueError:
        return False
    now = int(time.time())
    if abs(now - ts_int) > 5 * 60:
        return False

    # Pull the HMAC key out of the secret. Svix secrets are
    # ``whsec_<base64>``; older raw secrets (no prefix) are accepted
    # as-is so dev/test setups don't have to mint a Svix one.
    if secret.startswith("whsec_"):
        try:
            key = base64.b64decode(secret[len("whsec_"):])
        except Exception:
            return False
    else:
        key = secret.encode()

    signed = f"{svix_id}.{svix_ts}.".encode() + raw_body
    mac = hmac.new(key, signed, hashlib.sha256).digest()
    expected_b64 = base64.b64encode(mac).decode()

    # Header may carry multiple sigs separated by spaces (key rotation).
    # Each entry is "<version>,<base64>" — only v1 is defined today.
    for token in svix_sig.split():
        if "," not in token:
            continue
        version, sig_b64 = token.split(",", 1)
        if version != "v1":
            continue
        if hmac.compare_digest(sig_b64, expected_b64):
            return True
    return False


# ─── Backends ──────────────────────────────────────────────────────

class _Backend:
    def send(self, *, to_email, to_name, subject, html, text,
             attachments: Optional[list[dict]] = None) -> SendResult:
        """Send a transactional email.

        ``attachments`` (optional) is a list of ``{"filename": str,
        "content": bytes}`` dicts. Backends that support attachments
        base64-encode the bytes before putting them on the wire; the
        noop backend just logs metadata. Keeping the caller interface
        as raw bytes means we only encode once, at the edge."""
        raise NotImplementedError


class _NoopBackend(_Backend):
    """Logs only. Used in tests and when no provider is configured."""

    def send(self, *, to_email, to_name, subject, html, text,
             attachments: Optional[list[dict]] = None) -> SendResult:
        att_bytes = sum(len(a.get("content", b"")) for a in (attachments or []))
        log.info(
            "[emailer:noop] would send to=%s subject=%s html_bytes=%d attachments=%d(%d bytes)",
            to_email, subject, len(html), len(attachments or []), att_bytes,
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

    def send(self, *, to_email, to_name, subject, html, text,
             attachments: Optional[list[dict]] = None) -> SendResult:
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
        if attachments:
            # Resend expects base64-encoded content. Each item: {filename, content}.
            payload["attachments"] = [
                {
                    "filename": a["filename"],
                    "content": base64.b64encode(a["content"]).decode("ascii"),
                }
                for a in attachments
                if a.get("content") is not None
            ]

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


def _esc(s) -> str:
    """HTML escape for template interpolation."""
    if s is None:
        return ""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))
