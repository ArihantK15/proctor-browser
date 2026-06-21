"""Pure output-judging for coding submissions (Edge Compiler, spec Approach A).

No code execution happens here — the client ran the student's code; this only
compares the client's outputs to the secret expected outputs the server holds.
Keeping it a pure function (no DB, no I/O) makes it exhaustively unit-testable and
keeps the judge endpoint thin. Normalization is deliberately CONSERVATIVE: we trim
trailing whitespace + trailing blank lines (the common harmless diff), but never
touch interior spacing — that can be significant, and authors print canonical output.
"""
from __future__ import annotations


def normalize_output(s: str | None) -> str:
    """Trim trailing whitespace per line and drop trailing blank lines. CRLF→LF."""
    lines = (s or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = [ln.rstrip() for ln in lines]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _float_match(a: str, e: str, tol: float) -> bool:
    """Compare two whitespace-separated numeric vectors within an absolute tol.

    Used only when the test case declares a float_tolerance — otherwise exact
    string equality applies. Any non-numeric token, or a differing value count,
    is a fail (never an exception)."""
    try:
        af = [float(x) for x in a.split()]
        ef = [float(x) for x in e.split()]
    except (ValueError, AttributeError):
        return False
    if len(af) != len(ef):
        return False
    return all(abs(x - y) <= tol for x, y in zip(af, ef))


def judge_outputs(actual: list, expected: list, tolerances: list) -> dict:
    """Compare client outputs to expected outputs.

    Args:
        actual:     client-produced outputs, index-aligned to ``expected``. A
                    missing/None entry counts as a fail (never raises).
        expected:   the secret expected outputs (server-side only). Defines ``total``.
        tolerances: per-case float tolerance or None (exact match) — index-aligned.

    Returns ``{"passed": int, "total": int, "per_case": list[bool]}``. Never leaks
    the expected values; the caller returns only ``passed``/``total`` to the client.
    """
    total = len(expected)
    per_case: list[bool] = []
    for i in range(total):
        e = normalize_output(expected[i])
        has_actual = i < len(actual) and actual[i] is not None
        a = normalize_output(actual[i]) if has_actual else None
        tol = tolerances[i] if i < len(tolerances) else None
        if a is None:
            per_case.append(False)
        elif tol is not None:
            per_case.append(_float_match(a, e, tol))
        else:
            per_case.append(a == e)
    return {"passed": sum(per_case), "total": total, "per_case": per_case}
