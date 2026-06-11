"""Sanitiser for values interpolated into log records.

The :func:`safe` helper neutralises control characters (CR, LF, NUL,
and the rest of the C0 / C1 ranges) before a value reaches a
``logger`` call. That defeats the log-injection vector where an
attacker submits a roll number / email / session id containing
newlines and forges an extra log line such as
``X-User: admin user=victim status=admin``.

Implementation note: the sanitisation goes through
:func:`urllib.parse.quote` (with a permissive ``safe=`` set so the
common readable chars in emails, JTIs, and UUIDs survive intact)
because CodeQL's stock ``py/log-injection`` model recognises
``urllib.parse.quote`` as a sanitiser. A bare ``re.sub`` doesn't get
recognised, so we'd be left with a wall of "I sanitised this, I
promise" annotations on every flagged line. ``quote`` is in the
allow-list — wrapping at the log site silences the alert and we
keep readable output.

Use it at every log statement that interpolates a value derived
from a request body, query string, header, or DB row that
originated from one. Don't bother for literals, integers,
``exc.__class__.__name__``, or constants — they're already safe.

    from .log_safe import safe
    logger.info("session %s not found", safe(session_id))
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

# Characters we want preserved verbatim in log output. Covers email
# (``@.``), JTIs / UUIDs (``-``), Razorpay IDs (``_``), org IDs
# (``:``), URL-ish values (``/?&=``), and percentages used in score
# strings. quote() will percent-encode anything else, which both
# neutralises control characters and keeps non-ASCII readable when
# possible — non-ASCII printables get percent-encoded, which is a
# minor readability hit but acceptable for log lines.
# ``*`` is included so the mask token from :func:`mask_email`
# (``a***@domain``) renders readably instead of percent-encoded; it is a
# printable with no control-character / log-forging significance.
_SAFE_CHARS = "@.-_:/?&=,+%[]()*"

_MAX = 200


def safe(value: Any) -> str:
    """Return ``value`` coerced to a log-injection-safe string.

    None-safe, exception-safe; never raises. Truncates at 200 chars
    with an ellipsis suffix so a rogue value can't fill a log buffer.

    The output is :func:`urllib.parse.quote` of ``str(value)`` —
    CodeQL recognises this as a sanitiser, and the result is still
    readable for typical log values (emails, UUIDs, IDs).
    """
    try:
        text = "" if value is None else str(value)
    except Exception:
        # __str__ on some objects can blow up — better to log a
        # placeholder than to crash the calling handler.
        return "<unprintable>"
    sanitised = quote(text, safe=_SAFE_CHARS)
    if len(sanitised) > _MAX:
        sanitised = sanitised[: _MAX - 1] + "…"
    return sanitised


def mask_email(value: Any) -> str:
    """Return an email reduced to a non-identifying log token: ``a***@domain``.

    Logging full teacher/student email addresses writes PII into log files
    that may be shipped off-box or retained — inconsistent with a product
    whose posture is "no PII egress". The masked form keeps the first local
    char and the full domain, which preserves enough to correlate and debug
    (which provider, which tenant) without persisting the identifier.

    None-safe and exception-safe; never raises. Non-email values (no ``@``)
    fall through to :func:`safe` so call sites can pass any value uniformly.
    The masked result is itself run through :func:`safe`, so it remains
    log-injection-safe like every other value at the log site.
    """
    try:
        text = "" if value is None else str(value)
    except Exception:
        return "<unprintable>"
    if "@" not in text:
        return safe(text)
    local, _, domain = text.partition("@")
    return safe(f"{(local[:1] or '')}***@{domain}")


__all__ = ["safe", "mask_email"]
