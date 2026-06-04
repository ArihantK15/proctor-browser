#!/usr/bin/env python3
"""Dump the live public schema to schema/columns.json.

Produces the authoritative snapshot that scripts/check_schema_refs.py
checks code column references against. Run it whenever the schema
changes (after applying a migration) so the guard stays current:

    DATABASE_URL=postgres://... python scripts/dump_schema.py

Reads DATABASE_URL the same way scripts/run_postgres_migrations.py does,
so on the prod box it works with the existing env. Output is sorted +
deterministic so re-dumps produce clean diffs.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "schema" / "columns.json"


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise SystemExit("DATABASE_URL is required (postgres://user:pass@host/db)")
    return url


async def _dump() -> dict[str, list[str]]:
    conn = await asyncpg.connect(_database_url(), statement_cache_size=0)
    try:
        rows = await conn.fetch(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
            ORDER BY table_name, column_name
            """
        )
    finally:
        await conn.close()
    schema: dict[str, list[str]] = {}
    for r in rows:
        schema.setdefault(r["table_name"], []).append(r["column_name"])
    return {t: sorted(cols) for t, cols in sorted(schema.items())}


def main() -> int:
    schema = asyncio.run(_dump())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    ntables = len(schema)
    ncols = sum(len(c) for c in schema.values())
    print(f"wrote {OUT.relative_to(ROOT)} — {ntables} tables, {ncols} columns")
    return 0


if __name__ == "__main__":
    sys.exit(main())
