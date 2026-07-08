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
// sendSetupStatus/sendSetupProgress (python-manager.js:834-849) — passed
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
