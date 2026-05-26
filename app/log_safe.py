"""Sanitiser for values interpolated into log records.

The :func:`safe` helper strips control characters (CR, LF, NUL, and the
rest of the C0 / C1 ranges) before a value reaches a ``logger`` call.
That defeats the log-injection vector where an attacker submits a roll
number / email / session id containing newlines and forges an extra
log line such as ``X-User: admin user=victim status=admin``.

This is intentionally a small ``re.sub``-based wrapper: CodeQL's
``py/log-injection`` query recognises ``re.sub`` calls that replace the
dangerous characters as a sanitiser, so wrapping a tainted value at the
log site silences the alert without per-line annotations.

Use it at every log statement that interpolates a value derived from a
request body, query string, header, or DB row that originated from one.
Don't bother for literals, integers, ``exc.__class__.__name__``, or
constants — they're already safe.

    from .log_safe import safe
    logger.info("session %s not found", safe(session_id))
"""
from __future__ import annotations

import re
from typing import Any

# Strip C0 (\\x00-\\x1f), DEL (\\x7f), and C1 (\\x80-\\x9f) controls.
# We deliberately keep printable Unicode (including non-Latin scripts)
# because student names and roll numbers can be Devanagari / Tamil /
# emoji and we want them readable in logs.
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")

# Cap to keep one rogue value from drowning a log line. Real values
# (emails, JTIs, plan IDs) are well under this.
_MAX = 200


def safe(value: Any) -> str:
    """Return ``value`` coerced to a control-char-free string.

    None-safe, exception-safe; never raises. Truncates at 200 chars
    with an ellipsis suffix so long payloads can't fill a log buffer.
    """
    try:
        text = "" if value is None else str(value)
    except Exception:
        # __str__ on some objects can blow up — better to log a
        # placeholder than to crash the calling handler.
        return "<unprintable>"
    sanitised = _CONTROL.sub("", text)
    if len(sanitised) > _MAX:
        sanitised = sanitised[: _MAX - 1] + "…"
    return sanitised


__all__ = ["safe"]
