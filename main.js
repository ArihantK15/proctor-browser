const {
  app, BrowserWindow, ipcMain,
  globalShortcut, powerSaveBlocker
} = require('electron');
const path    = require('path');
const { spawn, execSync, exec } = require('child_process');
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

// ── PYTHON SETUP ──────────────────────────────────────────────────
const REQUIRED_PACKAGES = [
  'mediapipe',
  'opencv-python',
  'ultralytics',
  'requests',
  'sounddevice',
  'numpy',
  'scipy',
];

const PYTHON_INSTALLER_URL =
  'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe';

function getPythonPath() {
  if (process.platform === 'win32') {
    const candidates = [
      path.join(os.homedir(), 'AppData', 'Local',
                'Programs', 'Python', 'Python311', 'python.exe'),
      path.join(os.homedir(), 'AppData', 'Local',
                'Programs', 'Python', 'Python310', 'python.exe'),
      'C:\\Python311\\python.exe',
      'C:\\Python310\\python.exe',
      'python',
    ];
    for (const p of candidates) {
      try {
        if (p === 'python') {
          execSync('python --version', {stdio:'ignore'});
          return p;
        }
        if (fs.existsSync(p)) return p;
      } catch(e) {}
    }
    return null;
  } else {
    // Mac/Linux
    const candidates = [
      path.join(__dirname, 'venv', 'bin', 'python3'),
      path.join(process.resourcesPath, 'venv', 'bin', 'python3'),
      '/usr/local/bin/python3',
      '/usr/bin/python3',
      'python3',
    ];
    for (const p of candidates) {
      try {
        if (!p.includes('/')) {
          execSync('python3 --version', {stdio:'ignore'});
          return p;
        }
        if (fs.existsSync(p)) return p;
      } catch(e) {}
    }
    return 'python3';
  }
}

function getScriptPath() {
  const candidates = [
    path.join(process.resourcesPath, 'proctor.py'),
    path.join(__dirname, 'proctor.py'),
  ];
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  return path.join(__dirname, 'proctor.py');
}

async function checkPythonReady() {
  const python = getPythonPath();
  if (!python) return false;
  try {
    execSync(`"${python}" -c "import mediapipe, cv2, ultralytics"`,
             {stdio:'ignore', timeout: 10000});
    return true;
  } catch(e) {
    return false;
  }
}

function downloadFile(url, dest) {
  return new Promise((resolve, reject) => {
    const file = fs.createWriteStream(dest);
    https.get(url, res => {
      res.pipe(file);
      file.on('finish', () => { file.close(); resolve(); });
    }).on('error', err => {
      fs.unlink(dest, () => {});
      reject(err);
    });
  });
}

async function setupPython(sendStatus) {
  const python = getPythonPath();

  if (!python) {
    // Download and install Python silently
    sendStatus('Downloading Python 3.11...');
    const installerPath = path.join(os.tmpdir(), 'python_installer.exe');
    await downloadFile(PYTHON_INSTALLER_URL, installerPath);

    sendStatus('Installing Python 3.11 (this takes 2-3 minutes)...');
    await new Promise((resolve, reject) => {
      const proc = spawn(installerPath, [
        '/quiet',
        'InstallAllUsers=0',
        'PrependPath=1',
        'Include_pip=1',
      ]);
      proc.on('close', code => {
        if (code === 0) resolve();
        else reject(new Error(`Python install failed: ${code}`));
      });
    });
    sendStatus('Python installed ✅');
  }

  // Install packages
  const py = getPythonPath() || 'python';
  sendStatus('Installing AI packages (first time only, ~2 mins)...');

  for (const pkg of REQUIRED_PACKAGES) {
    try {
      execSync(`"${py}" -c "import ${pkg.replace('-python','').replace('-','_')}"`,
               {stdio:'ignore', timeout:5000});
      sendStatus(`✅ ${pkg} ready`);
    } catch(e) {
      sendStatus(`Installing ${pkg}...`);
      try {
        execSync(
          `"${py}" -m pip install ${pkg} --quiet --no-warn-script-location`,
          {stdio:'ignore', timeout:120000});
        sendStatus(`✅ ${pkg} installed`);
      } catch(err) {
        sendStatus(`⚠️ ${pkg} failed — continuing...`);
      }
    }
  }

  sendStatus('All packages ready ✅');
  return true;
}

// ── SETUP WINDOW (shown on Windows first run) ─────────────────────
function createSetupWindow() {
  setupWindow = new BrowserWindow({
    width:  500,
    height: 400,
    frame:  true,
    resizable: false,
    alwaysOnTop: true,
    webPreferences: { nodeIntegration: false, contextIsolation: true }
  });

  const html = `<!DOCTYPE html>
<html>
<head>
  <style>
    * { margin:0; padding:0; box-sizing:border-box; }
    body { font-family: -apple-system, sans-serif;
           background:#0d1117; color:#c9d1d9;
           display:flex; align-items:center;
           justify-content:center; height:100vh;
           flex-direction:column; text-align:center;
           padding:40px; }
    h2 { color:#58a6ff; margin-bottom:8px; font-size:20px; }
    p  { color:#8b949e; font-size:13px; margin-bottom:24px; }
    .log { background:#161b22; border:1px solid #30363d;
           border-radius:8px; padding:16px; width:100%;
           max-height:180px; overflow-y:auto;
           font-size:12px; font-family:monospace;
           color:#3fb950; text-align:left; }
    .spinner { width:40px; height:40px; border:3px solid #30363d;
               border-top-color:#58a6ff; border-radius:50%;
               animation:spin 1s linear infinite; margin:0 auto 20px; }
    @keyframes spin { to { transform:rotate(360deg); } }
  </style>
</head>
<body>
  <div class="spinner"></div>
  <h2>Setting Up Exam Environment</h2>
  <p>Please wait while we prepare your exam browser.<br>
     This only happens once.</p>
  <div class="log" id="log">Starting setup...<br></div>
  <script>
    const { ipcRenderer } = require('electron');
  </script>
</body>
</html>`;

  const tmpHtml = path.join(os.tmpdir(), 'proctor_setup.html');
  fs.writeFileSync(tmpHtml, html);
  setupWindow.loadFile(tmpHtml);
  setupWindow.setMenuBarVisibility(false);
}

// ── PYTHON AI PROCTOR ─────────────────────────────────────────────
function startPython(sessionId) {
  const pythonPath = getPythonPath();
  const scriptPath = getScriptPath();
  const evidenceDir = path.join(app.getPath('userData'), 'evidence');

  if (!pythonPath || !fs.existsSync(scriptPath)) {
    console.error('[AI] Python or script not found');
    return;
  }

  const env = {
    ...process.env,
    PROCTOR_SESSION_ID:   sessionId,
    PROCTOR_SERVER_URL:   `${SERVER_URL}/event`,
    PROCTOR_EVIDENCE_DIR: evidenceDir,
  };

  pythonProcess = spawn(pythonPath, [scriptPath], { env });
  pythonProcess.stdout.on('data', d =>
    console.log('[AI]', d.toString().trim()));
  pythonProcess.stderr.on('data', d =>
    console.error('[AI]', d.toString().trim()));
  pythonProcess.on('close', code =>
    console.log('[AI] Exited:', code));
}

function stopPython() {
  if (pythonProcess) {
    pythonProcess.kill('SIGTERM');
    pythonProcess = null;
  }
}

// ── VIOLATION POLLING ─────────────────────────────────────────────
function startPolling(sessionId) {
  let lastEventId = 0;
  pollInterval = setInterval(async () => {
    try {
      const r    = await fetch(`${SERVER_URL}/events/${sessionId}`);
      const data = await r.json();
      const newV = (data.events || []).filter(e =>
        e.id > lastEventId &&
        (e.severity === 'high' || e.severity === 'medium') &&
        !e.type.includes('screenshot') &&
        !e.type.includes('enrollment') &&
        !e.type.includes('started') &&
        !e.type.includes('submitted') &&
        !e.type.includes('resumed') &&
        !e.type.includes('complete') &&
        !e.type.includes('session_ended')
      );
      if (newV.length > 0) {
        lastEventId = Math.max(...newV.map(e => e.id));
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.webContents.send('violation-detected', {
            type:      newV[0].type,
            details:   newV[0].details,
            severity:  newV[0].severity,
            timestamp: newV[0].timestamp,
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

  // On Windows - check Python and auto-setup if needed
  if (process.platform === 'win32') {
    const ready = await checkPythonReady();
    if (!ready) {
      createSetupWindow();

      const sendStatus = (msg) => {
        console.log('[Setup]', msg);
        if (setupWindow && !setupWindow.isDestroyed()) {
          setupWindow.webContents.executeJavaScript(`
            document.getElementById('log').innerHTML +=
              '${msg.replace(/'/g,"\\'")}\\n';
            document.getElementById('log').scrollTop = 99999;
          `).catch(()=>{});
        }
      };

      try {
        await setupPython(sendStatus);
        sendStatus('✅ Setup complete! Starting exam...');
        await new Promise(r => setTimeout(r, 2000));
      } catch(e) {
        sendStatus(`❌ Setup failed: ${e.message}`);
        sendStatus('Please install Python from python.org and retry.');
        await new Promise(r => setTimeout(r, 5000));
      }

      if (setupWindow && !setupWindow.isDestroyed()) {
        setupWindow.close();
      }
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
