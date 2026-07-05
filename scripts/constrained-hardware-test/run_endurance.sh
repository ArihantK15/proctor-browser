#!/usr/bin/env bash
# 45-minute full-pipeline endurance test at a TIGHTER limit than the earlier
# smoke test — --memory=4g, deliberately BELOW the ~8GB industry-minimum
# target laptop RAM, to prove real headroom rather than just scraping by.
# Exercises face detect, landmarks, eye-openness, head-pose, gaze, YOLO
# object detection, and InsightFace identity together, paced at the real
# governor-controlled cadence — not just the face-detection slice.
#
# NOTE: this Mac may be Apple Silicon (ARM); a real budget Windows laptop
# is almost certainly x86 with different single-core characteristics. This
# is a strong pre-check for graceful degradation over a sustained run, NOT
# a substitute for real-hardware validation.
set -euo pipefail
cd "$(dirname "$0")/../.."
docker build -f scripts/constrained-hardware-test/Dockerfile -t proctor-constrained-test .
docker run --rm --memory=4g --cpus=2 \
  -e ENDURANCE_DURATION_SEC="${ENDURANCE_DURATION_SEC:-2700}" \
  proctor-constrained-test python3 endurance_test.py
