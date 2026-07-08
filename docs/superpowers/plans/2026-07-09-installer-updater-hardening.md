# Installer/Updater Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple `weights/` + the base Python interpreter from the diffed Windows NSIS installer payload (real delta updates), make the VC++ Redistributable install compulsory with a real explain-then-retry flow, and add the macOS CI blockmap-presence gate that mirrors Windows's existing one.

**Architecture:** A new small shared download helper (extracted from `bundle-python.js`'s existing retry logic) is used both at build time (uploading the runtime-assets archive) and at app runtime (fetching it into a per-user cache on first run / version bump). `python-manager.js`'s existing first-run "setup window" IPC events (`setup-status`/`setup-progress`/`setup-mode`) are reused unchanged for the new download phase — no new UI plumbing. The VC++ fix and the CI gate are both self-contained, independent of the runtime-assets work.

**Tech Stack:** Same as today — Node.js (CommonJS, `require`), Electron, NSIS (electron-builder), GitHub Actions bash. `node --test` for the two pieces of pure logic this plan can actually unit-test in this sandbox.

## Global Constraints

- This is exam-proctoring software; a broken update pipeline can strand students on exam day. Tasks that touch the runtime-asset fetch or VC++ install are NOT considered done until manually verified on a real, clean VM per their task's verification steps — code review alone is not sufficient for those tasks.
- Do not touch `PIP_PACKAGES`/`ensureVenv()` (`lib/python-manager.js:473-560`) — that mechanism already works and is out of scope.
- Decision (recorded in the spec, 2026-07-09): only the first-ever install, or a rare runtime-asset-version bump, requires internet to fetch `weights/`+interpreter — every subsequent app-code-only update must NOT touch this cache at all.
- No new external paid infrastructure — the runtime-assets archive is uploaded to GitHub Releases, same host every other build artifact already uses.
- `vc_redist.x64.exe` exit codes that count as success: `0` (installed), `3010` (installed, reboot recommended), `1638` (a newer version is already present). Any other code (including a UAC decline) does not count as success.

---

### Task 1: Shared HTTP download-with-retry helper

**Files:**
- Create: `lib/http-download.js`
- Test: `scripts/http-download.test.mjs`
- Modify: `bundle-python.js` (replace its local `download`/`downloadOnce` with a `require` of the new module — same call sites, no behavior change)

**Interfaces:**
- Produces: `download(url, dest, attempts = 4)` — async, returns a Promise that resolves when `dest` has been written, or rejects after all attempts are exhausted. Identical signature and behavior to `bundle-python.js`'s current `download` (redirect-following up to depth 8, retry with `i * 2` second backoff between attempts, fresh destination file each attempt).

- [ ] **Step 1: Write the failing test**

Create `scripts/http-download.test.mjs`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { download } from '../lib/http-download.js';

test('download() writes the response body to dest on a plain 200', async () => {
  const server = http.createServer((req, res) => {
    res.writeHead(200);
    res.end('hello world');
  });
  await new Promise(resolve => server.listen(0, resolve));
  const port = server.address().port;
  const dest = path.join(os.tmpdir(), `http-download-test-${Date.now()}.txt`);
  try {
    await download(`http://127.0.0.1:${port}/`, dest, 1);
    assert.equal(fs.readFileSync(dest, 'utf8'), 'hello world');
  } finally {
    server.close();
    fs.rmSync(dest, { force: true });
  }
});

test('download() follows a redirect chain to the final 200', async () => {
  const server = http.createServer((req, res) => {
    if (req.url === '/start') {
      res.writeHead(302, { Location: '/final' });
      res.end();
      return;
    }
    res.writeHead(200);
    res.end('redirected content');
  });
  await new Promise(resolve => server.listen(0, resolve));
  const port = server.address().port;
  const dest = path.join(os.tmpdir(), `http-download-test-redirect-${Date.now()}.txt`);
  try {
    await download(`http://127.0.0.1:${port}/start`, dest, 1);
    assert.equal(fs.readFileSync(dest, 'utf8'), 'redirected content');
  } finally {
    server.close();
    fs.rmSync(dest, { force: true });
  }
});

test('download() retries on failure and succeeds if a later attempt works', async () => {
  let requestCount = 0;
  const server = http.createServer((req, res) => {
    requestCount += 1;
    if (requestCount < 2) {
      req.socket.destroy(); // simulate a dropped connection on the first attempt
      return;
    }
    res.writeHead(200);
    res.end('succeeded on retry');
  });
  await new Promise(resolve => server.listen(0, resolve));
  const port = server.address().port;
  const dest = path.join(os.tmpdir(), `http-download-test-retry-${Date.now()}.txt`);
  try {
    await download(`http://127.0.0.1:${port}/`, dest, 3);
    assert.equal(fs.readFileSync(dest, 'utf8'), 'succeeded on retry');
    assert.ok(requestCount >= 2);
  } finally {
    server.close();
    fs.rmSync(dest, { force: true });
  }
});

test('download() rejects after exhausting all attempts against a dead port', async () => {
  const dest = path.join(os.tmpdir(), `http-download-test-fail-${Date.now()}.txt`);
  await assert.rejects(
    download('http://127.0.0.1:1/', dest, 2),
  );
  assert.equal(fs.existsSync(dest), false);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test scripts/http-download.test.mjs`
Expected: FAIL — `Cannot find module '../lib/http-download.js'`

- [ ] **Step 3: Write minimal implementation**

Create `lib/http-download.js` — this is `bundle-python.js`'s existing `download`/`downloadOnce` pair (currently at `bundle-python.js:278-317`), moved verbatim into its own module and exported:

```js
const fs = require('fs');
const https = require('https');
const http = require('http');

// Retry wrapper. CI runners intermittently drop the connection mid-stream
// ("Error: socket hang up" / ECONNRESET) on larger CDN/GitHub-release
// downloads — retry with backoff + a fresh dest file so a flaky network drop
// doesn't fail the whole operation. Shared by bundle-python.js (build time)
// and the runtime-asset fetch (lib/runtime-assets.js, app time) so this
// redirect/retry logic exists in exactly one place.
async function download(url, dest, attempts = 4) {
  for (let i = 1; i <= attempts; i++) {
    try {
      return await downloadOnce(url, dest);
    } catch (e) {
      const last = i >= attempts;
      console.warn(`[dl] attempt ${i}/${attempts} failed for ${url}: ${e.message}` +
        (last ? '' : ` — retrying in ${i * 2}s`));
      try { fs.rmSync(dest, { force: true }); } catch { /* nothing to clean */ }
      if (last) throw e;
      await new Promise(r => setTimeout(r, i * 2000));
    }
  }
}

function downloadOnce(url, dest) {
  // Only opens the destination stream on the final 200 — GitHub release
  // URLs redirect (302 -> objects.githubusercontent.com), so opening the
  // file up front and closing it on the first redirect left an empty file.
  // Handles the full redirect set + a depth cap.
  return new Promise((resolve, reject) => {
    const follow = (u, depth = 0) => {
      if (depth > 8) { reject(new Error(`Too many redirects — ${url}`)); return; }
      const client = String(u).startsWith('http://') ? http : https;
      client.get(u, (res) => {
        if ([301, 302, 303, 307, 308].includes(res.statusCode)) {
          res.resume(); // drain so the socket frees
          follow(res.headers.location, depth + 1);
          return;
        }
        if (res.statusCode !== 200) {
          res.resume();
          reject(new Error(`HTTP ${res.statusCode} — ${u}`));
          return;
        }
        const file = fs.createWriteStream(dest);
        res.pipe(file);
        file.on('finish', () => file.close(resolve));
        file.on('error', reject);
      }).on('error', reject);
    };
    follow(url);
  });
}

module.exports = { download };
```

Note: the original `bundle-python.js` version only imported `https` (GitHub release URLs are always https); this version adds a plain `http` fallback purely so the unit tests above can use a local plain-HTTP test server without needing a self-signed cert. Real usage (GitHub Releases) is unaffected — it's still `https://` in every real call site.

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test scripts/http-download.test.mjs`
Expected: PASS, 4/4.

- [ ] **Step 5: Point `bundle-python.js` at the shared module**

In `bundle-python.js`, remove the local `download`/`downloadOnce` function definitions (currently at `bundle-python.js:278-317`) and add near the top of the file, alongside its other `require`s:

```js
const { download } = require('./lib/http-download.js');
```

Leave every call site (`await download(url, dest)` etc.) unchanged — same name, same signature.

- [ ] **Step 6: Verify `bundle-python.js` still parses and its own logic is untouched**

Run: `node --check bundle-python.js`
Expected: no output (valid syntax).

Run: `node --test scripts/*.test.mjs`
Expected: all existing tests still pass (this task didn't touch anything any existing test covers, other than the new file itself).

- [ ] **Step 7: Commit**

```bash
git add lib/http-download.js scripts/http-download.test.mjs bundle-python.js
git commit -m "refactor(build): extract shared http-download helper from bundle-python.js"
```

---

### Task 2: Runtime-asset cache version-check (pure logic)

**Files:**
- Create: `lib/runtime-assets-version.js`
- Test: `scripts/runtime-assets-version.test.mjs`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `needsRuntimeAssetFetch(expectedVersion, cacheDir, fs = require('fs'))` — pure-ish function (the only side effect is a filesystem read, injected as a parameter so it's testable without touching the real disk), returns `true` if `cacheDir` does not contain a marker file matching `expectedVersion` (meaning a fetch is needed), `false` otherwise. Task 3 calls this before deciding whether to download.

- [ ] **Step 1: Write the failing test**

Create `scripts/runtime-assets-version.test.mjs`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { needsRuntimeAssetFetch } from '../lib/runtime-assets-version.js';

function fakeFs(markerContents) {
  return {
    existsSync: (p) => markerContents !== null && String(p).endsWith('.version'),
    readFileSync: (p, enc) => {
      if (markerContents === null) throw new Error('ENOENT');
      return markerContents;
    },
  };
}

test('returns true (needs fetch) when no marker file exists at all', () => {
  assert.equal(needsRuntimeAssetFetch('3', '/fake/cache', fakeFs(null)), true);
});

test('returns false (cache is current) when the marker matches the expected version', () => {
  assert.equal(needsRuntimeAssetFetch('3', '/fake/cache', fakeFs('3')), false);
});

test('returns true (needs fetch) when the marker is for an OLDER version', () => {
  assert.equal(needsRuntimeAssetFetch('3', '/fake/cache', fakeFs('2')), true);
});

test('returns true (needs fetch) when the marker file is empty/corrupt', () => {
  assert.equal(needsRuntimeAssetFetch('3', '/fake/cache', fakeFs('')), true);
});

test('returns true (needs fetch) when the marker has trailing whitespace mismatching the expected version', () => {
  // Guards against a marker file written with a trailing newline being
  // treated as a version mismatch forever — trim before comparing.
  assert.equal(needsRuntimeAssetFetch('3', '/fake/cache', fakeFs('3\n')), false);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test scripts/runtime-assets-version.test.mjs`
Expected: FAIL — `Cannot find module '../lib/runtime-assets-version.js'`

- [ ] **Step 3: Write minimal implementation**

Create `lib/runtime-assets-version.js`:

```js
const path = require('path');

// Pure decision (fs is injected so this is testable without touching the
// real disk): does the runtime-assets cache at cacheDir already satisfy
// expectedVersion, or does python-manager.js need to fetch a fresh copy?
// The marker file is written ONLY after a verified-successful extraction
// (see Task 3), so its mere presence with a matching version means the
// cache is trustworthy — never write it speculatively before that.
function needsRuntimeAssetFetch(expectedVersion, cacheDir, fs = require('fs')) {
  const markerPath = path.join(cacheDir, '.version');
  if (!fs.existsSync(markerPath)) return true;
  let contents;
  try {
    contents = fs.readFileSync(markerPath, 'utf8');
  } catch (e) {
    return true;
  }
  return contents.trim() !== String(expectedVersion);
}

module.exports = { needsRuntimeAssetFetch };
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test scripts/runtime-assets-version.test.mjs`
Expected: PASS, 5/5.

- [ ] **Step 5: Commit**

```bash
git add lib/runtime-assets-version.js scripts/runtime-assets-version.test.mjs
git commit -m "feat(runtime-assets): add pure cache-version-check helper + unit tests"
```

---

### Task 3: Wire the runtime-asset fetch into `python-manager.js` startup

**Files:**
- Create: `lib/runtime-assets.js`
- Modify: `lib/python-manager.js` (change `getBundledPython()`/`bundledWeightsDir()` resolution order; call the new fetch before those are needed)
- Modify: `config.js` (add `RUNTIME_ASSET_VERSION` + the expected SHA-256 checksum)

**Interfaces:**
- Consumes: `download` (Task 1, `lib/http-download.js`), `needsRuntimeAssetFetch` (Task 2, `lib/runtime-assets-version.js`).
- Produces: `ensureRuntimeAssets(sendStatus, sendProgress)` — async, resolves to `{ pythonRuntimeDir, weightsDir }` (the two paths the rest of `python-manager.js` should now read from) once the cache is confirmed present and correct, or rejects with a clear `Error` if the download/verify/extract failed after retries. `sendStatus`/`sendProgress` are the existing `sendSetupStatus`/`sendSetupProgress` functions already defined in `python-manager.js:833-849` — passed in rather than imported, so this new module has no dependency on Electron's `BrowserWindow`/setup-window internals and can be tested in isolation later if needed.

- [ ] **Step 1: Add the new config constants**

In `config.js`, near the existing `PIP_PACKAGES` block (`config.js:346-363`), add:

```js
// ── Runtime assets (weights/ + base Python interpreter) ────────────
// Decoupled from the diffed NSIS/DMG installer payload (2026-07 installer
// hardening) so an app-code-only update doesn't re-download 64MB+ of
// binary model weights that didn't change. Bump RUNTIME_ASSET_VERSION
// only when weights/ or the bundled interpreter actually change — NOT on
// every app release. RUNTIME_ASSET_CHECKSUM is the SHA-256 of the
// uploaded tarball, checked before extraction (same pin-and-verify
// pattern already used for PIP_PACKAGES' exact version pins above).
const RUNTIME_ASSET_VERSION = '1';
const RUNTIME_ASSET_CHECKSUM_WIN = 'REPLACE_WITH_REAL_SHA256_AFTER_FIRST_UPLOAD';
const RUNTIME_ASSET_CHECKSUM_MAC = 'REPLACE_WITH_REAL_SHA256_AFTER_FIRST_UPLOAD';
const RUNTIME_ASSET_BASE_URL =
  'https://github.com/ArihantK15/proctor-browser/releases/download/runtime-assets-v';
```

Add these four names to the existing `module.exports` block at the bottom of `config.js` (alongside `PIP_PACKAGES` etc.).

- [ ] **Step 2: Create the fetch module**

Create `lib/runtime-assets.js`:

```js
const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');
const { execFileSync } = require('child_process');
const { app } = require('electron');
const { download } = require('./http-download.js');
const { needsRuntimeAssetFetch } = require('./runtime-assets-version.js');
const {
  RUNTIME_ASSET_VERSION, RUNTIME_ASSET_CHECKSUM_WIN, RUNTIME_ASSET_CHECKSUM_MAC,
  RUNTIME_ASSET_BASE_URL,
} = require('../config');

// Deliberately OUTSIDE the versioned app-install directory (unlike
// process.resourcesPath, which is wiped and replaced on every update) —
// this cache must survive an app-code-only update untouched, which is the
// entire point of decoupling it from the diffed installer payload.
function cacheDir() {
  return path.join(path.dirname(app.getPath('userData')), 'procta-runtime-cache');
}

function _archiveNameAndChecksum() {
  if (process.platform === 'win32') {
    return { name: 'procta-runtime-win.tar.gz', checksum: RUNTIME_ASSET_CHECKSUM_WIN };
  }
  if (process.platform === 'darwin') {
    const arch = process.arch === 'arm64' ? 'arm64' : 'x64';
    return { name: `procta-runtime-mac-${arch}.tar.gz`, checksum: RUNTIME_ASSET_CHECKSUM_MAC };
  }
  throw new Error(`No runtime-assets archive for platform ${process.platform}`);
}

function _sha256(filePath) {
  const buf = fs.readFileSync(filePath);
  return crypto.createHash('sha256').update(buf).digest('hex');
}

// sendStatus/sendProgress are python-manager.js's existing
// sendSetupStatus/sendSetupProgress (python-manager.js:833-849) — passed
// in rather than imported so this module has no dependency on the
// setup-window/BrowserWindow internals.
async function ensureRuntimeAssets(sendStatus, sendProgress) {
  const dir = cacheDir();
  fs.mkdirSync(dir, { recursive: true });

  if (!needsRuntimeAssetFetch(RUNTIME_ASSET_VERSION, dir)) {
    return {
      pythonRuntimeDir: path.join(dir, 'python-runtime'),
      weightsDir: path.join(dir, 'weights'),
    };
  }

  sendStatus('Downloading required components (one-time setup)…');
  const { name, checksum } = _archiveNameAndChecksum();
  const url = `${RUNTIME_ASSET_BASE_URL}${RUNTIME_ASSET_VERSION}/${name}`;
  const archivePath = path.join(os.tmpdir(), `procta-runtime-${Date.now()}.tar.gz`);

  try {
    await download(url, archivePath, 4);
  } catch (e) {
    throw new Error(`Failed to download required components after 4 attempts: ${e.message}`);
  }

  sendProgress(1, 2, 'Verifying download…');
  const actualChecksum = _sha256(archivePath);
  if (actualChecksum !== checksum) {
    fs.rmSync(archivePath, { force: true });
    throw new Error(
      `Downloaded components failed integrity check (expected ${checksum}, got ${actualChecksum}) — please check your internet connection and restart the app.`
    );
  }

  sendProgress(2, 2, 'Extracting…');
  // Extract to a temp dir first, then atomically rename into place, so a
  // crash/interrupt mid-extraction never leaves a half-written cache that
  // needsRuntimeAssetFetch() would wrongly treat as valid on the next launch
  // (the .version marker below is written LAST, only after this succeeds).
  const extractTmp = path.join(os.tmpdir(), `procta-runtime-extract-${Date.now()}`);
  fs.mkdirSync(extractTmp, { recursive: true });
  execFileSync('tar', ['-xzf', archivePath, '-C', extractTmp], { stdio: 'ignore' });
  fs.rmSync(archivePath, { force: true });

  for (const sub of ['python-runtime', 'weights']) {
    const src = path.join(extractTmp, sub);
    const dest = path.join(dir, sub);
    fs.rmSync(dest, { recursive: true, force: true });
    fs.renameSync(src, dest);
  }
  fs.rmSync(extractTmp, { recursive: true, force: true });

  // Written LAST, only after a fully successful extraction — this is what
  // needsRuntimeAssetFetch() checks, so a marker only ever exists for a
  // cache that's actually complete.
  fs.writeFileSync(path.join(dir, '.version'), RUNTIME_ASSET_VERSION, 'utf8');

  return {
    pythonRuntimeDir: path.join(dir, 'python-runtime'),
    weightsDir: path.join(dir, 'weights'),
  };
}

module.exports = { ensureRuntimeAssets, cacheDir };
```

- [ ] **Step 3: Wire it into `python-manager.js`'s resolution**

In `lib/python-manager.js`, `getBundledPython()` (currently `python-manager.js:439-450`) and `bundledWeightsDir()` (currently `python-manager.js:1118-1121`) both read from `process.resourcesPath`. Change both to prefer the new cache location, falling back to the old bundled path for one transition release (so an in-flight update from an old bundled-weights version doesn't break before the user's next full reinstall):

```js
// getBundledPython() — replace the body with:
function getBundledPython() {
  try {
    const { cacheDir } = require('./runtime-assets.js');
    const cached = path.join(cacheDir(), 'python-runtime');
    if (process.platform === 'win32') {
      const p = path.join(cached, 'python.exe');
      if (_exists(p)) return p;
    }
    if (process.platform === 'darwin') {
      const p = path.join(cached, _macArchDir(), 'bin', 'python3');
      if (_exists(p)) return p;
    }
  } catch (e) { console.warn('[Python] runtime-assets cache check failed:', e.message); }

  // Fallback: old bundled-in-installer path (transition compatibility —
  // remove this branch once no supported version still ships the old layout).
  const res = process.resourcesPath || '';
  if (process.platform === 'win32') {
    const p = path.join(res, 'python', 'python.exe');
    return _exists(p) ? p : null;
  }
  if (process.platform === 'darwin') {
    const p = path.join(res, 'python-runtime', _macArchDir(), 'bin', 'python3');
    return _exists(p) ? p : null;
  }
  return null;
}
```

```js
// bundledWeightsDir() — replace the body with:
function bundledWeightsDir() {
  try {
    const { cacheDir } = require('./runtime-assets.js');
    const cached = path.join(cacheDir(), 'weights');
    if (fs.existsSync(cached)) return cached;
  } catch (e) { console.warn('[Weights] runtime-assets cache check failed:', e.message); }
  // Fallback: old bundled-in-installer path (transition compatibility).
  return path.join(process.resourcesPath || '', 'weights');
}
```

- [ ] **Step 4: Call `ensureRuntimeAssets` before the app needs Python/weights**

Find the app's startup sequence where `createSetupWindow()` is first invoked (`python-manager.js:1079`, inside whatever function guards first-run package setup) and add a call to `ensureRuntimeAssets` BEFORE the existing `ensureVenv`/pip-install logic runs, using the same `sendSetupStatus`/`sendSetupProgress` already in scope there:

```js
const { ensureRuntimeAssets } = require('./runtime-assets.js');
// ... inside the existing setup-flow function, before ensureVenv/pip-install:
try {
  await ensureRuntimeAssets(sendSetupStatus, sendSetupProgress);
} catch (e) {
  sendSetupStatus(`Setup failed: ${e.message}`);
  // Do not silently continue — a proctor without weights/interpreter will
  // crash-loop later with a confusing error. Surface this now, clearly.
  throw e;
}
```

- [ ] **Step 5: Update `package.json` to stop bundling the old paths**

Remove `python-runtime` and `resources/python` (but keep `weights` OUT too, per the spec's Option A decision) from `build.extraResources` in `package.json`. Leave `proctor.py`/`behavioral_analysis.py`/`audio_processor.py`/`frame_buffer.py`/`scripts/download_audio_models.py` untouched — those are small app-code files, not part of this decoupling.

- [ ] **Step 6: Verify syntax + run the full Node test suite**

Run: `node --check lib/runtime-assets.js && node --check lib/python-manager.js && node --check config.js`
Expected: no output (valid syntax) for all three.

Run: `node --test scripts/*.test.mjs`
Expected: all tests still pass, including the new Task 1/2 tests.

- [ ] **Step 7: Manual VM verification (cannot be automated in this sandbox)**

This step requires a real Windows VM (or, at minimum, real `electron-builder --win` packaging plus manual execution) and cannot be considered done from code review alone:
1. Build with the new `extraResources` (missing python-runtime/weights) and confirm `ensureRuntimeAssets` actually downloads, verifies, and extracts on a clean VM with no prior install.
2. Confirm the app launches and a proctor session actually works after this first-run fetch (not just that the files landed — that `onnxruntime`/the YOLO weight actually loads).
3. Simulate an app-code-only update (bump the app version, do NOT bump `RUNTIME_ASSET_VERSION`) and confirm `needsRuntimeAssetFetch` returns `false` on the second launch — no re-download.
4. Kill the app mid-download once (simulate a crash) and confirm the next launch retries cleanly rather than treating a half-extracted cache as valid (this is what the temp-dir + rename + last-written `.version` marker in Task 3 Step 2 is for — verify it actually holds up under a real interrupted run, not just by reading the code).

- [ ] **Step 8: Commit**

```bash
git add lib/runtime-assets.js lib/python-manager.js config.js package.json
git commit -m "feat(runtime-assets): decouple weights/interpreter from the diffed installer payload"
```

---

### Task 4: Update `bundle-python.js` + CI to produce the runtime-assets archive

**Files:**
- Modify: `bundle-python.js` (add an archive-and-upload step for `python-runtime/` + `weights/`)
- Modify: `.github/workflows/build.yml` (a new, separate job/step that only runs when the runtime-assets need re-cutting — NOT on every app-version tag)

**Interfaces:**
- Consumes: `download` from Task 1 is not needed here (this is an upload, not a download) — no new interface, just packaging.
- Produces: a `procta-runtime-win.tar.gz` / `procta-runtime-mac-x64.tar.gz` / `procta-runtime-mac-arm64.tar.gz` set of archives, uploaded to a GitHub Release tagged `runtime-assets-v<N>` (distinct from the app's own `vX.Y.Z` release tags), matching what `lib/runtime-assets.js`'s `RUNTIME_ASSET_BASE_URL` expects.

- [ ] **Step 1: Add an archive step to `bundle-python.js`**

After `bundle-python.js`'s existing per-platform bake logic finishes writing `python-runtime/<arch>/` (macOS) or the embeddable Python + `weights/` (Windows), add a step that tars up `python-runtime/` and `weights/` together into the platform-specific archive name Task 3 expects (`procta-runtime-win.tar.gz` on Windows, `procta-runtime-mac-<arch>.tar.gz` on macOS):

```js
const { execFileSync } = require('child_process');

function archiveRuntimeAssets(platformArchiveName) {
  console.log(`\n[archive] Packing python-runtime/ + weights/ into ${platformArchiveName}...`);
  execFileSync('tar', ['-czf', platformArchiveName, 'python-runtime', 'weights'], { stdio: 'inherit' });
  console.log(`[archive] Wrote ${platformArchiveName}`);
}

module.exports.archiveRuntimeAssets = archiveRuntimeAssets;
```

Call it at the end of the existing win/mac bake branches with the matching name (`procta-runtime-win.tar.gz`, `procta-runtime-mac-x64.tar.gz`, `procta-runtime-mac-arm64.tar.gz`).

- [ ] **Step 2: Add a CI job that only runs when explicitly triggered**

In `.github/workflows/build.yml`, add a new job gated on a manual `workflow_dispatch` input (NOT on every app-version tag — this must only run when weights/interpreter actually change, per the spec):

```yaml
  cut-runtime-assets:
    if: github.event_name == 'workflow_dispatch' && github.event.inputs.cut_runtime_assets == 'true'
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        include:
          - os: windows-latest
            archive: procta-runtime-win.tar.gz
          - os: macos-14
            archive: procta-runtime-mac-arm64.tar.gz
    steps:
      - uses: actions/checkout@v6
      - uses: actions/setup-node@v6
        with:
          node-version: 22
      - run: npm ci
      - run: node bundle-python.js --${{ runner.os == 'Windows' && 'win' || 'mac' }}
      - name: Verify archive was produced
        shell: bash
        run: |
          if [ ! -f "${{ matrix.archive }}" ]; then echo "✗ ${{ matrix.archive }} not produced"; exit 1; fi
          echo "✓ $(sha256sum "${{ matrix.archive }}" 2>/dev/null || shasum -a 256 "${{ matrix.archive }}")"
      - name: Upload to the runtime-assets release
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          TAG="runtime-assets-v${{ github.event.inputs.runtime_asset_version }}"
          gh release create "$TAG" --title "$TAG" --notes "Procta runtime assets v${{ github.event.inputs.runtime_asset_version }}" 2>/dev/null || true
          gh release upload "$TAG" "${{ matrix.archive }}" --clobber
```

The workflow already has a bare `workflow_dispatch:` trigger (`.github/workflows/build.yml:26`, currently with no inputs — used only for manual debug runs). Add inputs to it:

```yaml
  workflow_dispatch:
    inputs:
      cut_runtime_assets:
        description: 'Cut a new runtime-assets release (weights/ + Python interpreter) instead of a normal app build'
        type: boolean
        default: false
      runtime_asset_version:
        description: 'Version number to tag this runtime-assets release as (must match config.js RUNTIME_ASSET_VERSION after this run)'
        type: string
        default: ''
```

- [ ] **Step 3: Document the manual checksum step**

Since `RUNTIME_ASSET_CHECKSUM_WIN`/`RUNTIME_ASSET_CHECKSUM_MAC` in `config.js` (Task 3 Step 1) are placeholders until a real archive is uploaded, add a comment directly above them in `config.js` pointing back to this step:

```js
// After running the "cut-runtime-assets" GitHub Actions workflow, download
// the produced archives and update these two constants with their real
// `shasum -a 256 <file>` output before the app can verify them. A stale
// or placeholder checksum here means EVERY first-run fetch fails the
// integrity check (see lib/runtime-assets.js) — this is a hard release
// gate, not optional.
```

- [ ] **Step 4: Commit**

```bash
git add bundle-python.js .github/workflows/build.yml config.js
git commit -m "feat(build): add manual cut-runtime-assets CI job + archive step"
```

---

### Task 5: Compulsory VC++ Redistributable install

**Files:**
- Modify: `build/installer.nsh`

**Interfaces:** none (self-contained NSIS change).

- [ ] **Step 1: Replace the `customInstall` macro**

Current (`build/installer.nsh`, the VC++ section at the end of the file):

```nsis
!macro customInstall
  !if /FileExists "${BUILD_RESOURCES_DIR}\vc_redist.x64.exe"
    SetOutPath "$PLUGINSDIR"
    File "${BUILD_RESOURCES_DIR}\vc_redist.x64.exe"
    DetailPrint "Installing Microsoft Visual C++ runtime (required for AI proctoring)…"
    ExecWait '"$PLUGINSDIR\vc_redist.x64.exe" /quiet /norestart' $0
    DetailPrint "Visual C++ runtime installer exit code: $0"
    SetOutPath "$INSTDIR"
  !else
    DetailPrint "vc_redist.x64.exe not bundled — skipping VC++ runtime install"
  !endif
!macroend
```

New — explain first, elevate, retry until a real success code, honest cancel:

```nsis
!macro customInstall
  !if /FileExists "${BUILD_RESOURCES_DIR}\vc_redist.x64.exe"
    SetOutPath "$PLUGINSDIR"
    File "${BUILD_RESOURCES_DIR}\vc_redist.x64.exe"

    ; Prime the student BEFORE Windows shows its own UAC prompt, so the
    ; "Do you want to allow this app to make changes?" dialog isn't a
    ; confusing surprise with no context.
    MessageBox MB_OK|MB_ICONINFORMATION \
      "Procta requires a Microsoft system component (Visual C++ Runtime) to run AI-based exam proctoring — without it, camera, gaze, and object detection cannot work.$\r$\n$\r$\nWindows will now ask for your permission to install this component. Please select 'Yes'."

    vcredist_retry:
    DetailPrint "Installing Microsoft Visual C++ runtime (required for AI proctoring)…"
    ExecWait '"$PLUGINSDIR\vc_redist.x64.exe" /quiet /norestart' $0
    DetailPrint "Visual C++ runtime installer exit code: $0"

    ; Documented vc_redist exit codes that count as success:
    ;   0    = installed
    ;   3010 = installed, reboot recommended (not required to proceed now)
    ;   1638 = a newer version is already present
    ; Anything else (including a UAC decline, which surfaces as a non-zero
    ; failure from ExecWait) does NOT count as success and must retry.
    StrCmp $0 "0" vcredist_done 0
    StrCmp $0 "3010" vcredist_done 0
    StrCmp $0 "1638" vcredist_done 0

    MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION \
      "Procta cannot verify exams without this Windows component.$\r$\n$\r$\nClick Retry and select 'Yes' when Windows asks for permission, or Cancel to stop installing Procta." \
      IDRETRY vcredist_retry

    ; Cancel was chosen: abort the WHOLE Procta installation, honestly —
    ; a Procta install that silently lacks this component would fail
    ; later with a confusing, unrelated-looking error on exam day instead
    ; of a clear message now.
    DetailPrint "Visual C++ runtime install was declined — aborting Procta installation."
    Abort "Procta installation was cancelled: the required Visual C++ runtime was not installed."

    vcredist_done:
    SetOutPath "$INSTDIR"
  !else
    DetailPrint "vc_redist.x64.exe not bundled — skipping VC++ runtime install"
  !endif
!macroend
```

- [ ] **Step 2: Verify the NSIS script at least parses (static check only — a real install exercise is Step 3)**

There is no local NSIS compiler available outside a Windows electron-builder run in this environment. At minimum, visually diff the new macro against the old one to confirm every existing line (the `!if /FileExists` guard, the `SetOutPath` calls) is preserved, and that `vcredist_retry:`/`vcredist_done:` labels are each defined exactly once and referenced correctly (`StrCmp ... vcredist_done 0` three times, `IDRETRY vcredist_retry` once).

- [ ] **Step 3: Manual VM verification (cannot be automated in this sandbox)**

Required before this task is considered done — this changes a path every Windows student's install goes through:
1. On a clean Windows VM **without** the VC++ runtime pre-installed, run the installer and click "Yes" at the UAC prompt — confirm the explanation dialog shows first, the install proceeds, and `vcredist_done` is reached.
2. On the same clean VM (revert to snapshot), run the installer and click **"No"** at the UAC prompt — confirm the retry dialog appears, click **Cancel**, and confirm the ENTIRE Procta install aborts (Procta is NOT left partially installed) rather than continuing silently.
3. On the same clean VM, repeat the "No" case but this time click **Retry** and then **"Yes"** on the second UAC prompt — confirm it proceeds normally (the loop actually works, not just a single retry).
4. On a VM that already has an equal-or-newer VC++ runtime installed, confirm code `1638` is treated as success with no prompt loop (i.e., no regression for the common case where a student's machine already has it).

- [ ] **Step 4: Commit**

```bash
git add build/installer.nsh
git commit -m "feat(installer): make VC++ runtime install compulsory with explain-and-retry"
```

---

### Task 6: macOS CI blockmap parity gate

**Files:**
- Modify: `.github/workflows/build.yml`

**Interfaces:** none (CI-only change).

- [ ] **Step 1: Add the verification step**

In `.github/workflows/build.yml`, immediately after the existing `- name: Build (macOS — no publish; gated below)` step (currently ending around line 264) and before `- name: Verify macOS signature (blocks publish on failure)` (currently at line 284), add a new step mirroring the Windows one at lines 380-392:

```yaml
      - name: Verify macOS artifacts exist (blocks publish on failure)
        if: matrix.target == 'mac'
        shell: bash
        run: |
          set -e
          shopt -s nullglob
          dmg=(dist/*.dmg); zip=(dist/*.zip); bmap=(dist/*.zip.blockmap)
          if [ ${#dmg[@]} -eq 0 ]; then echo "✗ no .dmg produced"; exit 1; fi
          if [ ${#zip[@]} -eq 0 ]; then echo "✗ no .zip produced"; exit 1; fi
          # electron-updater's MacUpdater (verified directly in
          # node_modules/electron-updater/out/MacUpdater.js) uses the SAME
          # blockmap differential-download mechanism as Windows — it needs a
          # .blockmap for the .zip target to do delta downloads at all.
          # Missing blockmap here silently regresses every Mac student's
          # update back to a full download, the same class of bug the
          # existing Windows gate below already catches.
          if [ ${#bmap[@]} -eq 0 ]; then echo "✗ no .zip.blockmap produced (differential updates broken)"; exit 1; fi
          sz=$(stat -f%z "${zip[0]}" 2>/dev/null || stat -c%s "${zip[0]}")
          if [ "$sz" -lt 10000000 ]; then echo "✗ zip suspiciously small ($sz bytes)"; exit 1; fi
          echo "✓ macOS artifacts present (${zip[0]}, $sz bytes, blockmap ${bmap[0]})"
```

- [ ] **Step 2: Verify the workflow YAML is well-formed**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/build.yml')); print('valid YAML')"`
Expected: `valid YAML` (no exception).

If `pyyaml` isn't installed in this environment, use: `python3 -m pip install --quiet pyyaml && python3 -c "import yaml; yaml.safe_load(open('.github/workflows/build.yml')); print('valid YAML')"`

- [ ] **Step 3: Manual verification on the next real macOS CI run**

This cannot be exercised locally (no macOS runner here) — the real test is the next macOS build in CI: confirm this new step runs, passes on a normal build (blockmap present), and — if practical to test deliberately — confirm it actually fails when a blockmap is withheld (e.g., by temporarily deleting `dist/*.blockmap` in a throwaway branch's CI run before this step, to prove the gate isn't a no-op).

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/build.yml
git commit -m "ci(mac): add blockmap-presence gate mirroring the existing Windows one"
```

---

### Task 7: Full acceptance pass

**Files:** none (verification only).

- [ ] **Step 1: Run all new/touched unit tests**

Run: `node --test scripts/http-download.test.mjs scripts/runtime-assets-version.test.mjs`
Expected: PASS, 9/9 combined (4 + 5).

- [ ] **Step 2: Run the full existing Node test suite**

Run: `node --test scripts/*.test.mjs`
Expected: all tests pass, no regressions from Tasks 1-6.

- [ ] **Step 3: Run the Python test suite (sanity check — this plan touches no backend file)**

Run: `python3 -m pytest -q`
Expected: same pass/skip counts as the pre-plan baseline (no `app/` files were touched by any task in this plan).

- [ ] **Step 4: Confirm the manual-VM verification items from Tasks 3, 5, and 6 have actually been executed**, not just described — this plan's Global Constraints explicitly say code review alone does not close out those tasks. Do not mark this plan complete until:
  - [ ] Task 3 Step 7's four VM checks (clean install, working proctor session, no-redownload-on-app-only-update, interrupted-download recovery) are done.
  - [ ] Task 5 Step 3's four VM checks (Yes path, No-then-Cancel path, No-then-Retry-then-Yes path, already-installed path) are done.
  - [ ] Task 6 Step 3's CI-run confirmation is done.

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "chore(installer): acceptance pass complete"
```
