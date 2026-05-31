const path = require('path');
const fs = require('fs');
const os = require('os');
const crypto = require('crypto');
const { spawn, spawnSync, exec } = require('child_process');
const https = require('https');
const { app } = require('electron');
const {
  getPythonCandidates, PIP_PACKAGES,
  SETUP_WIDTH, SETUP_HEIGHT,
} = require('../config');

let resolvedPython = null;
let pythonProcess = null;
let pythonShouldRun = false;
let calProcess = null;
let setupWindow = null;

function _exec(cmd, timeout = 8000) {
  return new Promise(resolve => {
    exec(cmd, { encoding: 'utf8', timeout }, (err, stdout) => {
      resolve(err ? '' : stdout);
    });
  });
}

async function findPython() {
  if (resolvedPython) return resolvedPython;

  const isWin = process.platform === 'win32';
  const candidates = getPythonCandidates();

  for (const p of candidates) {
    try {
      if (p.includes('/') || p.includes('\\')) {
        if (fs.existsSync(p)) {
          resolvedPython = p;
          return p;
        }
      }
    } catch(e) { console.debug('[Python] candidate path check failed:', p, e.message); }
  }

  for (const cmd of (isWin ? ['python','py','python3'] : ['python3','python'])) {
    const output = await _exec(`${cmd} --version`, 3000);
    if (output) {
      resolvedPython = cmd;
      return cmd;
    }
  }

  return null;
}

function getScriptPath() {
  const candidates = [
    path.join(process.resourcesPath || '', 'proctor.py'),
    path.join(__dirname, '..', 'proctor.py'),
    path.join(app.getAppPath(), 'proctor.py'),
  ];
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  return path.join(__dirname, '..', 'proctor.py');
}

async function checkPackagesReady(python) {
  // Full import-readiness probe. MUST stay in sync with PIP_PACKAGES
  // in config.js — every package the proctor needs at runtime is
  // imported once in a single subprocess. Atomic: any missing package
  // → false → the setup flow runs. The four-package check this
  // replaced passed silently when a half-completed install had cv2 +
  // uniface but not vosk/ultralytics, leaving audio + object
  // detection silently disabled with no log signal.
  //
  // Import names differ from pip names in two places:
  //   pip "opencv-python"   → import cv2
  //   pip "websocket-client" → import websocket
  // PIP_PACKAGES "numpy" / "requests" are transitive of cv2 +
  // ultralytics but checked explicitly so a corrupted numpy install
  // (rare but happens after a Python version swap) is caught here.
  return new Promise(resolve => {
    const importLine = [
      'import cv2',
      'import numpy',
      'import requests',
      'import uniface',
      'import onnxruntime',
      'import ultralytics',
      'import sounddevice',
      'import vosk',
      'import python_speech_features',
      'import insightface',
      'import websocket',
      'import psutil',
    ].join('; ');
    // 30s ceiling — `import ultralytics` lazily pulls torch which can
    // take 5-10s on the first invocation after a fresh install.
    // Sequential checks at 10s each would have totalled 120s; this
    // single-subprocess shape is both faster AND atomic.
    const child = spawn(python, ['-c', importLine], { timeout: 30000 });
    let stderr = '';
    if (child.stderr) child.stderr.on('data', d => { stderr += d.toString(); });
    child.on('exit', code => {
      if (code !== 0 && stderr) {
        // Surface which package is missing so the setup window's
        // status text isn't just "checking..." for 30 seconds with
        // no diagnostic. Trims to first ImportError line.
        const firstErr = stderr.split('\n').find(l => l.includes('ImportError') || l.includes('ModuleNotFoundError'));
        if (firstErr) console.warn('[Setup] package check failed:', firstErr.trim());
      }
      resolve(code === 0);
    });
    child.on('error', () => resolve(false));
  });
}

function downloadFile(url, dest) {
  return new Promise((resolve, reject) => {
    const file = fs.createWriteStream(dest);
    const req  = https.get(url, res => {
      if (res.statusCode === 302 || res.statusCode === 301) {
        file.close();
        fs.unlink(dest, () => {});
        downloadFile(res.headers.location, dest).then(resolve, reject);
        return;
      }
      if (res.statusCode < 200 || res.statusCode >= 300) {
        file.close();
        fs.unlink(dest, () => {});
        reject(new Error(`Download failed: HTTP ${res.statusCode}`));
        res.resume();
        return;
      }
      res.pipe(file);
      file.on('finish', () => { file.close(); resolve(); });
    });
    req.on('error', err => {
      fs.unlink(dest, () => {});
      reject(err);
    });
    req.setTimeout(30000, () => {
      req.destroy();
      reject(new Error('Download timeout'));
    });
  });
}

function createSetupWindow() {
  const { BrowserWindow } = require('electron');
  setupWindow = new BrowserWindow({
    width: SETUP_WIDTH, height: SETUP_HEIGHT,
    frame: true, resizable: false, alwaysOnTop: true,
    title: 'Procta',
    icon: path.join(__dirname, '..', 'build', 'icon.png'),
    backgroundColor: '#0d1117',
    webPreferences: {
      preload: path.join(__dirname, '..', 'setup-preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      devTools: false,
      backgroundThrottling: false,
      spellcheck: false,
    }
  });

  const html = `<!DOCTYPE html>
<html>
<head><meta charset="utf-8">
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{font-family:-apple-system,'Segoe UI',sans-serif;background:#0d1117;color:#c9d1d9;
       display:flex;align-items:center;justify-content:center;height:100vh;
       flex-direction:column;padding:32px;text-align:center}
  h2{color:#58a6ff;margin-bottom:8px;font-size:18px}
  p{color:#8b949e;font-size:13px;margin-bottom:20px}
  /* Real progress bar — replaces the previous indeterminate spinner.
     Two-layer track: outer dim rail + inner accent fill driven by
     the progress IPC events from python-manager.js. */
  .bar-wrap{width:100%;max-width:480px;margin:0 auto 12px}
  .bar-track{position:relative;height:10px;background:#1c2230;
             border:1px solid #30363d;border-radius:6px;overflow:hidden}
  .bar-fill{position:absolute;left:0;top:0;bottom:0;width:0%;
            background:linear-gradient(90deg,#3b82f6,#60a5fa);
            transition:width 220ms ease;border-radius:5px}
  /* Indeterminate stripes while bar-fill width is 0 — used at very
     start before the first progress event lands. */
  .bar-fill.indet{width:100%;background:
    repeating-linear-gradient(45deg,#1f2937,#1f2937 8px,#374151 8px,#374151 16px);
    animation:slide 1s linear infinite}
  @keyframes slide{0%{background-position:0 0}100%{background-position:32px 0}}
  .bar-meta{display:flex;justify-content:space-between;margin-top:6px;
            font-size:11px;color:#6b7280;font-family:monospace}
  .current{margin-top:14px;font-size:13px;color:#e2e8f0;font-weight:500;
           min-height:18px}
  .log{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px;
       width:100%;max-height:180px;overflow-y:auto;font-size:11px;
       font-family:monospace;color:#3fb950;text-align:left;line-height:1.6;
       margin-top:14px}
</style></head>
<body>
  <h2 id="title">Procta</h2>
  <p id="subtitle">Preparing the AI exam environment…</p>
  <div class="bar-wrap">
    <div class="bar-track"><div class="bar-fill indet" id="bar"></div></div>
    <div class="bar-meta">
      <span id="step-label">Starting…</span>
      <span id="step-count"></span>
    </div>
  </div>
  <div class="current" id="current"></div>
  <div class="log" id="log">Starting setup…\n</div>
  <script>
    // Use the contextBridge-exposed setupApi, NOT require('electron')
    // — this window runs with contextIsolation:true + nodeIntegration:false
    // so direct require() returns undefined. v2.3.5's progress bar was
    // broken for exactly this reason; v2.3.7 routes through setup-preload.
    const bar = document.getElementById('bar');
    const stepLabel = document.getElementById('step-label');
    const stepCount = document.getElementById('step-count');
    const current = document.getElementById('current');
    const log = document.getElementById('log');
    if (window.setupApi && typeof window.setupApi.onSetupStatus === 'function') {
      window.setupApi.onSetupStatus((msg) => {
        log.appendChild(document.createTextNode(msg + '\\n'));
        log.scrollTop = log.scrollHeight;
        current.textContent = String(msg || '').replace(/^\\s+/, '');
      });
    }
    if (window.setupApi && typeof window.setupApi.onSetupProgress === 'function') {
      window.setupApi.onSetupProgress((p) => {
        // p = { step:0..total, total, label?:string }
        if (!p || typeof p.step !== 'number' || typeof p.total !== 'number') return;
        bar.classList.remove('indet');
        const pct = p.total > 0 ? Math.max(0, Math.min(100, (p.step / p.total) * 100)) : 0;
        bar.style.width = pct.toFixed(1) + '%';
        if (p.label) stepLabel.textContent = p.label;
        stepCount.textContent = p.step + ' / ' + p.total;
      });
    }
    if (window.setupApi && typeof window.setupApi.onSetupMode === 'function') {
      const title = document.getElementById('title');
      const subtitle = document.getElementById('subtitle');
      window.setupApi.onSetupMode((mode) => {
        if (mode === 'recheck') {
          if (title) title.textContent = 'Checking for updates';
          if (subtitle) subtitle.textContent = 'Verifying Procta is ready to go…';
        } else if (mode === 'fresh') {
          if (title) title.textContent = 'Setting up Procta';
          if (subtitle) subtitle.textContent = 'First-time install. Takes a few minutes — leave this window open.';
        }
      });
    }
  </script>
</body>
</html>`;

  // Use a crypto-random suffix so a hostile process on the same
  // machine can't pre-create a symlink at the predictable path
  // (CodeQL js/insecure-temporary-file). `wx` flag fails fast if
  // the file already exists, defeating the TOCTOU race outright.
  const tmpHtml = path.join(
    os.tmpdir(),
    `proctor_setup_${crypto.randomBytes(8).toString('hex')}.html`,
  );
  fs.writeFileSync(tmpHtml, html, { flag: 'wx' });
  setupWindow.loadFile(tmpHtml);
  setupWindow.setMenuBarVisibility(false);
}

// Strip CR/LF/NUL from internally-generated status strings before
// they land in the renderer console — defends against an upstream
// caller accidentally interpolating a tainted value into `msg`
// (CodeQL js/log-injection). The message itself is internal but
// the helper centralises the rule so future callers can't regress.
function _scrubMsg(msg) {
  return String(msg == null ? '' : msg).replace(/[\r\n\x00]/g, ' ');
}

function sendSetupStatus(msg) {
  const safeMsg = _scrubMsg(msg);
  console.log('[Setup]', safeMsg);
  if (setupWindow && !setupWindow.isDestroyed()) {
    setupWindow.webContents.send('setup-status', safeMsg);
  }
}

// Drives the visual progress bar in the setup window. Step counts are
// computed from the same arithmetic used by the install loop so the
// bar reflects actual work done rather than wall-clock estimate.
function sendSetupProgress(step, total, label) {
  if (setupWindow && !setupWindow.isDestroyed()) {
    setupWindow.webContents.send('setup-progress',
      { step, total, label: _scrubMsg(label || '') });
  }
}

// Tells the setup window whether this is a fresh install (`'fresh'`)
// or a re-launch where packages are already there (`'recheck'`). The
// renderer uses this to swap "Setting up Procta" → "Checking for
// updates" and adjust the subtitle accordingly.
function sendSetupMode(mode) {
  if (setupWindow && !setupWindow.isDestroyed()) {
    setupWindow.webContents.send('setup-mode', String(mode || ''));
  }
}

async function runWindowsSetup() {
  // Bigger UX change: bundle ALL 12 pip packages into a single pip
  // call instead of 12 sequential calls. pip resolves once and
  // downloads in parallel internally — typically 3-5x faster on
  // the same network than serial installs, and a fraction of the
  // resolver overhead. The slowest single package (ultralytics →
  // torch ~2GB) dominates either way; bundling means torch downloads
  // concurrently with everything else.
  //
  // Progress bar: 4 phases.
  //   1. Python ready
  //   2. Install AI packages (bundled)
  //   3. Verify imports
  //   4. Download speech models
  const STEP_TOTAL = 4;
  let stepDone = 0;
  const step = (label) => sendSetupProgress(++stepDone, STEP_TOTAL, label);

  sendSetupProgress(0, STEP_TOTAL, 'Finding Python…');
  let python = await findPython();

  if (!python) {
    sendSetupStatus('Python not found. Downloading Python 3.11…');
    sendSetupProgress(0, STEP_TOTAL, 'Downloading Python 3.11…');
    const installerPath = path.join(os.tmpdir(), 'python_installer.exe');
    try {
      await downloadFile(
        'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe',
        installerPath
      );
      sendSetupStatus('Installing Python 3.11 silently…');
      const r = spawnSync(installerPath,
        ['/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_pip=1'],
        { timeout: 300000 });
      if (r.status === 0) {
        sendSetupStatus('Python installed.');
        resolvedPython = null;
        python = await findPython();
      } else {
        sendSetupStatus('Python install failed. Trying pip packages anyway…');
        python = 'python';
      }
    } catch(e) {
      sendSetupStatus(`Download failed: ${e.message}`);
      python = 'python';
    }
  } else {
    sendSetupStatus(`Python found: ${python}`);
  }
  step('Python ready');

  if (await checkPackagesReady(python)) {
    // Already-installed path — flip the window into "Checking for
    // updates" mode and finish fast. The renderer swaps the H2.
    sendSetupMode('recheck');
    sendSetupStatus('All AI packages already installed.');
    sendSetupProgress(STEP_TOTAL, STEP_TOTAL, 'Ready');
    return true;
  }

  sendSetupMode('fresh');
  sendSetupStatus(`Installing AI packages in one pass — torch (~2 GB) is the slow one. Do NOT close this window.`);
  const setupStart = Date.now();

  // Single bundled pip call. --prefer-binary skips slow source builds
  // when a wheel exists; --no-warn-script-location quietens noise.
  const pipArgs = ['-m', 'pip', 'install', '--quiet', '--prefer-binary',
                   '--no-warn-script-location', ...PIP_PACKAGES];

  // Heartbeat so the setup window doesn't look frozen during torch's
  // multi-minute download. Updates the bar's "current" line every
  // 4 s with elapsed wall-clock time. The bar fill stays at the
  // step-2 mark until pip exits — partial progress inside pip isn't
  // observable from outside.
  const heartbeat = setInterval(() => {
    const elapsed = Math.round((Date.now() - setupStart) / 1000);
    sendSetupStatus(`  Installing AI packages — ${elapsed}s elapsed (large packages download in parallel)`);
  }, 4000);

  const pipOk = await new Promise(resolve => {
    // 15-minute ceiling. On a 5 Mbps demo wifi, 2 GB ≈ 50 min — but
    // most demo networks should be much faster. If pip times out the
    // user retries with a hotspot. Better than letting it hang
    // forever with no signal.
    const child = spawn(python, pipArgs, { timeout: 15 * 60 * 1000, stdio: 'ignore' });
    child.on('exit', code => resolve(code === 0));
    child.on('error', () => resolve(false));
  });
  clearInterval(heartbeat);
  const totalSecs = Math.round((Date.now() - setupStart) / 1000);
  sendSetupStatus(pipOk
    ? `Package install done (${totalSecs}s).`
    : `Package install ended with errors (${totalSecs}s). Some AI features may be limited.`);
  step('AI packages installed');

  const ready = await checkPackagesReady(python);
  sendSetupStatus(ready ?
    'All packages verified.' :
    'Some packages still missing — AI features may be limited');
  step('Verified packages');

  // Phase 75 — pull the on-device audio models (Vosk en-IN + hi-IN +
  // Silero VAD) into ./weights/ if missing. Idempotent; fast no-op
  // when already present. Non-fatal: a download hiccup leaves the
  // proctor running with the RMS-only voice path, never blocks the
  // exam start.
  try {
    await downloadAudioModels(python);
  } catch(e) {
    console.warn('[setup] audio model download failed (proctor falls back to RMS-only):', e.message);
  }
  step('Speech models ready');
  return ready;
}

async function downloadAudioModels(python) {
  // Cross-platform via Python (urllib + zipfile from stdlib). Python
  // is guaranteed to exist by the time we get here — runSetup() just
  // pip-installed packages into it. Avoids two pitfalls:
  //   1. Bash isn't on Windows.
  //   2. ELECTRON_RUN_AS_NODE is disabled by our security fuses.
  const candidates = [
    path.join(__dirname, '..', 'scripts', 'download_audio_models.py'),
    path.join(process.resourcesPath || '', 'scripts', 'download_audio_models.py'),
  ];
  const script = candidates.find(p => { try { return fs.existsSync(p); } catch { return false; } });
  if (!script) {
    console.warn('[setup] audio model script not found — skipping');
    return;
  }
  if (!python) {
    console.warn('[setup] no python — skipping audio model download');
    return;
  }
  sendSetupStatus('Downloading speech models (first run only)…');
  await new Promise(resolve => {
    // 5-minute total cap. Fast no-op on a cache hit (no network).
    const child = spawn(python, [script], { timeout: 300000, stdio: 'ignore' });
    child.on('exit', code => {
      if (code === 0) sendSetupStatus('Speech models ready.');
      else sendSetupStatus('Speech models could not download — voice keywords disabled.');
      resolve();
    });
    child.on('error', () => { sendSetupStatus('Speech models skipped.'); resolve(); });
  });
}

async function startPython(sessionId, serverUrl, studentToken, calBiases) {
  if (!pythonShouldRun) return;

  const python = await findPython();
  const script = getScriptPath();

  console.log('[AI] Python:', python);
  console.log('[AI] Script:', script);
  console.log('[AI] Script exists:', fs.existsSync(script));

  if (!python) { console.error('[AI] No Python found — AI proctoring disabled'); return; }
  if (!fs.existsSync(script)) { console.error('[AI] proctor.py not found'); return; }
  if (!pythonShouldRun) return;

  const evidenceDir = path.join(app.getPath('userData'), 'evidence');
  try { fs.mkdirSync(evidenceDir, { recursive: true }); } catch(e) { console.warn('[AI] evidence dir creation failed:', e.message); }

  const envVars = {
    ...process.env,
    PROCTOR_SESSION_ID:              sessionId,
    // Base URL only — proctor.py builds the /api/v1/event POST target
    // itself. The old "${serverUrl}/event" form is still accepted by
    // proctor.py for backward compat, but every URL it derived from
    // that (HEARTBEAT_URL, system-check, ws upgrade) routed wrong
    // because of an rstrip bug. Pass the base and let proctor.py
    // normalise.
    PROCTOR_SERVER_URL:              serverUrl,
    PROCTOR_EVIDENCE_DIR:            evidenceDir,
    PROCTOR_JWT_TOKEN:               studentToken || '',
    PROCTOR_SKIP_ENROLLMENT:         '1',
    PROCTOR_WRONG_PERSON_THRESHOLD:  '0.25',
    PROCTOR_VOICE_THRESHOLD:         '0.035',
  };

  if (calBiases) {
    envVars.PROCTOR_GAZE_YAW_BIAS   = String(calBiases.gaze_yaw);
    envVars.PROCTOR_GAZE_PITCH_BIAS = String(calBiases.gaze_pitch);
    envVars.PROCTOR_HEAD_YAW_BIAS   = String(calBiases.head_yaw);
    envVars.PROCTOR_HEAD_PITCH_BIAS = String(calBiases.head_pitch);
    if (calBiases.gaze_yaw_range != null) {
      envVars.PROCTOR_GAZE_YAW_RANGE   = String(calBiases.gaze_yaw_range);
      envVars.PROCTOR_GAZE_PITCH_RANGE = String(calBiases.gaze_pitch_range);
      envVars.PROCTOR_HEAD_YAW_RANGE   = String(calBiases.head_yaw_range);
      envVars.PROCTOR_HEAD_PITCH_RANGE = String(calBiases.head_pitch_range);
    }
  }

  pythonProcess = spawn(python, [script], { env: envVars });

  pythonProcess.stdout.on('data', d => console.log('[AI]', d.toString().trim()));
  pythonProcess.stderr.on('data', d => console.error('[AI]', d.toString().trim()));
  pythonProcess.on('close', code => {
    console.log('[AI] Exited:', code);
    pythonProcess = null;
    if (code !== 0 && code !== null && pythonShouldRun) {
      console.log('[AI] Unexpected exit — restarting in 3s');
      const restartTimer = setTimeout(() => {
        if (pythonShouldRun) {
          startPython(sessionId, serverUrl, studentToken, calBiases);
        }
      }, 3000);
      restartTimer.unref();
    } else if (code === null && pythonShouldRun) {
      console.log('[AI] Killed by signal — restarting in 5s');
      const restartTimer = setTimeout(() => {
        if (pythonShouldRun) {
          startPython(sessionId, serverUrl, studentToken, calBiases);
        }
      }, 5000);
      restartTimer.unref();
    }
  });
  pythonProcess.on('error', err => console.error('[AI] Spawn error:', err.message));
}

function stopPython() {
  pythonShouldRun = false;
  if (pythonProcess) {
    try {
      if (process.platform === 'win32') {
        spawnSync('taskkill', ['/pid', String(pythonProcess.pid), '/f', '/t'],
          { timeout: 5000 });
      } else {
        // Send SIGINT first so Python can run its finally blocks (cap.release)
        pythonProcess.kill('SIGINT');
        // Fallback: SIGTERM if process still alive after 2s
        const fallbackTimer = setTimeout(() => {
          try { pythonProcess.kill('SIGTERM'); } catch(e) {}
        }, 2000);
        fallbackTimer.unref();
      }
    } catch(e) { console.error('[AI] Stop error:', e.message); }
    pythonProcess = null;
  }
}

async function startCalibration(sessionId, serverUrl, studentToken, mainWindow) {
  stopCalibration();

  const python = await findPython();
  const script = getScriptPath();
  if (!python || !fs.existsSync(script)) {
    console.error('[CAL] Python or script not found — calibration unavailable');
    return;
  }

  calProcess = spawn(python, [script], {
    env: {
      ...process.env,
      PROCTOR_SESSION_ID:       sessionId,
      PROCTOR_SERVER_URL:       serverUrl,
      PROCTOR_JWT_TOKEN:        studentToken || '',
      PROCTOR_CALIBRATION_MODE: '1',
      PROCTOR_SKIP_ENROLLMENT:  '1',
      PROCTOR_HEADLESS:         '1',
    }
  });

  calProcess.stdout.on('data', d => {
    const lines = d.toString().split('\n');
    for (const line of lines) {
      const trimmed = line.trim();
      if (trimmed.startsWith('CAL:')) {
        try {
          const reading = JSON.parse(trimmed.slice(4));
          if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.webContents.send('cal-reading', reading);
          }
        } catch (e) { console.debug('[CAL] malformed JSON:', e.message); }
      } else if (trimmed) {
        console.log('[CAL]', trimmed);
      }
    }
  });
  calProcess.stderr.on('data', d => console.error('[CAL]', d.toString().trim()));
  calProcess.on('close', code => { console.log('[CAL] Exited:', code); calProcess = null; });
  calProcess.on('error', err => console.error('[CAL] Spawn error:', err.message));
  console.log('[CAL] Calibration proctor started');
}

function stopCalibration() {
  if (calProcess) {
    try {
      if (process.platform === 'win32') {
        spawnSync('taskkill', ['/pid', String(calProcess.pid), '/f', '/t'],
          { timeout: 5000 });
      } else {
        calProcess.kill('SIGINT');
        const fallbackTimer = setTimeout(() => {
          try { calProcess.kill('SIGTERM'); } catch(e) {}
        }, 2000);
        fallbackTimer.unref();
      }
    } catch (e) { console.error('[CAL] Stop error:', e.message); }
    calProcess = null;
  }
}

function getSetupWindow() { return setupWindow; }
function closeSetupWindow() {
  if (setupWindow && !setupWindow.isDestroyed()) setupWindow.close();
  setTimeout(() => cleanupTempHtml(), 1000);
}

function cleanupTempHtml() {
  // Temp files from createSetupWindow() use predictable prefix + random suffix
  // in os.tmpdir(). Clean up any leftover files so they don't accumulate.
  try {
    const tmpDir = os.tmpdir();
    const prefix = 'proctor_setup_';
    for (const f of fs.readdirSync(tmpDir)) {
      if (f.startsWith(prefix) && f.endsWith('.html')) {
        const fp = path.join(tmpDir, f);
        try { fs.unlinkSync(fp); } catch(e) { console.debug('[Setup] tmp cleanup:', e.message); }
      }
    }
  } catch(e) { console.debug('[Setup] tmpdir scan:', e.message); }
}

module.exports = {
  findPython, getScriptPath, checkPackagesReady,
  runWindowsSetup, createSetupWindow, getSetupWindow, closeSetupWindow,
  cleanupTempHtml,
  startPython, stopPython,
  startCalibration, stopCalibration,
};
