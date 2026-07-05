"""RLS coverage alarm — the "operational pg_policies query" from the cutover
runbook (docs/TENANCY_RLS_HARDENING.md), automated (follow-up #18).

Every RLS gap this app has ever shipped (invite_send_counters — phase137,
issues — phase137, email_otps — phase138, chat/room-cam WS context, coding
submissions) was discovered the same way: a feature went silently dead in
production (chat stopped working, invites stopped sending) and someone
noticed the user-facing symptom before anyone thought to check pg_policies.
This turns that manual, ad-hoc "run this query before you flip the cutover
flag" step into a periodic automated check so a NEW migration that adds a
table without RLS coverage — or a stray policy still keyed on the retired
auth.uid() model — pages before a customer does.

Three checks, matching exactly what the cutover runbook describes as the
pre-flight sweep:
  1. RLS-enabled table with zero policies       → deny-all landmine.
  2. A (table, command) with NO working coverage → dead under procta_app
     (auth.uid() is NULL off the Supabase/PostgREST connection).
  3. A table with a tenant column (teacher_id/org_id/account_id) that has
     RLS disabled entirely — not a deny-all risk, but zero DB-level
     tenant isolation on data that looks like it should have some.

Runs unconditionally (not gated on RLS_SESSION_CONTEXT) — a gap is worth
knowing about whether or not enforcement happens to be live right now; the
whole point is catching it BEFORE the flag flips, not after.

Check 2's precision, 2026-07-05: the original version flagged any policy
whose text contained the literal substring "auth.uid". That missed the far
more common case — a policy calling a retired HELPER function
(get_my_teacher_id, get_my_student_account_id, get_my_roll_numbers, ...)
whose body resolves via auth.uid() but whose own name doesn't contain that
substring — exactly the gap that let 3 real stragglers (admin_audit_log,
consent_records, demo_requests — fixed in phase152) go undetected by this
alarm in the first place. Fixed to resolve the actual set of auth.uid()-
resolving functions dynamically (so a future retired helper is still
caught), but doing that naively also flagged ~82 OTHER policies: leftover
DEAD policies sitting alongside an already-working app.* replacement for
the SAME (table, command) — harmless, since Postgres OR-combines multiple
permissive policies for one command, but indistinguishable from a real
gap by a plain per-policy check. The fix groups by (table, command) and
only reports a gap when EVERY policy covering that exact combination is
stale — i.e. no working replacement exists at all — which is the only
case that's actually a deny-all risk. Verified against a real from-scratch
schema: reintroducing the 3 known stragglers via their down migration is
caught exactly (4 landmine groups, matching the original manual sweep);
the harmless dead-policy-coexistence cases (e.g. `answers`, which has
both an old dead policy and a working app.* one for the same command)
are correctly NOT flagged.
"""
from __future__ import annotations

import asyncio
import logging
import os

from typing import Any

logger = logging.getLogger(__name__)

RLS_ALARM_INTERVAL_SECS = int(os.environ.get("RLS_ALARM_INTERVAL_SECS", "21600"))  # 6 hours
RLS_ALARM_STARTUP_DELAY_SECS = int(os.environ.get("RLS_ALARM_STARTUP_DELAY_SECS", "300"))  # 5 min

_TENANT_COLUMNS = ("teacher_id", "org_id", "account_id")


async def rls_coverage_gaps() -> dict[str, Any]:
    """Run the three checks against the live schema. Cheap — three queries
    against pg_catalog/information_schema, no app-table scans."""
    from ..postgres_table import get_pool
    from .. import db_context as _dbctx

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # pg_catalog/information_schema aren't RLS-gated (or gatable)
            # regardless of context — this never touches a tenant table — but
            # every raw pool.acquire() applies context anyway
            # (test_rls_raw_pool_guard.py), same as every other background job
            # in this file's family. apply_request_context uses SET LOCAL, so
            # it must run inside an explicit transaction.
            await _dbctx.apply_request_context(conn, force_system=True)
            policyless = await conn.fetch(
                """
                SELECT t.tablename
                FROM pg_tables t
                LEFT JOIN pg_policies p
                  ON p.tablename = t.tablename AND p.schemaname = t.schemaname
                WHERE t.schemaname = 'public' AND t.rowsecurity
                GROUP BY t.tablename
                HAVING count(p.policyname) = 0
                ORDER BY t.tablename
                """
            )
            stale_auth_uid = await conn.fetch(
                """
                WITH auth_uid_fns AS (
                  SELECT proname FROM pg_proc WHERE prosrc ILIKE '%auth.uid()%'
                ),
                classified AS (
                  SELECT
                    p.tablename,
                    -- Expand ALL into each concrete command so coverage is
                    -- checked per real command, not lumped under the
                    -- literal word "ALL" (every policy in this schema is
                    -- PERMISSIVE — confirmed via a real schema build before
                    -- writing this query — so OR-combination across
                    -- policies for the same (table, command) is safe to
                    -- assume).
                    unnest(CASE WHEN p.cmd = 'ALL'
                                THEN ARRAY['SELECT','INSERT','UPDATE','DELETE']
                                ELSE ARRAY[p.cmd] END) AS cmd,
                    p.policyname,
                    (coalesce(p.qual, '') ILIKE '%auth.uid%'
                     OR coalesce(p.with_check, '') ILIKE '%auth.uid%'
                     OR EXISTS (
                       SELECT 1 FROM auth_uid_fns f
                       WHERE coalesce(p.qual, '') ~ ('\\m' || f.proname || '\\M')
                          OR coalesce(p.with_check, '') ~ ('\\m' || f.proname || '\\M')
                     )
                    ) AS is_stale
                  FROM pg_policies p
                  WHERE p.schemaname = 'public'
                )
                SELECT tablename, cmd, array_agg(policyname ORDER BY policyname) AS stale_policies
                FROM classified
                GROUP BY tablename, cmd
                HAVING bool_and(is_stale)
                ORDER BY tablename, cmd
                """
            )
            rls_disabled = await conn.fetch(
                """
                SELECT DISTINCT c.table_name
                FROM information_schema.columns c
                JOIN pg_tables t ON t.tablename = c.table_name AND t.schemaname = c.table_schema
                WHERE c.table_schema = 'public'
                  AND c.column_name = ANY($1::text[])
                  AND NOT t.rowsecurity
                ORDER BY c.table_name
                """,
                list(_TENANT_COLUMNS),
            )
    return {
        "policyless_rls_tables": [r["tablename"] for r in policyless],
        "stale_auth_uid_policies": [
            f"{r['tablename']}.{r['cmd']} ({', '.join(r['stale_policies'])})"
            for r in stale_auth_uid
        ],
        "tenant_tables_without_rls": [r["table_name"] for r in rls_disabled],
    }


def _has_gaps(gaps: dict[str, Any]) -> bool:
    return any(gaps.values())


def _alert_message(gaps: dict[str, Any]) -> str:
    parts = []
    if gaps["policyless_rls_tables"]:
        parts.append(f"policy-less RLS tables (deny-all at cutover): {gaps['policyless_rls_tables']}")
    if gaps["stale_auth_uid_policies"]:
        parts.append(f"policies still on the retired auth.uid() model (dead under procta_app): {gaps['stale_auth_uid_policies']}")
    if gaps["tenant_tables_without_rls"]:
        parts.append(f"tenant-column tables with RLS disabled entirely: {gaps['tenant_tables_without_rls']}")
    return "RLS coverage gap(s) found — " + "; ".join(parts)


async def rls_alarm_loop() -> None:
    """Leader-worker loop: periodically sweep for RLS coverage gaps and alert
    on any finding. WARNING log always; Sentry capture only when SENTRY_DSN
    is configured (capture_message is a safe no-op otherwise)."""
    await asyncio.sleep(RLS_ALARM_STARTUP_DELAY_SECS)
    while True:
        try:
            gaps = await rls_coverage_gaps()
            if _has_gaps(gaps):
                msg = _alert_message(gaps)
                logger.error("[ALERT] %s", msg)
                try:
                    import sentry_sdk
                    sentry_sdk.capture_message(msg, level="error")
                except Exception:
                    logger.debug("rls_alarm: sentry capture skipped", exc_info=True)
        except Exception:
            logger.warning("rls_alarm: check failed", exc_info=True)
        await asyncio.sleep(RLS_ALARM_INTERVAL_SECS)
