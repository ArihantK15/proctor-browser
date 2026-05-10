#!/bin/sh
# Startup entrypoint: screenshot disk guard + uvicorn
# Deletes screenshots older than 90 days to prevent droplet disk-fill.

SCREENSHOT_DIR="/app/screenshots"
RETENTION_DAYS="${SCREENSHOT_RETENTION_DAYS:-90}"

# Run cleanup once on startup
find "$SCREENSHOT_DIR" -type f -mtime +"$RETENTION_DAYS" -delete 2>/dev/null

# Run pending Supabase migrations (safe no-op if already applied)
python scripts/run_migrations.py || true

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

# Replace shell with uvicorn (signal passthrough)
exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers 2 \
  --loop uvloop \
  --timeout-keep-alive 15 \
  --log-level warning
