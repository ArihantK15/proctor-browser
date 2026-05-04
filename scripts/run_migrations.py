#!/usr/bin/env python3
"""Run pending Supabase migrations at startup.

Usage:
    python scripts/run_migrations.py

Environment:
    SUPABASE_URL              Base Supabase project URL
    SUPABASE_SERVICE_ROLE_KEY  Service role key (for reading schema_migrations)
    SUPABASE_MANAGEMENT_TOKEN  Management API token (for executing SQL)
                               If not set, migrations are skipped.

This script:
  1. Creates a ``schema_migrations`` table if it doesn't exist.
  2. Scans ``migrations/*.sql`` sorted by filename.
  3. Executes any file whose basename is not already recorded.
  4. Prints what was applied / skipped.

If ``SUPABASE_MANAGEMENT_TOKEN`` is not set the script exits cleanly
and prints instructions — the API still starts normally.
"""

import os
import sys
from pathlib import Path

import httpx

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
MGMT_TOKEN = os.environ.get("SUPABASE_MANAGEMENT_TOKEN", "")

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"


def _headers() -> dict:
    return {
        "apikey": SERVICE_KEY,
        "Authorization": f"Bearer {SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }


def _mgmt_headers() -> dict:
    return {
        "Authorization": f"Bearer {MGMT_TOKEN}",
        "Content-Type": "application/json",
    }


def _exec_sql(sql: str) -> None:
    """Execute SQL via the Supabase Management API."""
    project_ref = SUPABASE_URL.split("//")[1].split(".")[0]
    url = f"https://api.supabase.com/v1/projects/{project_ref}/sql"
    resp = httpx.post(url, json={"query": sql}, headers=_mgmt_headers(), timeout=60)
    if resp.status_code not in (200, 201, 204):
        raise RuntimeError(f"SQL exec failed ({resp.status_code}): {resp.text}")


def main() -> int:
    if not SUPABASE_URL or not SERVICE_KEY:
        print("[migrations] Skipping: SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set", flush=True)
        return 0

    if not MGMT_TOKEN:
        print("[migrations] Skipping: SUPABASE_MANAGEMENT_TOKEN not set", flush=True)
        print("[migrations] To apply migrations run:", flush=True)
        print("[migrations]   supabase db push --db-url postgresql://postgres.<ref>:<password>@db.<ref>.supabase.co:5432/postgres", flush=True)
        return 0

    # Check if schema_migrations table exists
    check_url = f"{SUPABASE_URL}/rest/v1/schema_migrations?select=filename&limit=1"
    try:
        resp = httpx.get(check_url, headers=_headers(), timeout=10)
        if resp.status_code == 404:
            _exec_sql(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT NOW());"
            )
            print("[migrations] Created schema_migrations table", flush=True)
    except Exception as e:
        print(f"[migrations] Connection check failed: {e}", flush=True)
        return 0

    # Get already-applied migrations
    try:
        resp = httpx.get(
            f"{SUPABASE_URL}/rest/v1/schema_migrations?select=filename",
            headers=_headers(), timeout=10
        )
        applied = {r["filename"] for r in (resp.json() if resp.status_code == 200 else [])}
    except Exception as e:
        print(f"[migrations] Failed to fetch applied migrations: {e}", flush=True)
        return 0

    # Scan migration files
    mig_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not mig_files:
        print("[migrations] No migration files found", flush=True)
        return 0

    ran = 0
    for fpath in mig_files:
        fname = fpath.name
        if fname in applied:
            print(f"[migrations] SKIP {fname} (already applied)", flush=True)
            continue

        sql = fpath.read_text()
        try:
            _exec_sql(sql)
            _exec_sql(
                f"INSERT INTO schema_migrations (filename) VALUES ('{fname}') "
                f"ON CONFLICT DO NOTHING;"
            )
            print(f"[migrations] APPLIED {fname}", flush=True)
            ran += 1
        except Exception as e:
            print(f"[migrations] FAILED {fname}: {e}", flush=True)
            return 1

    if ran == 0:
        print("[migrations] All migrations up to date", flush=True)
    else:
        print(f"[migrations] Applied {ran} migration(s)", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
