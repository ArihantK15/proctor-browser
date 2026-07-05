"""Real-Postgres regression test for rls_alarm's precise per-(table, command)
coverage check.

Every other test for rls_alarm.py (tests/test_rls_alarm.py) mocks
conn.fetch — proves the Python formatting logic works, but structurally
cannot catch a bug in the raw SQL query text itself. That's exactly the
history here: the ORIGINAL query only matched a literal '%auth.uid%'
substring, missing indirect helper-function stragglers (fixed once), and a
naive broadening of that fix then flagged ~82 harmless dead-policy-
coexistence cases alongside real gaps (caught before shipping, see
decision_rls_alarm_detection_scope memory). This test locks in the FINAL,
precise version: group by (table, command), only flag when EVERY policy
for that exact combination is stale.

Deliberately NOT wired into the shared integration_tests/conftest.py
fixture (that fixture's schema.sql has no RLS policies or `app` schema at
all, and is shared by many other integration tests). Runs against its own
dedicated env var instead, skips cleanly if unset.

Usage:
    docker run -d --name rls-alarm-it -e POSTGRES_PASSWORD=test \
        -e POSTGRES_DB=procta -p 15439:5432 postgres:16
    RLS_ALARM_TEST_DATABASE_URL=postgresql://postgres:test@localhost:15439/procta \
        DATABASE_URL=postgresql://postgres:test@localhost:15439/procta \
        pytest integration_tests/test_rls_alarm_precise_coverage_integration.py -v
"""
import os
import pathlib
import subprocess
import sys

import pytest
import pytest_asyncio

_HERE = pathlib.Path(__file__).parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_ROOT))

_DB_URL = os.environ.get("RLS_ALARM_TEST_DATABASE_URL", "").strip()

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not _DB_URL, reason="RLS_ALARM_TEST_DATABASE_URL not set"),
]


def _run_migrations() -> None:
    env = dict(os.environ)
    env["DATABASE_BACKEND"] = "postgres"
    env["DATABASE_URL"] = _DB_URL
    baseline = _ROOT / "migrations" / "baseline" / "000_baseline.sql"
    baseline_data = _ROOT / "migrations" / "baseline" / "001_schema_migrations_data.sql"
    for f in (baseline, baseline_data):
        subprocess.run(["psql", _DB_URL, "-f", str(f)], check=True, capture_output=True, text=True)
    subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "run_postgres_migrations.py")],
        check=True, capture_output=True, text=True, env=env,
    )


def _run_sql_file(path: pathlib.Path) -> None:
    subprocess.run(["psql", _DB_URL, "-f", str(path)], check=True, capture_output=True, text=True)


@pytest_asyncio.fixture(scope="module", autouse=True)
async def _real_schema():
    _run_migrations()
    yield


@pytest_asyncio.fixture(autouse=True)
async def _restore_forward_state():
    """Each test starts from the phase152-fixed (zero-gap) state."""
    _run_sql_file(_ROOT / "migrations" / "phase152_rls_stragglers_sweep.sql")
    yield


async def test_fixed_schema_reports_zero_gaps(monkeypatch):
    from app import postgres_table
    monkeypatch.setenv("DATABASE_URL", _DB_URL)
    await postgres_table.close_pool()

    from app.services import rls_alarm
    gaps = await rls_alarm.rls_coverage_gaps()

    assert gaps["stale_auth_uid_policies"] == []
    await postgres_table.close_pool()


async def test_harmless_dead_policy_coexistence_not_flagged(monkeypatch):
    """`answers`, `exam_config`, and others have a leftover dead auth.uid()-
    based policy sitting ALONGSIDE an already-working app.* replacement for
    the same command. Postgres OR-combines permissive policies, so these
    are harmless — the precise check must not flag them, unlike the naive
    per-policy version that would have."""
    from app import postgres_table
    monkeypatch.setenv("DATABASE_URL", _DB_URL)
    await postgres_table.close_pool()

    from app.services import rls_alarm
    gaps = await rls_alarm.rls_coverage_gaps()

    joined = " ".join(gaps["stale_auth_uid_policies"])
    assert "answers." not in joined
    assert "exam_config." not in joined
    await postgres_table.close_pool()


async def test_reintroduced_stragglers_are_detected_precisely(monkeypatch):
    """The real regression assertion: reintroduce the three genuine
    indirect-helper-function stragglers via phase152's own down migration
    (which have NO working replacement for their command), and confirm the
    precise query catches exactly those 4 (table, command) groups — no
    more, no less."""
    _run_sql_file(_ROOT / "migrations" / "down" / "phase152_rls_stragglers_sweep.sql")

    from app import postgres_table
    monkeypatch.setenv("DATABASE_URL", _DB_URL)
    await postgres_table.close_pool()

    from app.services import rls_alarm
    gaps = await rls_alarm.rls_coverage_gaps()

    found_tables_cmds = {entry.split(" (")[0] for entry in gaps["stale_auth_uid_policies"]}
    assert found_tables_cmds == {
        "admin_audit_log.SELECT",
        "consent_records.INSERT",
        "consent_records.SELECT",
        "demo_requests.SELECT",
    }
    await postgres_table.close_pool()
