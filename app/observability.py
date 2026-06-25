"""Shared Sentry configuration — ONE PII scrubber for every component.

Procta is a proctoring app: HTTP requests and background jobs carry OTP codes,
reauth tokens, exam-answer payloads, student roll numbers / emails, and (in the
RQ worker) recipient emails + names as job arguments. Every process that ships
errors to Sentry — the FastAPI API (app/main.py) AND the RQ worker (worker.py) —
MUST run the SAME scrubber, or one process quietly leaks what another carefully
redacts (DPDP §8 — reasonable security safeguards). This module is the single
source of truth so the two cannot drift.

(execsvc/app.py is a SEPARATE deployment that only ever sees source + stdin; it
ships neither bodies nor frame locals and intentionally does not depend on this
app/ module.)
"""
import json
import re

# Header names that must never leave the server (compared case-insensitively —
# Sentry preserves whatever casing the client sent).
SENTRY_REDACT_HEADERS = frozenset({
    "authorization", "cookie", "set-cookie",
    "x-csrf-token", "x-reauth-token", "x-api-key",
    "x-forwarded-for",  # IP — also covered by send_default_pii=False
})

# URL / body / job-arg keys whose VALUES are PII or auth material.
SENTRY_REDACT_KEYS = frozenset({
    "password", "code", "otp", "token", "reauth_token", "refresh_token",
    "captcha_token", "access_code", "key", "secret",
    "answer", "answers", "email", "roll_number", "full_name", "name",
    # Student PII (Indian exam context): never ship to error tracking.
    "phone", "address", "dob", "aadhaar",
})

_REDACT_QS_RE = re.compile(
    r"(?i)(\b(?:" + "|".join(SENTRY_REDACT_KEYS) + r")=)[^&]*"
)


def _scrub_dict(d):
    """Recursively redact PII-shaped keys in a dict/list payload (in place)."""
    if isinstance(d, dict):
        for k in list(d.keys()):
            if k.lower() in SENTRY_REDACT_KEYS:
                d[k] = "[REDACTED]"
            else:
                _scrub_dict(d[k])
    elif isinstance(d, list):
        for item in d:
            _scrub_dict(item)
    return d


def scrub_sentry_event(event, hint):
    """Strip PII + auth material before an event leaves the host.

    Proctoring requests carry OTP codes in bodies, reauth tokens in headers,
    exam-answer payloads, and student roll/emails. Background jobs carry the
    same in their arguments. Default Sentry capture would ship all of these to
    a third-party host. Safe to use as `before_send` for HTTP and non-HTTP
    (worker) events alike — the `request` sections simply don't exist for the
    latter, and the exception-value scrub still applies.
    """
    req = event.get("request") or {}
    # Headers — case-insensitive redact.
    headers = req.get("headers") or {}
    for h in list(headers.keys()):
        if h.lower() in SENTRY_REDACT_HEADERS:
            headers[h] = "[REDACTED]"
    # Query string (Sentry stores both `query_string` and `url`).
    if req.get("query_string"):
        req["query_string"] = _REDACT_QS_RE.sub(r"\1[REDACTED]", req["query_string"])
    if "?" in (req.get("url") or ""):
        base, qs = req["url"].split("?", 1)
        req["url"] = base + "?" + _REDACT_QS_RE.sub(r"\1[REDACTED]", qs)
    # Request body — dict gets keys redacted; a string body is parsed as JSON
    # and scrubbed key-wise when possible, else redacted wholesale if it looks
    # like it carries PII.
    body = req.get("data")
    if isinstance(body, (dict, list)):
        _scrub_dict(body)
    elif isinstance(body, str):
        try:
            req["data"] = _scrub_dict(json.loads(body))
        except (ValueError, TypeError):
            low = body.lower()
            if any(k in low for k in SENTRY_REDACT_KEYS):
                req["data"] = "[REDACTED — contained PII]"
    # `extra` carries worker job context (job_args etc.) and any app-set
    # extras — scrub PII-shaped keys/values there too.
    extra = event.get("extra")
    if isinstance(extra, (dict, list)):
        _scrub_dict(extra)
    # Exception value strings can leak too (e.g. a ValueError echoing an OTP).
    for exc in (event.get("exception") or {}).get("values") or []:
        val = exc.get("value")
        if isinstance(val, str):
            exc["value"] = _REDACT_QS_RE.sub(r"\1[REDACTED]", val)
    return event


# Defaults every Procta Sentry init should pass. PII off, stack-frame locals off
# (they hold the exact OTP/answer/email the before_send hook strips elsewhere),
# and a small request-body window so the key-wise scrub isn't truncated mid-JSON.
SAFE_SENTRY_KWARGS = dict(
    send_default_pii=False,
    include_local_variables=False,
    max_request_body_size="small",
)
