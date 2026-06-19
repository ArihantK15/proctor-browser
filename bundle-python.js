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
 *   lib/python-manager.js: aarch64-apple-darwin / x86_64-apple-darwin) and
 *   BAKES the proctor wheels straight into the matching interpreter's
 *   site-packages, so the signed .app needs zero first-launch pip. Which
 *   arch to bake is chosen by PROCTA_BAKE_ARCH (set by the CI matrix); the
 *   x64 leg is cross-built on the Apple-Silicon runner via Rosetta 2.
 *   electron-builder ships python-runtime/ via extraResources. The legacy
 *   first-launch venv path remains only as a dev/no-bundle fallback.
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

// Optional exact-version lockfile. PIP_PACKAGES pins API-breaking majors but
// leaves patch/minor free, so two builds weeks apart can resolve different
// wheels — which changes the runtime blob and defeats differential
// auto-updates. If a committed `requirements-proctor.lock` exists (generate it
// once with `pip freeze` from a known-good bake), pip honours it as a
// constraint so the exact same versions resolve every build. Absent → today's
// behaviour (ranged resolution). Pairs with normalizeForReproducibility below,
// which handles the per-build .pyc/mtime churn that floats even when versions
// don't.
const PIP_LOCK = path.join(__dirname, 'requirements-proctor.lock');
function pipLockArgs() {
  return fs.existsSync(PIP_LOCK) ? ['--constraint', PIP_LOCK] : [];
}

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

// The python-build-standalone runtime for THIS host's native arch. Used as
// the default bake target on a dev machine where no arch is specified.
function hostMacTarget() {
  return process.arch === 'arm64' ? 'aarch64-apple-darwin' : 'x86_64-apple-darwin';
}

// Which python-runtime/<target> to bake the proctor wheels INTO. baking runs
// `<runtime>/bin/python3 -m pip install …`, and that interpreter normally only
// EXECUTES on its native arch — which is why x64 dmgs once needed a real Intel
// host. The CI matrix now passes the wanted arch via PROCTA_BAKE_ARCH so the
// x64 leg can be CROSS-BUILT on an Apple-Silicon runner (macos-14): with
// Rosetta 2 installed, the x86_64 interpreter runs emulated and its pip pulls
// genuine x86_64 wheels (numpy/opencv/onnxruntime/insightface all ship them).
// Unset (dev / single-host) → host arch only; the other runtime ships
// package-less and the app falls back to a first-launch venv there.
function bakeMacTarget() {
  const a = (process.env.PROCTA_BAKE_ARCH || '').toLowerCase();
  if (a === 'arm64' || a === 'aarch64') return 'aarch64-apple-darwin';
  if (a === 'x64' || a === 'x86_64' || a === 'x86-64') return 'x86_64-apple-darwin';
  return hostMacTarget();
}

// True if this interpreter already imports every proctor package — lets a
// re-run skip the (slow) pip install. Interpreter-path based, so it works
// for the macOS bundled python3 AND the Windows embeddable python.exe.
// Mirrors checkPackagesReady in lib/python-manager.js (import names differ
// from pip names in two places).
function packagesReady(py) {
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
  if (packagesReady(py)) {
    console.log('[pip] packages already present — skipping install.');
    return;
  }
  console.log('[pip] Upgrading pip…');
  execSync(`"${py}" -m pip install --upgrade pip`, { stdio: 'inherit' });
  console.log(`[pip] Installing ${PIP_PACKAGES.length} proctor packages (several minutes)…`);
  const constraint = fs.existsSync(PIP_LOCK) ? `--constraint "${PIP_LOCK}" ` : '';
  execSync(
    `"${py}" -m pip install --prefer-binary --no-warn-script-location ${constraint}` +
    PIP_PACKAGES.map(p => `"${p}"`).join(' '),
    { stdio: 'inherit' });
  if (!packagesReady(py)) {
    throw new Error('pip install completed but a package still fails to import — aborting bake.');
  }
  console.log('[pip] ✅ All proctor packages import cleanly.');
}

// ── Reproducible-build normalization ──────────────────────────────────────
// A plain `pip install` bake is NOT byte-reproducible: every .pyc embeds the
// source mtime+size, and freshly written files carry the build's wall-clock
// mtime. Those bytes shift block boundaries in the packaged artifact, so
// electron-updater's differential download can't reuse the previous release's
// blocks and re-pulls the whole ~50-80 MB runtime even for a code-only
// release. Normalizing the baked tree means an unchanged dependency set
// produces an identical blob → differential updates transfer only the changed
// app code (a few MB) instead of the entire interpreter.
//
//   1. Purge every __pycache__/.pyc (the worst offenders — mtime-tagged).
//   2. Recompile with hash-based, mtime-independent .pyc headers
//      (--invalidation-mode unchecked-hash): identical source → identical
//      bytecode regardless of when/where it was built, and CPython trusts the
//      shipped .pyc at runtime without a recompile or a source-mtime check.
//   3. Stamp every file+dir mtime to a fixed epoch so archive headers
//      (zip / NSIS) are deterministic too.
//
// Runs in bundle-python.js (before electron-builder packs + signs), so the
// signature is computed over the already-normalized files. mtime changes don't
// affect code signatures (those seal content, not timestamps).
const SOURCE_DATE_EPOCH = 1577836800; // 2020-01-01T00:00:00Z, fixed.

function _purgePycache(rootDir) {
  const stack = [rootDir];
  while (stack.length) {
    const dir = stack.pop();
    let entries;
    try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { continue; }
    for (const e of entries) {
      const full = path.join(dir, e.name);
      if (e.isDirectory()) {
        if (e.name === '__pycache__') fs.rmSync(full, { recursive: true, force: true });
        else stack.push(full);
      } else if (e.name.endsWith('.pyc') || e.name.endsWith('.pyo')) {
        try { fs.unlinkSync(full); } catch { /* best-effort */ }
      }
    }
  }
}

function _stampMtimes(rootDir, epochSecs) {
  const when = new Date(epochSecs * 1000);
  const stack = [rootDir];
  while (stack.length) {
    const p = stack.pop();
    let st;
    try { st = fs.lstatSync(p); } catch { continue; }
    if (st.isSymbolicLink()) continue; // leave symlinks; their targets get stamped on the walk
    try { fs.utimesSync(p, when, when); } catch { /* read-only / permission — skip */ }
    if (st.isDirectory()) {
      let entries;
      try { entries = fs.readdirSync(p); } catch { continue; }
      for (const name of entries) stack.push(path.join(p, name));
    }
  }
}

function normalizeForReproducibility(rootDir, pyExe) {
  if (!fs.existsSync(rootDir)) return;
  console.log(`[repro] normalizing ${rootDir} for byte-reproducible packaging…`);
  _purgePycache(rootDir);
  const r = spawnSync(
    pyExe,
    ['-m', 'compileall', '-q', '-f', '--invalidation-mode', 'unchecked-hash', rootDir],
    { stdio: 'inherit' });
  if (r.status !== 0) {
    // Recompile failed (e.g. a syntax-incompatible vendored file) — ship
    // source-only .py; CPython recompiles on first import. Make sure no
    // half-written .pyc survive so the blob stays deterministic.
    console.warn('[repro] compileall returned non-zero — shipping source-only .py. Continuing.');
    _purgePycache(rootDir);
  }
  _stampMtimes(rootDir, SOURCE_DATE_EPOCH);
  console.log('[repro] done.');
}

async function runMac() {
  console.log('\n=== Procta — macOS Python runtime bundler ===\n');
  fs.mkdirSync(MAC_RUNTIME_DIR, { recursive: true });

  const bakeTarget = bakeMacTarget();
  const crossArch  = bakeTarget !== hostMacTarget();
  console.log(`[host] running on ${process.arch}; baking proctor packages into ` +
              `${bakeTarget}${crossArch ? ' (cross-arch — requires Rosetta 2)' : ''}\n`);

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

    // Bake wheels into the requested target only. For x86_64 on an arm64
    // host this executes the interpreter under Rosetta 2 (installed by CI).
    if (target === bakeTarget) {
      bakeMacPackages(destDir);
      // Make the baked runtime byte-reproducible so differential auto-updates
      // can skip it on a code-only release. The non-bake target ships the
      // pristine python-build-standalone extraction (already deterministic).
      normalizeForReproducibility(destDir, py);
    } else {
      console.log(`[pip] ${target} != bake target — bundling interpreter only ` +
                  `(its wheels are baked by the ${target} build leg).`);
    }
  }

  console.log('\n✅ macOS runtime(s) bundled under python-runtime/.');
  console.log('   afterPack.js code-signs the interpreter AND the baked');
  console.log('   site-packages native libs; the app uses them with no');
  console.log('   first-launch pip (venv path remains a dev fallback).\n');
}

// Retry wrapper. CI runners intermittently drop the connection mid-stream
// ("Error: socket hang up" / ECONNRESET) on the larger CDN/GitHub-release
// downloads — that took out two macOS bakes in a row AFTER pip had already
// succeeded. Retry with backoff + a fresh dest file so a flaky network drop
// no longer fails the whole build.
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

// Fetch CPython dev headers + import libs (matching PYTHON_VERSION) from the
// python-build-standalone Windows tarball and install them into the embeddable
// interpreter dir so source C-extension builds (insightface) can compile + link.
async function fetchWindowsBuildHeaders(outDir) {
  if (fs.existsSync(path.join(outDir, 'include', 'Python.h'))) {
    console.log('[hdr] dev headers already present — skipping.');
    return;
  }
  const url = pbsUrl('x86_64-pc-windows-msvc');
  const tgz = path.join(os.tmpdir(), 'pbs-win-headers.tar.gz');
  console.log(`[3b/4] Fetching Windows dev headers (python-build-standalone ${PYTHON_VERSION})...`);
  await download(url, tgz);
  const tmp = path.join(os.tmpdir(), 'pbs-win-extract');
  fs.rmSync(tmp, { recursive: true, force: true });
  fs.mkdirSync(tmp, { recursive: true });
  // bsdtar ships with Windows 10 1803+ as tar.exe.
  execSync(`tar -xzf "${tgz}" -C "${tmp}"`, { stdio: 'inherit' });
  for (const sub of ['include', 'libs']) {
    const src = path.join(tmp, 'python', sub);
    const dst = path.join(outDir, sub);
    if (!fs.existsSync(src)) {
      console.error(`[ERROR] python-build-standalone tarball missing python/${sub} — aborting.`);
      process.exit(1);
    }
    fs.cpSync(src, dst, { recursive: true });
    console.log(`      installed ${sub}/ -> ${dst}`);
  }
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

  // 3b. Dev headers + import libs. The python.org embeddable zip ships NO
  // Python.h or python311.lib, so building any C-extension from source fails
  // with "Cannot open include file: 'Python.h'". insightface is source-only
  // and its mesh_core_cython is a C++ extension, so the bake needs them.
  // python-build-standalone's Windows tarball is the SAME CPython 3.11.9 and
  // bundles include/ + libs/ — drop just those next to the embeddable
  // interpreter (sys.prefix) so cl.exe compiles + the linker finds
  // python311.lib. Build-time only; harmless if shipped.
  await fetchWindowsBuildHeaders(OUT_DIR);

  // 4. pip
  console.log('[3/4] Installing pip...');
  await download(GET_PIP_URL, GET_PIP_SCRIPT);
  spawnSync(pyExe, [GET_PIP_SCRIPT, '--no-warn-script-location'],
    { stdio: 'inherit' });

  // 5. Packages. insightface ships SOURCE-ONLY (no wheel on any platform); its
  // setup.py does `import numpy` at build time. On a NORMAL interpreter pip's
  // PEP 517 build isolation installs insightface's build-system.requires (incl.
  // numpy) into the isolated build env, so it builds fine (macOS does). But the
  // Windows EMBEDDABLE python has no ensurepip/venv, so pip's isolated build env
  // comes up broken/empty → `import numpy` fails → "ModuleNotFoundError: No
  // module named 'numpy'" while getting requirements to build insightface's
  // wheel, and the whole bake aborts. Fix: don't rely on build isolation on the
  // embeddable interpreter. Pre-install the build deps (numpy + Cython) into the
  // MAIN env, then install everything with --no-build-isolation so insightface
  // builds against the numpy we just put there. --prefer-binary still uses wheels
  // for every package that ships one (only insightface + the pure-python
  // python_speech_features/srt build from source).
  console.log(`[4/4] Installing ${PIP_PACKAGES.length} AI packages (several minutes)...`);
  spawnSync(pyExe, ['-m', 'pip', 'install', '--upgrade', 'pip', '--no-warn-script-location'],
    { stdio: 'inherit' });
  // Build deps first. Keep numpy in lockstep with the pinned range in
  // PIP_PACKAGES so the pre-install can't drift from what the bake resolves.
  const numpySpec = PIP_PACKAGES.find(p => /^numpy\b/i.test(p)) || 'numpy';
  const buildDeps = [numpySpec, 'Cython', 'setuptools', 'wheel'];
  console.log(`[4/4]   build deps (no-isolation prep): ${buildDeps.join(' ')}`);
  const prep = spawnSync(
    pyExe,
    ['-m', 'pip', 'install', '--prefer-binary', '--no-warn-script-location',
      ...pipLockArgs(), ...buildDeps],
    { stdio: 'inherit' }
  );
  if (prep.status !== 0) {
    console.error('[ERROR] failed to install build deps (numpy/Cython) — aborting Windows bake.');
    process.exit(1);
  }
  spawnSync(
    pyExe,
    ['-m', 'pip', 'install', '--prefer-binary', '--no-build-isolation',
      '--no-warn-script-location', ...pipLockArgs(), ...PIP_PACKAGES],
    { stdio: 'inherit' }
  );

  // Post-bake assertion: a Windows installer that ships without a working
  // import set would silently fall back to a 300-400MB first-launch pip (the
  // very thing this bake removes) or fail proctoring outright. Fail the BUILD
  // here instead so a broken bake can never reach the release channel.
  if (!packagesReady(pyExe)) {
    console.error('[ERROR] pip install completed but a proctor package still ' +
      'fails to import — aborting Windows bake.');
    process.exit(1);
  }
  console.log('[pip] ✅ All proctor packages import cleanly.');

  // Make the baked embeddable runtime byte-reproducible so differential
  // auto-updates can skip it on a code-only release. (pyExe runs natively on
  // the Windows runner; only site-packages .py are recompiled — the stdlib
  // ships zipped in python311.zip and is untouched.)
  normalizeForReproducibility(OUT_DIR, pyExe);

  console.log(`\n✅ Done — ${OUT_DIR}`);
  console.log('   Now run: npm run build:win\n');
})();
