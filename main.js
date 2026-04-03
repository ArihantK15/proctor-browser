const {
  app, BrowserWindow, ipcMain,
  globalShortcut, powerSaveBlocker
} = require('electron');
const path    = require('path');
const { spawn, spawnSync } = require('child_process');
const os      = require('os');
const fs      = require('fs');
const https   = require('https');

const SERVER_URL = 'https://aiproc.ngrok.dev';
const ADMIN_CODE = 'EXIT2026';

let mainWindow    = null;
let setupWindow   = null;
let pythonProcess = null;
let powerBlockId  = null;
let pollInterval  = null;
let isKiosk       = true;
let resolvedPython = null;

// ── PYTHON FINDER ─────────────────────────────────────────────────
function findPython() {
  if (resolvedPython) return resolvedPython;

  const isWin = process.platform === 'win32';
  const candidates = isWin ? [
    path.join(os.homedir(),'AppData','Local','Programs','Python','Python311','python.exe'),
    path.join(os.homedir(),'AppData','Local','Programs','Python','Python312','python.exe'),
    path.join(os.homedir(),'AppData','Local','Programs','Python','Python310','python.exe'),
    'C:\\Python311\\python.exe',
    'C:\\Python312\\python.exe',
    'C:\\Python310\\python.exe',
    path.join(os.homedir(),'AppData','Local','Microsoft','WindowsApps','python3.exe'),
  ] : [
    path.join(__dirname, 'venv', 'bin', 'python3'),
    path.join(process.resourcesPath || '', 'venv', 'bin', 'python3'),
    '/usr/local/bin/python3',
    '/usr/bin/python3',
  ];

  for (const p of candidates) {
    try {
      if (p.includes('/') || p.includes('\\')) {
        if (fs.existsSync(p)) {
          resolvedPython = p;
          return p;
        }
      }
    } catch(e) {}
  }

  // Try system commands
  for (const cmd of (isWin ? ['python','py','python3'] : ['python3','python'])) {
    try {
      const r = spawnSync(cmd, ['--version'],
        { encoding:'utf8', timeout:3000 });
      if (r.status === 0) {
        resolvedPython = cmd;
        return cmd;
      }
    } catch(e) {}
  }

  return null;
}

function getScriptPath() {
  const candidates = [
    path.join(process.resourcesPath || '', 'proctor.py'),
    path.join(__dirname, 'proctor.py'),
    path.join(app.getAppPath(), 'proctor.py'),
  ];
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  return path.join(__dirname, 'proctor.py');
}

// ── CHECK IF PACKAGES READY ───────────────────────────────────────
function checkPackagesReady(python) {
  try {
    const r = spawnSync(python,
      ['-c', 'import cv2, mediapipe, ultralytics, sounddevice'],
      { encoding:'utf8', timeout:10000 });
    return r.status === 0;
  } catch(e) { return false; }
}

// ── DOWNLOAD FILE ─────────────────────────────────────────────────
function downloadFile(url, dest) {
  return new Promise((resolve, reject) => {
    const file = fs.createWriteStream(dest);
    const req  = https.get(url, res => {
      if (res.statusCode === 302 || res.statusCode === 301) {
        file.close();
        downloadFile(res.headers.location, dest)
          .then(resolve).catch(reject);
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

// ── SETUP WINDOW ──────────────────────────────────────────────────
function createSetupWindow() {
  setupWindow = new BrowserWindow({
    width:    520,
    height:   420,
    frame:    true,
    resizable: false,
    alwaysOnTop: true,
    webPreferences: {
      nodeIntegration:  true,
      contextIsolation: false,
    }
  });

  const html = `<!DOCTYPE html>
<html>
<head>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family:-apple-system,sans-serif; background:#0d1117;
         color:#c9d1d9; display:flex; align-items:center;
         justify-content:center; height:100vh;
         flex-direction:column; padding:32px; text-align:center; }
  h2  { color:#58a6ff; margin-bottom:8px; font-size:18px; }
  p   { color:#8b949e; font-size:13px; margin-bottom:20px; }
  .spinner { width:36px; height:36px; border:3px solid #30363d;
             border-top-color:#58a6ff; border-radius:50%;
             animation:spin 1s linear infinite; margin:0 auto 20px; }
  @keyframes spin { to { transform:rotate(360deg); } }
  .log { background:#161b22; border:1px solid #30363d;
         border-radius:8px; padding:12px; width:100%;
         max-height:200px; overflow-y:auto;
         font-size:11px; font-family:monospace;
         color:#3fb950; text-align:left; line-height:1.6; }
</style>
</head>
<body>
  <div class="spinner"></div>
  <h2>Setting Up AI Exam Environment</h2>
  <p>Installing required components.<br>This only happens once (~3 mins).</p>
  <div class="log" id="log">Starting...\n</div>
  <script>
    const { ipcRenderer } = require('electron');
    ipcRenderer.on('setup-status', (_, msg) => {
      const log = document.getElementById('log');
      log.innerHTML += msg + '\n';
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
  let python = findPython();

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
        sendSetupStatus('✅ Python installed!');
        resolvedPython = null; // reset cache
        python = findPython();
      } else {
        sendSetupStatus('⚠️ Python install failed. Trying pip packages anyway...');
        python = 'python';
      }
    } catch(e) {
      sendSetupStatus(`⚠️ Download failed: ${e.message}`);
      python = 'python';
    }
  } else {
    sendSetupStatus(`✅ Python found: ${python}`);
  }

  // Check if packages already installed
  if (checkPackagesReady(python)) {
    sendSetupStatus('✅ All AI packages ready!');
    return true;
  }

  // Install packages
  sendSetupStatus('Installing AI packages (this takes 2-3 mins)...');
  const packages = [
    'opencv-python',
    'mediapipe',
    'ultralytics',
    'sounddevice',
    'numpy',
    'scipy',
    'requests',
  ];

  for (const pkg of packages) {
    sendSetupStatus(`  Installing ${pkg}...`);
    try {
      const r = spawnSync(python,
        ['-m', 'pip', 'install', pkg,
         '--quiet', '--no-warn-script-location'],
        { encoding:'utf8', timeout:120000 });
      sendSetupStatus(r.status === 0 ? `  ✅ ${pkg}` : `  ⚠️ ${pkg} failed`);
    } catch(e) {
      sendSetupStatus(`  ⚠️ ${pkg} error`);
    }
  }

  const ready = checkPackagesReady(python);
  sendSetupStatus(ready ?
    '✅ All packages ready! Starting exam...' :
    '⚠️ Some packages missing — AI features may be limited');
  return ready;
}

// ── START/STOP PYTHON ─────────────────────────────────────────────
function startPython(sessionId) {
  const python = findPython();
  const script = getScriptPath();

  console.log('[AI] Python:', python);
  console.log('[AI] Script:', script);
  console.log('[AI] Script exists:', fs.existsSync(script));

  if (!python) {
    console.error('[AI] No Python found — AI proctoring disabled');
    return;
  }
  if (!fs.existsSync(script)) {
    console.error('[AI] proctor.py not found');
    return;
  }

  const evidenceDir = path.join(app.getPath('userData'), 'evidence');
  try { fs.mkdirSync(evidenceDir, { recursive: true }); } catch(e) {}

  pythonProcess = spawn(python, [script], {
    env: {
      ...process.env,
      PROCTOR_SESSION_ID:   sessionId,
      PROCTOR_SERVER_URL:   `${SERVER_URL}/event`,
      PROCTOR_EVIDENCE_DIR: evidenceDir,
    }
  });

  pythonProcess.stdout.on('data', d =>
    console.log('[AI]', d.toString().trim()));
  pythonProcess.stderr.on('data', d =>
    console.error('[AI]', d.toString().trim()));
  pythonProcess.on('close', code =>
    console.log('[AI] Exited:', code));
  pythonProcess.on('error', err =>
    console.error('[AI] Spawn error:', err.message));
}

function stopPython() {
  if (pythonProcess) {
    pythonProcess.kill('SIGTERM');
    pythonProcess = null;
  }
}

// ── POLLING ───────────────────────────────────────────────────────
function startPolling(sessionId) {
  let lastEventId = 0;
  pollInterval = setInterval(async () => {
    try {
      const r    = await fetch(`${SERVER_URL}/events/${sessionId}`);
      const data = await r.json();
      const newV = (data.events || []).filter(e =>
        e.id > lastEventId &&
        (e.severity === 'high' || e.severity === 'medium') &&
        !['screenshot','enrollment','started',
          'submitted','resumed','complete',
          'session_ended','answer_selected'].some(x =>
          e.type.includes(x))
      );
      if (newV.length > 0) {
        lastEventId = Math.max(...newV.map(e => e.id));
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.webContents.send('violation-detected', {
            type:     newV[0].type,
            details:  newV[0].details,
            severity: newV[0].severity,
          });
        }
      }
    } catch(e) { console.error('[Poll]', e.message); }
  }, 2000);
}

function stopPolling() {
  if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
}

// ── MAIN WINDOW ───────────────────────────────────────────────────
function createWindow() {
  mainWindow = new BrowserWindow({
    fullscreen:      isKiosk,
    kiosk:           isKiosk,
    alwaysOnTop:     isKiosk,
    resizable:       !isKiosk,
    movable:         !isKiosk,
    minimizable:     !isKiosk,
    maximizable:     !isKiosk,
    closable:        !isKiosk,
    frame:           !isKiosk,
    width:           1280,
    height:          900,
    autoHideMenuBar: true,
    webPreferences: {
      preload:          path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration:  false,
      devTools:         !isKiosk,
    }
  });

  mainWindow.loadFile(
    path.join(__dirname, 'renderer', 'index.html'));

  if (!isKiosk) mainWindow.webContents.openDevTools();

  mainWindow.webContents.on('will-navigate', (e, url) => {
    if (!url.startsWith(SERVER_URL) && !url.startsWith('file://'))
      e.preventDefault();
  });
  mainWindow.webContents.setWindowOpenHandler(
    () => ({ action: 'deny' }));
  mainWindow.webContents.on('devtools-opened', () => {
    if (isKiosk) mainWindow.webContents.closeDevTools();
  });

  if (isKiosk) {
    mainWindow.on('blur',  () => mainWindow.focus());
    mainWindow.on('close', e  => e.preventDefault());
    powerBlockId = powerSaveBlocker.start('prevent-display-sleep');
  }
}

// ── APP START ─────────────────────────────────────────────────────
app.whenReady().then(async () => {
  if (isKiosk) {
    globalShortcut.registerAll([
      'Alt+F4','Cmd+Q','Cmd+W','Cmd+M','Cmd+H',
      'Cmd+Tab','Alt+Tab','F11','F12','Escape',
      'Cmd+Shift+I','Ctrl+Shift+I',
      'Cmd+R','Ctrl+R','F5',
      'PrintScreen','Cmd+Shift+3','Cmd+Shift+4',
      'Cmd+C','Cmd+V','Cmd+X',
      'Ctrl+C','Ctrl+V','Ctrl+X',
    ], () => false);
  }

  // Windows: auto-setup Python if needed
  if (process.platform === 'win32') {
    const python = findPython();
    const packagesOk = python && checkPackagesReady(python);

    if (!packagesOk) {
      createSetupWindow();
      try {
        await runWindowsSetup();
        await new Promise(r => setTimeout(r, 2000));
      } catch(e) {
        console.error('[Setup] Failed:', e);
      }
      if (setupWindow && !setupWindow.isDestroyed()) {
        setupWindow.close();
      }
    } else {
      console.log('[Setup] Python ready, skipping setup');
    }
  }

  createWindow();
});

app.on('before-quit', () => {
  stopPython();
  stopPolling();
  if (powerBlockId !== null) powerSaveBlocker.stop(powerBlockId);
});

app.on('window-all-closed', () => {
  if (!isKiosk) {
    stopPython();
    stopPolling();
    app.quit();
  }
});

// ── IPC ───────────────────────────────────────────────────────────
ipcMain.handle('validate-student', async (_, roll) => {
  const r = await fetch(`${SERVER_URL}/api/validate-student`, {
    method:  'POST',
    headers: {'Content-Type': 'application/json'},
    body:    JSON.stringify({roll_number: roll})
  });
  if (!r.ok) { const e = await r.json(); throw new Error(e.detail); }
  return r.json();
});

ipcMain.handle('get-questions', async () => {
  const r = await fetch(`${SERVER_URL}/api/questions`);
  if (!r.ok) throw new Error('Could not load questions');
  return r.json();
});

ipcMain.handle('log-event', async (_, data) => {
  try {
    await fetch(`${SERVER_URL}/event`, {
      method:  'POST',
      headers: {'Content-Type': 'application/json'},
      body:    JSON.stringify(data)
    });
  } catch(e) { console.error('[log-event]', e.message); }
});

ipcMain.handle('submit-exam', async (_, data) => {
  const r = await fetch(`${SERVER_URL}/api/submit-exam`, {
    method:  'POST',
    headers: {'Content-Type': 'application/json'},
    body:    JSON.stringify(data)
  });
  if (!r.ok) throw new Error('Submission failed');
  return r.json();
});

ipcMain.handle('get-events', async (_, sessionId) => {
  const r = await fetch(`${SERVER_URL}/events/${sessionId}`);
  return r.json();
});

ipcMain.handle('start-proctor', (_, { sessionId }) => {
  startPython(sessionId);
  return { started: true };
});

ipcMain.handle('start-polling', (_, { sessionId }) => {
  startPolling(sessionId);
  return { polling: true };
});

ipcMain.handle('stop-proctor', () => {
  stopPython();
  stopPolling();
  return { stopped: true };
});

ipcMain.handle('admin-exit', (_, code) => {
  if (code === ADMIN_CODE || code === 'AUTO_CLOSE') {
    isKiosk = false;
    stopPython();
    stopPolling();
    globalShortcut.unregisterAll();
    if (powerBlockId !== null) powerSaveBlocker.stop(powerBlockId);
    app.quit();
    return { success: true };
  }
  return { success: false };
});
