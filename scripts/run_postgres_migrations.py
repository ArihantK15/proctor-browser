#!/usr/bin/env python3
"""Run app SQL migrations directly against plain Postgres.

This is the DATABASE_BACKEND=postgres companion to scripts/run_migrations.py.
It assumes the baseline schema has already been restored from Supabase with
pg_dump/pg_restore, then applies any repo migrations that are not recorded in
schema_migrations.
"""
from __future__ import annotations

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


async def _apply_file(conn: asyncpg.Connection, path: Path) -> None:
    sql = path.read_text()
    async with conn.transaction():
        await conn.execute(sql)
        await conn.execute(
            "INSERT INTO schema_migrations (filename) VALUES ($1) ON CONFLICT DO NOTHING",
            path.name,
        )


async def main_async() -> int:
    conn = None
    last_exc: Exception | None = None
    for attempt in range(1, 16):
        try:
            conn = await asyncpg.connect(_database_url())
            break
        except Exception as exc:
            last_exc = exc
            print(f"[postgres-migrations] waiting for postgres ({attempt}/15): {exc}", flush=True)
            await asyncio.sleep(2)
    if conn is None:
        raise RuntimeError(f"could not connect to postgres: {last_exc}")
    try:
        await _ensure_bookkeeping(conn)
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
    finally:
        await conn.close()


def main() -> int:
    try:
        return asyncio.run(main_async())
    except Exception as exc:
        print(f"[postgres-migrations] FATAL: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
