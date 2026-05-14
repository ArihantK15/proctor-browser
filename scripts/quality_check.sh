#!/usr/bin/env bash
set -euo pipefail

OUT_DIR="${OUT_DIR:-logs/code-quality}"
mkdir -p "$OUT_DIR"

python3 -m compileall -q app tests | tee "$OUT_DIR/python-compile.log"
pytest tests/ --ignore=tests/browser --ignore=tests/test_proctor_e2e.py --ignore=tests/test_proctor_features.py -q --tb=short | tee "$OUT_DIR/pytest.log"
npm audit --audit-level=low | tee "$OUT_DIR/npm-audit-root.log"

(cd app/dashboard-ui && npm audit --audit-level=low && npm run build) | tee "$OUT_DIR/dashboard.log"
(cd website && npm audit --audit-level=low && npm run build) | tee "$OUT_DIR/website.log"

docker compose config --quiet | tee "$OUT_DIR/docker-compose.log"
git diff --stat > "$OUT_DIR/git-diff-stat.log"
git diff > "$OUT_DIR/git-diff.patch"

echo "Quality logs written to $OUT_DIR"
