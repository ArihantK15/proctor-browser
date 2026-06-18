"""RLS session-context (phase124 step B) — role coercion + task isolation.

Security-critical: a malformed/unknown role must never resolve to a privileged
one, and context must not leak across asyncio tasks (FastAPI requests).
"""
import asyncio

from app import db_context as dc


def _fresh():
    # Each test runs in its own task so the ContextVar starts clean.
    dc.reset_context(dc._ctx.set(None))


def test_valid_roles_preserved():
    for role in ("superadmin", "admin", "owner", "teacher", "student", "system"):
        dc.set_context(role=role, teacher_id="t1")
        assert dc.current_context()["role"] == role


def test_unknown_role_no_identity_denies():
    # No identity → empty role → policies see NULL → deny-all (never privileged).
    dc.set_context(role="root", teacher_id="", account_id="")
    assert dc.current_context()["role"] == ""


def test_unknown_role_coerces_to_least_privilege_by_identity():
    dc.set_context(role="bogus", teacher_id="t1")
    assert dc.current_context()["role"] == "teacher"
    dc.set_context(role="bogus", account_id="a1")
    assert dc.current_context()["role"] == "student"


def test_blank_role_never_privileged():
    dc.set_context(role=None, teacher_id="t1", org_id="o1")
    assert dc.current_context()["role"] == "teacher"  # not admin/superadmin


def test_system_context():
    dc.set_system_context()
    c = dc.current_context()
    assert c["role"] == "system" and c["teacher_id"] == "" and c["account_id"] == ""


def test_ids_stringified():
    dc.set_context(role="teacher", teacher_id=123, org_id=456)
    c = dc.current_context()
    assert c["teacher_id"] == "123" and c["org_id"] == "456"


def test_task_isolation():
    # Context set in one task must not leak into a sibling task.
    dc.set_context(role="teacher", teacher_id="outer")

    async def _inner():
        # Fresh task → inherits a COPY; setting here must not affect the parent
        # after the task completes (ContextVar copy semantics).
        assert dc.current_context()["teacher_id"] == "outer"
        dc.set_context(role="student", account_id="inner")
        assert dc.current_context()["role"] == "student"

    async def _runner():
        await asyncio.create_task(_inner())
        # Parent context unchanged by the child task.
        assert dc.current_context()["teacher_id"] == "outer"
        assert dc.current_context()["role"] == "teacher"

    asyncio.run(_runner())


def test_flag_off_by_default():
    # RLS_SESSION_CONTEXT is evaluated at import; absent in the test env → False,
    # so the execute layer takes the byte-identical autocommit path.
    assert dc.RLS_SESSION_CONTEXT is False


# ── apply_request_context (raw-asyncpg-path RLS context) ──────────────

class _CapConn:
    def __init__(self):
        self.calls = []

    async def execute(self, sql, *args):
        self.calls.append(args)


def test_apply_request_context_noop_when_disabled(monkeypatch):
    _fresh()
    monkeypatch.setattr(dc, "RLS_SESSION_CONTEXT", False)
    conn = _CapConn()
    dc.set_context(role="teacher", teacher_id="t1")
    asyncio.run(dc.apply_request_context(conn))
    assert conn.calls == []  # gated off → no GUCs emitted


def test_apply_request_context_uses_current_context(monkeypatch):
    _fresh()
    monkeypatch.setattr(dc, "RLS_SESSION_CONTEXT", True)
    conn = _CapConn()
    dc.set_context(role="teacher", teacher_id="t1", org_id="o1")

    async def _run():
        await dc.apply_request_context(conn)
    asyncio.run(_run())
    assert len(conn.calls) == 1
    role, teacher_id, org_id, account_id = conn.calls[0]
    assert (role, teacher_id, org_id) == ("teacher", "t1", "o1")


def test_apply_request_context_defaults_to_system_without_context(monkeypatch):
    monkeypatch.setattr(dc, "RLS_SESSION_CONTEXT", True)
    conn = _CapConn()
    dc._ctx.set(None)  # clear any ambient context leaked from a prior test
    asyncio.run(dc.apply_request_context(conn))  # no request context → system
    assert conn.calls[0][0] == "system"


def test_apply_request_context_force_system_overrides_request_context(monkeypatch):
    _fresh()
    monkeypatch.setattr(dc, "RLS_SESSION_CONTEXT", True)
    conn = _CapConn()
    dc.set_context(role="teacher", teacher_id="t1")  # would otherwise be teacher
    asyncio.run(dc.apply_request_context(conn, force_system=True))
    assert conn.calls[0][0] == "system"
    assert conn.calls[0][1] == ""  # teacher_id blanked under system
