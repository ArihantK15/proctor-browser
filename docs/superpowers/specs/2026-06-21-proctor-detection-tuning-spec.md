# Proctor detection-tuning spec (standby-agent handoff)

**Context:** Windows mock-exam run surfaced detection issues. The proctor itself
is healthy — a standalone run (`proctor.py` with no server) monitored perfectly
(earbuds, multi-voice, gaze, head, eyes all fired). These are tuning/quality
fixes, NOT "detection is broken." Do them in `proctor.py` (+ one dashboard UI
surface for voice). **Do not touch** `_open_camera_retry`, the spawn env, or any
rate limiter — those are already fixed on `main` (PRs #143, #145). Work on a
branch off latest `main`; rebase if it moves.

Verify every anchor with grep before editing — line numbers drift. Run
`python3 -c "import ast; ast.parse(open('proctor.py').read())"` after each task.

---

## Task 1 — Head-pose bias sanity (HIGHEST priority: stops false accusations)

**Problem:** the renderer dot-calibration handed the proctor
`head_yaw_bias = -34.85°`, wildly inconsistent with the gaze bias (`-0.17 rad ≈
-10°`). Head yaw is corrected as `corrected = raw - bias`, so a garbage `-35°`
bias amplifies any rightward turn into a physically-impossible reading (the run
logged `head_turned … yaw:+122°` — humans top out ~70°). Result: false
`head_turned` / `gaze_away` EXTREME violations against honest students.

**Anchors:** `_INITIAL_HEAD_YAW_BIAS` (~`proctor.py:844`, reads
`PROCTOR_HEAD_YAW_BIAS`); head-pose correction in the main loop (grep
`head_yaw` for the `raw - bias` site); the self-calibration path (collects ~45
frames on the proctor's own feed when no preset bias is present).

**Changes:**
1. On startup, validate the incoming preset bias. If `abs(head_yaw_bias) > 20`
   (or `abs(head_pitch_bias) > 25`), **reject it** and fall back to
   self-calibration (the same path used when no preset bias is supplied). Log
   one clear line: `[CAL] preset head bias rejected (yaw=…°) — self-calibrating`.
2. Add a sanity clamp at the correction site: clamp `corrected_head_yaw` to
   `[-70, 70]` and `corrected_head_pitch` to `[-60, 60]` before the threshold
   comparison, so a single bad frame can't manufacture an EXTREME.

**Acceptance:** with `PROCTOR_HEAD_YAW_BIAS=-34.85`, the proctor logs the
rejection and self-calibrates; no `head_turned` fires while facing forward;
corrected yaw never exceeds 70°.

---

## Task 2 — Dynamic YOLO cadence under throttle (phone recall)

**Problem:** at 98% CPU the governor dropped the loop to 0.5 fps; with
`YOLO_EVERY_N=5`, YOLO inferred ~once per 10s, so a briefly-held phone never
landed on a YOLO frame. (NOTE: SAHI is **off by default** —
`PROCTOR_ENABLE_SAHI` / `_sahi_available` — it was NOT running, so don't bother
"gating SAHI"; it's already off. The lever is the main YOLO cadence.)

**Anchors:** `YOLO_EVERY_N` (~line 634); the governor throttle/recover events
(grep `hardware governor` / `effective.*fps`); the main-loop frame counter that
decides when to submit a frame to the YOLO worker queue.

**Change:** make the effective YOLO interval adapt to the governor. When the
governor is in a throttled state (effective fps below ~5), submit **every**
frame to YOLO (`effective_yolo_every_n = 1`); when recovered, restore the
configured `YOLO_EVERY_N`. Keep the `Queue(maxsize=2)` — at low fps it won't
overflow. Do NOT raise cadence when not throttled (no extra CPU at 15 fps).

**Acceptance:** during a simulated/real throttle, a phone held ~3s produces a
`cheat_object_detected` (class 67). No measurable CPU increase when un-throttled.

---

## Task 3 — Expand cheat-object classes

**Anchor:** the COCO-id→label cheat-object map (grep `67: "Phone"`,
currently also has `65` remote).

**Change:** add COCO ids: `62` TV/monitor, `63` laptop, `67` cell phone (confirm
present), `65` remote (present). For `73` book: add it **gated behind a config
flag** (e.g. `PROCTOR_FLAG_BOOKS` / an exam-level open-book setting) — default
OFF, because books false-positive on legitimate paper and break open-book exams.
Use the existing `YOLO_CONFIDENCE` gate; do not lower it globally.

**Acceptance:** laptop/TV/phone/remote in view → `cheat_object_detected` with the
right label; a book does nothing unless the flag is on.

---

## Task 4 — Voice: solo signal + UI requirements surface

**Problem:** "voice didn't fire" was mostly expectation. `multiple_voices` needs
2 speakers (solo won't trip), `keyword_uttered` needs flagged keywords (the exam
had **+0 custom**), `sustained_voice` needs >8s continuous over RMS 0.035. None
of these match a teacher saying "answer / C / B" briefly while solo.

**Anchors (proctor):** `sustained_voice` logic (~`proctor.py:2746-2759`,
`_sustained_voice_start`), `VOICE_THRESHOLD` (line 998), `VOICE_SUSTAINED_SECS`.

**Changes:**
1. **proctor:** keep `sustained_voice` as the robust **solo** talking signal.
   Consider lowering `VOICE_SUSTAINED_SECS` default from 8s to ~5s (confirm the
   constant name/value first) so a student reading answers aloud trips it
   sooner. Don't change `multiple_voices`/`keyword` logic.
2. **dashboard UI (`app/static/dashboard.html` + `dashboard-app.js`,
   HTML-only — see CLAUDE.md):** on the exam/proctoring settings surface, show a
   short helper explaining what each voice signal requires: *multiple voices =
   2+ speakers; keyword alerts = only fire for keywords you add below (none
   configured = off); sustained talking = Ns continuous.* Make the
   custom-keyword field discoverable so teachers know to populate it.

**Acceptance:** ~5s of solo speech → `sustained_voice`; the settings UI states
the requirements; CSP-safe (external JS + `addEventListener`, no inline script).

---

## Task 5 — Earbud heuristic: stop false positives (interim, until the model ships)

**Problem:** `weights/earbud_classifier.onnx` doesn't exist, so `EarClassifier`
falls back to `_heuristic_detect` (Canny edge-density + dark-ratio), which fires
`right_earbud` on any textured/dark patch beside the face — phantom earbuds.
A real model is being produced in a **separate, out-of-repo** training project
(training scripts + datasets are kept out of this product on purpose); when the
resulting `.onnx` is dropped in `weights/` the classifier auto-loads and this
heuristic is bypassed entirely — **don't remove the heuristic path**, just make
it conservative for the no-model case.

**Anchors:** `EarClassifier._heuristic_detect` (~`proctor.py:744`), the earbud
threshold/frame-count gate in `_process_ear_detection` (~`proctor.py:2686`).

**Change (pick the lighter-touch that the team prefers — flag it in the PR):**
- **Tighten:** narrow the heuristic accept band (e.g. require `edge_density`
  AND `dark_ratio` in a tighter range) and raise the per-side confidence floor,
  AND require more sustained frames before `left/right_earbud` logs (raise the
  frame-count threshold) so a momentary patch can't trip it; **or**
- **Disable:** have `_heuristic_detect` return `(0.0, 0.0)` so earbud detection
  is simply OFF until the real model exists — no false accusations (preferred if
  the model is landing soon; a false earbud flag is worse than a missed one).

Make it a one-line switch (env `PROCTOR_EARBUD_HEURISTIC=0/1`, default off) so
it's trivially reversible.

**Acceptance:** with no model present and the heuristic disabled/tightened, a
plain face (no earbuds) under normal lighting produces **no** `*_earbud` events.

## Cross-cutting gotchas
- Any new column/config field needs BOTH `schema/columns.json` and
  `integration_tests/schema.sql` (schema-ref guard + integration test).
- Object-class / voice changes: add/adjust unit tests; the suite is
  shuffle-clean (pytest-randomly) — don't introduce order-dependent state.
- No emoji-only diagnostics that could crash cp1252 (PYTHONUTF8 covers it, but
  keep new prints ASCII-safe to be defensive).
- Detection-quality changes can't be unit-verified for true accuracy — call out
  what needs on-device testing in the PR description.
