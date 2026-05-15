#!/usr/bin/env bash
set -euo pipefail

INTERVAL="${INTERVAL:-60}"
MODE="${MODE:-fast}"
RUN_LLM_ON_PASS="${RUN_LLM_ON_PASS:-0}"
OUT_DIR="${OUT_DIR:-logs/code-quality}"
mkdir -p "$OUT_DIR"

fingerprint() {
  {
    git status --short
    git diff --stat
    git diff --cached --stat
  } | shasum -a 256 | awk '{print $1}'
}

last=""
echo "Watching for local changes every ${INTERVAL}s."
echo "MODE=$MODE RUN_LLM_ON_PASS=$RUN_LLM_ON_PASS OUT_DIR=$OUT_DIR"

while true; do
  current="$(fingerprint)"
  if [ "$current" != "$last" ]; then
    last="$current"
    ts="$(date '+%Y-%m-%d %H:%M:%S')"
    echo "[$ts] Change detected. Running quality gate..."
    if OUT_DIR="$OUT_DIR" MODE="$MODE" scripts/quality_check.sh; then
      echo "[$ts] Quality gate passed."
      if [ "$RUN_LLM_ON_PASS" = "1" ]; then
        echo "[$ts] Running local LLM review..."
        OUT_DIR="$OUT_DIR" scripts/llm_review.sh || true
      fi
    else
      echo "[$ts] Quality gate failed. See $OUT_DIR."
    fi
  fi
  sleep "$INTERVAL"
done
