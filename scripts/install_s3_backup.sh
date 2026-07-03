#!/usr/bin/env bash
# One-shot installer for the Procta off-site S3 (ap-south-1, Mumbai) backup cron.
#
# Replaces install_b2_backup.sh: Backblaze B2 has no India region, so DB
# backups + media archives shipped there sat outside India while evidence
# (screenshots, question images) already lived in S3 Mumbai — a data-
# residency mismatch against the DPDP/DPIA posture (docs/DPIA.md). This
# installer wires scripts/backup_to_s3.sh onto the same schedule the B2
# job used, reusing the AWS credentials the app already has for evidence
# storage (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / S3_REGION).
#
# Run once on the prod server as root. Idempotent — re-running is safe
# (won't clobber an existing /etc/procta/secrets.env, won't duplicate
# the cron). After install, you must edit /etc/procta/secrets.env to
# add your AWS credentials (or confirm they're already there from the
# screenshot-storage setup), then verify with a manual run.
#
# What this does:
#   1. Ensures the `aws` CLI is installed.
#   2. Creates /etc/procta/ (mode 0700) with a secrets.env template, or
#      appends the AWS_* / BACKUP_S3_BUCKET keys to an existing one.
#   3. Installs /etc/cron.d/procta-s3-backup running the backup at
#      01:30 UTC = 07:00 IST daily — same slot install_b2_backup.sh used,
#      90 min after Ofelia's local pg_dump (00:00 UTC).
#   4. Removes the old B2 cron file if present (/etc/cron.d/procta-b2-backup)
#      so the same backup doesn't ship to two providers on every run —
#      backup_to_b2.sh and its installer stay in the repo as a manual
#      fallback, just no longer scheduled.
#   5. Enables the system cron service (idempotent).
#
# After install, finish setup with:
#   sudo vi /etc/procta/secrets.env                              # fill/confirm creds
#   sudo /root/proctor-browser/scripts/backup_to_s3.sh            # test once
#   tail -f /var/log/procta-s3-backup.log                         # watch tomorrow's run
set -euo pipefail

if [ "$EUID" -ne 0 ]; then
  echo "ERROR: install_s3_backup.sh must run as root (use sudo)" >&2
  exit 1
fi

PROJECT_ROOT="${PROJECT_ROOT:-/root/proctor-browser}"
SECRETS_DIR="/etc/procta"
SECRETS_FILE="$SECRETS_DIR/secrets.env"
CRON_FILE="/etc/cron.d/procta-s3-backup"
OLD_B2_CRON_FILE="/etc/cron.d/procta-b2-backup"
LOG_FILE="/var/log/procta-s3-backup.log"
BACKUP_SCRIPT="$PROJECT_ROOT/scripts/backup_to_s3.sh"

if [ ! -x "$BACKUP_SCRIPT" ]; then
  if [ -f "$BACKUP_SCRIPT" ]; then
    chmod +x "$BACKUP_SCRIPT"
  else
    echo "ERROR: $BACKUP_SCRIPT not found. Is PROJECT_ROOT correct?" >&2
    exit 1
  fi
fi

# ── 1. Ensure aws CLI is present ──────────────────────────────────
echo "==> checking for aws CLI..."
if ! command -v aws >/dev/null 2>&1; then
  echo "==> installing awscli..."
  apt-get update -qq
  apt-get install -y -qq awscli
fi
aws --version >/dev/null 2>&1 || { echo "ERROR: aws CLI install failed" >&2; exit 1; }
echo "==> aws CLI: $(aws --version 2>&1 | head -1)"

# ── 2. Secrets directory + template ───────────────────────────────
echo "==> creating $SECRETS_DIR (mode 0700)..."
mkdir -p "$SECRETS_DIR"
chmod 700 "$SECRETS_DIR"

if [ -f "$SECRETS_FILE" ]; then
  if grep -q '^AWS_ACCESS_KEY_ID=' "$SECRETS_FILE" 2>/dev/null; then
    echo "==> $SECRETS_FILE already has AWS_ACCESS_KEY_ID — not touching it."
  else
    echo "==> appending AWS/S3 backup keys to existing $SECRETS_FILE..."
    cat >> "$SECRETS_FILE" <<'EOF'

# S3 off-site backup credentials. Required by scripts/backup_to_s3.sh.
# Reuse the same AWS IAM user as evidence-screenshot storage if it already
# has s3:PutObject on the backup bucket, or create a dedicated one scoped
# to just the backup bucket. Never commit this file. chmod 600.
AWS_ACCESS_KEY_ID=<replace-with-your-aws-key-id>
AWS_SECRET_ACCESS_KEY=<replace-with-your-aws-secret>
BACKUP_S3_BUCKET=procta-backups
S3_REGION=ap-south-1
EOF
  fi
else
  cat > "$SECRETS_FILE" <<'EOF'
# Procta off-site backup credentials. Required by scripts/backup_to_s3.sh.
# Fill in real values. Never commit this file. chmod 600.

# From AWS IAM -> Users -> (backup user) -> Security credentials.
# Needs s3:PutObject on the backup bucket at minimum.
AWS_ACCESS_KEY_ID=<replace-with-your-aws-key-id>
AWS_SECRET_ACCESS_KEY=<replace-with-your-aws-secret>

# Bucket in ap-south-1 (Mumbai) created for backups (default name matches
# backup_to_s3.sh). Give it a lifecycle rule to expire objects after 30 days.
BACKUP_S3_BUCKET=procta-backups
S3_REGION=ap-south-1
EOF
  chmod 600 "$SECRETS_FILE"
  echo "==> wrote $SECRETS_FILE template (chmod 600)"
fi

# ── 3. Cron job ───────────────────────────────────────────────────
echo "==> installing $CRON_FILE..."
cat > "$CRON_FILE" <<EOF
# Procta off-site backup -> AWS S3 (ap-south-1, Mumbai). Keeps DB dumps
# and media archives in India, closing the DPDP residency gap the old
# Backblaze B2 job left open (B2 has no India region).
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

# ── 4. Retire the old B2 cron so backups don't ship to two providers ──
if [ -f "$OLD_B2_CRON_FILE" ]; then
  echo "==> removing old $OLD_B2_CRON_FILE (B2 backup stays available manually, just no longer scheduled)..."
  rm -f "$OLD_B2_CRON_FILE"
fi

# ── 5. Make sure cron is running ──────────────────────────────────
systemctl enable --now cron 2>/dev/null \
  || systemctl enable --now crond 2>/dev/null \
  || echo "WARN: couldn't enable cron service automatically — check manually"

echo
echo "✓ Install complete."
echo
echo "Next steps:"
echo "  1. Edit/confirm credentials: sudo vi $SECRETS_FILE"
echo "  2. Create/verify S3 bucket:  bucket 'procta-backups' in ap-south-1"
echo "                               with a 30-day lifecycle expiry rule"
echo "  3. Test the backup:          sudo $BACKUP_SCRIPT"
echo "  4. Watch tomorrow:           tail -f $LOG_FILE"
echo
echo "First scheduled run: tomorrow at 01:30 UTC (07:00 IST)."
