"""Guards for the RLS session-context elevation (phase124/phase125 cutover).

The student lobby + a few authed flows perform server-side reconciliation
writes to teacher-owned rows. Under RLS those must run as `system` or they
silently affect 0 rows (the 2026-06-17 empty-lobby incident). These tests
pin that wiring so it can't be removed without a failure.
"""
import os
import asyncio
from unittest.mock import patch, MagicMock

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")


def test_system_context_elevates_then_restores():
    from app.db_context import system_context, set_context, current_context, reset_context
    tok = set_context(role="student", account_id="acct-1")
    try:
        assert current_context()["role"] == "student"
        with system_context():
            assert current_context()["role"] == "system"
        # prior student context is restored on exit
        assert current_context()["role"] == "student"
    finally:
        reset_context(tok)


def test_enrollment_autolink_runs_as_system_under_rls():
    """The stranded-roster auto-link write must execute under the `system`
    context (so RLS lets it touch teacher-owned `students` rows); the
    account-scoped read after it stays under the caller's context."""
    from app.routers import auth as authmod
    from app.db_context import set_context, current_context, reset_context

    roles_at_op = []

    class _FakeQ:
        def update(self, *a, **k): return self
        def ilike(self, *a, **k): return self
        def is_(self, *a, **k): return self
        def select(self, *a, **k): return self
        def eq(self, *a, **k): return self
        async def execute(self):
            roles_at_op.append((current_context() or {}).get("role"))
            return MagicMock(data=[])

    tok = set_context(role="student", account_id="acct-1")
    try:
        with patch.object(authmod, "_atable", lambda name: _FakeQ()):
            asyncio.run(authmod._student_enrollments_for_account(
                {"id": "acct-1"}, "stud@a.com", "roll_number, teacher_id"))
    finally:
        reset_context(tok)

    # First DB op is the auto-link UPDATE — must be elevated to system.
    assert roles_at_op, "expected the auto-link + read to hit the DB"
    assert roles_at_op[0] == "system", \
        f"roster auto-link must run as system under RLS, ran as {roles_at_op[0]!r}"
    # The subsequent account-scoped read runs under the caller's own context.
    if len(roles_at_op) > 1:
        assert roles_at_op[1] == "student"
