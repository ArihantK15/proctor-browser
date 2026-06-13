#!/usr/bin/env python3
"""Run app SQL migrations directly against plain Postgres.

This is the DATABASE_BACKEND=postgres companion to scripts/run_migrations.py.
It assumes the baseline schema has already been restored from Supabase with
pg_dump/pg_restore, then applies any repo migrations that are not recorded in
schema_migrations.

Subcommands (no-arg = forward apply, unchanged from the original):
    (no args)  — apply pending migrations forward (preflight-deploy path)
    --status   — list applied migrations + any pending
    --down <filename>  — reverse a migration via migrations/down/<filename>
    --rollback-last    — reverse the most recently applied migration (by applied_at)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import asyncpg

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_BACKEND=postgres requires DATABASE_URL")
    return url


async def _ensure_bookkeeping(conn: asyncpg.Connection) -> None:
    # Several older Supabase-oriented migrations reference auth.uid() in RLS
    # policy definitions. Plain Postgres does not ship that schema, so provide a
    # harmless compatibility shim. The app uses its own JWT auth and connects as
    # the owner role, so this function is only here to let legacy SQL compile.
    await conn.execute(
        """
        CREATE EXTENSION IF NOT EXISTS pgcrypto;

        CREATE SCHEMA IF NOT EXISTS auth;
        CREATE OR REPLACE FUNCTION auth.uid()
        RETURNS uuid
        LANGUAGE sql
        STABLE
        AS $$ SELECT NULL::uuid $$;

        CREATE TABLE IF NOT EXISTS schema_migrations (
          filename TEXT PRIMARY KEY,
          applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )


async def _applied(conn: asyncpg.Connection) -> set[str]:
    rows = await conn.fetch("SELECT filename FROM schema_migrations")
    return {str(r["filename"]) for r in rows}


async def _applied_with_dates(conn: asyncpg.Connection) -> list[dict]:
    rows = await conn.fetch(
        "SELECT filename, applied_at FROM schema_migrations ORDER BY applied_at"
    )
    return [{"filename": str(r["filename"]), "applied_at": r["applied_at"]} for r in rows]


async def _apply_file(conn: asyncpg.Connection, path: Path) -> None:
    sql = path.read_text()
    async with conn.transaction():
        await conn.execute(sql)
        await conn.execute(
            "INSERT INTO schema_migrations (filename) VALUES ($1) ON CONFLICT DO NOTHING",
            path.name,
        )


async def main_apply(conn: asyncpg.Connection) -> int:
    """Forward-apply pending migrations. Returns 0 on success, 1 on failure."""
    applied = await _applied(conn)
    ran = 0
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name in applied:
            print(f"[postgres-migrations] SKIP {path.name} (already applied)", flush=True)
            continue
        try:
            await _apply_file(conn, path)
        except Exception as exc:
            print(f"[postgres-migrations] FAILED {path.name}: {exc}", flush=True)
            return 1
        print(f"[postgres-migrations] APPLIED {path.name}", flush=True)
        ran += 1
    if ran:
        print(f"[postgres-migrations] Applied {ran} migration(s)", flush=True)
    else:
        print("[postgres-migrations] All migrations up to date", flush=True)
    return 0


async def cmd_status(conn: asyncpg.Connection) -> int:
    """Print applied and pending migrations."""
    applied = await _applied(conn)
    applied_with_dates = await _applied_with_dates(conn)

    print("[postgres-migrations] Applied migrations:", flush=True)
    if applied_with_dates:
        for row in applied_with_dates:
            print(f"  {row['filename']}  (applied {row['applied_at']})", flush=True)
    else:
        print("  (none)", flush=True)

    pending = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name not in applied:
            pending.append(path.name)
    if pending:
        print(f"[postgres-migrations] Pending ({len(pending)}):", flush=True)
        for name in pending:
            print(f"  {name}", flush=True)
    else:
        print("[postgres-migrations] No pending migrations", flush=True)
    return 0


async def cmd_down(conn: asyncpg.Connection, filename: str) -> int:
    """Reverse a single migration via migrations/down/<filename>."""
    down_path = MIGRATIONS_DIR / "down" / filename
    if not down_path.exists():
        print(f"[postgres-migrations] ERROR: no reverse script at {down_path}", flush=True)
        print("[postgres-migrations] This migration is not reversible; use expand-contract.", flush=True)
        return 1

    applied = await _applied(conn)
    if filename not in applied:
        print(f"[postgres-migrations] ERROR: {filename} is not in schema_migrations", flush=True)
        return 1

    sql = down_path.read_text()
    async with conn.transaction():
        await conn.execute(sql)
        await conn.execute(
            "DELETE FROM schema_migrations WHERE filename = $1", filename
        )
    print(f"[postgres-migrations] DOWN {filename} — reverted and removed from schema_migrations", flush=True)
    return 0


async def cmd_rollback_last(conn: asyncpg.Connection) -> int:
    """Reverse the most recently applied migration (by applied_at, not filename)."""
    row = await conn.fetchrow(
        "SELECT filename FROM schema_migrations ORDER BY applied_at DESC LIMIT 1"
    )
    if row is None:
        print("[postgres-migrations] No migrations to roll back", flush=True)
        return 0
    return await cmd_down(conn, str(row["filename"]))


async def main_async(args: argparse.Namespace) -> int:
    conn = None
    last_exc: Exception | None = None
    for attempt in range(1, 16):
        try:
            # statement_cache_size=0 → asyncpg doesn't reuse prepared
            # statements between calls. Required when the DATABASE_URL
            # points at pgbouncer in transaction-pooling mode (our
            # current prod setup): pgbouncer drops prepared statements
            # at transaction boundaries, so reuse blows up with
            # "prepared statement __asyncpg_stmt_N__ does not exist".
            conn = await asyncpg.connect(_database_url(), statement_cache_size=0)
            break
        except Exception as exc:
            last_exc = exc
            print(f"[postgres-migrations] waiting for postgres ({attempt}/15): {exc}", flush=True)
            await asyncio.sleep(2)
    if conn is None:
        raise RuntimeError(f"could not connect to postgres: {last_exc}")
    try:
        await _ensure_bookkeeping(conn)

        if args.cmd == "apply":
            return await main_apply(conn)
        elif args.cmd == "status":
            return await cmd_status(conn)
        elif args.cmd == "down":
            return await cmd_down(conn, args.filename)
        elif args.cmd == "rollback_last":
            return await cmd_rollback_last(conn)
        else:
            print(f"[postgres-migrations] Unknown command: {args.cmd}", flush=True)
            return 1
    finally:
        await conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run (or reverse) Postgres schema migrations"
    )
    parser.add_argument("--status", action="store_true", help="List applied and pending migrations")
    parser.add_argument("--down", type=str, metavar="FILENAME", help="Reverse a migration via migrations/down/FILENAME")
    parser.add_argument("--rollback-last", action="store_true", help="Reverse the most recently applied migration")
    args = parser.parse_args()

    count = sum([args.status, bool(args.down), args.rollback_last])
    if count > 1:
        print("[postgres-migrations] ERROR: --status, --down, and --rollback-last are mutually exclusive", flush=True)
        return 1

    ns = argparse.Namespace()
    if args.status:
        ns.cmd = "status"
    elif args.down:
        ns.cmd = "down"
        ns.filename = args.down
    elif args.rollback_last:
        ns.cmd = "rollback_last"
    else:
        ns.cmd = "apply"

    try:
        return asyncio.run(main_async(ns))
    except Exception as exc:
        print(f"[postgres-migrations] FATAL: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
