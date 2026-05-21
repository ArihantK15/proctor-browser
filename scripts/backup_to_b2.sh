#!/usr/bin/env bash
# Procta backup → Backblaze B2.
#
# What gets backed up:
#   1. Postgres database (pg_dump custom format, compressed)
#   2. Student screenshots directory
#   3. Question images directory
#
# What does NOT get backed up:
#   - Redis cache (ephemeral)
#   - Caddy logs (rotated locally)
#   - .env (NEVER — secrets go to a separate secrets manager)
#
# Schedule: nightly via cron (see /etc/cron.d/procta-backup or ofelia
# config). Keeps 30 days of versions via the bucket's lifecycle rule.
#
# Restore: see scripts/restore_from_b2.sh
#
# Requirements on the KVM:
#   apt install -y backblaze-b2     # or pip3 install b2 --user
#   # In /etc/procta/secrets.env (chmod 600):
#   B2_APPLICATION_KEY_ID=...
#   B2_APPLICATION_KEY=...
#   B2_BUCKET=procta-backups
#   PG_PASSWORD=... (matches POSTGRES_PASSWORD in .env)

set -euo pipefail

# ── config ────────────────────────────────────────────────────────
SECRETS_FILE="${SECRETS_FILE:-/etc/procta/secrets.env}"
PROJECT_ROOT="${PROJECT_ROOT:-/root/proctor-browser}"
BACKUP_TMP="${BACKUP_TMP:-/tmp/procta-backup}"
RETENTION_LOCAL_DAYS="${RETENTION_LOCAL_DAYS:-2}"

if [ -f "$SECRETS_FILE" ]; then
  # shellcheck source=/dev/null
  set -a; . "$SECRETS_FILE"; set +a
fi

: "${B2_APPLICATION_KEY_ID:?B2_APPLICATION_KEY_ID must be set in $SECRETS_FILE}"
: "${B2_APPLICATION_KEY:?B2_APPLICATION_KEY must be set in $SECRETS_FILE}"
: "${B2_BUCKET:=procta-backups}"

mkdir -p "$BACKUP_TMP"
TS="$(date -u +%Y%m%dT%H%M%SZ)"

# ── 1. Postgres dump ──────────────────────────────────────────────
echo "[backup] dumping Postgres at $TS..."
PGDUMP="$BACKUP_TMP/procta-pg-$TS.dump"
docker exec proctor-postgres pg_dump \
  -U procta -d procta \
  -F c -Z 6 \
  --no-owner --no-acl \
  > "$PGDUMP"
echo "[backup]   pg dump: $(du -h "$PGDUMP" | cut -f1)"

# ── 2. Screenshots tarball ────────────────────────────────────────
SS_DIR="$PROJECT_ROOT/screenshots"
SS_TAR="$BACKUP_TMP/procta-screenshots-$TS.tar.zst"
if [ -d "$SS_DIR" ] && [ -n "$(ls -A "$SS_DIR" 2>/dev/null || true)" ]; then
  echo "[backup] archiving screenshots..."
  tar --use-compress-program="zstd -8" -cf "$SS_TAR" -C "$PROJECT_ROOT" screenshots
  echo "[backup]   screenshots: $(du -h "$SS_TAR" | cut -f1)"
else
  SS_TAR=""
  echo "[backup]   screenshots: skipped (empty)"
fi

# ── 3. Question images tarball ────────────────────────────────────
QI_DIR="$PROJECT_ROOT/question_images"
QI_TAR="$BACKUP_TMP/procta-question-images-$TS.tar.zst"
if [ -d "$QI_DIR" ] && [ -n "$(ls -A "$QI_DIR" 2>/dev/null || true)" ]; then
  echo "[backup] archiving question images..."
  tar --use-compress-program="zstd -8" -cf "$QI_TAR" -C "$PROJECT_ROOT" question_images
  echo "[backup]   question images: $(du -h "$QI_TAR" | cut -f1)"
else
  QI_TAR=""
  echo "[backup]   question images: skipped (empty)"
fi

# ── 4. Upload to B2 ───────────────────────────────────────────────
echo "[backup] authorising with B2..."
b2 account authorize "$B2_APPLICATION_KEY_ID" "$B2_APPLICATION_KEY" >/dev/null

upload() {
  local path="$1"
  [ -z "$path" ] && return 0
  local name
  name="$(basename "$path")"
  echo "[backup] uploading $name..."
  b2 file upload --quiet "$B2_BUCKET" "$path" "$name"
}
upload "$PGDUMP"
upload "$SS_TAR"
upload "$QI_TAR"

# ── 5. Local cleanup (keep $RETENTION_LOCAL_DAYS days of dumps) ───
find "$BACKUP_TMP" -name 'procta-*' -type f -mtime "+$RETENTION_LOCAL_DAYS" -delete

echo "[backup] done at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
