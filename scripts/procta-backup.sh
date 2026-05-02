#!/bin/bash
# Procta — nightly off-site backup of forensic screenshots.
#
# Uses restic (https://restic.net) to snapshot the screenshots/
# directory to an S3-compatible / Backblaze B2 bucket. Restic does
# de-duplication so even daily snapshots take little extra space
# (only the new files transferred + indexed).
#
# Installation: see DEPLOY.md §2.5. Credentials must be in
# /etc/procta-backup.env with strict permissions (0600).
#
# What this DOES backup:
#   • screenshots/  (forensic frames — institution-owned evidence)
#
# What it does NOT:
#   • Database — Supabase backs that up automatically. If you self-
#     host Postgres, add `pg_dumpall` here.
#   • Application code — that's in git.
#   • Caddy + Redis volumes — they're recoverable from rebuild.

set -euo pipefail

# Load credentials. Set RESTIC_REPOSITORY, B2_ACCOUNT_ID,
# B2_ACCOUNT_KEY, RESTIC_PASSWORD here.
if [ ! -f /etc/procta-backup.env ]; then
  echo "ERROR: /etc/procta-backup.env missing — see DEPLOY.md §2.5" >&2
  exit 1
fi
set -a
# shellcheck disable=SC1091
source /etc/procta-backup.env
set +a

SCREENSHOT_DIR="${SCREENSHOT_DIR:-/root/proctor-browser/screenshots}"
ts() { date '+%Y-%m-%d %H:%M:%S'; }

if [ ! -d "$SCREENSHOT_DIR" ]; then
  echo "[$(ts)] WARN: $SCREENSHOT_DIR does not exist — nothing to back up"
  exit 0
fi

echo "[$(ts)] Backup start. Source=$SCREENSHOT_DIR"

# --tag lets us identify backup runs in `restic snapshots`.
# --host overrides the hostname so backups across droplet replacements
# stay aggregated under one logical host.
restic backup \
  --tag procta-screenshots \
  --host procta-prod \
  "$SCREENSHOT_DIR"

# Retention: 7 daily, 4 weekly, 6 monthly. restic does this with the
# `forget --prune` command which marks old snapshots for deletion AND
# reclaims the underlying chunks. Without --prune the bucket grows
# forever even when snapshots are forgotten.
echo "[$(ts)] Pruning old snapshots..."
restic forget \
  --keep-daily   7 \
  --keep-weekly  4 \
  --keep-monthly 6 \
  --prune

# Quick integrity sanity check — restic check verifies the repo
# structure. Doesn't read every file (that's restic check --read-data
# and is too slow for a nightly cron); we run it weekly via
# `--read-data-subset 5%` instead.
DAY_OF_WEEK=$(date +%u)
if [ "$DAY_OF_WEEK" -eq 7 ]; then
  echo "[$(ts)] Sunday — running integrity check on 5% of data"
  restic check --read-data-subset 5%
else
  restic check --no-cache
fi

echo "[$(ts)] Backup complete."
