const {
  app, BrowserWindow, ipcMain, screen,
  globalShortcut, powerSaveBlocker
} = require('electron');
const path    = require('path');
const { spawn, spawnSync, execSync } = require('child_process');
const os      = require('os');
const fs      = require('fs');
const https   = require('https');

const SERVER_URL = process.env.PROCTOR_SERVER_URL || 'https://app.procta.net';
const ADMIN_CODE = process.env.EXIT_CODE || 'EXIT2026';

let mainWindow      = null;   // the exam window (kiosk) when present
let lobbyWindow     = null;   // the pre-exam web dashboard window (unlocked)
let setupWindow     = null;
let pythonProcess   = null;
let pythonShouldRun = false; // guard against restart after intentional stop
let calProcess      = null;  // calibration-mode proctor.py (streams gaze readings)
let calBiases       = null;  // {gaze_yaw, gaze_pitch, head_yaw, head_pitch} from dot calibration
let powerBlockId    = null;
let pollInterval    = null;
let studentToken    = null; // JWT issued after validate-student
// Phase 2: kiosk mode is opt-in per-session, not a global launch flag.
// `isKiosk` now reflects whether the CURRENTLY OPEN exam window is locked;
// it starts false on app launch (lobby is never locked) and is toggled
// on only when an exam window is created via the lobby bridge.
let isKiosk        = false;
const KIOSK_ALLOWED = !process.argv.includes('--no-kiosk') &&
                      process.env.PROCTOR_DEBUG !== '1';
let currentSessionId = null;  // set once an exam session is active
let examContext      = null;  // {rollNumber, accessCode, examTitle, teacherId} stashed by lobby
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

  const envVars = {
    ...process.env,
    PROCTOR_SESSION_ID:              sessionId,
    PROCTOR_SERVER_URL:              `${SERVER_URL}/event`,
    PROCTOR_EVIDENCE_DIR:            evidenceDir,
    PROCTOR_JWT_TOKEN:               studentToken || '',
    PROCTOR_SKIP_ENROLLMENT:         '1',  // renderer handled the UI phase
    PROCTOR_WRONG_PERSON_THRESHOLD:  '0.25',
    PROCTOR_VOICE_THRESHOLD:         '0.035',
  };
  // Pass calibration biases from the dot-calibration step (if available)
  if (calBiases) {
    envVars.PROCTOR_GAZE_YAW_BIAS  = String(calBiases.gaze_yaw);
    envVars.PROCTOR_GAZE_PITCH_BIAS = String(calBiases.gaze_pitch);
    envVars.PROCTOR_HEAD_YAW_BIAS  = String(calBiases.head_yaw);
    envVars.PROCTOR_HEAD_PITCH_BIAS = String(calBiases.head_pitch);
  }

  pythonProcess = spawn(python, [script], { env: envVars });

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

// ── CALIBRATION MODE ────────────────────────────────────────────
// Spawns proctor.py with PROCTOR_CALIBRATION_MODE=1 so it streams
// gaze/head readings as CAL:{...} JSON lines on stdout. The renderer
// uses these to verify the student is looking at each calibration dot.
function startCalibration(sessionId) {
  stopCalibration(); // kill any prior instance

  const python = findPython();
  const script = getScriptPath();
  if (!python || !fs.existsSync(script)) {
    console.error('[CAL] Python or script not found — calibration unavailable');
    return;
  }

  calProcess = spawn(python, [script], {
    env: {
      ...process.env,
      PROCTOR_SESSION_ID:          sessionId,
      PROCTOR_SERVER_URL:          `${SERVER_URL}/event`,
      PROCTOR_JWT_TOKEN:           studentToken || '',
      PROCTOR_CALIBRATION_MODE:    '1',
      PROCTOR_SKIP_ENROLLMENT:     '1',
      PROCTOR_HEADLESS:            '1',
    }
  });

  calProcess.stdout.on('data', d => {
    const lines = d.toString().split('\n');
    for (const line of lines) {
      const trimmed = line.trim();
      if (trimmed.startsWith('CAL:')) {
        try {
          const reading = JSON.parse(trimmed.slice(4));
          // Forward to the exam renderer window
          if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.webContents.send('cal-reading', reading);
          }
        } catch (e) { /* malformed JSON — skip */ }
      } else if (trimmed) {
        console.log('[CAL]', trimmed);
      }
    }
  });
  calProcess.stderr.on('data', d =>
    console.error('[CAL]', d.toString().trim()));
  calProcess.on('close', code =>
    console.log('[CAL] Exited:', code));
  calProcess.on('error', err =>
    console.error('[CAL] Spawn error:', err.message));

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

// ── LOBBY WINDOW (pre-exam, NOT kiosk) ───────────────────────────
//
// Phase 2: Electron now boots into a normal window showing the student web
// dashboard (/student). The student logs in there, sees upcoming exams, and
// clicks "Start exam" to trigger an IPC that spawns a locked exam window.
// Everything outside of an active exam is unlocked — no shortcut capture,
// no kiosk mode, no always-on-top, devtools allowed for debugging.
function createLobbyWindow() {
  if (lobbyWindow && !lobbyWindow.isDestroyed()) {
    lobbyWindow.show();
    lobbyWindow.focus();
    return lobbyWindow;
  }

  console.log('[Lobby] creating lobby window (fullscreen:false, kiosk:false)');

  lobbyWindow = new BrowserWindow({
    width:           1180,
    height:          820,
    minWidth:        900,
    minHeight:       640,
    // Defense-in-depth: macOS may remember a previous Space state from
    // the pre-Phase-2 kiosk builds. Explicitly declare non-kiosk geometry
    // so nothing drags the lobby into full-screen mode at launch.
    fullscreen:      false,
    fullscreenable:  true,
    kiosk:           false,
    alwaysOnTop:     false,
    frame:           true,
    resizable:       true,
    movable:         true,
    minimizable:     true,
    maximizable:     true,
    closable:        true,
    autoHideMenuBar: true,
    title:           'Procta',
    webPreferences: {
      preload:          path.join(__dirname, 'lobby_preload.js'),
      contextIsolation: true,
      nodeIntegration:  false,
      devTools:         true,
    }
  });

  // Phase 2 fix: the lobby HTML lives inside the Electron bundle, not on
  // the server. The student dashboard is a shell that talks to the
  // backend via fetch(`${SERVER_URL}/api/...`), so deploying the backend
  // is NOT a prerequisite for the app to boot cleanly. Previously the
  // lobby loaded `${SERVER_URL}/student` directly, which failed hard if
  // the route wasn't deployed yet — giving the impression the app was
  // "stuck in the old startup screen".
  const lobbyHtml = findLobbyHtml();
  console.log('[Lobby] loading:', lobbyHtml);
  lobbyWindow.loadFile(lobbyHtml);

  lobbyWindow.webContents.on('did-fail-load', (_, errorCode, errorDescription, validatedURL) => {
    if (errorCode === -3) return; // user-initiated abort
    console.error('[Lobby] load failed:', errorCode, errorDescription, validatedURL);
    const offline = `
      <!DOCTYPE html><html><head><meta charset="utf-8">
      <title>Procta — Error</title>
      <style>
        body{margin:0;font-family:-apple-system,sans-serif;background:#0a0e1a;color:#cbd5e1;
             display:flex;align-items:center;justify-content:center;height:100vh;text-align:center;padding:24px}
        .box{max-width:480px;background:#0f1629;border:1px solid rgba(255,255,255,.06);
             border-radius:14px;padding:36px 32px}
        h1{color:#fff;font-size:20px;margin:0 0 10px}
        p{color:#94a3b8;font-size:13px;line-height:1.7;margin:0 0 10px}
        code{font-family:monospace;color:#60a5fa;font-size:11px;word-break:break-all;
             display:block;margin-top:8px;padding:8px 10px;background:rgba(255,255,255,.03);border-radius:6px}
      </style></head><body>
      <div class="box">
        <h1>Lobby failed to open</h1>
        <p>Couldn't load the local student dashboard bundle.</p>
        <code>${errorDescription || errorCode}\n${validatedURL || lobbyHtml}</code>
        <p style="margin-top:16px">Relaunch the app, or reinstall if the problem persists.</p>
      </div></body></html>`;
    lobbyWindow.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(offline));
  });

  lobbyWindow.webContents.on('did-finish-load', () => {
    console.log('[Lobby] did-finish-load OK');
  });

  lobbyWindow.webContents.on('will-navigate', (e, url) => {
    // Allow file:// (our own bundle), data: (error page), and SERVER_URL
    // redirects. Everything else is denied.
    try {
      const u = new URL(url);
      const ok = url.startsWith(SERVER_URL) ||
                 u.protocol === 'data:' ||
                 u.protocol === 'file:' ||
                 u.protocol === 'about:';
      if (!ok) e.preventDefault();
    } catch { e.preventDefault(); }
  });
  lobbyWindow.webContents.setWindowOpenHandler(
    () => ({ action: 'deny' }));

  lobbyWindow.on('closed', () => { lobbyWindow = null; });
  return lobbyWindow;
}

// Resolve the lobby HTML path in both dev (project root) and packaged
// (resources) layouts. In dev the file lives at `app/static/student.html`
// relative to main.js. When packaged by electron-builder we copy it to
// `renderer/lobby.html` (added to build.files).
function findLobbyHtml() {
  const candidates = [
    path.join(__dirname, 'renderer', 'lobby.html'),
    path.join(__dirname, 'app', 'static', 'student.html'),
    path.join(process.resourcesPath || '', 'app', 'static', 'student.html'),
    path.join(process.resourcesPath || '', 'renderer', 'lobby.html'),
  ];
  for (const p of candidates) {
    try { if (fs.existsSync(p)) return p; } catch(e) {}
  }
  // Fallback — return the first candidate so did-fail-load fires with a
  // useful error rather than crashing.
  return candidates[0];
}

// ── EXAM WINDOW (kiosk-locked) ───────────────────────────────────
//
// Called by the lobby bridge when the student clicks "Start exam" on an
// exam card. Kiosk mode + global shortcut capture are engaged HERE, not at
// app launch, so the student is only locked down while actually sitting
// an exam. `releaseKiosk()` tears everything back down on submit / panic.
function createExamWindow() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.focus();
    return mainWindow;
  }

  isKiosk = KIOSK_ALLOWED;  // honor --no-kiosk / PROCTOR_DEBUG overrides

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

    // Global shortcut capture — kiosk lockdown keys. Only registered while
    // the exam window is alive; released by releaseKiosk() on submit/panic.
    globalShortcut.registerAll([
      'Alt+F4','Cmd+Q','Cmd+W','Cmd+M','Cmd+H',
      'Cmd+Tab','Alt+Tab','F11','F12','Escape',
      'Cmd+Shift+I','Ctrl+Shift+I',
      'Cmd+R','Ctrl+R','F5',
      'PrintScreen','Cmd+Shift+3','Cmd+Shift+4',
      'Cmd+C','Cmd+V','Cmd+X',
      'Ctrl+C','Ctrl+V','Ctrl+X',
    ], () => false);

    // Emergency escape — admin-code exit (legacy).
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

    // ── Panic unlock chord ──────────────────────────────────────
    // Cmd/Ctrl+Shift+F12 → confirmation → release kiosk + flag session.
    // Never auto-submits. The session remains in_progress and the teacher
    // sees a high-severity `panic_unlock` event on their dashboard so they
    // can decide whether to accept the partial work or void the session.
    globalShortcut.register('CommandOrControl+Shift+F12', async () => {
      if (!mainWindow || mainWindow.isDestroyed()) return;
      try {
        const confirmed = await mainWindow.webContents.executeJavaScript(`
          (function() {
            return confirm(
              'PANIC UNLOCK\\n\\n' +
              'This releases the exam lockdown and flags your session for your teacher to review.\\n\\n' +
              'Your work will NOT be submitted automatically.\\n\\n' +
              'Continue?'
            );
          })()
        `);
        if (!confirmed) return;
        await handlePanicUnlock('student-triggered');
      } catch(e) {
        console.error('[Panic] chord error:', e.message);
      }
    });
  }
}

// ── KIOSK TEARDOWN ───────────────────────────────────────────────
// IMPORTANT: window destruction happens FIRST, before any other cleanup,
// so a hung Python process or stuck shortcut can't leave the user with a
// frozen kiosk window on screen. We use destroy() (not close()) because
// close() can be silently swallowed by lingering close-handlers, beforeunload
// handlers, or macOS fullscreen-transition races. destroy() is the documented
// nuclear option: it bypasses all of that and guarantees the window goes away.
function releaseKiosk({ reopenLobby = true } = {}) {
  console.log('[Kiosk] releasing', reopenLobby ? '(→ lobby)' : '(→ quit)');
  isKiosk = false;

  // Step 1: tear down the window FIRST. Nothing else matters if the user
  // is staring at a frozen exam screen.
  const winRef = mainWindow;
  mainWindow = null;
  if (winRef && !winRef.isDestroyed()) {
    try {
      winRef.removeAllListeners('close');
      winRef.removeAllListeners('blur');
      winRef.removeAllListeners('focus');
      try { winRef.setKiosk(false); }       catch(e) {}
      try { winRef.setFullScreen(false); }  catch(e) {}
      try { winRef.setAlwaysOnTop(false); } catch(e) {}
      try { winRef.setClosable(true); }     catch(e) {}
      // destroy() bypasses all close handlers + beforeunload + fullscreen
      // transitions. Window WILL go away.
      winRef.destroy();
      console.log('[Kiosk] window destroyed');
    } catch(e) {
      console.error('[Kiosk] destroy error:', e.message);
    }
  }

  // Step 2: watchdog. If somehow destroy() didn't take, force it again
  // after a beat. Belt-and-suspenders for the bug we just hit.
  setTimeout(() => {
    if (winRef && !winRef.isDestroyed()) {
      console.error('[Kiosk] window survived destroy(); retrying');
      try { winRef.destroy(); } catch(e) {}
    }
  }, 500);

  // Step 3: now clean up the rest. None of this can leave a window stuck.
  try { stopPython(); }              catch(e) { console.error('[Kiosk] stopPython:', e.message); }
  try { stopPolling(); }             catch(e) { console.error('[Kiosk] stopPolling:', e.message); }
  try { globalShortcut.unregisterAll(); } catch(e) {}
  if (powerBlockId !== null) {
    try { powerSaveBlocker.stop(powerBlockId); } catch(e) {}
    powerBlockId = null;
  }

  currentSessionId = null;
  examContext = null;
  studentToken = null;

  if (reopenLobby) {
    setTimeout(() => createLobbyWindow(), 200);
  }
}

// ── PANIC UNLOCK HANDLER ─────────────────────────────────────────
// Flags the active session with a high-severity event (so the teacher can
// see it), then releases kiosk and returns to the lobby. No auto-submit.
async function handlePanicUnlock(reason) {
  const sid = currentSessionId;
  if (sid && studentToken) {
    try {
      await fetch(`${SERVER_URL}/event`, {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({
          session_id: sid,
          event_type: 'panic_unlock',
          severity:   'high',
          details:    `Panic unlock triggered (${reason}). Session left in_progress for teacher review.`,
        }),
      });
    } catch(e) { console.error('[Panic] event post failed:', e.message); }
  }
  releaseKiosk({ reopenLobby: true });
}

// ── APP START ─────────────────────────────────────────────────────
app.whenReady().then(async () => {
  // Phase 2: NO global shortcut capture at boot — the lobby is unlocked.
  // Shortcut registration happens inside createExamWindow() when the
  // student actively enters a proctored exam.

  // Run integrity checks before window creation
  integrityFlags = runIntegrityChecks();

  // Windows: auto-setup Python if needed (runs in background while the
  // lobby is already visible — no need to block the dashboard on this).
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

  createLobbyWindow();

  // Deferred GPU-based VM check (needs any window)
  if (lobbyWindow) {
    lobbyWindow.webContents.on('did-finish-load', () => {
      _checkGPU(lobbyWindow);
    });
  }
});

app.on('before-quit', () => {
  stopPython();
  stopPolling();
  if (powerBlockId !== null) powerSaveBlocker.stop(powerBlockId);
});

app.on('window-all-closed', () => {
  // Quit whenever every window (lobby + exam) is gone. In Phase 2 the
  // lobby is always closable, so this just works — no more "isKiosk guard".
  stopPython();
  stopPolling();
  try { globalShortcut.unregisterAll(); } catch(e) {}
  app.quit();
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

ipcMain.handle('get-questions', async (_, sessionId) => {
  const qs = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : '';
  const r = await fetch(`${SERVER_URL}/api/questions${qs}`,
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

ipcMain.handle('start-calibration', (_, { sessionId }) => {
  currentSessionId = sessionId;
  startCalibration(sessionId);
  return { started: true };
});

ipcMain.handle('stop-calibration', (_, data) => {
  const biases = data && data.biases;
  if (biases) {
    calBiases = biases; // {gaze_yaw, gaze_pitch, head_yaw, head_pitch}
    console.log('[CAL] Biases received:', JSON.stringify(calBiases));
  }
  stopCalibration();
  return { stopped: true };
});

ipcMain.handle('start-proctor', (_, { sessionId }) => {
  currentSessionId = sessionId;
  startPython(sessionId);
  return { started: true };
});

ipcMain.handle('start-polling', (_, { sessionId }) => {
  currentSessionId = sessionId;
  startPolling(sessionId);
  return { polling: true };
});

ipcMain.handle('stop-proctor', () => {
  stopPython();
  stopPolling();
  return { stopped: true };
});

// Phase 2: the exam renderer fetches pre-filled context (roll, access code,
// exam title) stashed by the lobby bridge so the student doesn't have to
// retype what they already entered on the web dashboard. Returns null if
// the exam window was opened directly (legacy / debug).
ipcMain.handle('get-exam-context', () => {
  return examContext;
});

// Lobby → main bridge: the student clicked "Start exam" on an exam card.
// We stash their context and spawn the locked exam window. The lobby
// window stays open in the background so that panic/submit can return to
// it cleanly (we just re-focus it after releaseKiosk).
ipcMain.handle('lobby-launch-exam', async (_, ctx) => {
  if (!ctx || !ctx.rollNumber) {
    return { ok: false, error: 'Missing roll number' };
  }
  examContext = {
    rollNumber:  String(ctx.rollNumber).trim().toUpperCase(),
    accessCode:  String(ctx.accessCode || '').trim().toUpperCase(),
    examTitle:   ctx.examTitle || '',
    teacherId:   ctx.teacherId || null,
  };
  console.log('[Lobby] launch exam:', examContext);
  // Hide (don't destroy) the lobby so we can come back to it cleanly.
  if (lobbyWindow && !lobbyWindow.isDestroyed()) {
    try { lobbyWindow.hide(); } catch(e) {}
  }
  createExamWindow();
  return { ok: true };
});

// Panic unlock fired from the renderer (in-exam button) — same effect as
// the Cmd/Ctrl+Shift+F12 chord.
ipcMain.handle('panic-unlock', async (_, payload) => {
  await handlePanicUnlock((payload && payload.reason) || 'renderer-triggered');
  return { ok: true };
});

// Normal post-submit exit → release kiosk + reopen lobby.
ipcMain.handle('exit-exam-to-lobby', () => {
  releaseKiosk({ reopenLobby: true });
  return { ok: true };
});

ipcMain.handle('admin-exit', (_, code) => {
  // AUTO_CLOSE is fired by the renderer after a successful submit. In
  // Phase 2 we treat it as "return to lobby" instead of "quit app" so the
  // student lands back on their dashboard to see the submitted status and
  // browse practice, etc. The manual admin-code path (typed by a teacher)
  // still quits the entire app.
  if (code === 'AUTO_CLOSE') {
    console.log('[admin-exit] AUTO_CLOSE received');
    // Capture a ref before releaseKiosk nulls it, so the outer watchdog
    // below can verify the destruction took effect even if releaseKiosk
    // itself throws partway through.
    const winRef = mainWindow;
    try {
      releaseKiosk({ reopenLobby: true });
    } catch(e) {
      console.error('[admin-exit] releaseKiosk threw:', e.message);
    }
    // Outer-most safety net: if for ANY reason the kiosk window is still
    // alive 1s later, force-destroy it. The user must NEVER end up staring
    // at a frozen "Exam Submitted" card with no way out.
    setTimeout(() => {
      if (winRef && !winRef.isDestroyed()) {
        console.error('[admin-exit] window still alive after releaseKiosk; force-destroying');
        try { winRef.destroy(); } catch(e) {}
      }
      if (lobbyWindow && !lobbyWindow.isDestroyed()) {
        try { lobbyWindow.show(); lobbyWindow.focus(); } catch(e) {}
      } else {
        // Lobby got lost too — recreate it so the user has somewhere to land.
        try { createLobbyWindow(); } catch(e) {}
      }
    }, 1000);
    return { success: true };
  }
  if (code === ADMIN_CODE) {
    releaseKiosk({ reopenLobby: false });
    app.quit();
    return { success: true };
  }
  return { success: false };
});
