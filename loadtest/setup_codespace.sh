#!/usr/bin/env bash
# Run ONCE inside a fresh GitHub Codespace to make it ready for
# distributed load testing.
#
# What this installs:
#   - k6 (load tester)
#   - jq (token-file slicer, used by run_distributed.sh)
#
# Why it's a separate script: Codespace base images don't ship k6, and
# the install incantation (apt key + repo) is annoying to type from
# memory. Run this once per Codespace creation; subsequent runs are
# no-ops because apt skips already-installed packages.
#
# Usage (inside the Codespace terminal):
#   bash loadtest/setup_codespace.sh

set -euo pipefail

echo "── installing k6 + jq ──"

# k6 official apt repo (preferred over snap so we get the pinned version)
if ! command -v k6 >/dev/null; then
  sudo gpg -k >/dev/null 2>&1 || true
  sudo gpg --no-default-keyring \
    --keyring /usr/share/keyrings/k6-archive-keyring.gpg \
    --keyserver hkp://keyserver.ubuntu.com:80 \
    --recv-keys C5AD17C747E3415A3642D57D77C6C491D6AC1D69
  echo "deb [signed-by=/usr/share/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" \
    | sudo tee /etc/apt/sources.list.d/k6.list >/dev/null
  sudo apt-get update -qq
  sudo apt-get install -y k6 jq
else
  command -v jq >/dev/null || sudo apt-get install -y jq
fi

# Raise file-descriptor limit so k6 can hold >1024 concurrent connections.
# Codespace defaults are usually 1024; we want 65536 for safety.
ulimit -n 65536 || true

echo ""
echo "── versions ──"
k6 version
jq --version

echo ""
echo "✓ Codespace ready. Return to your Mac and run:"
echo "    loadtest/run_distributed.sh <teacher-email> <password> <total-vus> <exam-secs>"
