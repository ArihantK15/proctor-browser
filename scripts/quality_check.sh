#!/usr/bin/env bash
set -euo pipefail

OUT_DIR="${OUT_DIR:-logs/code-quality}"
MODE="${MODE:-full}"
RUN_LLM="${RUN_LLM:-0}"
mkdir -p "$OUT_DIR"

echo "Quality mode: $MODE"
echo "Logs: $OUT_DIR"

python3 -m compileall -q app tests worker.py | tee "$OUT_DIR/python-compile.log"

case "$MODE" in
  fast)
    pytest tests/test_privacy_appeals.py \
      tests/test_admin_scorecards_coverage.py::TestExportPDF \
      tests/test_admin_scorecards_coverage.py::TestScorecardPDF \
      tests/test_endpoints_coverage.py::TestAnalyzeFrame::test_practice_sandbox_returns_ok \
      -q --tb=short | tee "$OUT_DIR/pytest.log"
    ;;
  full)
    pytest tests/ \
      --ignore=tests/browser \
      --ignore=tests/test_proctor_e2e.py \
      --ignore=tests/test_proctor_features.py \
      -q --tb=short | tee "$OUT_DIR/pytest.log"
    ;;
  *)
    echo "Unknown MODE='$MODE'. Use MODE=fast or MODE=full." >&2
    exit 2
    ;;
esac

git diff --check | tee "$OUT_DIR/git-diff-check.log"
npm audit --audit-level=low | tee "$OUT_DIR/npm-audit-root.log"

(cd app/dashboard-ui && npm audit --audit-level=low && npm run build) | tee "$OUT_DIR/dashboard.log"

if [ "$MODE" = "full" ]; then
  (cd website && npm audit --audit-level=low && npm run build) | tee "$OUT_DIR/website.log"
else
  echo "Skipped website build in MODE=fast" | tee "$OUT_DIR/website.log"
fi

docker compose config --quiet | tee "$OUT_DIR/docker-compose.log"
git diff --stat > "$OUT_DIR/git-diff-stat.log"
git diff > "$OUT_DIR/git-diff.patch"

if [ "$RUN_LLM" = "1" ]; then
  scripts/llm_review.sh
fi

echo "Quality logs written to $OUT_DIR"
