#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -z "${SUPABASE_DB_URL:-}" ]]; then
  echo "SUPABASE_DB_URL is required" >&2
  echo "Example: SUPABASE_DB_URL='postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres'" >&2
  exit 1
fi

mkdir -p backups/supabase

ts="$(date -u +%Y%m%dT%H%M%SZ)"
out="backups/supabase/procta-supabase-${ts}.dump"
client_image="${PG_CLIENT_IMAGE:-postgres:17-alpine}"

# Use the official Postgres client image so the server does not need pg_dump
# installed on the host. The excluded Supabase-managed schemas are not portable
# to plain Postgres and are recreated/replaced by local auth/app migrations.
docker run --rm \
  -e PGPASSWORD="${PGPASSWORD:-}" \
  -v "$PWD/backups/supabase:/backups" \
  "$client_image" \
  pg_dump "$SUPABASE_DB_URL" \
    --format=custom \
    --no-owner \
    --no-privileges \
    --exclude-schema=auth \
    --exclude-schema=storage \
    --exclude-schema=realtime \
    --file="/backups/$(basename "$out")"

echo "$out"
