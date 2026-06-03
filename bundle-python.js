/**
 * bundle-python.js — run on the build machine BEFORE packaging.
 *
 *   node bundle-python.js
 *
 * Windows: downloads Python 3.11 embeddable + pip, installs proctor
 *   packages into resources/python/ (legacy offline path).
 *
 * macOS: downloads a relocatable python-build-standalone interpreter for
 *   BOTH architectures into python-runtime/<arch>/ (arch dir names match
 *   lib/python-manager.js: aarch64-apple-darwin / x86_64-apple-darwin).
 *   electron-builder ships python-runtime/ via extraResources. At first
 *   launch the app creates a per-user venv from this interpreter and pip-
 *   installs the proctor packages into it (PEP 668-safe; never writes
 *   inside the signed .app). No silent system install, no admin prompt.
 *
 * CI (.github/workflows/build.yml) runs this before `electron-builder
 * --mac`. In dev, python-runtime/ ships empty (.gitkeep) and the app
 * falls back to system python3 — see python-manager.getBundledPython().
 */

const https   = require('https');
const fs      = require('fs');
const path    = require('path');
const os      = require('os');
const { execSync, spawnSync } = require('child_process');

// Single source of truth for the proctor package set. config.js#PIP_PACKAGES
// is the canonical list (asserted ⊇ requirements-proctor.txt by
// scripts/electron-smoke-test.mjs). Importing it here — rather than keeping a
// second hand-maintained array — is what stops the build-time installer from
// drifting (the bug where Windows shipped without uniface/vosk/etc. and fell
// into a nondeterministic first-launch pip). config.js requires electron only
// lazily, so this require is safe in a plain-Node build script.
const { PIP_PACKAGES } = require('./config');

const PYTHON_VERSION = '3.11.9';
const PYTHON_ZIP_URL =
  `https://www.python.org/ftp/python/${PYTHON_VERSION}` +
  `/python-${PYTHON_VERSION}-embed-amd64.zip`;
const GET_PIP_URL    = 'https://bootstrap.pypa.io/get-pip.py';

const OUT_DIR        = path.join(__dirname, 'resources', 'python');
const PYTHON_ZIP     = path.join(os.tmpdir(), 'python-embed.zip');
const GET_PIP_SCRIPT = path.join(os.tmpdir(), 'get-pip.py');

// ── macOS relocatable runtime (python-build-standalone) ───────────
// Pinned release tag + matching CPython build. install_only tarballs
// unpack to a top-level `python/` dir containing bin/python3.
const PBS_RELEASE = '20240814';
const MAC_RUNTIME_DIR = path.join(__dirname, 'python-runtime');
const MAC_TARGETS = [
  'aarch64-apple-darwin',
  'x86_64-apple-darwin',
];
const pbsUrl = (target) =>
  `https://github.com/astral-sh/python-build-standalone/releases/download/` +
  `${PBS_RELEASE}/cpython-${PYTHON_VERSION}+${PBS_RELEASE}-${target}-install_only.tar.gz`;

// Which python-runtime/<target> matches the arch we're building ON. A
// python-build-standalone interpreter only EXECUTES on its native arch, so
// we can only pip-install into the matching one. The per-arch CI matrix
// (arm64 on macos-14, x64 on macos-13) runs this once per arch; each runner
// bakes its own wheels natively and ships only its own dmg. On a single-host
// dev build the non-matching runtime is bundled package-less and the app
// falls back to a first-launch venv there.
function hostMacTarget() {
  return process.arch === 'arm64' ? 'aarch64-apple-darwin' : 'x86_64-apple-darwin';
}

// True if this interpreter already imports every proctor package — lets a
// re-run skip the (slow) pip install. Mirrors checkPackagesReady in
// lib/python-manager.js (import names differ from pip names in two places).
function macPackagesReady(py) {
  const importLine = [
    'import cv2', 'import numpy', 'import requests', 'import uniface',
    'import onnxruntime', 'import sounddevice', 'import vosk',
    'import python_speech_features', 'import insightface',
    'import websocket', 'import psutil',
  ].join('; ');
  const r = spawnSync(py, ['-c', importLine], { stdio: 'ignore' });
  return r.status === 0;
}

// Bake the proctor wheels straight into the bundled interpreter's
// site-packages (NOT a venv) so the shipped .app needs zero first-launch
// pip. --prefer-binary avoids slow source builds when a wheel exists.
function bakeMacPackages(destDir) {
  const py = path.join(destDir, 'bin', 'python3');
  if (macPackagesReady(py)) {
    console.log('[pip] packages already present — skipping install.');
    return;
  }
  console.log('[pip] Upgrading pip…');
  execSync(`"${py}" -m pip install --upgrade pip`, { stdio: 'inherit' });
  console.log(`[pip] Installing ${PIP_PACKAGES.length} proctor packages (several minutes)…`);
  execSync(
    `"${py}" -m pip install --prefer-binary --no-warn-script-location ` +
    PIP_PACKAGES.map(p => `"${p}"`).join(' '),
    { stdio: 'inherit' });
  if (!macPackagesReady(py)) {
    throw new Error('pip install completed but a package still fails to import — aborting bake.');
  }
  console.log('[pip] ✅ All proctor packages import cleanly.');
}

async function runMac() {
  console.log('\n=== Procta — macOS Python runtime bundler ===\n');
  fs.mkdirSync(MAC_RUNTIME_DIR, { recursive: true });

  const hostTarget = hostMacTarget();
  console.log(`[host] building on ${process.arch} → will bake packages into ${hostTarget}\n`);

  for (const target of MAC_TARGETS) {
    const destDir = path.join(MAC_RUNTIME_DIR, target);
    const py = path.join(destDir, 'bin', 'python3');
    if (fs.existsSync(py)) {
      console.log(`[skip] ${target} interpreter already present.`);
    } else {
      const tgz = path.join(os.tmpdir(), `pbs-${target}.tar.gz`);
      console.log(`[1/3] Downloading ${target}…`);
      await download(pbsUrl(target), tgz);

      console.log('[2/3] Extracting…');
      // tarball contains a top-level `python/` dir → extract to a temp,
      // then relocate its contents to python-runtime/<target>/.
      const tmpExtract = path.join(os.tmpdir(), `pbs-extract-${target}`);
      fs.rmSync(tmpExtract, { recursive: true, force: true });
      fs.mkdirSync(tmpExtract, { recursive: true });
      execSync(`tar -xzf "${tgz}" -C "${tmpExtract}"`, { stdio: 'inherit' });

      const extractedPython = path.join(tmpExtract, 'python');
      if (!fs.existsSync(path.join(extractedPython, 'bin', 'python3'))) {
        throw new Error(`Unexpected archive layout for ${target} — no python/bin/python3`);
      }
      fs.rmSync(destDir, { recursive: true, force: true });
      fs.renameSync(extractedPython, destDir);

      console.log(`[3/3] ${target} → ${destDir}`);
      fs.rmSync(tmpExtract, { recursive: true, force: true });
      try { fs.unlinkSync(tgz); } catch { /* best-effort cleanup */ }
    }

    // Only the matching-arch interpreter can be executed to run pip.
    if (target === hostTarget) {
      bakeMacPackages(destDir);
    } else {
      console.log(`[pip] ${target} != host arch — bundling interpreter only ` +
                  `(its wheels are baked by the ${target} CI runner).`);
    }
  }

  console.log('\n✅ macOS runtime(s) bundled under python-runtime/.');
  console.log('   afterPack.js code-signs the interpreter AND the baked');
  console.log('   site-packages native libs; the app uses them with no');
  console.log('   first-launch pip (venv path remains a dev fallback).\n');
}

// Build-time install set == config.js#PIP_PACKAGES (see require above).
// ultralytics/torch were already dropped (YOLOv8n runs on onnxruntime from a
// bundled weights/yolov8n.onnx); mediapipe/scipy are likewise not imported by
// any .py and are no longer installed.
const PACKAGES = PIP_PACKAGES;

function download(url, dest) {
  // Only opens the destination stream on the final 200 — GitHub release
  // URLs redirect (302 → objects.githubusercontent.com), so opening the
  // file up front and closing it on the first redirect (the old bug)
  // left an empty file. Handles the full redirect set + a depth cap.
  return new Promise((resolve, reject) => {
    const follow = (u, depth = 0) => {
      if (depth > 8) { reject(new Error(`Too many redirects — ${url}`)); return; }
      https.get(u, (res) => {
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

(async () => {
  if (process.platform === 'darwin') {
    await runMac();
    process.exit(0);
  }
  if (process.platform !== 'win32') {
    console.log('bundle-python.js supports win32 + darwin. Nothing to do on', process.platform);
    process.exit(0);
  }

  console.log('\n=== AI Proctor — Windows Python bundler ===\n');

  if (fs.existsSync(path.join(OUT_DIR, 'python.exe'))) {
    console.log(`[Skip] ${OUT_DIR}/python.exe already exists.`);
    console.log('       Delete resources/python/ to rebuild.\n');
    process.exit(0);
  }

  fs.mkdirSync(OUT_DIR, { recursive: true });

  // 1. Download
  console.log(`[1/4] Downloading Python ${PYTHON_VERSION} embeddable...`);
  await download(PYTHON_ZIP_URL, PYTHON_ZIP);

  // 2. Unzip (PowerShell is always available on Win10+)
  console.log('[2/4] Extracting...');
  execSync(
    `powershell -Command "Expand-Archive -Path '${PYTHON_ZIP}' -DestinationPath '${OUT_DIR}' -Force"`,
    { stdio: 'inherit' }
  );

  const pyExe = path.join(OUT_DIR, 'python.exe');
  if (!fs.existsSync(pyExe)) {
    console.error('[ERROR] python.exe not found after extraction — aborting.');
    process.exit(1);
  }

  // 3. Enable site-packages (embeddable zip has it disabled by default)
  const pthFiles = fs.readdirSync(OUT_DIR).filter(f => f.endsWith('._pth'));
  for (const f of pthFiles) {
    const p = path.join(OUT_DIR, f);
    let c   = fs.readFileSync(p, 'utf8');
    c = c.replace(/^#\s*import site/m, 'import site');
    if (!/^import site/m.test(c)) c += '\nimport site\n';
    if (!c.includes('Lib\\site-packages')) c += 'Lib\\site-packages\n';
    fs.writeFileSync(p, c);
    console.log(`      Patched ${f}`);
  }

  // 4. pip
  console.log('[3/4] Installing pip...');
  await download(GET_PIP_URL, GET_PIP_SCRIPT);
  spawnSync(pyExe, [GET_PIP_SCRIPT, '--no-warn-script-location'],
    { stdio: 'inherit' });

  // 5. Packages
  console.log('[4/4] Installing AI packages (several minutes)...');
  for (const pkg of PACKAGES) {
    process.stdout.write(`  ${pkg}... `);
    const r = spawnSync(
      pyExe,
      ['-m', 'pip', 'install', pkg, '--quiet', '--no-warn-script-location'],
      { stdio: 'inherit' }
    );
    console.log(r.status === 0 ? '✅' : '⚠️ failed');
  }

  console.log(`\n✅ Done — ${OUT_DIR}`);
  console.log('   Now run: npm run build:win\n');
})();
