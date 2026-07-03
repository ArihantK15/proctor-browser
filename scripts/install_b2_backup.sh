#!/usr/bin/env bash
# DEPRECATED — use scripts/install_s3_backup.sh instead (AWS S3, ap-south-1
# Mumbai). B2 has no India region; see scripts/backup_to_b2.sh for the full
# rationale. Kept only for standing up a manual B2 fallback if ever needed.
#
# One-shot installer for the Procta off-site B2 backup cron.
#
# Run once on the prod server as root. Idempotent — re-running is safe
# (won't clobber an existing /etc/procta/secrets.env, won't duplicate
# the cron). After install, you must edit /etc/procta/secrets.env to
# add your real B2 credentials, then verify with a manual run.
#
# What this does:
#   1. Ensures the `b2` CLI is installed (pip-installs it if missing).
#   2. Creates /etc/procta/ (mode 0700) with a secrets.env template.
#   3. Installs /etc/cron.d/procta-b2-backup running the backup at
#      01:30 UTC = 07:00 IST daily — 90 min after Ofelia's local
#      pg_dump (00:00 UTC) so on-disk + off-site don't clash.
#   4. Enables the system cron service (already on by default on most
#      distros, but `enable --now` is idempotent so harmless).
#
# After install, finish setup with:
#   sudo vi /etc/procta/secrets.env                              # fill creds
#   sudo /root/proctor-browser/scripts/backup_to_b2.sh           # test once
#   tail -f /var/log/procta-b2-backup.log                        # watch tomorrow's run
set -euo pipefail

if [ "$EUID" -ne 0 ]; then
  echo "ERROR: install_b2_backup.sh must run as root (use sudo)" >&2
  exit 1
fi

PROJECT_ROOT="${PROJECT_ROOT:-/root/proctor-browser}"
SECRETS_DIR="/etc/procta"
SECRETS_FILE="$SECRETS_DIR/secrets.env"
CRON_FILE="/etc/cron.d/procta-b2-backup"
LOG_FILE="/var/log/procta-b2-backup.log"
BACKUP_SCRIPT="$PROJECT_ROOT/scripts/backup_to_b2.sh"

if [ ! -x "$BACKUP_SCRIPT" ]; then
  if [ -f "$BACKUP_SCRIPT" ]; then
    chmod +x "$BACKUP_SCRIPT"
  else
    echo "ERROR: $BACKUP_SCRIPT not found. Is PROJECT_ROOT correct?" >&2
    exit 1
  fi
fi

# ── 1. Ensure b2 CLI is present ───────────────────────────────────
echo "==> checking for b2 CLI..."
if ! command -v b2 >/dev/null 2>&1; then
  echo "==> installing b2 via pip3..."
  if ! command -v pip3 >/dev/null 2>&1; then
    apt-get update -qq
    apt-get install -y -qq python3-pip
  fi
  pip3 install --quiet --break-system-packages b2 2>/dev/null \
    || pip3 install --quiet b2
fi
b2 version >/dev/null 2>&1 || { echo "ERROR: b2 CLI install failed" >&2; exit 1; }
echo "==> b2 CLI: $(b2 version | head -1)"

# ── 2. Secrets directory + template ───────────────────────────────
echo "==> creating $SECRETS_DIR (mode 0700)..."
mkdir -p "$SECRETS_DIR"
chmod 700 "$SECRETS_DIR"

if [ -f "$SECRETS_FILE" ]; then
  echo "==> $SECRETS_FILE exists — not overwriting."
else
  cat > "$SECRETS_FILE" <<'EOF'
# Procta off-site backup credentials. Required by scripts/backup_to_b2.sh.
# Fill in real values. Never commit this file. chmod 600.

# From Backblaze console -> Account -> Application Keys
B2_APPLICATION_KEY_ID=<replace-with-your-b2-key-id>
B2_APPLICATION_KEY=<replace-with-your-b2-app-key>

# Bucket created in Backblaze (default name matches backup_to_b2.sh)
B2_BUCKET=procta-backups
EOF
  chmod 600 "$SECRETS_FILE"
  echo "==> wrote $SECRETS_FILE template (chmod 600)"
fi

# ── 3. Cron job ───────────────────────────────────────────────────
echo "==> installing $CRON_FILE..."
cat > "$CRON_FILE" <<EOF
# Procta off-site backup → Backblaze B2.
# 01:30 UTC = 07:00 IST. Runs 90 min after Ofelia's local pg_dump
# (00:00 UTC) so the upload doesn't compete for IO with the on-disk
# rotation. Logs to $LOG_FILE.
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
MAILTO=""
30 1 * * * root $BACKUP_SCRIPT >> $LOG_FILE 2>&1
EOF
chmod 644 "$CRON_FILE"

touch "$LOG_FILE"
chmod 600 "$LOG_FILE"

# ── 4. Make sure cron is running ──────────────────────────────────
systemctl enable --now cron 2>/dev/null \
  || systemctl enable --now crond 2>/dev/null \
  || echo "WARN: couldn't enable cron service automatically — check manually"

echo
echo "✓ Install complete."
echo
echo "Next steps:"
echo "  1. Edit credentials:    sudo vi $SECRETS_FILE"
echo "  2. Create B2 bucket:    log in at https://www.backblaze.com/b2/"
echo "                          → create bucket 'procta-backups' (private)"
echo "  3. Test the backup:     sudo $BACKUP_SCRIPT"
echo "  4. Watch tomorrow:      tail -f $LOG_FILE"
echo
echo "First scheduled run: tomorrow at 01:30 UTC (07:00 IST)."
