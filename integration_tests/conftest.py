"""Integration-test harness — REAL Postgres, no mocks.

This suite lives OUTSIDE tests/ on purpose: tests/conftest.py replaces
app.database with a MagicMock in sys.modules at import time, so anything under
tests/ can never touch a real database. Here we want the opposite — exercise the
real asyncpg query builder (app.postgres_table) and real SQL against a live
Postgres, so schema/column bugs that the mocked unit suite cannot see get caught.

Run as its OWN pytest invocation (never together with tests/, or the mock would
leak in via collection order):

    DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/procta \
        pytest integration_tests/

Skips cleanly when DATABASE_URL is unset, so a plain `pytest` run is unaffected.
"""
import os
import sys

# Make the repo root (containing app/) importable no matter how pytest is
# launched: the `pytest` console script does NOT put CWD on sys.path the way
# `python -m pytest` does, so `import app` would fail in CI without this.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import-time env so `app.constants` (and friends) load. Procta is Postgres-only
# now (DATABASE_BACKEND=supabase hard-fails), so these are just satisfy-the-gate
# values; the real DB target is DATABASE_URL.
os.environ.setdefault("DATABASE_BACKEND", "postgres")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")
os.environ.setdefault("SUPABASE_URL", "https://fake.local")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-service-role-key")
os.environ.setdefault("SCREENSHOTS_DIR", "/tmp/procta_it_shots")
os.environ.setdefault("QUESTION_IMG_DIR", "/tmp/procta_it_qimg")
os.environ.setdefault("LOG_DIR", "/tmp/procta_it_logs")
os.environ.setdefault("RAZORPAY_WEBHOOK_SECRET", "whsec_test")
# Point Redis at a closed port so the cache layer degrades to a no-op fast
# instead of hanging on a real connection during tests.
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6390")

import asyncio
import pathlib

import asyncpg
import pytest
import pytest_asyncio

_HERE = pathlib.Path(__file__).parent
_SCHEMA = _HERE / "schema.sql"
_PHASE96 = _HERE.parent / "migrations" / "phase96_billing_enterprise.sql"

# Truncate order doesn't matter with CASCADE, but list every table the suite
# writes so state never leaks between tests.
_TABLES = [
    "billing_events", "answers", "violations", "exam_sessions",
    "question_bank", "subscriptions", "teachers", "organizations",
]


def _database_url() -> str:
    return os.environ.get("DATABASE_URL", "").strip()


def pytest_collection_modifyitems(config, items):
    """No DATABASE_URL → skip the whole suite (don't fail a DB-less `pytest`)."""
    if _database_url():
        return
    skip = pytest.mark.skip(reason="integration tests require DATABASE_URL (a real Postgres)")
    for item in items:
        item.add_marker(skip)


@pytest.fixture(scope="session", autouse=True)
def _build_schema():
    """Build the fixture schema once, then apply phase96 ON TOP — which both
    sets up billing_events / gstin / past_due_since AND proves that migration's
    DDL actually applies. Runs in its own event loop (asyncio.run) so it never
    collides with pytest-asyncio's per-test loops."""
    if not _database_url():
        return

    async def _setup():
        conn = await asyncpg.connect(_database_url(), statement_cache_size=0)
        try:
            await conn.execute(_SCHEMA.read_text())
            await conn.execute(_PHASE96.read_text())  # validates the real migration
        finally:
            await conn.close()

    asyncio.run(_setup())


@pytest_asyncio.fixture(autouse=True)
async def _clean_db():
    """Per-test: start from empty tables; afterwards close the app's asyncpg pool
    so the next test (a fresh pytest-asyncio event loop) opens its own pool
    instead of reusing one bound to a closed loop."""
    if not _database_url():
        yield
        return
    conn = await asyncpg.connect(_database_url(), statement_cache_size=0)
    try:
        await conn.execute("TRUNCATE " + ", ".join(_TABLES) + " RESTART IDENTITY CASCADE")
    finally:
        await conn.close()
    yield
    from app.postgres_table import close_pool
    await close_pool()
