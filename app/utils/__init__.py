"""Pure utility functions with no external dependencies.

Extracted from app/dependencies.py to break up the god module.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from ..constants import IST


# ─── Time helpers ─────────────────────────────────────────────────

def now_ist():
    return datetime.now(IST)


def fmt_ist(ts_str):
    if not ts_str:
        return ""
    try:
        if isinstance(ts_str, datetime):
            dt = ts_str
        else:
            dt = datetime.fromisoformat(str(ts_str).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(IST).strftime("%d %b %Y, %I:%M:%S %p IST")
    except Exception:
        return str(ts_str)


def ts_to_id(ts_str: str) -> int:
    try:
        dt = datetime.fromisoformat(str(ts_str).replace('Z', '+00:00'))
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0


# ─── Excel / CSV safety ───────────────────────────────────────────

def _xlsx_safe(v):
    if isinstance(v, str) and v and v[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


# ─── Filename / path helpers ──────────────────────────────────────

def _safe_filename(s: str, fallback: str = "file") -> str:
    if not s:
        return fallback
    cleaned = "".join(c for c in str(s) if c.isalnum() or c in "-_.")[:80]
    return cleaned or fallback


def _safe_path_component(s: str, fallback: str = "path") -> str:
    """Strip directory traversal, keep only safe chars.  Use for URL
    params that become path segments."""
    if not s:
        return fallback
    return _safe_filename(Path(str(s)).name, fallback)


def _assert_within_directory(path: Path, base: Path) -> None:
    """Raise ValueError if *path* is not a descendant of *base*."""
    path.resolve().relative_to(base.resolve())


# ─── HTML escaping ────────────────────────────────────────────────

def _html_escape(s) -> str:
    """Escape user data for safe embedding in HTML body OR attributes.

    Wraps stdlib `html.escape(quote=True)` so the standard CodeQL/Bandit
    sanitizer pattern is recognised, then adds `/` escaping. The slash
    isn't dangerous in HTML body context, but escaping it prevents a
    `</script>` breakout if the value is ever interpolated near a
    `<script>` block — defence-in-depth for templates that mix HTML and
    inline JS.

    All callers (auth router, emailer, invite landing, scorecards) put
    the result in HTML body or attribute context, where browsers decode
    `&#x27;` back to `'` and `&#x2F;` back to `/` — display is unchanged.
    """
    if s is None:
        return ""
    import html as _html
    return _html.escape(str(s), quote=True).replace("/", "&#x2F;")
