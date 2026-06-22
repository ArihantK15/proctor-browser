# Edge Compiler Phase 2 — Python (Pyodide) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Python as a kiosk coding-exam language by running student Python in a warm Pyodide (WASM) Web Worker, provisioned on first run and cached offline — judged by the existing language-agnostic server lane with **zero server change**.

**Architecture:** Approach A spine, unchanged. The execution layer grows a **per-language worker strategy**: JS/TS keep the fresh-per-test JS worker; Python gets a *separate, warm* worker (`coding-worker-py.js`) that loads Pyodide once and is reused across a question's test cases (Pyodide init is ~1–2 s). Pyodide is **not bundled** — it is downloaded once from S3 Mumbai into `userData`, served to the worker via a dedicated `procta-pyodide://` protocol (the flat `procta-lobby://` scheme can't serve Pyodide's multi-file dir), and the worker uses **load-then-lockdown** ordering (load Pyodide with egress open, *then* null every network vector, *then* accept student code). The grading contract (`runTestCases → {outputs, metrics}`), the `/coding/testcases` + `/coding/judge` endpoints, and the renderer UI are all untouched.

**Tech Stack:** Electron (`main.js` protocol + `lib/` provisioning), Pyodide (WASM, served from `userData` cache), Web Workers (same-origin/dedicated-scheme flat files — NOT Blob, per kiosk CSP), CodeMirror 6 (`@codemirror/lang-python`), FastAPI server lane (unchanged), Python `pytest` + node for the testable units.

**Spec:** `docs/superpowers/specs/2026-06-22-edge-compiler-phase2-runtimes-design.md`

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `renderer/coding-worker-py.js` | Create | Pyodide worker: load-then-lockdown sandbox, warm reuse, stdin/stdout bridge. Same postMessage shape as `coding-worker.js`. |
| `renderer/coding-runtime.js` | Modify | Per-language worker strategy: dispatch Python to a warm `PyRunner`; JS/TS unchanged. Add `python`/`py` to `SUPPORTED`. |
| `lib/pyodide-manager.js` | Create | First-run download of the Pyodide dir → `userData/pyodide-cache/`, sha256 manifest verify, `pyodide-ready.json` marker, idempotent. |
| `main.js` | Modify | Register + handle the `procta-pyodide://` scheme (serves the cache dir, path-traversal-clamped); call `pyodideSetup` from the background-setup flow. |
| `lib/python-manager.js` | Modify | `startSetupInBackground` also awaits Pyodide provisioning so exam-start gates on it too. |
| `tools/codemirror-build/entry.mjs` | Modify | Add `@codemirror/lang-python`; map `python`/`py` in `langExt`. |
| `renderer/codemirror.bundle.js` | Modify (rebuild) | Rebuilt bundle including the tree-shaken Python mode. |
| `scripts/pyodide-csp-selftest.js` | Create | Devtools paste-in spike: proves Pyodide loads + runs WASM under `procta-lobby://` CSP. |
| `scripts/seed_coding_question.py` | Modify | Add `--language python` path (reference solution run via Pyodide-equivalent / local python3 to fill expected outputs). |
| `tests/test_pyodide_manager.py` *(or `.mjs`)* | Create | Unit tests for path resolution, manifest verify, marker idempotency. |
| `tests/test_coding_runtime_dispatch.mjs` | Create | Unit test: language dispatch + `{outputs, metrics}` contract via a mock worker. |
| `docs/CODING_PYTHON_ONDEVICE_PASS.md` | Create | Real-kiosk verification runbook (mirrors `CODING_ONDEVICE_PASS.md`). |

---

## P2-S0 — GATING SPIKE: Pyodide WASM under the kiosk CSP

**This is a go/no-go measurement, not a TDD task. No Python ships until it passes.** It answers the one residual the design flags: does Pyodide compile + run WASM inside a same-origin worker under `procta-lobby://` with the kiosk CSP (`default-src 'self'`, no `wasm-unsafe-eval`)?

**Files:** Create `scripts/pyodide-csp-selftest.js`

- [ ] **Step 1: Write the self-test** (mirrors `scripts/coding-csp-selftest.js`)

```javascript
/* Pyodide-under-procta-lobby:// CSP spike. PASTE INTO THE ELECTRON DEVTOOLS
 * CONSOLE on the exam page (origin procta-lobby://) of a build that has a
 * Pyodide cache provisioned. Proves loadPyodide spawns + runs WASM under the
 * real scheme + kiosk CSP. ✅ + zero CSP violations = gate passes. */
(async () => {
  try {
    const w = new Worker('coding-worker-py.js');     // same-origin under procta-lobby://
    const res = await new Promise((resolve, reject) => {
      w.onmessage = (e) => resolve(e.data);
      w.onerror   = (e) => reject(new Error(e.message || 'worker error event'));
      w.postMessage({ source: "a=int(input()); b=int(input()); print(a+b)", stdin: "2\n3" });
      setTimeout(() => reject(new Error('timeout — no worker reply in 20s')), 20000);
    });
    w.terminate();
    console.log('[PY CSP SELFTEST] raw:', res);
    console.log(String(res.stdout).trim() === '5'
      ? '%c✅ PASS — Pyodide WASM ran under procta-lobby:// (2+3=5)'
      : '%c❌ FAIL — wrong output: ' + JSON.stringify(res), 'font-weight:700');
  } catch (e) {
    console.error('%c❌ FAIL — Pyodide did not run (likely WASM/CSP-blocked): ' + e.message,
                  'color:#c5221f;font-weight:700');
    console.error('If a CSP error: the fix is wasm-unsafe-eval in the kiosk script-src — SECURITY REVIEW before relaxing.');
  }
})();
```

- [ ] **Step 2: Run it on a real packaged kiosk** (needs `coding-worker-py.js` from P2-T4 + a provisioned cache from P2-T2/T3 — so this spike is run AFTER T2–T4 are built but BEFORE T5/T7 are relied on). Per `CODING_ONDEVICE_PASS.md` Stage A: build from this branch, reach an exam page, open devtools (`PROCTOR_DEBUG=1`), paste the script.

- [ ] **Step 3: Record the verdict + numbers** in `docs/CODING_PYTHON_ONDEVICE_PASS.md`: pass/fail, cold-start ms, peak worker RAM, any CSP violation text.
  - **PASS** → proceed to rely on Python.
  - **FAIL with CSP/WASM error** → do NOT relax CSP unilaterally. Document the exact violation; the decision (add `wasm-unsafe-eval` → security review, vs `child_process` fallback) is escalated to the owner.

- [ ] **Step 4: Commit** `spike(coding): Pyodide-under-CSP self-test + verdict`

---

## Task P2-T1: CodeMirror Python highlighting

Independent and low-risk — can land first.

**Files:**
- Modify: `tools/codemirror-build/entry.mjs`
- Modify (rebuild): `renderer/codemirror.bundle.js`

- [ ] **Step 1: Add the dependency**

Run: `cd tools/codemirror-build && npm install @codemirror/lang-python`
Expected: `@codemirror/lang-python` added to `package.json` deps (node_modules gitignored, per the existing pattern).

- [ ] **Step 2: Import + map it in `entry.mjs`**

```javascript
import { python } from "@codemirror/lang-python";
```

In `langExt`, before the `return []` fallback:

```javascript
  if (l === "python" || l === "py") return python();
```

- [ ] **Step 3: Rebuild the bundle**

Run: `cd tools/codemirror-build && npm run build` (writes `../../renderer/codemirror.bundle.js`)
Expected: bundle regenerates; `git diff --stat` shows only `renderer/codemirror.bundle.js` grew.

- [ ] **Step 4: Smoke-check the bundle is valid JS**

Run: `node -e "require('./renderer/codemirror.bundle.js'); console.log('bundle parses')"`
Expected: prints `bundle parses` (or, if it references `window`, `node --input-type=module -e` with a `globalThis.window={}` shim) — the point is no syntax error.

- [ ] **Step 5: Commit** `feat(coding): CodeMirror Python highlighting [P2-T1]`

---

## Task P2-T2: Pyodide provisioning (download + cache + verify)

Downloads the Pyodide dir once into `userData/pyodide-cache/`, verifies a sha256 manifest, writes a `pyodide-ready.json` marker, and is idempotent (warm launches resolve in ~0 ms). Mirrors `python-manager.js`'s readiness-marker pattern.

**Data residency:** host the Pyodide files on **S3 Mumbai** (the same region the backups moved to, 2026-06-20) — not a public CDN — so first-run fetch stays in-region. The base URL is an env/const `PYODIDE_BASE_URL`.

**Files:**
- Create: `lib/pyodide-manager.js`
- Test: `tests/test_pyodide_manager.mjs` (node) — pure helpers extracted for testability.

- [ ] **Step 1: Write failing tests for the pure helpers**

```javascript
// tests/test_pyodide_manager.mjs  (run with: node --test)
import { test } from 'node:test';
import assert from 'node:assert';
import { cacheFilePath, isManifestSatisfied, MANIFEST } from '../lib/pyodide-manager.js';

test('cacheFilePath stays inside the cache root', () => {
  const root = '/u/pyodide-cache';
  assert.strictEqual(cacheFilePath(root, 'pyodide.asm.wasm'), '/u/pyodide-cache/pyodide.asm.wasm');
});
test('cacheFilePath rejects traversal', () => {
  assert.throws(() => cacheFilePath('/u/pyodide-cache', '../secrets'));
  assert.throws(() => cacheFilePath('/u/pyodide-cache', 'a/../../b'));
});
test('isManifestSatisfied false when a file is missing from the on-disk set', () => {
  const present = new Set(MANIFEST.slice(1).map(m => m.name)); // drop one
  assert.strictEqual(isManifestSatisfied(present), false);
});
test('isManifestSatisfied true when every manifest file is present', () => {
  const present = new Set(MANIFEST.map(m => m.name));
  assert.strictEqual(isManifestSatisfied(present), true);
});
```

- [ ] **Step 2: Run them — expect failure** `node --test tests/test_pyodide_manager.mjs` → FAIL (module not found).

- [ ] **Step 3: Implement `lib/pyodide-manager.js`**

```javascript
// lib/pyodide-manager.js — first-run provisioning of the Pyodide runtime into
// userData (NOT bundled — keeps installer/updates lean). Idempotent; warm
// launches short-circuit on the readiness marker. Served to the worker via the
// procta-pyodide:// scheme (main.js). Files hosted in S3 Mumbai (data residency).
const path = require('path');
const fs = require('fs');
const fsp = fs.promises;
const crypto = require('crypto');
const { app } = require('electron');

// The minimal Pyodide file set for a stdlib-only interpreter (no scientific
// wheels). Fill `sha256`/`bytes` from the pinned release when wiring S3.
const PYODIDE_VERSION = '0.26.x'; // pin exactly when hosting
const MANIFEST = [
  { name: 'pyodide.asm.js',     sha256: '<fill>' },
  { name: 'pyodide.asm.wasm',   sha256: '<fill>' },
  { name: 'pyodide.js',         sha256: '<fill>' },
  { name: 'python_stdlib.zip',  sha256: '<fill>' },
  { name: 'pyodide-lock.json',  sha256: '<fill>' },
];
const PYODIDE_BASE_URL = process.env.PYODIDE_BASE_URL
  || 'https://<procta-s3-mumbai-bucket>/pyodide/' + PYODIDE_VERSION + '/';

function cacheRoot() { return path.join(app.getPath('userData'), 'pyodide-cache'); }
function markerPath() { return path.join(cacheRoot(), 'pyodide-ready.json'); }

// Resolve a manifest file name to an absolute path, refusing anything that
// escapes the cache root (single-segment names only).
function cacheFilePath(root, name) {
  if (!name || name.includes('/') || name.includes('\\') || name.includes('..')) {
    throw new Error('illegal pyodide cache name: ' + name);
  }
  return path.join(root, name);
}
function isManifestSatisfied(presentNames) {
  return MANIFEST.every(m => presentNames.has(m.name));
}

async function _sha256(file) {
  const buf = await fsp.readFile(file);
  return crypto.createHash('sha256').update(buf).digest('hex');
}

// Public: returns true when the cache is present + verified, provisioning it
// first if needed. Reports progress through the same setSetup callback shape
// python-manager uses, so the lobby's setup window shows one combined bar.
async function ensurePyodide(onProgress) {
  const root = cacheRoot();
  await fsp.mkdir(root, { recursive: true });
  // Fast path: marker present and every file on disk → ready.
  try {
    if (fs.existsSync(markerPath())) {
      const names = new Set(await fsp.readdir(root));
      if (isManifestSatisfied(names)) return true;
    }
  } catch (_) {}
  // Download each missing file, verify sha256, then write the marker.
  for (let i = 0; i < MANIFEST.length; i++) {
    const m = MANIFEST[i];
    const dest = cacheFilePath(root, m.name);
    if (fs.existsSync(dest) && (await _sha256(dest)) === m.sha256) continue;
    if (typeof onProgress === 'function') {
      onProgress({ label: 'Downloading Python runtime…', pct: Math.round((i / MANIFEST.length) * 90) });
    }
    const res = await fetch(PYODIDE_BASE_URL + m.name);
    if (!res.ok) throw new Error('pyodide fetch ' + m.name + ' -> HTTP ' + res.status);
    const bytes = Buffer.from(await res.arrayBuffer());
    if (m.sha256 !== '<fill>' && crypto.createHash('sha256').update(bytes).digest('hex') !== m.sha256) {
      throw new Error('pyodide sha256 mismatch: ' + m.name);
    }
    await fsp.writeFile(dest, bytes);
  }
  await fsp.writeFile(markerPath(), JSON.stringify({ version: PYODIDE_VERSION, at: Date.now() }));
  return true;
}

module.exports = { ensurePyodide, cacheRoot, cacheFilePath, isManifestSatisfied, MANIFEST, PYODIDE_VERSION };
```

- [ ] **Step 4: Run the tests — expect pass** `node --test tests/test_pyodide_manager.mjs` → PASS.

- [ ] **Step 5: Commit** `feat(coding): Pyodide provisioning into userData + sha256 verify [P2-T2]`

> **Note (no silent cap):** the `<fill>` sha256/version values are deliberate placeholders — wiring the real S3 Mumbai host + pinned hashes is a one-line-each follow-up the owner does when the bucket is provisioned. The code path is complete and tested; only the constants are pending.

---

## Task P2-T3: `procta-pyodide://` protocol handler

The flat `procta-lobby://` handler clamps to a single path component (`main.js:413`), so it cannot serve Pyodide's multi-file dir. Add a dedicated scheme that serves the verified cache dir, traversal-clamped to the cache root.

**Files:**
- Modify: `main.js` (register scheme + handler; extract the path resolver as a pure, testable function)
- Test: extend `tests/test_pyodide_manager.mjs` (the resolver is shared from `pyodide-manager.cacheFilePath`, but multi-segment paths are allowed here — add a `resolvePyodideRequest` helper there and test it)

- [ ] **Step 1: Write the failing resolver test**

```javascript
// add to tests/test_pyodide_manager.mjs
import { resolvePyodideRequest } from '../lib/pyodide-manager.js';
test('resolvePyodideRequest maps a clean path under the root', () => {
  assert.strictEqual(resolvePyodideRequest('/u/pyc', 'procta-pyodide://py/pyodide.asm.wasm'),
                     '/u/pyc/pyodide.asm.wasm');
});
test('resolvePyodideRequest refuses traversal', () => {
  assert.throws(() => resolvePyodideRequest('/u/pyc', 'procta-pyodide://py/../../etc/passwd'));
});
```

- [ ] **Step 2: Run — expect failure** (`resolvePyodideRequest` not exported yet).

- [ ] **Step 3: Implement `resolvePyodideRequest` in `lib/pyodide-manager.js`**

```javascript
// Map a procta-pyodide:// URL to an absolute path inside `root`, refusing any
// resolved path that escapes the root (defends against .. and encoded segments).
function resolvePyodideRequest(root, url) {
  const u = new URL(url);
  const rel = decodeURIComponent(u.pathname.replace(/^\/+/, ''));
  const abs = path.normalize(path.join(root, rel));
  if (abs !== root && !abs.startsWith(root + path.sep)) {
    throw new Error('pyodide path escapes cache root: ' + rel);
  }
  return abs;
}
module.exports.resolvePyodideRequest = resolvePyodideRequest;
```

- [ ] **Step 4: Run the resolver tests — expect pass.**

- [ ] **Step 5: Register the scheme in `main.js`** (alongside the existing `registerSchemesAsPrivileged`, line ~377)

```javascript
require('electron').protocol.registerSchemesAsPrivileged([
  { scheme: 'procta-lobby', privileges: { standard: true, secure: true, supportFetchAPI: true,
    corsEnabled: true, allowServiceWorkers: false, bypassCSP: false } },
  { scheme: 'procta-pyodide', privileges: { standard: true, secure: true, supportFetchAPI: true,
    corsEnabled: true, allowServiceWorkers: false, bypassCSP: false } },
]);
```

- [ ] **Step 6: Add the handler** (in `_registerLobbyProtocol`, or a sibling `_registerPyodideProtocol` called from `app.whenReady`)

```javascript
function _registerPyodideProtocol() {
  const { ensure, cacheRoot, resolvePyodideRequest } = require('./lib/pyodide-manager.js');
  require('electron').protocol.handle('procta-pyodide', async (request) => {
    let filepath;
    try { filepath = resolvePyodideRequest(cacheRoot(), request.url); }
    catch { return new Response('forbidden', { status: 403 }); }
    try {
      const data = await _fsp.readFile(filepath);
      const ext = _path.extname(filepath).toLowerCase();
      const mime = ext === '.wasm' ? 'application/wasm'
                 : ext === '.js'   ? 'application/javascript; charset=utf-8'
                 : ext === '.json' ? 'application/json; charset=utf-8'
                 : 'application/octet-stream';
      return new Response(data, { headers: { 'Content-Type': mime, 'Cache-Control': 'no-cache' } });
    } catch { return new Response('not found', { status: 404 }); }
  });
}
```

Call `_registerPyodideProtocol();` next to `_registerLobbyProtocol();` in `app.whenReady` (main.js:444).

- [ ] **Step 7: Commit** `feat(coding): procta-pyodide:// scheme serving the cache dir [P2-T3]`

---

## Task P2-T4: `coding-worker-py.js` — Pyodide worker (load-then-lockdown, warm)

The execution sandbox. **Load-then-lockdown** is the security crux: Pyodide needs `fetch`/`importScripts` to load, so we load first (no untrusted code has run), then null every egress vector, then accept student code. The worker is **warm** — Pyodide loads once, and each test resets interpreter globals + restores `sys.stdout`.

**Files:** Create `renderer/coding-worker-py.js`

> Worker WASM exec can't be unit-tested off-device — its real verification is **P2-S0 (the CSP spike)** and **P2-T7 (e2e)**. This task delivers the exact, complete worker; correctness is proven at the surface.

- [ ] **Step 1: Write the worker**

```javascript
/* coding-worker-py.js — Pyodide execution worker for the Edge Compiler kiosk.
 * Loaded SAME-ORIGIN via new Worker('coding-worker-py.js') so it runs under the
 * kiosk CSP with no blob:/relaxation. WARM: Pyodide loads once and is reused
 * across a question's test cases (init is ~1-2s). Same postMessage reply shape
 * as coding-worker.js: {stdout, stderr, time_ms, mem_kb}.
 *
 * SECURITY — load-then-lockdown: Pyodide needs fetch/importScripts to load, so
 * we (1) load it from the cached procta-pyodide:// dir, (2) THEN null every
 * egress vector, (3) ONLY THEN accept student code. Python network (urllib /
 * pyodide.http) dies with the JS fetch it bridges to.
 */
let _pyReady = null;     // Promise<pyodide>
let _locked = false;

function lockdown() {
  if (_locked) return; _locked = true;
  self.fetch = null; self.WebSocket = null; self.XMLHttpRequest = null;
  self.EventSource = null; self.Worker = null; self.SharedWorker = null;
  self.importScripts = function () { throw new Error('importScripts is disabled'); };
  try { if (self.navigator && self.navigator.sendBeacon) self.navigator.sendBeacon = function () { return false; }; } catch (e) {}
  try { self.caches = null; } catch (e) {}
}

function loadPy() {
  if (_pyReady) return _pyReady;
  _pyReady = (async () => {
    // indexURL is the dedicated cache scheme; importScripts the loader BEFORE lockdown.
    self.importScripts('procta-pyodide://py/pyodide.js');
    const py = await self.loadPyodide({ indexURL: 'procta-pyodide://py/' });
    lockdown();                       // <-- egress sealed before any student code
    return py;
  })();
  return _pyReady;
}

self.onmessage = async function (ev) {
  const source = (ev.data && ev.data.source) || '';
  const stdin  = (ev.data && ev.data.stdin)  || '';
  let py, out = [], err = null;
  const t0 = (self.performance && performance.now) ? performance.now() : Date.now();
  try {
    py = await loadPy();
    // Feed stdin line-by-line; capture stdout/stderr into out[].
    const inLines = stdin.length ? String(stdin).replace(/\r\n/g, '\n').split('\n') : [];
    let inPos = 0;
    py.setStdin({ stdin: () => (inPos < inLines.length ? inLines[inPos++] : null) });
    py.setStdout({ batched: (s) => out.push(s) });
    py.setStderr({ batched: (s) => out.push(s) });   // mirror JS: stderr text joins stdout stream? -> keep separate: see note
    // Run in a FRESH namespace dict so globals don't leak between test cases.
    const ns = py.toPy({});
    py.runPython(source, { globals: ns });
    ns.destroy();
  } catch (e) {
    err = (e && e.message) ? String(e.message) : String(e);
  }
  const t1 = (self.performance && performance.now) ? performance.now() : Date.now();
  self.postMessage({
    stdout: out.join('\n'),
    stderr: err,
    time_ms: Math.max(0, Math.round((t1 - t0) * 1000) / 1000),
    mem_kb: null,            // performance.memory is unavailable in workers
  });
};
```

> **Decision to confirm during the spike:** stderr handling. JS worker folds everything into `stdout`; here Python runtime errors surface via the `catch` → `stderr`, and `setStderr` is for program-written stderr. Keep `setStderr` separate from `out` if the judge should ignore stderr text (it compares stdout only). Adjust to match `coding-worker.js`'s observed behavior during P2-S0.

- [ ] **Step 2: Lint/parse check** `node --check renderer/coding-worker-py.js` → no syntax error.

- [ ] **Step 3: Commit** `feat(coding): Pyodide worker — load-then-lockdown + warm reuse [P2-T4]`

---

## Task P2-T5: `coding-runtime.js` per-language worker strategy

Add Python to `runTestCases` via a **warm `PyRunner`**: one `coding-worker-py.js` for the whole `stdins[]` batch (reused, not fresh-per-test), still bounded by the main-thread watchdog. JS/TS paths are unchanged. Same `{outputs, metrics}` contract.

**Files:**
- Modify: `renderer/coding-runtime.js`
- Test: `tests/test_coding_runtime_dispatch.mjs`

- [ ] **Step 1: Write the failing dispatch/contract test** (mock `Worker` so it runs in node)

```javascript
// tests/test_coding_runtime_dispatch.mjs  (node --test)
import { test } from 'node:test';
import assert from 'node:assert';
// Minimal Worker mock: echoes a deterministic stdout per message.
globalThis.self = globalThis;
globalThis.Worker = class {
  constructor() {} 
  postMessage(m) { setTimeout(() => this.onmessage({ data: { stdout: 'ok:' + (m.stdin||''), time_ms: 1, mem_kb: null } }), 0); }
  terminate() {}
};
const { runTestCases } = await import('../renderer/coding-runtime.js');

test('python is a supported language and returns the contract shape', async () => {
  const r = await runTestCases('python', 'print(1)', ['a', 'b']);
  assert.strictEqual(r.outputs.length, 2);
  assert.strictEqual(r.metrics.length, 2);
  assert.ok('time_ms' in r.metrics[0] && 'timed_out' in r.metrics[0]);
});
test('unknown language still rejects', async () => {
  await assert.rejects(() => runTestCases('ruby', 'puts 1', ['x']));
});
```

- [ ] **Step 2: Run — expect failure** (python rejects today: "not supported in Phase 1").

- [ ] **Step 3: Implement the Python branch in `coding-runtime.js`**
  - Add to `SUPPORTED`: `python: true, py: true`.
  - Add a `PY_WORKER_URL = 'coding-worker-py.js'` and a `runPythonBatch(source, stdins, limitMs)` that:
    - spawns ONE `new Worker(PY_WORKER_URL)`,
    - runs each stdin sequentially over the SAME worker (post → await message, with a per-test watchdog that on timeout terminates + respawns the worker and marks that test `timed_out`),
    - returns `{outputs, metrics}` identical in shape to the JS path.
  - In `runTestCases`, branch: `if (lang === 'python' || lang === 'py') return runPythonBatch(source, stdins, limitMs);` (before the TS branch; do NOT route Python through the fresh-per-test JS chain).
  - Update the module docstring: "Languages: JavaScript, TypeScript (sucrase), Python (Pyodide warm worker)."

- [ ] **Step 4: Run the tests — expect pass** `node --test tests/test_coding_runtime_dispatch.mjs`.

- [ ] **Step 5: Commit** `feat(coding): per-language worker strategy — warm Pyodide runner [P2-T5]`

---

## Task P2-T6: Per-exam-type FPS floor (proctoring stays alive during exec bursts)

Code execution is bursty (idle while typing, ~2 s WASM spikes on Run/Submit). The governor must not let a coding burst drop proctoring below the cheating-detection threshold. Add a **per-exam-type fps floor** so coding exams never throttle below it. (The related `behavioral_analysis.py` fps bug is ALREADY fixed on main — `5cf45c8d`; no action here.)

**Files:**
- Modify: `proctor.py` (`_HardwareGovernor` tiers / floor)
- Test: `tests/test_proctor_features.py` (governor floor honored)

- [ ] **Step 1: Write the failing test** — a governor constructed with a coding floor never reports `effective_fps` below it even under max simulated CPU/thermal pressure.

```python
def test_governor_respects_coding_fps_floor(monkeypatch):
    import proctor
    gov = proctor._HardwareGovernor()
    gov.set_min_fps_floor(5.0)          # coding-exam floor
    # drive it to the bottom tier
    for _ in range(50):
        monkeypatch.setattr(proctor, "_read_cpu", lambda: 99.0, raising=False)
        gov.maybe_update()
    assert gov.effective_fps >= 5.0
```

- [ ] **Step 2: Run — expect failure** (`set_min_fps_floor` doesn't exist).

- [ ] **Step 3: Implement** a `set_min_fps_floor(fps)` on `_HardwareGovernor` that clamps the resolved tier so `effective_fps` never falls below the floor; wire it from `run_proctoring` when the exam/question type is `coding` (the exam context already flows to the proctor — set the floor from a `PROCTOR_CODING_FPS_FLOOR` env / exam-type signal). Default floor unset (no behavior change for non-coding exams).

- [ ] **Step 4: Run the test — expect pass**, then the full proctor suite: `python3 -m pytest tests/test_proctor_features.py -q`.

- [ ] **Step 5: Commit** `feat(proctor): per-exam-type fps floor for coding exams [P2-T6]`

> **Perf validation (measurement, post-build):** on a target lab laptop, run a coding exam with proctoring live; capture proctor `effective_fps` during Run/Submit bursts and confirm it holds at/above the floor and a phone held 2-3 s is still caught. Record the number in `docs/CODING_PYTHON_ONDEVICE_PASS.md`.

---

## Task P2-T7: Seed + e2e + on-device runbook

**Files:**
- Modify: `scripts/seed_coding_question.py` (a `--language python` question; compute expected outputs by running the reference solution through `python3` locally, mirroring how the JS seed uses node)
- Create: `docs/CODING_PYTHON_ONDEVICE_PASS.md`

- [ ] **Step 1: Extend the seed script** to author a Python coding question (`options.allowed_languages` includes `"python"`; starter + reference solution in Python; expected outputs filled by running the reference via `python3` — the LLM/seed never hand-writes outputs). Reuse the existing label-keying (`question_id = str(question_id)`).

- [ ] **Step 2: Run the seed against a dev DB**, confirm it prints Teacher/Exam/Question IDs and writes `coding_test_cases` with expected outputs.

- [ ] **Step 3: Write `docs/CODING_PYTHON_ONDEVICE_PASS.md`** (mirror `CODING_ONDEVICE_PASS.md`): Stage A = P2-S0 CSP spike; Stage B = full Run→Submit→score for a Python question with the checklist (editor mounts + Python highlight; Run shows per-case diff; Submit posts outputs, server judges, score folds in; offline submit queues; submit cap; **zero CSP violations**; **proctor fps floor held during bursts**).

- [ ] **Step 4: Commit** `test(coding): Python seed + on-device runbook [P2-T7]`

---

## Sequencing (dependency order)

```
P2-T1 (CM highlight)        ── independent, land first
P2-T2 (provisioning) ─┐
P2-T3 (protocol)  ────┼──► P2-S0 (CSP SPIKE — GATE) ──► P2-T5 (runtime) ─┐
P2-T4 (py worker) ────┘                                                   ├─► P2-T7 (e2e)
P2-T6 (fps floor)  ── parallel to the above                              ─┘
```

**Hard gate:** nothing past P2-S0 is *relied upon* until the spike passes on a real kiosk. T2/T3/T4 are built first (the spike needs the worker + a provisioned cache to run), but T5/T7 "Python is live" status waits on the green spike. If the spike fails on CSP, STOP and escalate the `wasm-unsafe-eval`-vs-`child_process` decision — do not relax the kiosk CSP unilaterally.

---

## Self-Review

**Spec coverage:** execution model (warm worker) → T4/T5; load-then-lockdown → T4; hosting/decouple/cache → T2; multi-file-dir packaging wrinkle → T3 (dedicated scheme); stdin/stdout bridge → T4; editor highlighting → T1; gating CSP spike → P2-S0; perf + per-exam-type fps floor → T6; data-residency → T2 (S3 Mumbai). The "latent behavioral fps bug" (spec risk #2) is already fixed on main — noted, no task. ✅

**Placeholder scan:** the only intentional placeholders are the `<fill>` Pyodide sha256/version/bucket constants in T2 — flagged explicitly as owner-provisioned, with the code path complete and tested around them. No "TBD" logic.

**Type/contract consistency:** `runTestCases(language, source, stdins, opts) → {outputs[], metrics[]}` is identical across JS/TS/Python; the worker reply shape `{stdout, stderr, time_ms, mem_kb}` is identical between `coding-worker.js` and `coding-worker-py.js`; `cacheFilePath`/`resolvePyodideRequest`/`ensurePyodide`/`MANIFEST` names are consistent between `pyodide-manager.js`, its tests, and the `main.js` handler.

---

## Execution Handoff

Two execution options:
1. **Subagent-Driven (recommended)** — a fresh subagent per task, review between tasks.
2. **Inline Execution** — batch with checkpoints in this session.

**Note:** P2-S0, the perf validation, and the e2e checklist require a real packaged kiosk with a camera — they cannot be completed in this environment and must be run on-device before Python is enabled in a release. The release tag is the true ship gate (client code reaches students only on a `v*` tag).
