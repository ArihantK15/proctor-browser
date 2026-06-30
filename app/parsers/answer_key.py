"""Parse a trailing answer-key section into {question_number: answer}.

Pure, no I/O. Handles the dominant institute formats:
  1-C 2-A 3-D   |   1. C  2) A   |   1 - AC (multi)   |   1-42 (numeric)
"""
import re
from typing import Any

# qnum <sep> answer, where answer is letters (A-F, one or more) or a number.
_PAIR = re.compile(
    r"(?<![A-Za-z0-9])(\d{1,3})\s*[-.\)\:]\s*([A-Fa-f]{1,6}|\d+(?:\.\d+)?)(?![A-Za-z0-9])"
)
# Whitespace-separated pairs ("1 A"), as in tabular answer keys. Letters only —
# numeric answers stay on the strict (separator) form to avoid mis-pairing two
# adjacent numbers. Applied only inside an isolated answer-key section.
_PAIR_WS = re.compile(r"(?<![A-Za-z0-9])(\d{1,3})\s+([A-Fa-f]{1,6})(?![A-Za-z0-9])")
_HEADING = re.compile(r"(?im)^\s*(answers?|answer\s*keys?|solutions?)\s*:?\s*$")


def parse_answer_key(text: str) -> dict[int, Any]:
    out: dict[int, Any] = {}
    for m in _PAIR.finditer(text or ""):
        num = int(m.group(1))
        ans = m.group(2)
        # Letters → upper; numbers stay as-is.
        if re.fullmatch(r"[A-Fa-f]{1,6}", ans):
            ans = ans.upper()
        out[num] = ans
    # Fill any gaps from whitespace-separated tabular keys. Strict form (above)
    # takes precedence where both could match the same question number.
    for m in _PAIR_WS.finditer(text or ""):
        num = int(m.group(1))
        if num not in out:
            out[num] = m.group(2).upper()
    return out


def find_answer_key_block(text: str) -> tuple[str, dict[int, Any]]:
    """Split body from a trailing answer-key section.

    Strategy: if an 'Answers'/'Answer Key'/'Solutions' heading exists, everything
    after the LAST such heading is the key and everything before is the body.
    Otherwise return the whole text as body with an empty key (answers, if any,
    are inline).
    """
    if not text:
        return "", {}
    last = None
    for m in _HEADING.finditer(text):
        last = m
    if not last:
        return text, {}
    body = text[: last.start()].rstrip()
    key = parse_answer_key(text[last.end():])
    return body, key
