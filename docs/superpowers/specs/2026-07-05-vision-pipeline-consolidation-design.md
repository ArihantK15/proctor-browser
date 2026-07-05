# Vision pipeline consolidation: SCRFD + 2d106det (design)

## Context

`proctor.py` runs these per-frame CPU-only ONNX models today (post the InsightFace
double-detection fix earlier this session):

1. RetinaFace (`uniface`) — face detection + 5-point landmarks
2. GazeEstimator (ResNet18) — dedicated yaw/pitch regressor — **kept unchanged, out of scope**
3. Eyes — OpenCV Haar cascade (`haarcascade_eye.xml`) — not a neural net, effectively free
4. InsightFace recognition (`w600k_mbf.onnx`) — identity embedding, now fed RetinaFace's
   landmarks directly instead of running its own internal SCRFD pass
5. YOLO26n — multi-class cheat-object detector (earphone/headphone/phone/watch/
   calculator/laptop/monitor/tablet/book), trained this session on Lightning AI,
   replacing the dormant EarClassifier

A real historical incident (the "v2.3.50 Windows misfire": a degenerate `solvePnP`
head-pose calibration baseline of +53° caused every forward glance to misfire as
"off-screen EXTREME") demonstrates that RetinaFace's 5-point landmarks are a fragile
basis for head-pose. Separately, the Haar cascade eyes-closed detector is a
well-known weak point (angle/occlusion blind spots) versus modern landmark-based
approaches.

Arihant asked to consolidate RetinaFace + the eyes model into something better,
having previously found MediaPipe unreliable on macOS. Priority: **accuracy first,
CPU cost second** (both matter, accuracy matters more).

## Decision: SCRFD + 2d106det (InsightFace model zoo)

Rejected alternatives:
- **MediaPipe (even via a community ONNX export, bypassing its own runtime)** —
  unproven third-party conversion provenance, requires reimplementing MediaPipe's
  anchor-decoding/NMS ourselves, and is the exact tool family that already burned
  this project once. Not worth the risk given a cleaner option exists.
- **PFLD** — legitimate and CPU-fast, but a community research implementation
  (not an officially maintained SDK) and only replaces landmarks, not detection —
  doesn't actually consolidate anything (still two separate models).

Chosen: swap `uniface` RetinaFace for InsightFace's own **SCRFD** detector
(published to beat a comparably-sized RetinaFace variant by ~21% hard-AP while
using *less* compute), paired with **2d106det** (106-point landmarks, official
InsightFace model). Both load via `insightface.model_zoo.get_model()` — the exact
same package + pattern already verified working on this machine today for the
recognition-model fix.

This is not a CPU reduction — 2d106det is a genuinely new inference pass that
doesn't exist in the pipeline today (RetinaFace's 5 landmarks come free as part of
its single detection pass). SCRFD replacing RetinaFace is a net efficiency win per
published benchmarks, but adding 2d106det on top likely offsets it. Net effect:
roughly a wash, possibly a small increase — not claimed as a performance win, only
an accuracy one.

## Architecture / data flow

1. `detect_faces(frame)` — same external contract as today `(bbox, lm_2d)` per
   face — now backed by SCRFD internally. Runs every frame (bbox freshness
   matters for face-missing/multi-face detection), same as RetinaFace does today.
2. On the `_head_n` cadence (existing `GAZE_EVERY_N`-derived value, already
   doubling once `governor.effective_fps < TARGET_FPS`) — run `2d106det` on the
   current face crop → 106 landmarks. Reused for:
   - `solvePnP` head-pose (replacing the 5-point version — this is the direct fix
     for the fragility behind the v2.3.50 incident)
   - Eyes-closed via Eye Aspect Ratio (EAR) geometry from eye-region landmarks
     (replacing the Haar cascade)
3. GazeEstimator, InsightFace recognition, YOLO: unchanged, same cadences.

Reusing `_head_n` rather than inventing a new cadence constant is deliberate:
2d106det is directly replacing/feeding head-pose, so it inherits the exact
cadence (and governor-aware throttle doubling) that computation already runs on.
Blink/eyes-closed doesn't need per-frame freshness — the actual cheating signal
is *sustained* closure (`EYES_CLOSED_FRAMES` accumulation), not catching
individual blinks, so this cadence is more than sufficient.

## Error handling

Matches the existing `XXX_AVAILABLE` fail-open pattern used throughout `proctor.py`:
- `2d106det` unavailable → fall back to SCRFD's own 5-point landmarks for
  head-pose (same tier as pre-migration, not a crash).
- EAR geometry unavailable → fall back to the existing Haar cascade code (kept,
  not deleted — same "dormant, not removed" treatment as EarClassifier).
- External telemetry field names in the Python selftest's `models: {...}` report
  (`retina`, `eyes`) stay unchanged even though the internals swap, so nothing
  downstream (scorecards, system-check UI, `_buildSystemCheckResult`) breaks.

## Testing plan

1. Full existing test suite (73+ tests) must stay green throughout.
2. New unit tests: EAR-based eyes-closed classification against known synthetic
   landmark coordinates at known-open/known-closed ratios; `solvePnP` sanity
   check comparing 106-point vs 5-point output on a synthetic frontal face
   (values should stay within existing plausibility bounds, `HEAD_BIAS_MAX_YAW`/
   `HEAD_BIAS_MAX_PITCH`).
3. **Docker-based constrained-hardware harness** — `docker run --memory=6g
   --cpus=2` with `PROCTOR_ORT_THREADS=2`, actually exercising the real pipeline
   (not synthetic-only) to confirm no OOM/crash and reasonable per-frame latency
   under real memory+CPU pressure, and that the governor's tier-stepping behaves
   sanely under simulated constraint.
4. **Explicit caveat, not a limitation to paper over:** this Mac is Apple
   Silicon (ARM); a ₹30,000-class Lenovo is almost certainly an x86 Intel/AMD
   budget chip with different single-core characteristics. The Docker harness is
   a strong pre-check for graceful degradation under constrained RAM/threads, but
   is **not a substitute for real-hardware validation** before "runs on a
   ₹30,000 Lenovo" is used as a customer-facing claim.

## Naming note

`EAR_EVERY_N` already exists in this codebase and means "EarClassifier cadence"
(the dormant earbud classifier being retired), not "Eye Aspect Ratio." To avoid
a confusing collision, the new eyes-closed geometry code must NOT abbreviate as
"EAR" — use a distinct name (e.g. `EYE_OPEN_RATIO` / `_eye_openness`) until the
dormant `EAR_EVERY_N`/EarClassifier code is actually deleted in a later cleanup.

## Out of scope

- GazeEstimator — explicitly kept as-is.
- YOLO/EarClassifier retirement — already done this session, unrelated to this
  migration.
- Any change to `_HardwareGovernor`'s core CPU/thermal/battery logic — it's
  already well-engineered (multi-signal, hysteresis, hard floors); this design
  only wires a new model into its *existing* cadence-scaling pattern.
