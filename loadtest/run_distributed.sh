#!/usr/bin/env bash
# Distributed real-exam load test — Mac + GitHub Codespace.
#
# What this does (one command):
#   1. SSH to KVM → setup exam + mint TOTAL_VUS tokens.
#   2. SCP the full token file to the Mac.
#   3. Slice it locally with jq into two halves:
#        loadtest/loadtest_tokens.mac.json
#        loadtest/loadtest_tokens.codespace.json
#   4. Upload the Codespace half via `gh codespace cp`.
#   5. Print a synchronized-start instruction block — two terminals,
#      one on Mac and one in Codespace, both started within 5 seconds.
#   6. Wait for both to finish, pull Codespace summary back to Mac.
#   7. Run merge_k6_summaries.py → aggregate result.
#
# Prereqs on Mac:
#   - k6 (brew install k6)
#   - jq (brew install jq)
#   - gh CLI authed (gh auth status)
#   - The codespace MUST exist already. To create it:
#       gh codespace create -r ArihantK15/proctor-browser -b main
#     Then SSH into it once to ensure it's warm:
#       gh codespace ssh
#     And run the one-time setup inside it:
#       sudo apt update && sudo apt install -y k6 jq
#       # OR k6 binary install (newer):
#       #   sudo gpg -k && sudo gpg --no-default-keyring \
#       #     --keyring /usr/share/keyrings/k6-archive-keyring.gpg \
#       #     --keyserver hkp://keyserver.ubuntu.com:80 \
#       #     --recv-keys C5AD17C747E3415A3642D57D77C6C491D6AC1D69
#       #   echo "deb [signed-by=/usr/share/keyrings/k6-archive-keyring.gpg] \
#       #     https://dl.k6.io/deb stable main" | sudo tee /etc/apt/sources.list.d/k6.list
#       #   sudo apt update && sudo apt install -y k6
#
# Usage:
#   loadtest/run_distributed.sh <teacher-email> <password> <total-vus> <exam-secs> [codespace-name]
#
# Examples:
#   loadtest/run_distributed.sh loadtest@procta.net 'LoadTest!2026' 3000 300
#   loadtest/run_distributed.sh loadtest@procta.net 'LoadTest!2026' 4000 300 my-codespace
#
# Override defaults:
#   TARGET=https://app.procta.net   KVM_HOST=root@1.2.3.4   ORIGIN_IP=1.2.3.4   BYPASS_CF=1

set -euo pipefail

TEACHER_EMAIL="${1:?teacher-email required}"
TEACHER_PASSWORD="${2:?password required}"
TOTAL_VUS="${3:-3000}"
EXAM_SECONDS="${4:-300}"
CODESPACE_NAME="${5:-}"

TARGET="${TARGET:-https://app.procta.net}"
KVM_HOST="${KVM_HOST:-root@187.127.169.89}"
KVM_REPO="${KVM_REPO:-/root/proctor-browser}"
ORIGIN_IP="${ORIGIN_IP:-187.127.169.89}"
BYPASS_CF="${BYPASS_CF:-1}"   # default ON for distributed test (isolate server from CF)

HERE="$(cd "$(dirname "$0")" && pwd)"
TOK_ALL="${HERE}/loadtest_tokens.json"
TOK_MAC="${HERE}/loadtest_tokens.mac.json"
TOK_CS="${HERE}/loadtest_tokens.codespace.json"

MAC_VUS=$(( TOTAL_VUS / 2 ))
CS_VUS=$(( TOTAL_VUS - MAC_VUS ))

# Pre-flight checks
for cmd in k6 jq gh ssh scp; do
  command -v "$cmd" >/dev/null || { echo "missing: $cmd"; exit 1; }
done

# Pick the codespace (first running one if not specified)
if [ -z "$CODESPACE_NAME" ]; then
  CODESPACE_NAME=$(gh codespace list --json name,state -q '.[] | select(.state=="Available") | .name' | head -1)
  if [ -z "$CODESPACE_NAME" ]; then
    echo "No available codespace found. Create one with:"
    echo "  gh codespace create -r ArihantK15/proctor-browser -b main"
    exit 1
  fi
  echo "Using codespace: ${CODESPACE_NAME}"
fi

DURATION_MIN=$(( (EXAM_SECONDS + 60) / 60 + 10 ))

# ── Step 1: KVM-side setup + mint TOTAL_VUS tokens ────────────────
echo "── 1/6: KVM setup + mint (${TOTAL_VUS} students) ────"
ssh "${KVM_HOST}" bash -s \
    "${TEACHER_EMAIL}" "${TEACHER_PASSWORD}" "${TOTAL_VUS}" \
    "${TARGET}" "${DURATION_MIN}" "${KVM_REPO}" <<'REMOTE'
set -euo pipefail
TEACHER_EMAIL="$1"; TEACHER_PASSWORD="$2"; VUS="$3"
TARGET="$4"; DURATION_MIN="$5"; KVM_REPO="$6"
cd "${KVM_REPO}"
git pull --rebase=false 2>&1 | tail -3
docker compose up -d --no-deps --force-recreate --build api 2>&1 | tail -3
sleep 12
python3 loadtest/setup_test_data.py \
  --host "${TARGET}" --students "${VUS}" \
  --teacher-email "${TEACHER_EMAIL}" --teacher-password "${TEACHER_PASSWORD}" \
  --questions 20 --duration "${DURATION_MIN}"
EXAM_ID=$(python3 -c "import json; print(json.load(open('loadtest/test_students.json'))['exam_id'])")
TEACHER_ID=$(python3 -c "import json; print(json.load(open('loadtest/test_students.json'))['teacher_id'])")
echo "  exam_id=${EXAM_ID}  teacher_id=${TEACHER_ID}"
docker compose run --rm --no-deps --entrypoint python api \
  scripts/mint_loadtest_tokens.py \
  --count "${VUS}" --prefix LOADTEST --zero-pad 4 \
  --teacher-id "${TEACHER_ID}" --exam-id "${EXAM_ID}" \
  > loadtest/loadtest_tokens.json
echo "  tokens: $(wc -c < loadtest/loadtest_tokens.json) bytes"
REMOTE

# ── Step 2: download token file ───────────────────────────────────
echo ""
echo "── 2/6: pull tokens to Mac ──────────────────────────"
scp "${KVM_HOST}:${KVM_REPO}/loadtest/loadtest_tokens.json" "${TOK_ALL}"

# ── Step 3: slice ─────────────────────────────────────────────────
echo ""
echo "── 3/6: slicing (${MAC_VUS} Mac / ${CS_VUS} Codespace) ──"
jq ".[0:${MAC_VUS}]"            "${TOK_ALL}" > "${TOK_MAC}"
jq ".[${MAC_VUS}:${TOTAL_VUS}]" "${TOK_ALL}" > "${TOK_CS}"
echo "  Mac: $(jq length < "${TOK_MAC}") tokens"
echo "  Codespace: $(jq length < "${TOK_CS}") tokens"

# ── Step 4: ship Codespace half ───────────────────────────────────
echo ""
echo "── 4/6: upload Codespace tokens ─────────────────────"
# First make sure the Codespace's checkout is current and the
# loadtest/ directory exists.
gh codespace ssh -c "${CODESPACE_NAME}" -- \
  'cd /workspaces/proctor-browser && git pull --rebase=false 2>&1 | tail -3 && mkdir -p loadtest'

# `gh codespace cp` wraps the remote path in single quotes when it
# invokes scp under the hood, which scp then treats as part of the
# filename and errors with "No such file or directory" for any
# absolute path. Stream the file through SSH stdin instead — same
# effect, no quoting bug.
cat "${TOK_CS}" | gh codespace ssh -c "${CODESPACE_NAME}" -- \
  'cat > /workspaces/proctor-browser/loadtest/loadtest_tokens.json'
cat "${HERE}/real_exam_jwt.js" | gh codespace ssh -c "${CODESPACE_NAME}" -- \
  'cat > /workspaces/proctor-browser/loadtest/real_exam_jwt.js'

# ── Step 5: synchronized start ────────────────────────────────────
START_AT=$(($(date +%s) + 20))
START_HUMAN=$(date -r "${START_AT}" '+%H:%M:%S')

CS_CMD="cd /workspaces/proctor-browser && \
  BYPASS_CF=${BYPASS_CF} ORIGIN_IP=${ORIGIN_IP} \
  k6 run \
  -e TARGET='${TARGET}' \
  -e TOKEN_FILE=./loadtest/loadtest_tokens.json \
  -e VUS=${CS_VUS} \
  -e EXAM_SECONDS=${EXAM_SECONDS} \
  ${BYPASS_CF:+-e BYPASS_CF=${BYPASS_CF}} \
  ${ORIGIN_IP:+-e ORIGIN_IP=${ORIGIN_IP}} \
  loadtest/real_exam_jwt.js"

echo ""
echo "════════════════════════════════════════════════════════"
echo "  Synchronized start at: ${START_HUMAN} (in 20s)"
echo "════════════════════════════════════════════════════════"
echo ""
echo "  → Open ANOTHER terminal NOW and paste:"
echo ""
echo "    gh codespace ssh -c ${CODESPACE_NAME} -- \"${CS_CMD}\""
echo ""
echo "  The Mac side will start automatically at ${START_HUMAN}."
echo "════════════════════════════════════════════════════════"
echo ""

# Block until START_AT
while [ "$(date +%s)" -lt "$START_AT" ]; do
  REMAIN=$(( START_AT - $(date +%s) ))
  printf "\r  Mac k6 starts in %2ds..." "$REMAIN"
  sleep 1
done
echo ""

# ── Step 6: Mac k6 ────────────────────────────────────────────────
echo "── 5/6: Mac k6 (${MAC_VUS} VUs) ─────────────────────"
k6 run \
  -e TARGET="${TARGET}" \
  -e TOKEN_FILE="${TOK_MAC}" \
  -e VUS="${MAC_VUS}" \
  -e EXAM_SECONDS="${EXAM_SECONDS}" \
  ${BYPASS_CF:+-e BYPASS_CF="${BYPASS_CF}"} \
  ${ORIGIN_IP:+-e ORIGIN_IP="${ORIGIN_IP}"} \
  "${HERE}/real_exam_jwt.js"

echo ""
echo "── 6/6: pull Codespace summary + merge ──────────────"
echo "  Waiting 10s for Codespace k6 to finalize its summary..."
sleep 10

# Find newest summary in Codespace + pull it
CS_LATEST_SUMMARY=$(gh codespace ssh -c "${CODESPACE_NAME}" -- \
  "ls -1t /workspaces/proctor-browser/summary-real-exam-jwt-*.json 2>/dev/null | head -1" \
  | tr -d '\r\n')
if [ -n "$CS_LATEST_SUMMARY" ]; then
  CS_LOCAL="${HERE}/$(basename "$CS_LATEST_SUMMARY" .json)-codespace.json"
  # Pull via cat over SSH (same workaround as the upload — gh codespace cp
  # mangles absolute remote paths via single-quote injection into scp).
  gh codespace ssh -c "${CODESPACE_NAME}" -- "cat '${CS_LATEST_SUMMARY}'" > "${CS_LOCAL}"
  echo "  Codespace summary: $(basename "$CS_LOCAL")"
else
  echo "  WARN: no Codespace summary found (test may have errored out)"
  CS_LOCAL=""
fi

MAC_LATEST=$(ls -1t "${HERE}"/../summary-real-exam-jwt-*.json 2>/dev/null | head -1 || true)
MAC_LATEST=${MAC_LATEST:-$(ls -1t summary-real-exam-jwt-*.json 2>/dev/null | head -1)}
if [ -z "$MAC_LATEST" ]; then
  echo "  WARN: no Mac summary found"
  exit 0
fi
echo "  Mac summary:       $(basename "$MAC_LATEST")"

echo ""
echo "════════════════════════════════════════════════════════"
"${HERE}/merge_k6_summaries.py" "$MAC_LATEST" ${CS_LOCAL:+"$CS_LOCAL"}
