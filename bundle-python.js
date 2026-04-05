/**
 * bundle-python.js — run on the build machine BEFORE `npm run build:win`
 *
 *   node bundle-python.js
 *
 * Downloads Python 3.11 embeddable + pip, installs all proctor packages,
 * writes everything to resources/python/ which electron-builder ships
 * inside the .exe via the extraResources entry in package.json.
 * Students get a fully offline Python — no internet, no installer needed.
 */

const https   = require('https');
const fs      = require('fs');
const path    = require('path');
const os      = require('os');
const { execSync, spawnSync } = require('child_process');

const PYTHON_VERSION = '3.11.9';
const PYTHON_ZIP_URL =
  `https://www.python.org/ftp/python/${PYTHON_VERSION}` +
  `/python-${PYTHON_VERSION}-embed-amd64.zip`;
const GET_PIP_URL    = 'https://bootstrap.pypa.io/get-pip.py';

const OUT_DIR        = path.join(__dirname, 'resources', 'python');
const PYTHON_ZIP     = path.join(os.tmpdir(), 'python-embed.zip');
const GET_PIP_SCRIPT = path.join(os.tmpdir(), 'get-pip.py');

const PACKAGES = [
  'opencv-python',
  'mediapipe',
  'ultralytics',
  'sounddevice',
  'numpy',
  'scipy',
  'requests',
  'insightface',
  'onnxruntime',
];

function download(url, dest) {
  return new Promise((resolve, reject) => {
    const file = fs.createWriteStream(dest);
    const follow = (u) => {
      https.get(u, (res) => {
        if (res.statusCode === 301 || res.statusCode === 302) {
          file.close();
          follow(res.headers.location);
          return;
        }
        if (res.statusCode !== 200) {
          reject(new Error(`HTTP ${res.statusCode} — ${u}`));
          return;
        }
        res.pipe(file);
        file.on('finish', () => { file.close(); resolve(); });
      }).on('error', reject);
    };
    follow(url);
  });
}

(async () => {
  if (process.platform !== 'win32') {
    console.log('bundle-python.js is Windows-only. Nothing to do on', process.platform);
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
