#!/bin/bash
# Procta — screenshots retention cron.
#
# Deletes forensic screenshot frames older than RETENTION_DAYS from
# the proctor's bind-mount directory. Without this, the directory
# grows ~50 MB per 100 active students per day; a few weeks of
# usage will fill the droplet.
#
# Installation: see DEPLOY.md §2.4.
#
# Tuning RETENTION_DAYS:
#   30  — aggressive; keeps only the current term's evidence
#   90  — recommended; covers the typical "the student appealed
#         their result" window
#  180  — conservative; survives most academic-year audit cycles
#
# Set via env or edit the default below. Override per-run:
#   RETENTION_DAYS=180 /usr/local/bin/procta-screenshots-cleanup.sh

set -euo pipefail

# Where the screenshots live on the HOST (the path on the host side
# of the docker-compose bind mount). Adjust if your droplet stores
# the project under a different path.
SCREENSHOT_DIR="${SCREENSHOT_DIR:-/root/proctor-browser/screenshots}"
RETENTION_DAYS="${RETENTION_DAYS:-90}"

ts() { date '+%Y-%m-%d %H:%M:%S'; }

if [ ! -d "$SCREENSHOT_DIR" ]; then
  echo "[$(ts)] ERROR: $SCREENSHOT_DIR does not exist — check SCREENSHOT_DIR" >&2
  exit 1
fi

# Disk usage before
before=$(du -sh "$SCREENSHOT_DIR" 2>/dev/null | cut -f1)
file_count_before=$(find "$SCREENSHOT_DIR" -type f 2>/dev/null | wc -l)

echo "[$(ts)] Cleanup start. Path=$SCREENSHOT_DIR retention=${RETENTION_DAYS}d"
echo "[$(ts)] Before: ${file_count_before} files, ${before}"

# Delete files older than RETENTION_DAYS. -mtime is "modified time
# in days"; +N means strictly more than N days ago.
# Two-step: first count what we'd delete (dry visibility), then
# actually delete.
to_delete=$(find "$SCREENSHOT_DIR" -type f -mtime "+${RETENTION_DAYS}" 2>/dev/null | wc -l)
echo "[$(ts)] Files to delete: ${to_delete}"

if [ "$to_delete" -gt 0 ]; then
  find "$SCREENSHOT_DIR" -type f -mtime "+${RETENTION_DAYS}" -delete
fi

# Remove empty directories left behind. Skip the root.
find "$SCREENSHOT_DIR" -type d -empty -mindepth 1 -delete 2>/dev/null || true

after=$(du -sh "$SCREENSHOT_DIR" 2>/dev/null | cut -f1)
file_count_after=$(find "$SCREENSHOT_DIR" -type f 2>/dev/null | wc -l)
echo "[$(ts)] After:  ${file_count_after} files, ${after}"
echo "[$(ts)] Freed:  $((file_count_before - file_count_after)) files."
echo
