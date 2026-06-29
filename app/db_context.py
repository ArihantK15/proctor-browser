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

# Loud guard: this flag and the DB connection ROLE must be flipped together.
# RLS only enforces when the app connects as the restricted `procta_app` role
# (NOBYPASSRLS). With that role, this flag is what supplies the per-request
# tenant context — so:
#   • role=procta_app + flag=1  → enforced + scoped   (the intended state)
#   • role=procta_app + flag=0  → DENY-ALL: no context is ever set, every
#     SELECT returns 0 rows, ALL logins fail "invalid email/password"
#     (the 2026-06-17 outage). NEVER run this combination.
#   • role=owner (procta)       → RLS bypassed regardless of the flag (rollback)
# Policy set: phase124 + phase125 (student reads) + app-layer system_context()
# elevation for server-side reconciliation writes — verified end-to-end as
# procta_app. Cutover/rollback flips DATABASE_URL *and* this flag together;
# see docs/TENANCY_RLS_HARDENING.md. Warn at import so a flip is never silent.
if RLS_SESSION_CONTEXT:
    import logging as _logging
    _logging.getLogger("app.db_context").warning(
        "RLS_SESSION_CONTEXT is ENABLED — DB row-level security gates every "
        "query. This is ONLY safe when DATABASE_URL points at the restricted "
        "procta_app role. If the app is still connecting as the owner this is a "
        "no-op; if it connects as procta_app with this flag OFF, every query "
        "denies-all (all logins fail). Flip the role and this flag together."
    )

# Roles the policies understand. Anything else is coerced to the most-restrictive
# sensible value so a malformed role can never widen access.
_VALID_ROLES = frozenset({"superadmin", "admin", "owner", "teacher", "student", "system"})

_ctx: contextvars.ContextVar[dict | None] = contextvars.ContextVar("rls_ctx", default=None)


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
                account_id=None) -> contextvars.Token:
    """Set the tenant context for the current request/task. Returns a token for
    reset_context() (optional — task isolation usually makes reset unnecessary)."""
    ctx = {
        "role": _norm_role(role, has_teacher=bool(teacher_id), has_account=bool(account_id)),
        "teacher_id": str(teacher_id or ""),
        "org_id": str(org_id or ""),
        "account_id": str(account_id or ""),
    }
    return _ctx.set(ctx)


def set_system_context() -> contextvars.Token:
    """Full cross-tenant context for background work (reaper, billing, RQ jobs,
    reconciler) that legitimately operates across tenants."""
    return _ctx.set({"role": "system", "teacher_id": "", "org_id": "", "account_id": ""})


def reset_context(token) -> None:
    try:
        _ctx.reset(token)
    except Exception:
        pass


import contextlib as _contextlib


@_contextlib.contextmanager
def system_context():
    """Run a block as the cross-tenant ``system`` principal so RLS does not
    scope it. Use ONLY for server-side reconciliation an authenticated request
    legitimately performs across rows it does not "own" — e.g. auto-linking a
    student's stranded roster rows, propagating an email change to the roster,
    deleting a departing account's roster rows, exam-start auto-enrol. Under
    RLS these writes target teacher-owned tables that the student/exam context
    cannot touch; without elevation they silently affect 0 rows (the empty-lobby
    incident). Restores the prior context on exit.

    No-op-safe when RLS_SESSION_CONTEXT is off (the execute layer ignores the
    context entirely on that path), so it is harmless to leave wired in.
    """
    token = set_system_context()
    try:
        yield
    finally:
        reset_context(token)


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


_SYSTEM_CTX = {"role": "system", "teacher_id": "", "org_id": "", "account_id": ""}


async def apply_request_context(conn, *, force_system: bool = False) -> None:
    """Apply the current tenant context to a RAW asyncpg connection.

    Code paths that bypass ``PostgresTable.execute`` (direct ``pool.acquire()``
    + ``conn.execute``/``fetchrow`` — e.g. multi-statement transactions the
    PostgREST-shaped adapter can't express) MUST call this at the top of their
    open transaction so DB-level RLS scopes them the same way the adapter does.
    Without it, under the restricted ``procta_app`` role those queries carry NO
    ``app.*`` context, ``app.is_privileged()`` is false, and the policies match
    zero rows — silently breaking the operation at the RLS cutover.

    Mirrors ``PostgresTable.execute``: no-op when ``RLS_SESSION_CONTEXT`` is off
    (byte-identical to today), and defaults to the cross-tenant ``system``
    principal when there is no request context (pre-auth signup, workers,
    scripts). Pass ``force_system=True`` for background cross-tenant jobs (the
    TTL sweeper) that must run privileged regardless of any ambient context.

    MUST be called inside an open transaction (the GUCs are set LOCAL).
    """
    if not RLS_SESSION_CONTEXT:
        return
    ctx = dict(_SYSTEM_CTX) if force_system else (current_context() or _SYSTEM_CTX)
    await apply_to_connection(conn, ctx)
