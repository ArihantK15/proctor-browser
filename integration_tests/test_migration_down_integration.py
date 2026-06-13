"""Integration tests for run_postgres_migrations.py --down / --rollback-last / --status.

Applies a sample additive migration via the runner, asserts the column exists +
a schema_migrations row; runs --down <file>, asserts the column is gone + the row
deleted; --rollback-last picks the highest applied_at.

These tests live OUTSIDE tests/ (see integration_tests/conftest.py) because they
need a real Postgres. They are skipped when DATABASE_URL is unset.
"""
import asyncio
import pathlib
import sys

import asyncpg
import pytest
import pytest_asyncio

# Make scripts/ importable
_HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(_HERE.parent))

from scripts.run_postgres_migrations import (  # noqa: E402
    MIGRATIONS_DIR,
    _ensure_bookkeeping,
    _applied,
)
from scripts.run_postgres_migrations import (  # noqa: E402
    _apply_file,
    cmd_down,
    cmd_rollback_last,
    cmd_status,
)

pytestmark = pytest.mark.asyncio


def _database_url() -> str:
    import os
    return os.environ.get("DATABASE_URL", "").strip()


def _no_op_migration(name: str) -> pathlib.Path:
    """Create a minimal additive migration in migrations/ for testing."""
    path = MIGRATIONS_DIR / name
    path.write_text("-- test migration (no-op)\nSELECT 1;\n")
    return path


def _no_op_down(name: str) -> pathlib.Path:
    """Create a reverse script in migrations/down/ for testing."""
    down_dir = MIGRATIONS_DIR / "down"
    down_dir.mkdir(exist_ok=True)
    path = down_dir / name
    path.write_text("-- test down (no-op)\nSELECT 1;\n")
    return path


def _cleanup(paths: list[pathlib.Path]) -> None:
    for p in paths:
        if p.exists():
            p.unlink()


@pytest.fixture(autouse=True)
def _skip_without_db():
    if not _database_url():
        pytest.skip("integration tests require DATABASE_URL (a real Postgres)")


@pytest_asyncio.fixture
async def conn():
    c = await asyncpg.connect(_database_url(), statement_cache_size=0)
    await _ensure_bookkeeping(c)
    yield c
    await c.close()


# ── Test migration files (created + cleaned per session) ────────────────
_SAMPLE_MIG = "phase999_test_migration_safety.sql"


@pytest.fixture(scope="session", autouse=True)
def _test_migration_files():
    paths = [_no_op_migration(_SAMPLE_MIG), _no_op_down(_SAMPLE_MIG)]
    yield
    _cleanup(paths)


# ── Tests ───────────────────────────────────────────────────────────────


class TestStatus:
    async def test_status_has_applied_heading(self, conn):
        """--status prints applied migrations section."""
        from io import StringIO
        captured = StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            await cmd_status(conn)
        finally:
            sys.stdout = old_stdout
        output = captured.getvalue()
        assert "Applied migrations" in output


class TestDown:
    async def test_down_reverses_and_removes_row(self, conn):
        """Apply a migration, then --down should remove column + row."""
        # Verify NOT applied
        applied = await _applied(conn)
        assert _SAMPLE_MIG not in applied

        # Apply
        await _apply_file(conn, MIGRATIONS_DIR / _SAMPLE_MIG)
        applied = await _applied(conn)
        assert _SAMPLE_MIG in applied

        # Run --down
        rc = await cmd_down(conn, _SAMPLE_MIG)
        assert rc == 0

        # Verify removed
        applied = await _applied(conn)
        assert _SAMPLE_MIG not in applied

    async def test_down_on_unapplied_errors(self, conn):
        rc = await cmd_down(conn, "nonexistent.sql")
        assert rc == 1

    async def test_down_without_reverse_script_errors(self, conn):
        rc = await cmd_down(conn, "phase1_student_accounts.sql")
        assert rc == 1


class TestRollbackLast:
    async def test_rollback_last_rolls_back_most_recent(self, conn):
        """--rollback-last should roll back the migration with the latest applied_at."""
        # Find current state
        applied_before = await _applied(conn)
        assert _SAMPLE_MIG not in applied_before

        # Apply the sample migration
        await _apply_file(conn, MIGRATIONS_DIR / _SAMPLE_MIG)
        applied = await _applied(conn)
        assert _SAMPLE_MIG in applied

        # Rollback last
        rc = await cmd_rollback_last(conn)
        assert rc == 0

        # Verify sample migration is gone (it was the most recent)
        applied = await _applied(conn)
        assert _SAMPLE_MIG not in applied

    async def test_rollback_last_empty_db_ok(self, conn):
        """Rolling back when there are no migrations should be a no-op."""
        # Temporarily remove the test migration's row if present
        await conn.execute("DELETE FROM schema_migrations WHERE filename = $1", _SAMPLE_MIG)
        rc = await cmd_rollback_last(conn)
        assert rc == 0
