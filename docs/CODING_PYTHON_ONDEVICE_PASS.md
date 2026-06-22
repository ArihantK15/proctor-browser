# Edge Compiler — Python on-device verification pass (Electron)

Branch: **`feat/coding-phase2-python`** (all P2 tasks integrated). Run this on
the Windows kiosk box (or a macOS dev machine if that is the target platform).
Two stages — **Stage A is the gating one** (~10 min, settles Pyodide + WASM +
CSP under `procta-lobby://`, risks that cannot be confirmed off-device). Stage B
is the full Run → Submit → score flow and needs a backend with the coding
endpoints.

> **Owner prerequisite — Pyodide host + manifest hashes.** The S3 Mumbai
> bucket URL and per-file sha256s inside `lib/pyodide-manager.js`'s `MANIFEST`
> array are `<fill>` placeholders. These **must be populated before Stage A can
> run on a packaged build.** On a dev run (`npm start`) you may also set
> `PYODIDE_BASE_URL=http://localhost:<port>` to serve Pyodide files locally. Do
> not proceed past Stage A prep until the manifest is filled.

Env overrides (from `config.js`):
- `PROCTOR_SERVER_URL=http://localhost:8000` — point the kiosk at a local backend.
- `PROCTOR_DEBUG=1` (or `--no-kiosk`) — relax kiosk lockdown so devtools is reachable.

---

## Stage A — gating CSP / WASM spike (P2-S0)

This stage does **not** need the coding backend or a seeded Python question —
`coding-worker-py.js` ships in the build. Any exam page will do.

The spike answers three risks that off-device testing cannot settle:

### Risk 1 — Cross-scheme load under CSP

The Python worker lives at `procta-lobby://exam/coding-worker-py.js` (same-origin,
so spawning it is fine). But inside the worker, `loadPy()` calls:

```js
self.importScripts('procta-pyodide://py/pyodide.js');
const py = await self.loadPyodide({ indexURL: 'procta-pyodide://py/' });
```

This is a **cross-scheme fetch/importScripts** — the worker's origin is
`procta-lobby://exam` but it is pulling files from a different scheme
`procta-pyodide://`. Under the kiosk CSP (`default-src 'self'`, where `'self'`
is `procta-lobby://exam`), a cross-scheme `importScripts` or `fetch` may be
**blocked outright**.

**The spike must confirm the worker can load Pyodide files from the cross-scheme
URL.** If it is blocked:

- Option A: add `procta-pyodide:` to the relevant CSP directives
  (`connect-src`, `script-src`, `worker-src`) — **requires security review
  before shipping**. Do NOT widen the proctored-page CSP unilaterally; escalate
  to the owner.
- Option B: serve the Pyodide cache files under the same `procta-lobby://`
  scheme (changes the Electron protocol registration in `main.js`).

### Risk 2 — WASM compile under CSP

Even if the Pyodide `.js` and `.wasm` files load successfully (Risk 1 cleared),
**WASM compilation** requires either:

- No worker-level CSP (same-origin workers in Electron inherit no CSP from the
  page — the hoped-for path that eliminates the need for `wasm-unsafe-eval`), or
- An explicit `wasm-unsafe-eval` directive in the CSP.

**The spike must confirm zero CSP violations during `loadPyodide()`.** Watch the
devtools console carefully for any "Content Security Policy" messages. If
`wasm-unsafe-eval` is required, **escalate** — do not add it unilaterally; it
needs a security review.

### Risk 3 — Pyodide-init egress window

`coding-worker-py.js` uses a load-then-lockdown strategy: egress vectors
(`fetch`, `WebSocket`, `XMLHttpRequest`, `importScripts`, etc.) are nulled out
only **after** `loadPyodide()` resolves. This means there is a ~1–2 s window
during Pyodide init where those egress vectors are live and reachable.

Student code **cannot** run in this window — lockdown completes before the first
code message is processed, because `self.onmessage` awaits `loadPy()` which
resolves the `_pyReady` promise only after lockdown fires.

**However, the UI must not dispatch the first student code message until the
worker's `_pyReady` promise resolves.** This is a **wiring requirement** for
whoever integrates the warm worker into `coding-ui.js` (a follow-up integration
task, not yet done as of P2-T7). Note it in the integration spec: the Run button
must be disabled / the worker dispatch must be gated on a `ready` acknowledgment
message from the worker before the first `postMessage({source, stdin})` is sent.

---

### A1 — Build the kiosk from `feat/coding-phase2-python`

Quick dev run (no packaging needed):
```
npm install
PROCTOR_DEBUG=1 npm start
```
Or a packaged build: `npm run build:win` (Windows) / `npm run build:mac`
(macOS), then launch the `--dir` output with devtools enabled.

### A2 — Reach any exam page

Launch an exam the normal way (can point at prod — `coding-worker-py.js` is
bundled regardless of backend). Get past calibration to the question screen.
The origin in the address bar (or `location.origin` in devtools) should be
`procta-lobby://`.

### A3 — Open devtools

Works because `PROCTOR_DEBUG=1` / `--no-kiosk` was set. `Ctrl+Shift+I` or
`F12` on Windows; `Cmd+Option+I` on macOS.

### A4 — Run the spike

Paste the entire contents of `scripts/pyodide-csp-selftest.js` into the
**Console** tab and press Enter.

**What to expect:**

| Result | Meaning |
|--------|---------|
| `✅ PASS — Pyodide loaded from procta-pyodide://, WASM compiled, Python ran, lockdown applied` + zero CSP violations | Gating spike cleared. All three risks are settled. Proceed to Stage B. |
| Worker spawns but times out (>30 s) | Cache is cold (Pyodide files not yet in userData) OR importScripts was silently blocked. Check Network tab for 404s on `procta-pyodide://`. |
| `❌ FAIL — new Worker(…) threw` or CSP violation mentioning `worker-src` | Worker blocked under `procta-lobby://`. Report — fix is `worker-src 'self'` addition to kiosk CSP, needs security review before relaxing. |
| CSP violation mentioning `procta-pyodide:` in `script-src` or `connect-src` | Risk 1 is live. See remediation options above — escalate, do not self-fix. |
| CSP violation mentioning `wasm-unsafe-eval` | Risk 2 is live. Escalate — do not add `wasm-unsafe-eval` unilaterally. |
| Worker replies but stdout is wrong | Python ran but the stdin shim or output capture is broken. Check `result.stderr` in the logged raw reply. |

If Stage A passes, the architecture is proven. Proceed to Stage B.

---

## Stage B — full Run → Submit → score for a Python question

Needs the coding backend (local), a provisioned Pyodide cache (userData), and a
seeded Python question.

### B1 — backend up with the coding schema

From a `feat/coding-phase2-python` checkout, with your dev `.env`
(`DATABASE_URL`, etc.):

1. Apply migrations so `coding_test_cases` / `coding_submissions` /
   `exam_config.coding_max_submit_attempts` exist:
   ```
   python3 scripts/run_postgres_migrations.py
   ```
2. Start the API:
   ```
   uvicorn app.main:app --port 8000
   ```

### B2 — seed a Python coding question

```
python3 scripts/seed_coding_question.py --language python
```

This requires:
- `python3` on PATH (to compute expected outputs by running the reference
  solution through `python3 -c` — same mechanism as the JS path uses `node -e`).
- Same `.env` as the backend (writes to the same DB).

The script prints **Teacher ID / Exam ID / question\_id** and a curl line. Note
the `exam_id` — you will join that exam. If the exam needs an access code or a
registered roll to enter, add one the normal way; the seed creates the
`exam_config` + question + test cases.

### B3 — launch the kiosk at the local backend

```
PROCTOR_SERVER_URL=http://localhost:8000 PROCTOR_DEBUG=1 npm start
```

Join the seeded Python exam, get to the coding question.

### B4 — verification checklist

- [ ] **Editor mounts with Python syntax highlighting** — CodeMirror lang-python
      active (P2-T1). The language selector (if shown) defaults to Python.
- [ ] **Run (sample)** shows per-case input / expected / actual diff, graded
      client-side via the warm Pyodide worker (`coding-worker-py.js`). Result is
      instant (warm) or ~1–2 s on the first run (Pyodide init).
- [ ] **Submit** runs hidden inputs, POSTs outputs to `/coding/judge`, shows
      `Passed N/total` (counts only — no per-case detail, no expected output
      leaked to the client).
- [ ] **Source autosave** — type code, reload the page mid-edit → the source
      survives (rides the existing bulk-answer autosave at `answers[q.id]`).
- [ ] **Offline submit** — kill the network, hit Submit → it queues; restore
      network → it retries and lands.
- [ ] **Submit cap** — Submit more than `coding_max_submit_attempts` (default 10)
      times → server returns 429 "Submission limit reached".
- [ ] **Score fold-in** — finish the exam; the coding marks
      (`passed/total × question_marks`) appear in the score/scorecard alongside
      MCQ.
- [ ] **Zero CSP violations** throughout (devtools Console — confirms the
      procta-lobby:// + procta-pyodide:// wiring holds end-to-end through the
      real UI, not just the selftest script).
- [ ] **Proctor fps floor held during exec bursts** — set
      `PROCTOR_CODING_FPS_FLOOR` (e.g. `5`) in the env / exam config, run a
      coding exam with proctoring live. During Run and Submit, confirm
      `effective_fps` (logged by the proctor governor, P2-T6) never drops below
      the floor. Then hold a phone in frame for 2–3 s and confirm the phone
      detection event is still captured. Record the measured minimum fps here:
      `effective_fps_min = <fill after run>`.

### B5 — sanity on the server side (optional)

After a Submit, check the DB:
```sql
SELECT question_id, test_cases_passed, test_cases_total, is_fully_solved, created_at
FROM coding_submissions
ORDER BY created_at DESC
LIMIT 5;
```
Expect: `teacher_id` stamped from the JWT (not from the POST body),
`test_cases_passed / test_cases_total` correct, `is_fully_solved` computed,
`source_code` stored.

---

## If something fails

| Symptom | Likely cause / fix |
|---------|--------------------|
| Stage A: worker won't spawn (CSP error) | `worker-src 'self'` missing from kiosk CSP. Report — do not relax CSP unilaterally. |
| Stage A: `procta-pyodide:` CSP error | Cross-scheme load blocked (Risk 1). Escalate; see remediation options in Stage A. |
| Stage A: `wasm-unsafe-eval` CSP error | WASM compile needs directive (Risk 2). Escalate. |
| Stage A: timeout, no CSP error | Cache cold (Pyodide files not in userData). Run `ensurePyodide()` flow or copy files manually. |
| `/coding/testcases` returns 400 | Client must send `session_id` query param — confirm the build is from this branch. |
| Coding score is 0 despite passes | question-id keying mismatch. Confirm seed ran from this branch (not an older copy). The seed sets `coding_test_cases.question_id = str(question_id)` (the label, not a UUID PK). |
| Editor shows JS syntax highlighting for Python question | CodeMirror lang-python integration (P2-T1) not wired to the language field in `options.allowed_languages`. |
| Proctor fps drops below floor | FPS governor (P2-T6) not applying the floor — confirm `PROCTOR_CODING_FPS_FLOOR` env var is set and the exam type resolves to `coding`. |
| Camera / calibration blocks before the question | Proctor flow issue, not coding. Separate branch. |
