"""Helpers for surfacing the per-org student-quota trigger cleanly.

phase90/91 added a BEFORE INSERT trigger on students that raises
SQLSTATE 23514 (check_violation) when accepting the row would push
the org past organizations.max_students. asyncpg surfaces that as a
CheckViolationError.

Two layers of enforcement live in the stack:

1. `app/services/sessions.py:check_org_limits()` — pre-flight check
   the app does before a bulk write. Returns a clean HTTP 403 if the
   delta would overflow.
2. The DB trigger — last-line backstop that catches races (two
   parallel bulk imports, code regressions that skip the pre-flight).

When (2) fires, the calling endpoint sees a generic asyncpg
exception. Without this helper, every quota violation gets bucketed
as "row skipped" or "internal error" and the operator can't tell
why the import didn't complete. `is_quota_error()` lets call sites
detect the specific case and surface a meaningful message.
"""
from __future__ import annotations


# Substring that appears in the trigger's RAISE EXCEPTION text. The
# trigger uses 'Student quota exceeded for organization %' so any
# match on this fragment is reliable.
_QUOTA_MSG_FRAGMENT = "Student quota exceeded"

# SQLSTATE we set in phase90 (check_violation).
_QUOTA_SQLSTATE = "23514"


def is_quota_error(exc: BaseException) -> bool:
    """Return True if `exc` looks like the org-student-quota trigger
    firing rather than an unrelated check-constraint failure.

    We can't just match on SQLSTATE because other CHECK constraints
    (phase85 enum checks, appeals/consent_records CHECKs) share
    23514. Combining the SQLSTATE with the trigger's distinctive
    message keeps false-positives off.
    """
    # asyncpg exceptions expose `sqlstate`; the broader Exception
    # path falls through to the message-only check.
    sqlstate = getattr(exc, "sqlstate", None)
    msg = str(exc) or ""
    if sqlstate == _QUOTA_SQLSTATE and _QUOTA_MSG_FRAGMENT in msg:
        return True
    # Fallback: psycopg / supabase-py / older asyncpg may not expose
    # sqlstate cleanly. Match on the message alone.
    return _QUOTA_MSG_FRAGMENT in msg


class QuotaExceededError(Exception):
    """Raised by call-site wrappers when the org-student trigger
    rejects an insert. Carries the original DB exception for logging.
    """

    def __init__(self, original: BaseException, msg: str | None = None):
        super().__init__(msg or str(original))
        self.original = original
