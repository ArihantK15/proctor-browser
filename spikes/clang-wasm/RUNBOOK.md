# Spike P0-T1 — Clang-WASM C++ under concurrent proctoring (go/no-go)

**Question this spike answers (and ONLY this):** can the Electron kiosk compile + run
a medium C++ DS&A program in a Web Worker **while `proctor.py` is live** (webcam + YOLO
+ gaze) on a *representative low-end laptop*, **without** starving either side or OOMing?

This is the real exam-day condition. Cold-start size in isolation is **not** the gate —
the spec's whole risk is the *concurrent* load (same wall the proctor governor hit).

> **Status: harness built, NOT YET MEASURED.** The numbers below require running on
> target hardware. Everything here makes that a ~20-minute exercise. Fill the Results
> table and commit it; that table is the go/no-go artifact.

---

## The gate (all three must hold, measured TOGETHER)

| Gate | Pass threshold |
|------|----------------|
| **G1 — proctor fps holds** | `proctor.py` effective fps stays ≥ its throttled floor (**3 fps**) during the compile+run, i.e. the governor doesn't collapse and no `proctor_failed`/`camera_failed` event fires. |
| **G2 — compile+run completes in budget** | A medium DS&A C++ program **compiles and runs all sample inputs in < 8 s wall-clock** on the low-end box (tune per problem; 8 s is the working budget). |
| **G3 — no OOM** | Peak process RSS (Electron renderer + proctor.py together) stays under the box's RAM with headroom; **no tab/worker crash, no OS swap-thrash**. |

**Any gate fails → C++ is NOT greenlit as-is.** Documented fallbacks (try in order):
smaller Clang sysroot / `-O0` only / single-threaded lld / defer C++ to a "needs a
mid-tier machine" tier / drop C++ from v1.

---

## Target hardware (use the WEAKEST machine a customer plausibly uses)

- 4 cores (or 2c/4t), **4 GB RAM**, integrated GPU, HDD or slow SSD, Windows.
- This is the make-or-break box. Measuring on a dev laptop proves nothing — it must be
  a weak machine, because the proctor governor only throttles hard on weak machines.

## Toolchain under test

**Primary candidate: [binji/wasm-clang](https://github.com/binji/wasm-clang)** — the
canonical in-browser clang that compiles C++ → wasm → runs it. Vendor its assets
(`clang.wasm`, `lld.wasm`, the sysroot `.tar`, `api.js` / `shared.js`) into
`spikes/clang-wasm/vendor/`. Expected payload: **tens of MB** (record the exact number —
it feeds the P0-T3 installer-size budget).

**Lighter comparison (optional): [JSCPP](https://github.com/felixhao28/JSCPP)** — a C++
*interpreter*, ~1 MB, no real compilation. Run the same workload through it to see if a
much smaller runtime clears the gate when full Clang doesn't. (Tradeoff: weaker language
coverage; not a drop-in for real `<algorithm>`-heavy code.)

The worker adapter (`runtime-worker.js`) abstracts `loadToolchain / compile / run` so
swapping wasm-clang ↔ JSCPP is a one-file change.

---

## How to run

### 1. Vendor the toolchain
Drop the chosen toolchain's assets into `spikes/clang-wasm/vendor/` and wire the three
adapter functions at the top of `runtime-worker.js` (marked `ADAPTER:`).

### 2. Establish the concurrent load (pick A — B is the fallback)
- **A (the real gate):** launch the actual kiosk proctor on the target box — start an
  exam/calibration so `proctor.py` is running the webcam + models — then open the
  harness in the SAME Electron renderer (or a Chromium tab on the same machine). This is
  the true condition.
- **B (fallback if you can't stand up a full exam):** run `node cpu-load.js --cores=N`
  to simulate proctor CPU contention (N = cores the proctor typically pins on a weak
  box, usually 1–2 after governor throttle). Less faithful (no camera/GPU/RAM pressure)
  — note it in the results.

### 3. Run the harness
Open `harness.html` (Electron renderer or `python3 -m http.server` + Chrome). It:
- loads the toolchain (records cold-start payload size + load ms),
- compiles `workload.cpp` (records compile ms),
- runs it against each line of `workload.stdin` with a per-run wall-clock watchdog
  (records run ms per case + pass/fail vs `workload.expected`),
- samples `performance.memory` (Chrome) for peak JS heap,
- prints a results table to the page + console.

### 4. Read proctor fps concurrently
Watch the proctor's `[Gaze Debug]`/governor log (it prints effective fps). Record the
**minimum** fps observed during the compile+run window. That's G1.

### 5. Fill the Results table below, commit, ping Arihant.

---

## Results (FILL ON TARGET HARDWARE)

| Field | Value |
|-------|-------|
| Date / tester | |
| Box (CPU / RAM / disk / OS) | |
| Toolchain + version | |
| Concurrent-load method (A real proctor / B simulator) | |
| Cold-start payload size (MB) | |
| Toolchain load time (ms) | |
| Compile time — workload.cpp (ms) | |
| Run time per case (ms, min/median/max) | |
| **Total compile+run wall-clock (s)** → **G2** | |
| **Min proctor fps during window** → **G1** | |
| **Peak combined RSS (MB) / RAM / swap?** → **G3** | |
| **VERDICT: GO / NO-GO** | |
| If NO-GO: which gate failed + fallback chosen | |

---

## Notes
- Repeat the run **3×** and take the worst case — exam day has no warm cache guarantees
  and thermal throttling kicks in on the 2nd/3rd run on weak boxes.
- Also note **first-run vs warm-cache** compile time (IndexedDB/bundle warm). The kiosk
  bundles the runtime (spec Performance §), so warm is the realistic steady state, but
  first-question latency is the warm-up the student feels.
- If wasm-clang OOMs at load on 4 GB, that alone is a NO-GO for full Clang on that tier —
  record it and move to the JSCPP comparison or the mid-tier-machine fallback.
