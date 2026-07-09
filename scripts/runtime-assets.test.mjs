// scripts/runtime-assets.test.mjs — coverage for lib/runtime-assets.js, the
// highest-risk module in the runtime-asset-decoupling plan (Task 3) and
// previously the only piece of that plan with zero committed test coverage.
//
// Exercises the REAL flow end-to-end: a real local HTTP server serves a REAL
// tar.gz built with the `tar` CLI, and ensureRuntimeAssets() does a REAL
// SHA-256 verification + REAL extraction onto disk. `electron` and `../config`
// are intercepted via a Module._load hook (same pattern as
// scripts/main-ipc.test.mjs) so this can run outside an Electron process;
// everything downstream of that (download, hashing, tar, fs) is real.
//
//   node --test scripts/runtime-assets.test.mjs
//
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import Module from 'node:module';
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { execFileSync } from 'node:child_process';

const require = Module.createRequire(import.meta.url);
const RUNTIME_ASSETS_PATH = require.resolve('../lib/runtime-assets.js');

// ── Build a real tar.gz fixture (python-runtime/ + weights/) once ──────────
const fixtureRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'rta-fixture-'));
const srcDir = path.join(fixtureRoot, 'src');
fs.mkdirSync(path.join(srcDir, 'python-runtime', 'bin'), { recursive: true });
fs.mkdirSync(path.join(srcDir, 'weights'), { recursive: true });
fs.writeFileSync(path.join(srcDir, 'python-runtime', 'bin', 'python3'), '#!/bin/sh\necho fake-python\n');
fs.writeFileSync(path.join(srcDir, 'weights', 'yolo26n.onnx'), Buffer.from('fake-weights-content'));
const goodArchivePath = path.join(fixtureRoot, 'good.tar.gz');
execFileSync('tar', ['-czf', goodArchivePath, '-C', srcDir, 'python-runtime', 'weights']);
const goodBuf = fs.readFileSync(goodArchivePath);
const goodChecksum = crypto.createHash('sha256').update(goodBuf).digest('hex');
// Corrupted variant — same-ish bytes, but a checksum mismatch against goodChecksum.
const badBuf = Buffer.concat([goodBuf, Buffer.from('corruption')]);

// ── Mock `electron` and `../config` via Module._load ───────────────────────
// Both are read/mutated per-test so each test gets its own sandbox cache dir
// and its own version/checksum without touching the real config.js.
let currentUserDataPath = null;
let currentConfig = null;

const electronMock = {
  app: {
    getPath(name) {
      if (name !== 'userData') throw new Error(`unexpected app.getPath(${name})`);
      return currentUserDataPath;
    },
  },
};

const _origLoad = Module._load;
Module._load = function (request, ...rest) {
  if (request === 'electron') return electronMock;
  if (request === '../config') return currentConfig;
  return _origLoad.call(this, request, ...rest);
};

after(() => {
  Module._load = _origLoad;
  fs.rmSync(fixtureRoot, { recursive: true, force: true });
});

// lib/runtime-assets.js's _archiveNameAndChecksum() only knows about win32 and
// darwin (this app only ships Windows and Mac builds) — it throws on any
// other process.platform. CI's ubuntu-latest runner reports 'linux', so
// without stubbing, every test here would fail on that platform guard rather
// than exercising the download/checksum/extract logic it's meant to cover.
// Stub process.platform to 'darwin' for the duration of each test (restored
// in `finally`) so the real win32/darwin code path runs regardless of the
// host OS running `node --test`.
const REAL_PLATFORM = process.platform;
function stubPlatform(value) {
  Object.defineProperty(process, 'platform', { value, configurable: true });
}
function restorePlatform() {
  Object.defineProperty(process, 'platform', { value: REAL_PLATFORM, configurable: true });
}

// lib/runtime-assets.js destructures RUNTIME_ASSET_VERSION etc. from
// '../config' at module-load time, so each test needs a FRESH require (the
// require cache would otherwise pin the first test's config forever).
function freshRuntimeAssets() {
  delete require.cache[RUNTIME_ASSETS_PATH];
  return require(RUNTIME_ASSETS_PATH);
}

// A fresh sandbox per test: cacheDir() = dirname(userData)/procta-runtime-cache,
// so pointing userData at a fresh tmp dir gives ensureRuntimeAssets its own
// isolated cache directory (and its own sibling for the extraction tmp dir).
function newSandboxUserData() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'rta-sandbox-'));
  currentUserDataPath = path.join(root, 'userData');
}

// Serves `buf` for every request, regardless of path, and counts requests.
function startServer(buf) {
  let requestCount = 0;
  const server = http.createServer((req, res) => {
    requestCount += 1;
    res.writeHead(200);
    res.end(buf);
  });
  return new Promise(resolve => {
    server.listen(0, () => resolve({ server, port: server.address().port, getCount: () => requestCount }));
  });
}

test('ensureRuntimeAssets: happy path downloads, verifies checksum, extracts, and writes the version marker', async () => {
  const { server, port, getCount } = await startServer(goodBuf);
  stubPlatform('darwin');
  try {
    newSandboxUserData();
    currentConfig = {
      RUNTIME_ASSET_VERSION: '7',
      RUNTIME_ASSET_CHECKSUM_WIN: goodChecksum,
      RUNTIME_ASSET_CHECKSUM_MAC_ARM64: goodChecksum, RUNTIME_ASSET_CHECKSUM_MAC_X64: goodChecksum,
      RUNTIME_ASSET_BASE_URL: `http://127.0.0.1:${port}/runtime-assets/`,
    };
    const { ensureRuntimeAssets, cacheDir } = freshRuntimeAssets();

    const statuses = [];
    const result = await ensureRuntimeAssets((s) => statuses.push(s), () => {});

    const dir = cacheDir();
    assert.equal(fs.readFileSync(path.join(dir, '.version'), 'utf8'), '7');
    assert.match(fs.readFileSync(path.join(dir, 'python-runtime', 'bin', 'python3'), 'utf8'), /fake-python/);
    assert.equal(fs.readFileSync(path.join(dir, 'weights', 'yolo26n.onnx'), 'utf8'), 'fake-weights-content');
    assert.equal(result.pythonRuntimeDir, path.join(dir, 'python-runtime'));
    assert.equal(result.weightsDir, path.join(dir, 'weights'));
    assert.equal(getCount(), 1, 'exactly one download request made');
    assert.ok(statuses.length > 0, 'status callback was invoked');

    // No leftover extraction tmp dir left sitting next to cacheDir.
    const siblings = fs.readdirSync(path.dirname(dir));
    assert.ok(!siblings.some(n => n.includes('extract')), 'extraction tmp dir was cleaned up');
  } finally {
    restorePlatform();
    server.close();
  }
});

test('ensureRuntimeAssets: second call is idempotent — cache already current, no re-download', async () => {
  const { server, port, getCount } = await startServer(goodBuf);
  stubPlatform('darwin');
  try {
    newSandboxUserData();
    currentConfig = {
      RUNTIME_ASSET_VERSION: '9',
      RUNTIME_ASSET_CHECKSUM_WIN: goodChecksum,
      RUNTIME_ASSET_CHECKSUM_MAC_ARM64: goodChecksum, RUNTIME_ASSET_CHECKSUM_MAC_X64: goodChecksum,
      RUNTIME_ASSET_BASE_URL: `http://127.0.0.1:${port}/runtime-assets/`,
    };

    const first = freshRuntimeAssets();
    await first.ensureRuntimeAssets(() => {}, () => {});
    assert.equal(getCount(), 1, 'first call fetched once');

    const second = freshRuntimeAssets();
    const result = await second.ensureRuntimeAssets(() => {}, () => {});
    assert.equal(getCount(), 1, 'second call made NO additional network request');
    assert.equal(fs.readFileSync(path.join(second.cacheDir(), '.version'), 'utf8'), '9');
    assert.equal(result.pythonRuntimeDir, path.join(second.cacheDir(), 'python-runtime'));
  } finally {
    restorePlatform();
    server.close();
  }
});

test('ensureRuntimeAssets: checksum mismatch throws, writes no marker, leaves no half-extracted cache', async () => {
  const { server, port } = await startServer(badBuf);
  stubPlatform('darwin');
  try {
    newSandboxUserData();
    currentConfig = {
      RUNTIME_ASSET_VERSION: '11',
      RUNTIME_ASSET_CHECKSUM_WIN: goodChecksum, // expects goodChecksum; server serves badBuf
      RUNTIME_ASSET_CHECKSUM_MAC_ARM64: goodChecksum, RUNTIME_ASSET_CHECKSUM_MAC_X64: goodChecksum,
      RUNTIME_ASSET_BASE_URL: `http://127.0.0.1:${port}/runtime-assets/`,
    };
    const { ensureRuntimeAssets, cacheDir } = freshRuntimeAssets();

    await assert.rejects(
      () => ensureRuntimeAssets(() => {}, () => {}),
      /integrity check/i,
    );

    const dir = cacheDir();
    assert.equal(fs.existsSync(path.join(dir, '.version')), false, 'no version marker written');
    assert.equal(fs.existsSync(path.join(dir, 'python-runtime')), false, 'no half-extracted python-runtime');
    assert.equal(fs.existsSync(path.join(dir, 'weights')), false, 'no half-extracted weights');
  } finally {
    restorePlatform();
    server.close();
  }
});
