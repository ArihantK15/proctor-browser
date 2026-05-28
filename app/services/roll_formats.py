"""Roll-number format detection for Indian academic identifiers.

Coaching institutes and exam boards each have their own canonical
roll-number shape. When a teacher uploads a bulk-import CSV we want to
auto-detect which format dominates the file and flag rows that don't
match — that catches the typical "I copy-pasted from two different
spreadsheets" footgun before the rows hit the DB.

This is intentionally pattern-only, not authoritative. We don't call
out to NIC / DGE / NTA APIs to verify a number actually exists; that
needs the Aadhaar e-KYC path on the audit roadmap. The goal here is
just "these 27 rows look like they're from JEE Main, 3 look like
school roll numbers — flag the 3 for the teacher to fix before we
upsert."

Formats covered (extend as new ICPs land):

  cbse_board       8 digits, all numeric (CBSE board roll number)
  jee_main         9-10 digits, NTA application number (often "2026" prefix)
  jee_advanced     11 alphanumeric, IIT-issued (e.g. "23A12345678")
  neet             11 digits, NTA NEET-UG application number
  upsc             11 digits, CSE application number
  generic_alnum    fallback for valid-looking alphanumeric roll
  invalid          empty / bad characters / wrong length entirely

Designed for fast O(1) classification of each row plus an O(n)
"detect the dominant format" pass over the whole file.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

# Canonical format keys. Order matters for "best match wins" when a
# roll number could fit multiple patterns; place more specific
# patterns above the generic fallback.
_FORMAT_PATTERNS: list[tuple[str, re.Pattern]] = [
    # JEE Advanced — IIT-side identifier, mixed alphanumeric.
    ("jee_advanced", re.compile(r"^[0-9]{2}[A-Z][0-9]{8}$")),
    # CBSE board roll number — 8 digits, no letters.
    ("cbse_board",   re.compile(r"^[0-9]{8}$")),
    # JEE Main / NEET / UPSC — 9-11 digit NTA-style application IDs.
    # We collapse these three into one bucket because pattern-only we
    # can't tell them apart (all are 9-11 digit numeric). The teacher
    # can add a custom prefix at exam-config time to disambiguate.
    ("nta_app_id",   re.compile(r"^[0-9]{9,11}$")),
    # Generic safe-looking alnum — letters + digits + dash + underscore,
    # 4-32 chars. Used by school-internal roll numbers, GATE, GMAT,
    # custom coaching-institute IDs.
    ("generic_alnum", re.compile(r"^[A-Z0-9_-]{4,32}$")),
]

_HUMAN_LABELS = {
    "cbse_board":    "CBSE board (8-digit)",
    "jee_advanced":  "JEE Advanced (IIT alphanumeric)",
    "nta_app_id":    "NTA application ID (JEE Main / NEET / UPSC)",
    "generic_alnum": "Generic alphanumeric",
    "invalid":       "Invalid / unrecognised",
}


def classify_roll(roll: str) -> str:
    """Return the canonical format key for a single roll number.

    Empty string or characters outside [A-Z0-9_-] return ``"invalid"``.
    All comparisons are uppercased to match the bulk-register endpoint's
    own ``roll.upper()`` normalisation.
    """
    if not roll or not isinstance(roll, str):
        return "invalid"
    candidate = roll.strip().upper()
    if not candidate:
        return "invalid"
    for key, pattern in _FORMAT_PATTERNS:
        if pattern.match(candidate):
            return key
    return "invalid"


def detect_dominant_format(rolls: Iterable[str]) -> tuple[str, dict[str, int]]:
    """Classify every roll and return (dominant_format, counts).

    ``dominant_format`` is the key with the most matches that *isn't*
    ``invalid`` (so a file with 95 % valid CBSE rolls + 5 % typos still
    reports ``"cbse_board"`` as dominant). If every row is invalid, the
    dominant format is ``"invalid"``.

    The counts dict has one entry per format key encountered, useful
    for the dry-run preview UI:

        {"cbse_board": 297, "nta_app_id": 3, "invalid": 1}
    """
    counts: Counter[str] = Counter()
    for r in rolls:
        counts[classify_roll(r)] += 1

    # Filter out the invalid bucket when picking the dominant valid
    # format. Fall back to "invalid" only if literally every row is bad.
    valid_only = {k: v for k, v in counts.items() if k != "invalid"}
    if not valid_only:
        dominant = "invalid"
    else:
        dominant = max(valid_only, key=valid_only.get)
    return dominant, dict(counts)


def format_label(key: str) -> str:
    """Human-readable label for the UI."""
    return _HUMAN_LABELS.get(key, key)


__all__ = ["classify_roll", "detect_dominant_format", "format_label"]
