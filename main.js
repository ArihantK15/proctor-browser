const {
  app, BrowserWindow, ipcMain, screen,
  globalShortcut, powerSaveBlocker
} = require('electron');
const path    = require('path');
const { spawn, spawnSync, execSync } = require('child_process');
const os      = require('os');
const fs      = require('fs');
const https   = require('https');

const SERVER_URL = 'https://procta.net';
const ADMIN_CODE = 'EXIT2026';

let mainWindow      = null;
let setupWindow     = null;
let pythonProcess   = null;
let pythonShouldRun = false; // guard against restart after intentional stop
let powerBlockId    = null;
let pollInterval    = null;
let studentToken    = null; // JWT issued after validate-student
let isKiosk       = !process.argv.includes('--no-kiosk') &&
                    process.env.PROCTOR_DEBUG !== '1';
let resolvedPython = null;
let integrityFlags = []; // populated at startup, sent to renderer

// ── VM / INTEGRITY CHECKS ────────────────────────────────────────
function runIntegrityChecks() {
  const flags = [];
  const isWin = process.platform === 'win32';
  const isMac = process.platform === 'darwin';

  // 1. VM Detection — GPU renderer string
  //    Checked after app is ready via webContents GPU info
  //    (deferred to after window creation — see _checkGPU)

  // 2. VM Detection — MAC address prefixes (common VM vendors)
  const VM_MAC_PREFIXES = [
    '00:05:69', '00:0c:29', '00:1c:14', '00:50:56',           // VMware
    '08:00:27',                                                  // VirtualBox
    '00:15:5d',                                                  // Hyper-V
    '00:16:3e',                                                  // Xen
    '52:54:00',                                                  // QEMU/KVM
    '00:1a:4a',                                                  // Parallels
  ];
  try {
    const nets = os.networkInterfaces();
    for (const [name, addrs] of Object.entries(nets)) {
      for (const a of addrs) {
        if (a.mac && a.mac !== '00:00:00:00:00:00') {
          const prefix = a.mac.substring(0, 8).toLowerCase();
          if (VM_MAC_PREFIXES.includes(prefix)) {
            flags.push({
              type: 'vm_detected',
              severity: 'high',
              details: `VM MAC address detected (${a.mac}, interface: ${name})`
            });
          }
        }
      }
    }
  } catch(e) { console.error('[Integrity] MAC check error:', e.message); }

  // 3. VM Detection — platform-specific checks
  if (isWin) {
    try {
      // Check for VM-related services/drivers in systeminfo
      const info = execSync('systeminfo', { encoding: 'utf8', timeout: 10000 });
      const vmKeywords = ['vmware', 'virtualbox', 'hyper-v', 'qemu', 'xen', 'parallels'];
      const lower = info.toLowerCase();
      for (const kw of vmKeywords) {
        if (lower.includes(kw)) {
          flags.push({
            type: 'vm_detected',
            severity: 'high',
            details: `VM indicator in system info: ${kw}`
          });
          break; // one flag is enough
        }
      }
    } catch(e) {}

    // Check for VM-related processes
    try {
      const tasks = execSync('tasklist /fo csv /nh', { encoding: 'utf8', timeout: 8000 });
      const vmProcesses = [
        'vmtoolsd.exe', 'vmwaretray.exe', 'VBoxService.exe', 'VBoxTray.exe',
        'vmcompute.exe', 'xenservice.exe',
      ];
      const remoteProcesses = [
        'TeamViewer.exe', 'AnyDesk.exe', 'mstsc.exe', 'vncviewer.exe',
        'Chrome Remote Desktop Host', 'rustdesk.exe', 'parsec.exe',
        'ScreenConnect', 'LogMeIn',
      ];
      const screenShareProcesses = [
        'obs64.exe', 'obs32.exe', 'OBS Studio',
        'DiscordPTB.exe', 'Discord.exe',
      ];
      const tasksLower = tasks.toLowerCase();

      for (const p of vmProcesses) {
        if (tasksLower.includes(p.toLowerCase())) {
          flags.push({
            type: 'vm_detected',
            severity: 'high',
            details: `VM process running: ${p}`
          });
        }
      }
      for (const p of remoteProcesses) {
        if (tasksLower.includes(p.toLowerCase())) {
          flags.push({
            type: 'remote_desktop_detected',
            severity: 'high',
            details: `Remote desktop software detected: ${p}`
          });
        }
      }
      for (const p of screenShareProcesses) {
        if (tasksLower.includes(p.toLowerCase())) {
          flags.push({
            type: 'screen_share_detected',
            severity: 'medium',
            details: `Screen sharing software detected: ${p}`
          });
        }
      }
    } catch(e) {}
  }

  if (isMac) {
    try {
      const procs = execSync('ps -eo comm', { encoding: 'utf8', timeout: 5000 });
      const procsLower = procs.toLowerCase();
      const macChecks = [
        { proc: 'vmware', type: 'vm_detected', sev: 'high' },
        { proc: 'VBoxHeadless', type: 'vm_detected', sev: 'high' },
        { proc: 'parallels', type: 'vm_detected', sev: 'high' },
        { proc: 'TeamViewer', type: 'remote_desktop_detected', sev: 'high' },
        { proc: 'AnyDesk', type: 'remote_desktop_detected', sev: 'high' },
        { proc: 'screensharingd', type: 'screen_share_detected', sev: 'medium' },
        { proc: 'obs', type: 'screen_share_detected', sev: 'medium' },
      ];
      for (const { proc, type, sev } of macChecks) {
        if (procsLower.includes(proc.toLowerCase())) {
          flags.push({
            type, severity: sev,
            details: `Detected: ${proc}`
          });
        }
      }
    } catch(e) {}

    // Check for VM via sysctl (macOS-specific)
    try {
      const hw = execSync('sysctl -n machdep.cpu.brand_string', { encoding: 'utf8', timeout: 3000 });
      if (hw.toLowerCase().includes('qemu') || hw.toLowerCase().includes('virtual')) {
        flags.push({
          type: 'vm_detected', severity: 'high',
          details: `Virtual CPU detected: ${hw.trim()}`
        });
      }
    } catch(e) {}
  }

  // 4. Multiple monitors check
  try {
    const displays = screen.getAllDisplays();
    if (displays.length > 1) {
      flags.push({
        type: 'multiple_monitors',
        severity: 'medium',
        details: `${displays.length} displays detected — potential screen sharing setup`
      });
    }
  } catch(e) {}

  console.log(`[Integrity] ${flags.length} flag(s) found`);
  for (const f of flags) {
    console.log(`  [${f.severity.toUpperCase()}] ${f.type}: ${f.details}`);
  }
  return flags;
}

// GPU-based VM check — must run after BrowserWindow is created
async function _checkGPU(win) {
  try {
    const renderer = await win.webContents.executeJavaScript(`
      (function() {
        try {
          const c = document.createElement('canvas');
          const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
          if (!gl) return '';
          const ext = gl.getExtension('WEBGL_debug_renderer_info');
          return ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : '';
        } catch(e) { return ''; }
      })()
    `);
    if (renderer) {
      const lower = renderer.toLowerCase();
      const vmRenderers = [
        'vmware', 'virtualbox', 'llvmpipe', 'swiftshader',
        'microsoft basic render', 'chromium', 'virgl',
      ];
      for (const vr of vmRenderers) {
        if (lower.includes(vr)) {
          integrityFlags.push({
            type: 'vm_detected',
            severity: 'high',
            details: `Virtual GPU renderer: ${renderer}`
          });
          console.log(`[Integrity] VM GPU detected: ${renderer}`);
          break;
        }
      }
    }
  } catch(e) { console.error('[Integrity] GPU check error:', e.message); }
}

// ── PYTHON FINDER ─────────────────────────────────────────────────
function findPython() {
  if (resolvedPython) return resolvedPython;

  const isWin = process.platform === 'win32';
  const candidates = isWin ? [
    // 1. Bundled embeddable Python shipped inside the .exe (highest priority)
    path.join(process.resourcesPath || __dirname, 'python', 'python.exe'),
    path.join(__dirname, 'resources', 'python', 'python.exe'),
    // 2. User-installed Python
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
        fs.unlink(dest, () => {});
        downloadFile(res.headers.location, dest)
          .then(resolve).catch(reject);
        return;
      }
      if (res.statusCode < 200 || res.statusCode >= 300) {
        file.close();
        fs.unlink(dest, () => {});
        reject(new Error(`Download failed: HTTP ${res.statusCode}`));
        res.resume(); // drain the response so the socket closes
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
  const packages = [
    'opencv-python',
    'mediapipe',
    'ultralytics',
    'sounddevice',
    'numpy',
    'scipy',
    'requests',
  ];
  sendSetupStatus(`Installing AI packages (~3 mins, one-time only). Do NOT close this window.`);
  const setupStart = Date.now();

  for (let idx = 0; idx < packages.length; idx++) {
    const pkg = packages[idx];
    const elapsed = Math.round((Date.now() - setupStart) / 1000);
    sendSetupStatus(`  [${idx+1}/${packages.length}] Installing ${pkg}... (${elapsed}s elapsed)`);
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
  const totalSecs = Math.round((Date.now() - setupStart) / 1000);
  sendSetupStatus(`Setup complete in ${totalSecs}s.`);

  const ready = checkPackagesReady(python);
  sendSetupStatus(ready ?
    '✅ All packages ready! Starting exam...' :
    '⚠️ Some packages missing — AI features may be limited');
  return ready;
}

// ── START/STOP PYTHON ─────────────────────────────────────────────
function startPython(sessionId) {
  pythonShouldRun = true; // mark intent; stopPython() sets this to false

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
      PROCTOR_SESSION_ID:              sessionId,
      PROCTOR_SERVER_URL:              `${SERVER_URL}/event`,
      PROCTOR_EVIDENCE_DIR:            evidenceDir,
      PROCTOR_JWT_TOKEN:               studentToken || '',
      PROCTOR_SKIP_ENROLLMENT:         '1',  // renderer handled the UI phase
      PROCTOR_WRONG_PERSON_THRESHOLD:  '0.25',
      PROCTOR_VOICE_THRESHOLD:         '0.035',
    }
  });

  pythonProcess.stdout.on('data', d =>
    console.log('[AI]', d.toString().trim()));
  pythonProcess.stderr.on('data', d =>
    console.error('[AI]', d.toString().trim()));
  pythonProcess.on('close', code => {
    console.log('[AI] Exited:', code);
    if (code !== 0 && code !== null && pythonShouldRun) {
      console.log('[AI] Unexpected exit — restarting in 3s');
      setTimeout(() => startPython(sessionId), 3000);
    }
  });
  pythonProcess.on('error', err =>
    console.error('[AI] Spawn error:', err.message));
}

function stopPython() {
  pythonShouldRun = false;
  if (pythonProcess) {
    try {
      if (process.platform === 'win32') {
        // SIGTERM is ignored on Windows — use taskkill to force kill the
        // entire process tree (Python + any child cv2/mediapipe threads)
        spawnSync('taskkill', ['/pid', String(pythonProcess.pid), '/f', '/t'],
          { timeout: 5000 });
      } else {
        pythonProcess.kill('SIGTERM');
      }
    } catch(e) { console.error('[AI] Stop error:', e.message); }
    pythonProcess = null;
  }
}

// ── AUTH HEADERS ─────────────────────────────────────────────────
function authHeaders() {
  const base = { 'Content-Type': 'application/json' };
  return studentToken
    ? { ...base, 'Authorization': `Bearer ${studentToken}` }
    : base;
}

// ── POLLING ───────────────────────────────────────────────────────
function startPolling(sessionId) {
  if (pollInterval) return; // already polling — don't stack a second loop
  let lastEventId = 0;
  let forceSubmitSent = false;
  pollInterval = setInterval(async () => {
    try {
      const r    = await fetch(`${SERVER_URL}/events/${sessionId}`,
                               { headers: authHeaders() });
      const data = await r.json();
      const events = data.events || [];

      // Check for admin force-submit (only once)
      if (!forceSubmitSent && events.some(e => e.type === 'exam_submitted')) {
        forceSubmitSent = true;
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.webContents.send('force-submit');
        }
      }

      // Send violation banners for new high/medium events
      const IGNORED = ['screenshot','enrollment','started','submitted',
                       'resumed','complete','session_ended','answer_selected'];
      const newV = events.filter(e =>
        e.id > lastEventId &&
        (e.severity === 'high' || e.severity === 'medium') &&
        !IGNORED.some(x => e.type.includes(x))
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

  mainWindow.webContents.on('render-process-gone', (_, details) => {
    console.log('[App] Renderer gone:', details.reason, '— reloading');
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.reload();
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

  // Emergency escape — works even in kiosk, requires admin code
  globalShortcut.register('CommandOrControl+Shift+Alt+E', () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.executeJavaScript(`
        (function() {
          const code = prompt('Emergency exit — enter admin code:');
          window.proctor && window.proctor.adminExit(code);
        })()
      `);
    }
  });

  // Run integrity checks before window creation
  integrityFlags = runIntegrityChecks();

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

  // Deferred GPU-based VM check (needs a window)
  mainWindow.webContents.on('did-finish-load', () => {
    _checkGPU(mainWindow);
  });
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
ipcMain.handle('get-integrity-flags', () => {
  return integrityFlags;
});

ipcMain.handle('validate-student', async (_, roll, accessCode) => {
  const r = await fetch(`${SERVER_URL}/api/validate-student`, {
    method:  'POST',
    headers: {'Content-Type': 'application/json'},
    body:    JSON.stringify({roll_number: roll, access_code: accessCode || ''})
  });
  if (!r.ok) { const e = await r.json(); throw new Error(e.detail); }
  const data = await r.json();
  studentToken = data.token || null; // store JWT for all subsequent requests
  return data;
});

ipcMain.handle('get-questions', async () => {
  const r = await fetch(`${SERVER_URL}/api/questions`,
                        { headers: authHeaders() });
  if (!r.ok) throw new Error('Could not load questions');
  return r.json();
});

ipcMain.handle('log-event', async (_, data) => {
  try {
    await fetch(`${SERVER_URL}/event`, {
      method:  'POST',
      headers: authHeaders(),
      body:    JSON.stringify(data)
    });
  } catch(e) { console.error('[log-event]', e.message); }
});

ipcMain.handle('submit-exam', async (_, data) => {
  const r = await fetch(`${SERVER_URL}/api/submit-exam`, {
    method:  'POST',
    headers: authHeaders(),
    body:    JSON.stringify(data)
  });
  if (!r.ok) {
    const errText = await r.text();
    console.error('[Submit] Server error:', r.status, errText);
    throw new Error(`Submission failed: ${r.status} ${errText}`);
  }
  return r.json();
});

ipcMain.handle('get-events', async (_, sessionId) => {
  const r = await fetch(`${SERVER_URL}/events/${sessionId}`,
                        { headers: authHeaders() });
  if (!r.ok) return { events: [] };
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
    // Remove the kiosk close-prevention listener before quitting,
    // otherwise app.quit() triggers 'close', preventDefault() runs, and quit is cancelled
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.removeAllListeners('close');
      mainWindow.removeAllListeners('blur');
    }
    app.quit();
    return { success: true };
  }
  return { success: false };
});
