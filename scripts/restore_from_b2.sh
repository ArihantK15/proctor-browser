#!/usr/bin/env bash
# Procta restore drill from Backblaze B2.
#
# USE: This is a TEST restore — it pulls a backup into a throwaway
# Postgres container so you can verify the backup actually works
# WITHOUT touching production. Run this at least monthly. A backup
# you've never restored doesn't exist.
#
# For a real production restore (you're recovering from data loss),
# see the steps at the bottom of this file — DO NOT just run the
# script.

set -euo pipefail

SECRETS_FILE="${SECRETS_FILE:-/etc/procta/secrets.env}"
if [ -f "$SECRETS_FILE" ]; then
  # shellcheck source=/dev/null
  set -a; . "$SECRETS_FILE"; set +a
fi

: "${B2_APPLICATION_KEY_ID:?missing in $SECRETS_FILE}"
: "${B2_APPLICATION_KEY:?missing in $SECRETS_FILE}"
: "${B2_BUCKET:=procta-backups}"

TMP="${TMP:-/tmp/procta-restore-drill}"
mkdir -p "$TMP"

echo "[restore-drill] authorising with B2..."
b2 account authorize "$B2_APPLICATION_KEY_ID" "$B2_APPLICATION_KEY" >/dev/null

# Pick the latest pg dump
LATEST=$(b2 file list "$B2_BUCKET" 2>/dev/null \
  | awk '/procta-pg-.*\.dump$/ {print $NF}' \
  | sort | tail -n 1)
if [ -z "$LATEST" ]; then
  echo "[restore-drill] no procta-pg-*.dump found in B2 bucket $B2_BUCKET"
  exit 1
fi
echo "[restore-drill] latest dump: $LATEST"

DUMP="$TMP/$LATEST"
b2 file download "b2://$B2_BUCKET/$LATEST" "$DUMP"
echo "[restore-drill] downloaded $(du -h "$DUMP" | cut -f1)"

# Spin up a throwaway Postgres container
RESTORE_PORT="${RESTORE_PORT:-15433}"
CONTAINER_NAME="procta-restore-drill"
docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

echo "[restore-drill] starting throwaway Postgres on port $RESTORE_PORT..."
docker run -d --name "$CONTAINER_NAME" \
  -e POSTGRES_USER=procta \
  -e POSTGRES_PASSWORD=restore_test_password \
  -e POSTGRES_DB=procta \
  -p "$RESTORE_PORT:5432" \
  postgres:16-alpine >/dev/null
sleep 6

# Restore into the throwaway
echo "[restore-drill] restoring into throwaway container..."
docker cp "$DUMP" "$CONTAINER_NAME:/tmp/dump"
docker exec "$CONTAINER_NAME" pg_restore \
  -U procta -d procta \
  --no-owner --no-acl \
  --clean --if-exists \
  /tmp/dump 2>&1 | tail -20 || true

# Sanity checks: row counts on important tables
echo ""
echo "[restore-drill] row counts in restored DB:"
docker exec "$CONTAINER_NAME" psql -U procta -d procta -tA -c "
  SELECT 'teachers',         COUNT(*) FROM teachers
  UNION ALL SELECT 'exam_sessions',   COUNT(*) FROM exam_sessions
  UNION ALL SELECT 'exam_config',     COUNT(*) FROM exam_config
  UNION ALL SELECT 'questions',       COUNT(*) FROM questions
  UNION ALL SELECT 'answers',         COUNT(*) FROM answers
  UNION ALL SELECT 'organizations',   COUNT(*) FROM organizations;"

echo ""
echo "[restore-drill] cleanup throwaway container..."
docker rm -f "$CONTAINER_NAME" >/dev/null

echo "[restore-drill] PASSED. Backup is restorable."

# ─────────────────────────────────────────────────────────────────────
# REAL PRODUCTION RESTORE (manual — DO NOT automate):
#
# 1. STOP the api + worker containers (so no new writes):
#      docker compose stop api worker autosave-worker
#
# 2. Snapshot the current (broken?) DB just in case:
#      docker exec proctor-postgres pg_dump -U procta -d procta -F c \
#        > /root/pre-restore-snapshot-$(date +%s).dump
#
# 3. Drop and recreate the procta DB:
#      docker exec -it proctor-postgres psql -U procta -d postgres \
#        -c "DROP DATABASE procta;"
#      docker exec -it proctor-postgres psql -U procta -d postgres \
#        -c "CREATE DATABASE procta OWNER procta;"
#
# 4. Download the dump from B2:
#      b2 file download b2://procta-backups/procta-pg-YYYYMMDDTHHMMSSZ.dump \
#        /tmp/restore.dump
#
# 5. Restore:
#      docker cp /tmp/restore.dump proctor-postgres:/tmp/restore.dump
#      docker exec proctor-postgres pg_restore -U procta -d procta \
#        --no-owner --no-acl /tmp/restore.dump
#
# 6. Start everything back up:
#      docker compose up -d
#
# 7. Smoke-test login + a sample query before announcing recovery.
# ─────────────────────────────────────────────────────────────────────
