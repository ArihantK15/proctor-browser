#!/bin/sh
# Startup entrypoint: screenshot disk guard + uvicorn
# Deletes screenshots older than 90 days to prevent droplet disk-fill.

SCREENSHOT_DIR="/app/screenshots"
RETENTION_DAYS="${SCREENSHOT_RETENTION_DAYS:-90}"

# Run cleanup once on startup
find "$SCREENSHOT_DIR" -type f -mtime +"$RETENTION_DAYS" -delete 2>/dev/null

# Run pending Supabase migrations (failures are fatal — schema mismatch
# causes data corruption that is harder to debug than a startup crash).
python scripts/run_migrations.py

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
  _worker_pid=$!
  echo "[entrypoint] RQ worker started (PID=$_worker_pid)"
fi

UVICORN_WORKERS="${UVICORN_WORKERS:-${WEB_CONCURRENCY:-2}}"
UVICORN_KEEPALIVE="${UVICORN_KEEPALIVE:-15}"

set -- uvicorn app.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers "$UVICORN_WORKERS" \
  --loop uvloop \
  --timeout-keep-alive "$UVICORN_KEEPALIVE" \
  --log-level warning

if [ -n "${UVICORN_LIMIT_CONCURRENCY:-}" ]; then
  set -- "$@" --limit-concurrency "$UVICORN_LIMIT_CONCURRENCY"
fi

# Replace shell with uvicorn (signal passthrough)
exec "$@"
