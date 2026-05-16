#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

mkdir -p backups/postgres

ts="$(date -u +%Y%m%dT%H%M%SZ)"
db="${POSTGRES_DB:-procta}"
user="${POSTGRES_USER:-procta}"
out="backups/postgres/procta-${ts}.dump"

docker compose --profile postgres exec -T postgres \
  pg_dump -U "$user" -d "$db" --format=custom --no-owner --no-privileges \
  > "$out"

find backups/postgres -name 'procta-*.dump' -type f -mtime +14 -delete

echo "$out"
