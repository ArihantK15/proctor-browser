const path = require('path');
const fs = require('fs');
const os = require('os');
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
  return new Promise(resolve => {
    exec(`${python} -c "import cv2"`,
      { encoding: 'utf8', timeout: 10000 }, (err) => {
        resolve(!err);
      });
  });
}

function downloadFile(url, dest) {
  return new Promise((resolve, reject) => {
    const file = fs.createWriteStream(dest);
    const req  = https.get(url, res => {
      if (res.statusCode === 302 || res.statusCode === 301) {
        file.close();
        fs.unlink(dest, () => {});
        downloadFile(res.headers.location, dest)
          .then(resolve).catch(reject);
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
  body{font-family:-apple-system,sans-serif;background:#0d1117;color:#c9d1d9;
       display:flex;align-items:center;justify-content:center;height:100vh;
       flex-direction:column;padding:32px;text-align:center}
  h2{color:#58a6ff;margin-bottom:8px;font-size:18px}
  p{color:#8b949e;font-size:13px;margin-bottom:20px}
  .spinner{width:36px;height:36px;border:3px solid #30363d;border-top-color:#58a6ff;
           border-radius:50%;animation:spin 1s linear infinite;margin:0 auto 20px}
  @keyframes spin{to{transform:rotate(360deg)}}
  .log{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px;
       width:100%;max-height:200px;overflow-y:auto;font-size:11px;
       font-family:monospace;color:#3fb950;text-align:left;line-height:1.6}
</style></head>
<body>
  <div class="spinner"></div>
  <h2>Setting Up AI Exam Environment</h2>
  <p>Installing required components.<br>This only happens once (~3 mins).</p>
  <div class="log" id="log">Starting...\n</div>
  <script>
    const { ipcRenderer } = require('electron');
    ipcRenderer.on('setup-status', (_, msg) => {
      const log = document.getElementById('log');
      log.appendChild(document.createTextNode(msg + '\n'));
      log.scrollTop = log.scrollHeight;
    });
  </script>
</body>
</html>`;

  const tmpHtml = path.join(os.tmpdir(), 'proctor_setup.html');
  fs.writeFileSync(tmpHtml, html);
  setupWindow.loadFile(tmpHtml);
  setupWindow.setMenuBarVisibility(false);
}

function sendSetupStatus(msg) {
  console.log('[Setup]', msg);
  if (setupWindow && !setupWindow.isDestroyed()) {
    setupWindow.webContents.send('setup-status', msg);
  }
}

async function runWindowsSetup() {
  let python = await findPython();

  if (!python) {
    sendSetupStatus('Python not found. Downloading Python 3.11...');
    const installerPath = path.join(os.tmpdir(), 'python_installer.exe');
    try {
      await downloadFile(
        'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe',
        installerPath
      );
      sendSetupStatus('Installing Python 3.11 silently...');
      const r = spawnSync(installerPath,
        ['/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_pip=1'],
        { timeout: 300000 });
      if (r.status === 0) {
        sendSetupStatus('Python installed!');
        resolvedPython = null;
        python = await findPython();
      } else {
        sendSetupStatus('Python install failed. Trying pip packages anyway...');
        python = 'python';
      }
    } catch(e) {
      sendSetupStatus(`Download failed: ${e.message}`);
      python = 'python';
    }
  } else {
    sendSetupStatus(`Python found: ${python}`);
  }

  if (await checkPackagesReady(python)) {
    sendSetupStatus('All AI packages ready!');
    return true;
  }

  sendSetupStatus(`Installing AI packages (~3 mins, one-time only). Do NOT close this window.`);
  const setupStart = Date.now();

  for (let idx = 0; idx < PIP_PACKAGES.length; idx++) {
    const pkg = PIP_PACKAGES[idx];
    const elapsed = Math.round((Date.now() - setupStart) / 1000);
    sendSetupStatus(`  [${idx+1}/${PIP_PACKAGES.length}] Installing ${pkg}... (${elapsed}s elapsed)`);
    try {
      const ok = await new Promise(resolve => {
        exec(`${python} -m pip install ${pkg} --quiet --no-warn-script-location`,
          { encoding: 'utf8', timeout: 120000 }, (err) => resolve(!err));
      });
      sendSetupStatus(ok ? `  ${pkg} done` : `  ${pkg} failed`);
    } catch(e) {
      sendSetupStatus(`  ${pkg} error`);
    }
  }
  const totalSecs = Math.round((Date.now() - setupStart) / 1000);
  sendSetupStatus(`Setup complete in ${totalSecs}s.`);

  const ready = await checkPackagesReady(python);
  sendSetupStatus(ready ?
    'All packages ready! Starting exam...' :
    'Some packages missing — AI features may be limited');
  return ready;
}

async function startPython(sessionId, serverUrl, studentToken, calBiases) {
  pythonShouldRun = true;

  const python = await findPython();
  const script = getScriptPath();

  console.log('[AI] Python:', python);
  console.log('[AI] Script:', script);
  console.log('[AI] Script exists:', fs.existsSync(script));

  if (!python) { console.error('[AI] No Python found — AI proctoring disabled'); return; }
  if (!fs.existsSync(script)) { console.error('[AI] proctor.py not found'); return; }

  const evidenceDir = path.join(app.getPath('userData'), 'evidence');
  try { fs.mkdirSync(evidenceDir, { recursive: true }); } catch(e) { console.warn('[AI] evidence dir creation failed:', e.message); }

  const envVars = {
    ...process.env,
    PROCTOR_SESSION_ID:              sessionId,
    PROCTOR_SERVER_URL:              `${serverUrl}/event`,
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
      setTimeout(() => startPython(sessionId, serverUrl, studentToken, calBiases), 3000);
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
        pythonProcess.kill('SIGTERM');
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
      PROCTOR_SERVER_URL:       `${serverUrl}/event`,
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
        calProcess.kill('SIGTERM');
      }
    } catch (e) { console.error('[CAL] Stop error:', e.message); }
    calProcess = null;
  }
}

function getSetupWindow() { return setupWindow; }
function closeSetupWindow() {
  if (setupWindow && !setupWindow.isDestroyed()) setupWindow.close();
}

module.exports = {
  findPython, getScriptPath, checkPackagesReady,
  runWindowsSetup, createSetupWindow, getSetupWindow, closeSetupWindow,
  startPython, stopPython,
  startCalibration, stopCalibration,
};
