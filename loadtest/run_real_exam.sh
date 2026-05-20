#!/usr/bin/env bash
# Orchestrate the real-exam load test:
#   1. Create the exam + students in the database (setup_test_data.py)
#   2. Mint per-student JWTs inside the API container (so the JWT secret matches)
#   3. Run the k6 real-exam-jwt scenario
#
# Usage:
#   loadtest/run_real_exam.sh <teacher-email> <teacher-password> [VUs] [duration]
#
# Example:
#   loadtest/run_real_exam.sh prof@school.edu hunter2 500 600

set -euo pipefail

TEACHER_EMAIL="${1:?teacher-email required as arg 1}"
TEACHER_PASSWORD="${2:?teacher-password required as arg 2}"
VUS="${3:-500}"
EXAM_SECONDS="${4:-300}"
TARGET="${TARGET:-https://app.procta.net}"
HERE="$(cd "$(dirname "$0")" && pwd)"
MANIFEST="${HERE}/test_students.json"
TOKENS="${HERE}/loadtest_tokens.json"

echo "── 1/3: Creating exam + ${VUS} students in DB ───────────"
python3 "${HERE}/setup_test_data.py" \
  --host "${TARGET}" \
  --students "${VUS}" \
  --teacher-email "${TEACHER_EMAIL}" \
  --teacher-password "${TEACHER_PASSWORD}" \
  --questions 20 \
  --duration "$(( (EXAM_SECONDS + 60) / 60 + 10 ))"

# Extract IDs from the manifest
EXAM_ID="$(python3 -c "import json; print(json.load(open('${MANIFEST}'))['exam_id'])")"
TEACHER_ID="$(python3 -c "import json; print(json.load(open('${MANIFEST}'))['teacher_id'])")"

echo ""
echo "── 2/3: Minting ${VUS} JWTs inside the API container ────"
echo "       (exam_id=${EXAM_ID} teacher_id=${TEACHER_ID})"
# Run mint inside the API container so SUPABASE_JWT_SECRET matches production.
# `docker compose run` requires repo root context.
(
  cd "${HERE}/.."
  docker compose run --rm --no-deps --entrypoint python api \
    scripts/mint_loadtest_tokens.py \
    --count "${VUS}" \
    --prefix LOADTEST \
    --zero-pad 4 \
    --teacher-id "${TEACHER_ID}" \
    --exam-id   "${EXAM_ID}"
) > "${TOKENS}"
TOKEN_COUNT="$(python3 -c "import json; print(len(json.load(open('${TOKENS}'))))")"
echo "       ${TOKEN_COUNT} tokens written to ${TOKENS}"

echo ""
echo "── 3/3: Running k6 real_exam_jwt scenario ──────────────"
k6 run \
  -e TARGET="${TARGET}" \
  -e TOKEN_FILE="${TOKENS}" \
  -e VUS="${VUS}" \
  -e EXAM_SECONDS="${EXAM_SECONDS}" \
  "${HERE}/real_exam_jwt.js"
