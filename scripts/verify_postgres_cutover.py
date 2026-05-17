#!/usr/bin/env python3
"""Sanity-check a plain-Postgres Procta cutover before flipping traffic."""
from __future__ import annotations

import asyncio
import os
import sys

import asyncpg

REQUIRED_TABLES = {
    "answers",
    "auth_events",
    "auth_sessions",
    "exam_config",
    "exam_sessions",
    "organizations",
    "questions",
    "refresh_tokens",
    "student_accounts",
    "students",
    "teachers",
}

REQUIRED_COLUMNS = {
    "teachers": {"id", "email", "supabase_uid", "password_hash", "auth_provider", "password_changed_at"},
    "student_accounts": {"id", "email", "supabase_uid", "password_hash", "auth_provider", "password_changed_at"},
    "refresh_tokens": {"jti", "user_id", "kind", "expires_at", "revoked_at", "replaced_by_jti"},
}


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is required")
    return url


async def _table_names(conn: asyncpg.Connection) -> set[str]:
    rows = await conn.fetch(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_type = 'BASE TABLE'
        """
    )
    return {str(r["table_name"]) for r in rows}


async def _columns(conn: asyncpg.Connection, table: str) -> set[str]:
    rows = await conn.fetch(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = $1
        """,
        table,
    )
    return {str(r["column_name"]) for r in rows}


async def _count(conn: asyncpg.Connection, table: str) -> int:
    return int(await conn.fetchval(f'SELECT COUNT(*) FROM "{table}"'))


async def main_async() -> int:
    conn = await asyncpg.connect(_database_url())
    failures: list[str] = []
    try:
        tables = await _table_names(conn)
        missing_tables = sorted(REQUIRED_TABLES - tables)
        if missing_tables:
            failures.append(f"missing tables: {', '.join(missing_tables)}")

        for table, required in REQUIRED_COLUMNS.items():
            if table not in tables:
                continue
            cols = await _columns(conn, table)
            missing_cols = sorted(required - cols)
            if missing_cols:
                failures.append(f"{table} missing columns: {', '.join(missing_cols)}")

        counts = {}
        for table in sorted(REQUIRED_TABLES & tables):
            counts[table] = await _count(conn, table)

        print("[postgres-cutover] table counts:")
        for table, count in counts.items():
            print(f"  {table}: {count}")

        if failures:
            print("[postgres-cutover] FAILED:")
            for failure in failures:
                print(f"  - {failure}")
            return 1

        print("[postgres-cutover] OK: schema shape is ready for DATABASE_BACKEND=postgres")
        return 0
    finally:
        await conn.close()


def main() -> int:
    try:
        return asyncio.run(main_async())
    except Exception as exc:
        print(f"[postgres-cutover] FATAL: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
