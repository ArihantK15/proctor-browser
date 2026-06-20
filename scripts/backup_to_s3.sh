#!/usr/bin/env bash
# Procta nightly backup → AWS S3 (ap-south-1, Mumbai).
#
# Replaces backup_to_b2.sh: keeps DB dumps + media archives IN INDIA, closing
# the DPDP data-residency gap (Backblaze B2 has no India region). Reuses the
# AWS credentials the app already uses for screenshots (AWS_ACCESS_KEY_ID /
# AWS_SECRET_ACCESS_KEY / S3_REGION in $SECRETS_FILE). Object retention is
# enforced by the bucket LIFECYCLE rule (default 30 days), not by this script —
# only the local temp copies are pruned here.
#
# Layout in the bucket:
#   db/procta-pg-<ts>.dump
#   screenshots/procta-screenshots-<ts>.tar.zst
#   question_images/procta-question-images-<ts>.tar.zst
set -euo pipefail

SECRETS_FILE="${SECRETS_FILE:-/etc/procta/secrets.env}"
PROJECT_ROOT="${PROJECT_ROOT:-/root/proctor-browser}"
BACKUP_TMP="${BACKUP_TMP:-/tmp/procta-backup}"
RETENTION_LOCAL_DAYS="${RETENTION_LOCAL_DAYS:-2}"

if [ -f "$SECRETS_FILE" ]; then
  # shellcheck source=/dev/null
  set -a; . "$SECRETS_FILE"; set +a
fi

: "${AWS_ACCESS_KEY_ID:?AWS_ACCESS_KEY_ID must be set in $SECRETS_FILE}"
: "${AWS_SECRET_ACCESS_KEY:?AWS_SECRET_ACCESS_KEY must be set in $SECRETS_FILE}"
: "${BACKUP_S3_BUCKET:=procta-backups}"
: "${S3_REGION:=ap-south-1}"
export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY

if ! command -v aws >/dev/null 2>&1; then
  echo "[backup] ERROR: the 'aws' CLI is not installed. Install it once:" >&2
  echo "         (Debian/Ubuntu)  apt-get update && apt-get install -y awscli" >&2
  exit 1
fi

mkdir -p "$BACKUP_TMP"
TS="$(date -u +%Y%m%dT%H%M%SZ)"

echo "[backup] dumping Postgres at $TS..."
PGDUMP="$BACKUP_TMP/procta-pg-$TS.dump"
docker exec proctor-postgres pg_dump \
  -U procta -d procta \
  -F c -Z 6 \
  --no-owner --no-acl \
  > "$PGDUMP"
echo "[backup]   pg dump: $(du -h "$PGDUMP" | cut -f1)"

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

S3="s3://$BACKUP_S3_BUCKET"

# Server-side encryption. Default SSE-S3 (AES256). If BACKUP_S3_SSE_KMS_KEY_ID
# is set in secrets.env, use SSE-KMS with that customer-managed key instead —
# recommended so DB backups match the screenshots bucket's protection. The
# IAM user needs kms:GenerateDataKey on that key (the existing screenshots KMS
# grant already covers it if you reuse the same key).
SSE_ARGS=(--sse AES256)
if [ -n "${BACKUP_S3_SSE_KMS_KEY_ID:-}" ]; then
  SSE_ARGS=(--sse aws:kms --sse-kms-key-id "$BACKUP_S3_SSE_KMS_KEY_ID")
fi

upload() {
  local path="$1" prefix="$2"
  [ -z "$path" ] && return 0
  local name
  name="$(basename "$path")"
  echo "[backup] uploading $prefix/$name..."
  # --only-show-errors keeps the cron log quiet on success.
  aws s3 cp --only-show-errors --region "$S3_REGION" "${SSE_ARGS[@]}" \
    "$path" "$S3/$prefix/$name"
}
upload "$PGDUMP" "db"
upload "$SS_TAR"  "screenshots"
upload "$QI_TAR"  "question_images"

find "$BACKUP_TMP" -name 'procta-*' -type f -mtime "+$RETENTION_LOCAL_DAYS" -delete

echo "[backup] done at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
