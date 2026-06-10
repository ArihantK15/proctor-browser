#!/usr/bin/env bash
# Rebuild a Postgres database from the committed baseline + repo migrations.
#
# Used by the `schema-from-scratch` CI gate (proves the schema builds from zero
# on every push) and as the documented disaster-recovery path. Order:
#
#   1. load migrations/baseline/000_baseline.sql            (full prod schema)
#   2. load migrations/baseline/001_schema_migrations_data.sql
#                                                           (mark pre-baseline
#                                                            migrations applied)
#   3. run scripts/run_postgres_migrations.py               (apply only the
#                                                            migrations added
#                                                            AFTER the snapshot)
#
# Requires: DATABASE_URL, psql, python (+ asyncpg for the migration runner).
# This is a FRESH-DB bootstrap — it is NOT part of the normal deploy, which runs
# only step 3 incrementally against the already-populated prod DB.
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL is required}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASELINE="$ROOT/migrations/baseline/000_baseline.sql"
MIG_DATA="$ROOT/migrations/baseline/001_schema_migrations_data.sql"

if [ ! -f "$BASELINE" ]; then
  echo "[bootstrap] No baseline at $BASELINE."
  echo "[bootstrap] Capture it from prod (see migrations/baseline/README.md):"
  echo "[bootstrap]   pg_dump --schema-only --no-owner --no-privileges \"\$DATABASE_URL\" > $BASELINE"
  echo "[bootstrap]   pg_dump --data-only --inserts --table=schema_migrations \"\$DATABASE_URL\" > $MIG_DATA"
  exit 2
fi

echo "[bootstrap] 1/3 loading baseline schema → $(basename "$BASELINE")"
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q -f "$BASELINE"

if [ -f "$MIG_DATA" ]; then
  echo "[bootstrap] 2/3 seeding schema_migrations (already-applied set)"
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q -f "$MIG_DATA"
else
  echo "[bootstrap] 2/3 no schema_migrations data file — all repo migrations will be (re)applied"
fi

echo "[bootstrap] 3/3 applying post-baseline migrations"
PYTHON_BIN="$(command -v python3 || command -v python || true)"
: "${PYTHON_BIN:?python3 (or python) not found on PATH}"
"$PYTHON_BIN" "$ROOT/scripts/run_postgres_migrations.py"

echo "[bootstrap] done — database built from baseline + migrations"
