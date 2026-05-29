#!/bin/bash
# Must be bash, not sh: `set -o pipefail` is a bash/ksh extension.
# python:3.12-slim is Debian-based where /bin/sh -> dash, which errors
# on `pipefail` and exits before the script even starts. Verified with
# `/bin/dash -c 'set -euo pipefail'` → "Illegal option -o pipefail".
set -euo pipefail
# Startup entrypoint: screenshot disk guard + uvicorn
# Deletes screenshots older than 90 days to prevent droplet disk-fill.

SCREENSHOT_DIR="/app/screenshots"
RETENTION_DAYS="${SCREENSHOT_RETENTION_DAYS:-90}"

# Run cleanup once on startup
find "$SCREENSHOT_DIR" -type f -mtime +"$RETENTION_DAYS" -delete 2>/dev/null

# Run pending migrations (failures are fatal — schema mismatch causes data
# corruption that is harder to debug than a startup crash).
if [ "${DATABASE_BACKEND:-supabase}" = "postgres" ]; then
  python scripts/run_postgres_migrations.py
else
  python scripts/run_migrations.py
fi

# ── Cleanup trap ───────────────────────────────────────────────────
# On SIGTERM/SIGINT, kill all child processes so they don't become
# orphaned when uvicorn exits or the container stops.
_cleanup() {
  echo "[entrypoint] Shutting down background processes..."
  kill $(jobs -p) 2>/dev/null || true
  wait 2>/dev/null || true
}
trap _cleanup SIGTERM SIGINT SIGQUIT EXIT

# Background cleanup: run every 6 hours
(
  while true; do
    sleep 21600  # 6h
    find "$SCREENSHOT_DIR" -type f -mtime +"$RETENTION_DAYS" -delete 2>/dev/null
  done
) &

# Start RQ worker in background when enabled
if [ "${RQ_ENABLED:-0}" = "1" ]; then
  python worker.py &
  echo "[entrypoint] RQ worker started (PID=$!)"
fi

UVICORN_WORKERS="${UVICORN_WORKERS:-${WEB_CONCURRENCY:-2}}"
UVICORN_KEEPALIVE="${UVICORN_KEEPALIVE:-15}"
UVICORN_GRACEFUL_SHUTDOWN="${UVICORN_GRACEFUL_SHUTDOWN:-30}"

set -- uvicorn app.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers "$UVICORN_WORKERS" \
  --loop uvloop \
  --timeout-keep-alive "$UVICORN_KEEPALIVE" \
  --timeout-graceful-shutdown "$UVICORN_GRACEFUL_SHUTDOWN" \
  --log-level warning \
  --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-*}"

# `--forwarded-allow-ips="*"` tells uvicorn to honour X-Forwarded-For
# and X-Forwarded-Proto headers from any peer. This is safe because
# the API container's port 8000 is NOT exposed on the host — the only
# peer that can reach it is Caddy (which itself only trusts those
# headers when they come from Cloudflare ranges, see Caddyfile). To
# tighten further on hosts where 8000 is exposed, set
# `FORWARDED_ALLOW_IPS=172.18.0.0/16` (the Docker bridge subnet).

if [ -n "${UVICORN_LIMIT_CONCURRENCY:-}" ]; then
  set -- "$@" --limit-concurrency "$UVICORN_LIMIT_CONCURRENCY"
fi

# Run uvicorn in foreground so signals reach child processes via trap
"$@" &
wait $!
