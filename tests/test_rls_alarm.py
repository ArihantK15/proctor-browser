"""RLS coverage alarm (#18) — the automated pg_policies sweep.

Every RLS gap this app has shipped (invite_send_counters, issues, email_otps,
chat/room-cam WS context, coding submissions) was discovered by a feature
going silently dead in production first. These lock the three checks and the
alert-message formatting so a regression here is caught by CI, not by a
customer noticing chat is dead.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import rls_alarm


def _mock_pool(policyless_rows, stale_rows, disabled_rows):
    conn = MagicMock()
    conn.fetch = AsyncMock(side_effect=[policyless_rows, stale_rows, disabled_rows])
    txn_cm = MagicMock()
    txn_cm.__aenter__ = AsyncMock(return_value=None)
    txn_cm.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=txn_cm)
    conn_cm = MagicMock()
    conn_cm.__aenter__ = AsyncMock(return_value=conn)
    conn_cm.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=conn_cm)
    return pool


def _gaps(policyless_rows=(), stale_rows=(), disabled_rows=()):
    pool = _mock_pool(list(policyless_rows), list(stale_rows), list(disabled_rows))
    with patch("app.postgres_table.get_pool", new=AsyncMock(return_value=pool)):
        return asyncio.run(rls_alarm.rls_coverage_gaps())


def test_clean_schema_reports_no_gaps():
    gaps = _gaps()
    assert gaps == {
        "policyless_rls_tables": [],
        "stale_auth_uid_policies": [],
        "tenant_tables_without_rls": [],
    }
    assert rls_alarm._has_gaps(gaps) is False


def test_policyless_rls_table_detected():
    gaps = _gaps(policyless_rows=[{"tablename": "invite_send_counters"}])
    assert gaps["policyless_rls_tables"] == ["invite_send_counters"]
    assert rls_alarm._has_gaps(gaps) is True
    assert "invite_send_counters" in rls_alarm._alert_message(gaps)


def test_stale_auth_uid_policy_detected():
    """As of 2026-07-05 the SQL groups by (table, command) and only reports
    a gap when EVERY policy for that combination is stale — this row shape
    is what conn.fetch returns post-grouping, not one row per policy."""
    gaps = _gaps(stale_rows=[
        {"tablename": "teachers", "cmd": "SELECT", "stale_policies": ["teachers_select_own"]},
    ])
    assert gaps["stale_auth_uid_policies"] == ["teachers.SELECT (teachers_select_own)"]
    assert rls_alarm._has_gaps(gaps) is True
    assert "auth.uid()" in rls_alarm._alert_message(gaps)


def test_tenant_table_without_rls_detected():
    gaps = _gaps(disabled_rows=[{"table_name": "students"}])
    assert gaps["tenant_tables_without_rls"] == ["students"]
    assert rls_alarm._has_gaps(gaps) is True
    assert "students" in rls_alarm._alert_message(gaps)


def test_multiple_gaps_all_reported():
    gaps = _gaps(
        policyless_rows=[{"tablename": "issues"}],
        stale_rows=[{"tablename": "teachers", "cmd": "SELECT", "stale_policies": ["teachers_select_own"]}],
    )
    msg = rls_alarm._alert_message(gaps)
    assert "issues" in msg
    assert "teachers.SELECT" in msg
    assert "teachers_select_own" in msg
