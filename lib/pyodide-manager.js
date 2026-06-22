// lib/pyodide-manager.js — first-run provisioning of the Pyodide runtime into
// userData (NOT bundled — keeps installer/updates lean). Idempotent; warm
// launches short-circuit on the readiness marker. Served to the worker via the
// procta-pyodide:// scheme (main.js). Files hosted in S3 Mumbai (data residency).
//
// NOTE: require('electron') is intentionally lazy (inside cacheRoot/markerPath)
// so that the pure helpers (cacheFilePath, isManifestSatisfied, MANIFEST) can
// be imported and tested under plain `node --test` without Electron present.

'use strict';

const path = require('path');
const fs = require('fs');
const fsp = fs.promises;
const crypto = require('crypto');

const PYODIDE_VERSION = '0.26.x'; // pin exactly when hosting

const MANIFEST = [
  { name: 'pyodide.asm.js',    sha256: '<fill>' },
  { name: 'pyodide.asm.wasm',  sha256: '<fill>' },
  { name: 'pyodide.js',        sha256: '<fill>' },
  { name: 'python_stdlib.zip', sha256: '<fill>' },
  { name: 'pyodide-lock.json', sha256: '<fill>' },
];

const PYODIDE_BASE_URL = process.env.PYODIDE_BASE_URL
  || 'https://<procta-s3-mumbai-bucket>/pyodide/' + PYODIDE_VERSION + '/';

// Lazy: only called inside Electron process where app is available.
function cacheRoot() {
  const { app } = require('electron');
  return path.join(app.getPath('userData'), 'pyodide-cache');
}

function markerPath() {
  return path.join(cacheRoot(), 'pyodide-ready.json');
}

/**
 * Resolve a file name to an absolute path inside the cache root.
 * Throws on any path-traversal attempt.
 *
 * @param {string} root  Absolute cache directory path.
 * @param {string} name  Bare file name (no slashes, no dots-escape).
 * @returns {string}
 */
function cacheFilePath(root, name) {
  if (!name || name.includes('/') || name.includes('\\') || name.includes('..')) {
    throw new Error('illegal pyodide cache name: ' + name);
  }
  return path.join(root, name);
}

/**
 * Returns true iff every file in MANIFEST is present in the provided set.
 *
 * @param {Set<string>} presentNames  Set of file names already on disk.
 * @returns {boolean}
 */
function isManifestSatisfied(presentNames) {
  return MANIFEST.every(m => presentNames.has(m.name));
}

async function _sha256(file) {
  const buf = await fsp.readFile(file);
  return crypto.createHash('sha256').update(buf).digest('hex');
}

/**
 * Ensure all Pyodide runtime files are present and sha256-verified in the
 * Electron userData cache. Downloads missing/corrupt files from S3 Mumbai.
 * Idempotent: a readiness marker + directory listing short-circuit on warm runs.
 *
 * @param {function({label:string, pct:number}):void} [onProgress]
 * @returns {Promise<true>}
 */
async function ensurePyodide(onProgress) {
  const root = cacheRoot();
  await fsp.mkdir(root, { recursive: true });

  try {
    if (fs.existsSync(markerPath())) {
      const names = new Set(await fsp.readdir(root));
      if (isManifestSatisfied(names)) return true;
    }
  } catch (_) {}

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

module.exports = {
  ensurePyodide,
  cacheRoot,
  cacheFilePath,
  isManifestSatisfied,
  MANIFEST,
  PYODIDE_VERSION,
};
