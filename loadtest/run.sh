#!/usr/bin/env bash
# Procta load-test runner.
#
# Usage:
#   ./run.sh smoke           # 30s, 10 VUs against /health + /plans
#   ./run.sh exam            # 5 min, 500 VUs writing full exams
#   ./run.sh burst           # 75s, 300 VUs all hitting submit
#   ./run.sh sse             # streaming dashboard SSE connections
#
# Env vars:
#   TARGET       — backend URL  (default: https://app.procta.net)
#   VUS          — exam-test VU count override  (default: 500)
#   BURST_VUS    — burst-test VU count override (default: 300)
#   SSE_VUS      — SSE stream VU count override (default: 100)
#   ADMIN_TOKEN  — teacher/admin JWT for SSE scenario
#   DURATION_MIN — exam-test sustained duration in minutes (default: 3)

set -euo pipefail

cd "$(dirname "$0")"

SCENARIO="${1:-smoke}"

# Resolve k6 binary — friendlier error than `command not found`
if ! command -v k6 &> /dev/null; then
  echo "❌ k6 is not installed."
  echo
  echo "Install it:"
  echo "  macOS:   brew install k6"
  echo "  Ubuntu:  see loadtest/README.md"
  echo "  Windows: choco install k6"
  exit 1
fi

# Friendly target echo so you don't accidentally hammer production
TARGET="${TARGET:-https://app.procta.net}"
echo "──────────────────────────────────────────────"
echo " Procta load test: $SCENARIO"
echo " Target: $TARGET"
if [[ "$TARGET" == *procta.net* ]]; then
  echo " ⚠️  Hitting production. Practice-mode session IDs are used,"
  echo "    so no real exam data is touched — but the API still"
  echo "    serves these requests. Run during off-hours if possible."
fi
echo "──────────────────────────────────────────────"

case "$SCENARIO" in
  smoke)
    k6 run --env TARGET="$TARGET" smoke.js
    ;;
  exam)
    k6 run \
      --env TARGET="$TARGET" \
      --env VUS="${VUS:-500}" \
      --env DURATION_MIN="${DURATION_MIN:-3}" \
      exam_flow.js
    ;;
  burst)
    k6 run \
      --env TARGET="$TARGET" \
      --env BURST_VUS="${BURST_VUS:-300}" \
      submit_burst.js
    ;;
  sse)
    if [[ -z "${ADMIN_TOKEN:-}" ]]; then
      echo "❌ ADMIN_TOKEN is required for the SSE scenario."
      echo "   Example: ADMIN_TOKEN=... TARGET=https://staging.example.com ./run.sh sse"
      exit 1
    fi
    k6 run \
      --env TARGET="$TARGET" \
      --env ADMIN_TOKEN="$ADMIN_TOKEN" \
      --env SSE_VUS="${SSE_VUS:-100}" \
      --env SSE_HOLD_SECONDS="${SSE_HOLD_SECONDS:-60}" \
      sse_sessions.js
    ;;
  *)
    echo "❌ Unknown scenario: $SCENARIO"
    echo "   Use: smoke | exam | burst | sse"
    exit 1
    ;;
esac
