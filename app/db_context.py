"""Per-request tenant context for DB-level RLS (session-context model).

Holds the authenticated principal's {role, teacher_id, org_id, account_id} in a
ContextVar and emits it as ``SET LOCAL app.*`` on the asyncpg connection so the
RLS policies in migrations/phase124_rls_session_context.sql can ENFORCE tenant
isolation (instead of app-layer scoping alone).

Gated by the ``RLS_SESSION_CONTEXT`` env flag. While it is off (the default),
``current_context()`` is irrelevant — the execute layer never opens the extra
transaction and behaviour is byte-identical to today. The flag flips on only at
the staged cutover (restricted ``procta_app`` role + phase124 policies live);
see docs/TENANCY_RLS_HARDENING.md.

ContextVars are per-asyncio-Task, and Starlette runs each request in its own
task context, so a value set during one request never leaks into another.
"""
from __future__ import annotations

import contextvars
import os

RLS_SESSION_CONTEXT: bool = os.environ.get("RLS_SESSION_CONTEXT", "").strip().lower() in (
    "1", "true", "yes", "on",
)

# Loud guard: enabling this flips every query onto the DB row-level-security
# path, which only works if the phase124 policy set covers EVERY authenticated
# read. It does NOT yet (e.g. student_invites has no student-read policy, so
# the student lobby silently resolves 0 exams; migrations/rls_policies.sql is
# also stale Supabase auth.uid()-based). Keep this OFF until the policy set is
# completed and verified in staging — otherwise students/teachers get empty
# results with no error. Warn at import so flipping it can never be silent.
if RLS_SESSION_CONTEXT:
    import logging as _logging
    _logging.getLogger("app.db_context").warning(
        "RLS_SESSION_CONTEXT is ENABLED — DB row-level security now gates every "
        "query, but the policy set is known-incomplete for student reads "
        "(student_invites has no student-read policy). This silently empties "
        "the student lobby. Disable RLS_SESSION_CONTEXT unless the phase124 "
        "policies have been completed and tested."
    )

# Roles the policies understand. Anything else is coerced to the most-restrictive
# sensible value so a malformed role can never widen access.
_VALID_ROLES = frozenset({"superadmin", "admin", "owner", "teacher", "student", "system"})

_ctx: "contextvars.ContextVar[dict | None]" = contextvars.ContextVar("rls_ctx", default=None)


def _norm_role(role: str | None, *, has_teacher: bool, has_account: bool) -> str:
    r = (role or "").strip().lower()
    if r in _VALID_ROLES:
        return r
    # Unknown/blank role: pick the least-privileged identity that still matches
    # what we know about the principal. Never default to a privileged role.
    if has_account:
        return "student"
    if has_teacher:
        return "teacher"
    return ""  # no identity → policies see NULL context → deny-all (safe)


def set_context(*, role: str | None = None, teacher_id=None, org_id=None,
                account_id=None) -> "contextvars.Token":
    """Set the tenant context for the current request/task. Returns a token for
    reset_context() (optional — task isolation usually makes reset unnecessary)."""
    ctx = {
        "role": _norm_role(role, has_teacher=bool(teacher_id), has_account=bool(account_id)),
        "teacher_id": str(teacher_id or ""),
        "org_id": str(org_id or ""),
        "account_id": str(account_id or ""),
    }
    return _ctx.set(ctx)


def set_system_context() -> "contextvars.Token":
    """Full cross-tenant context for background work (reaper, billing, RQ jobs,
    reconciler) that legitimately operates across tenants."""
    return _ctx.set({"role": "system", "teacher_id": "", "org_id": "", "account_id": ""})


def reset_context(token) -> None:
    try:
        _ctx.reset(token)
    except Exception:
        pass


def current_context() -> dict | None:
    return _ctx.get()


async def apply_to_connection(conn, ctx: dict) -> None:
    """Emit the context as transaction-local GUCs on ``conn``. Uses
    ``set_config(key, value, is_local := true)`` — the parameterized equivalent
    of ``SET LOCAL`` (plain SET LOCAL can't take bind params). Empty strings
    become NULL via the ``nullif(..., '')`` in the app.* accessors. MUST be
    called inside an open transaction so the settings persist across the
    statement(s) that follow (and so pgbouncer transaction-pooling keeps them on
    one backend)."""
    await conn.execute(
        "SELECT set_config('app.role', $1, true),"
        "       set_config('app.teacher_id', $2, true),"
        "       set_config('app.org_id', $3, true),"
        "       set_config('app.account_id', $4, true)",
        ctx.get("role", ""), ctx.get("teacher_id", ""),
        ctx.get("org_id", ""), ctx.get("account_id", ""),
    )
