const {
  app, BrowserWindow, ipcMain,
  globalShortcut, powerSaveBlocker
} = require('electron');
const path    = require('path');
const { spawn } = require('child_process');
const os      = require('os');
const fs      = require('fs');

const SERVER_URL = 'https://aiproc.ngrok.dev';
const ADMIN_CODE = 'EXIT2026';

let mainWindow    = null;
let pythonProcess = null;
let powerBlockId  = null;
let pollInterval  = null;
let isKiosk       = true;  // PRODUCTION MODE

// ── FIND PYTHON ───────────────────────────────────────────────────
function findPython() {
  const candidates = [];

  if (process.platform === 'darwin') {
    // Mac - try bundled venv first, then system
    candidates.push(
      path.join(process.resourcesPath, 'venv', 'bin', 'python3'),
      path.join(__dirname, 'venv', 'bin', 'python3'),
      '/usr/local/bin/python3',
      '/usr/bin/python3',
      'python3',
    );
  } else if (process.platform === 'win32') {
    // Windows
    candidates.push(
      path.join(process.resourcesPath, 'venv', 'Scripts', 'python.exe'),
      path.join(__dirname, 'venv', 'Scripts', 'python.exe'),
      'C:\\Python311\\python.exe',
      'C:\\Python310\\python.exe',
      'python',
      'python3',
    );
  }

  for (const p of candidates) {
    try {
      if (p.includes('python3') || p.includes('python')) {
        if (!p.includes('/') && !p.includes('\\')) return p; // system cmd
        if (fs.existsSync(p)) return p;
      }
    } catch(e) {}
  }
  return 'python3'; // fallback
}

// ── FIND PROCTOR SCRIPT ───────────────────────────────────────────
function findScript() {
  const candidates = [
    path.join(process.resourcesPath, 'proctor.py'),
    path.join(__dirname, 'proctor.py'),
    path.join(app.getAppPath(), 'proctor.py'),
  ];
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  return path.join(__dirname, 'proctor.py');
}

// ── PYTHON AI PROCTOR ─────────────────────────────────────────────
function startPython(sessionId) {
  const pythonPath = findPython();
  const scriptPath = findScript();
  const evidenceDir = path.join(app.getPath('userData'), 'evidence');

  console.log('[AI] Python:', pythonPath);
  console.log('[AI] Script:', scriptPath);
  console.log('[AI] Session:', sessionId);

  if (!fs.existsSync(scriptPath)) {
    console.error('[AI] proctor.py not found at:', scriptPath);
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

// ── WINDOW ────────────────────────────────────────────────────────
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

  if (!isKiosk) {
    mainWindow.webContents.openDevTools();
  }

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

app.whenReady().then(() => {
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
