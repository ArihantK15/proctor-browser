#!/usr/bin/env bash
# Builds and runs the vision pipeline smoke test inside a container capped
# at 6GB RAM and 2 CPUs — simulating a budget laptop's resource envelope.
# NOTE: this Mac may be Apple Silicon (ARM); a real budget Windows laptop
# is almost certainly x86 with different single-core characteristics. This
# is a strong pre-check for graceful degradation, NOT a substitute for
# real-hardware validation before using "runs on a ₹30,000 laptop" as a
# customer-facing claim.
set -euo pipefail
cd "$(dirname "$0")/../.."
docker build -f scripts/constrained-hardware-test/Dockerfile -t proctor-constrained-test .
docker run --rm --memory=6g --cpus=2 proctor-constrained-test
