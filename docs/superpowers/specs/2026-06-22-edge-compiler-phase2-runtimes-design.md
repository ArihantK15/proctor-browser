# Edge Compiler Phase-2 runtimes — TypeScript + Python (design)

Date: 2026-06-22
Branch: `feat/edge-phase2` (off `main` @ 2.3.52)

## Goal
Add **TypeScript** and **Python** to the kiosk coding runtime. Expand the
execution layer only — the grading contract, server endpoints, and (now) the UI
are unchanged.

## Unchanged surface
- **Grading:** Approach A. `/api/v1/coding/testcases` + `/coding/judge` compare
  stdout strings; language-agnostic. No server change.
- **UI:** `_langsFor` honors `options.allowed_languages`, so a question seeded
  with `["javascript","typescript","python"]` offers all three with no renderer
  change.
- **Contract key:** `coding_test_cases.question_id = str(questions.question_id)`
  (the integer label, not the PK) — unchanged from Phase 1.

## TypeScript — DONE (commit 50bf3128)
- Transpiler **sucrase** (pure JS, no WASM → loads under `script-src 'self'`
  with no CSP relaxation). Type-stripping only; no bundling/minify needed.
- Built flat: `tools/sucrase-build/ → renderer/sucrase.bundle.js` (mirrors
  `codemirror-build`; `node_modules` gitignored; bundle committed).
- **Main-thread transpile:** `coding-runtime.js` calls `window.transformTS`
  once, then runs the resulting JS through the **existing JS worker path**.
  `coding-worker.js` stays JS-only and untouched. Compile error → every test
  fails with the compiler message.
- CM editor already highlights TS (`javascript({typescript:true})`).
- Verified in node (transpile + `new Function` exec). **Still needs real-kiosk
  e2e** (worker spawn under `procta-lobby://` CSP) before relying on it.

## Python — Pyodide (WASM in worker), design only

### Execution model
Separate worker `coding-worker-py.js`. Pyodide init is ~1–2s, so **one warm
Pyodide worker per question, reused across test cases** (reset interpreter
globals + restore `sys.stdout` between runs) — NOT fresh-per-test like JS/TS.
Terminate on leaving the question or on watchdog timeout. So `coding-runtime.js`
grows a **per-language worker strategy** (JS/TS: fresh-per-test; Python: warm).

### Load-then-lockdown ordering (sandbox redesign for this worker)
The JS worker nulls `fetch`/`importScripts` at the top, but Pyodide needs them to
load. Order: (1) load Pyodide from the fixed local cached path (egress open, but
no untrusted code has run yet); (2) null every egress vector
(`fetch`/`WebSocket`/`XHR`/`importScripts`/`sendBeacon`/nested workers); (3) only
then accept + run student code. Python network (`urllib`, `pyodide.http`) dies
with the JS `fetch` it bridges to.

### Hosting — decouple + cache (NOT bundled)
Provision Pyodide on first run via the existing `startSetupInBackground` flow
(gated before exam start, like the AI runtime) from a host origin
(own server / S3 Mumbai — see data-residency note), cached to the app `userData`
dir; offline after. Keeps the installer/updates lean (the Windows-update-bloat
concern). **Packaging wrinkle:** Pyodide is a multi-file dir
(`pyodide.asm.js` + `.wasm` + `python_stdlib.zip`) but `procta-lobby://` clamps
to a single path component — so load from the **cached on-disk directory**
(`loadPyodide({indexURL: <cacheDir>})`) via a small dedicated handler, not the
flat protocol.

### stdin/stdout bridge
Redirect Pyodide `sys.stdout`/`sys.stderr` into the `out[]` buffer; feed stdin
via `input()`/`sys.stdin` from the test input. `print(...)` → captured lines,
identical to the JS `console.log` capture. Same `{stdout,stderr,time_ms,mem_kb}`
postMessage shape.

### Editor highlighting
Add `@codemirror/lang-python` to `tools/codemirror-build/entry.mjs` and rebuild
`renderer/codemirror.bundle.js` (tree-shaken per enabled lang).

## Risks & gating spikes
1. **GATING — Pyodide WASM under the kiosk CSP.** The same-origin worker has no
   CSP of its own (why `new Function` works), so WASM *should* compile without
   `wasm-unsafe-eval`. **Must be proven in the real kiosk under
   `procta-lobby://`, not a harness** (the blob-worker trap). If it fails:
   either add `wasm-unsafe-eval` to the kiosk `script-src` (CSP relaxation →
   security review) or fall back to `child_process`. No Python ships until the
   spike passes.
2. **Perf, concurrent with proctoring.** Proctoring fps is governed
   (`TARGET_FPS=15`, `HardwareGovernor` throttles to effective_fps — ~7 on weak
   laptops; it is NOT a fixed dial). Code-exec CPU is **bursty** (idle while
   typing, ~2s spikes on Run/Submit). Optimization: keep proctoring high during
   typing, let the governor dip only during execution bursts, and add a
   **per-exam-type fps floor** so coding exams never drop below the cheating
   threshold. Needs a measured number on a target lab laptop.
   - Related latent bug (separate task): behavioral matchers in
     `behavioral_analysis.py` hardcode `fps=15.0` for duration math, miscomputed
     on throttled hardware → thread effective_fps in.
3. **First-run timing/size budget** — Pyodide fetch must finish before exam
   start (the setup-ready gate enforces this); fix the host origin + size.

## Sequencing
TS (done) → **Pyodide CSP spike (gate)** → Python worker + provisioning +
CM python highlight → perf validation + per-exam-type fps floor.
