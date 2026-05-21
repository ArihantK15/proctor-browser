#!/usr/bin/env bash
# Run the real-exam load test end-to-end FROM YOUR MAC.
#
# What this does, in one command:
#   1. SSHes to the KVM, runs `git pull`, runs setup_test_data.py to
#      create the exam + students, mints per-student JWTs inside the
#      API container (so SUPABASE_JWT_SECRET matches production).
#   2. scp's the token file from KVM → this Mac.
#   3. Runs k6 locally against https://app.procta.net using those tokens.
#
# k6 runs on the Mac (not on the KVM) so the API container's CPU isn't
# also generating load against itself — the latency numbers measure the
# real production path from outside.
#
# Usage (from your Mac, in the proctored-browser checkout):
#   loadtest/run_from_mac.sh <teacher-email> <password> [VUs] [exam-seconds]
#
# Example:
#   loadtest/run_from_mac.sh loadtest@procta.net 'LoadTest!2026' 200 300
#
# Prereqs:
#   - k6 installed on this Mac (brew install k6)
#   - SSH key auth working to the KVM (you've already done `ssh root@<ip>`
#     without a password prompt at least once)
#
# Override defaults via env vars:
#   TARGET=https://staging.procta.net   loadtest/run_from_mac.sh ...
#   KVM_HOST=root@1.2.3.4               loadtest/run_from_mac.sh ...
#   KVM_REPO=/opt/proctor-browser       loadtest/run_from_mac.sh ...
#
# This script does NOT require any repo state on the KVM beyond a checkout
# at KVM_REPO with the latest server-side code — it runs `git pull` on
# the KVM for you. On the Mac, only this file and loadtest/real_exam_jwt.js
# need to exist locally.

set -euo pipefail

TEACHER_EMAIL="${1:?teacher-email required as arg 1}"
TEACHER_PASSWORD="${2:?teacher-password required as arg 2}"
VUS="${3:-200}"
EXAM_SECONDS="${4:-300}"

TARGET="${TARGET:-https://app.procta.net}"
# `srv1675832` is the KVM's own hostname — not DNS-resolvable from the
# Mac. Use the public IP. Override KVM_HOST=root@some.host or
# KVM_HOST=root@your-ssh-config-alias if you have a different setup.
KVM_HOST="${KVM_HOST:-root@187.127.169.89}"
KVM_REPO="${KVM_REPO:-/root/proctor-browser}"

HERE="$(cd "$(dirname "$0")" && pwd)"
TOKENS_LOCAL="${HERE}/loadtest_tokens.json"

# Setup script wants a duration in MINUTES; pad a bit so the exam window
# doesn't close while the test is still submitting.
DURATION_MIN=$(( (EXAM_SECONDS + 60) / 60 + 10 ))

# ── Step 1: KVM-side setup + mint ──────────────────────────────
# bash -s lets us pass positional args ($1..$N) to the heredoc script
# without local shell expansion clobbering the remote script. The
# quoted `<<'REMOTE'` delimiter is what blocks local $-expansion.
echo "── 1/3: KVM setup + mint (${VUS} students) ────────"
ssh "${KVM_HOST}" bash -s \
    "${TEACHER_EMAIL}" "${TEACHER_PASSWORD}" "${VUS}" \
    "${TARGET}" "${DURATION_MIN}" "${KVM_REPO}" <<'REMOTE'
set -euo pipefail
TEACHER_EMAIL="$1"
TEACHER_PASSWORD="$2"
VUS="$3"
TARGET="$4"
DURATION_MIN="$5"
KVM_REPO="$6"

cd "${KVM_REPO}"
echo "  git pull..."
git pull --rebase=false 2>&1 | tail -3

# Rebuild the api image so `docker compose run` picks up any newly
# pulled changes in scripts/ (e.g. mint_loadtest_tokens.py). The image
# bakes those files in at build time; without this step the run uses
# yesterday's version of the script even after git pull.
echo "  rebuilding api image (picks up new scripts)..."
docker compose build api 2>&1 | tail -3

echo "  creating exam + ${VUS} students..."
python3 loadtest/setup_test_data.py \
  --host "${TARGET}" \
  --students "${VUS}" \
  --teacher-email "${TEACHER_EMAIL}" \
  --teacher-password "${TEACHER_PASSWORD}" \
  --questions 20 \
  --duration "${DURATION_MIN}"

EXAM_ID=$(python3 -c "import json; print(json.load(open('loadtest/test_students.json'))['exam_id'])")
TEACHER_ID=$(python3 -c "import json; print(json.load(open('loadtest/test_students.json'))['teacher_id'])")
echo "  exam_id=${EXAM_ID}  teacher_id=${TEACHER_ID}"

echo "  minting ${VUS} JWTs inside the API container..."
docker compose run --rm --no-deps --entrypoint python api \
  scripts/mint_loadtest_tokens.py \
  --count "${VUS}" \
  --prefix LOADTEST \
  --zero-pad 4 \
  --teacher-id "${TEACHER_ID}" \
  --exam-id   "${EXAM_ID}" \
  > loadtest/loadtest_tokens.json

TOK_BYTES=$(wc -c < loadtest/loadtest_tokens.json)
echo "  tokens written: ${TOK_BYTES} bytes"
REMOTE

# ── Step 2: pull the token file down ───────────────────────────
echo ""
echo "── 2/3: copying tokens to Mac ─────────────────────"
scp "${KVM_HOST}:${KVM_REPO}/loadtest/loadtest_tokens.json" "${TOKENS_LOCAL}"
ls -la "${TOKENS_LOCAL}"

# ── Step 3: run k6 locally ────────────────────────────────────
echo ""
echo "── 3/3: k6 run from Mac → ${TARGET} ───────────────"
k6 run \
  -e TARGET="${TARGET}" \
  -e TOKEN_FILE="${TOKENS_LOCAL}" \
  -e VUS="${VUS}" \
  -e EXAM_SECONDS="${EXAM_SECONDS}" \
  "${HERE}/real_exam_jwt.js"
