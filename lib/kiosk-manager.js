const {
  app, BrowserWindow, screen, globalShortcut,
  powerSaveBlocker, clipboard,
} = require('electron');
const path = require('path');
const {
  KIOSK_ALLOWED, BLOCKED_SHORTCUTS, PANIC_SHORTCUT, EMERGENCY_SHORTCUT,
  EXAM_WIDTH, EXAM_HEIGHT,
  LOBBY_WIDTH, LOBBY_HEIGHT, LOBBY_MIN_W, LOBBY_MIN_H,
  SERVER_URL,
} = require('../config');
const { checkGPU } = require('./integrity');

let mainWindow = null;
let lobbyWindow = null;
let isKiosk = false;
let currentSessionId = null;
let examContext = null;
let studentToken = null;
let calBiases = null;
let powerBlockId = null;
let integrityFlags = [];
let _integrityReady = null;
let pendingInviteToken = null;

let startProcessMonitor = null;
let stopProcessMonitorFn = null;
let startPollingFn = null;
let stopPollingFn = null;
let startPythonFn = null;
let stopPythonFn = null;
let startCalibrationFn = null;
let stopCalibrationFn = null;

function receiveInviteToken(token, source) {
  if (!token) return;
  console.log(`[Invite] received token via ${source} (len=${token.length})`);
  pendingInviteToken = token;
  if (lobbyWindow && !lobbyWindow.isDestroyed()) {
    try {
      lobbyWindow.webContents.send('invite-token-available', token);
      lobbyWindow.show();
      lobbyWindow.focus();
    } catch(e) { console.error('[Invite] failed to notify lobby:', e.message); }
  }
}

function getMainWindow() { return mainWindow; }
function setMainWindow(win) { mainWindow = win; }
function getLobbyWindow() { return lobbyWindow; }
function setLobbyWindow(win) { lobbyWindow = win; }
function getIntegrityFlags() { return integrityFlags; }
function getIntegrityReady() { return _integrityReady; }
function setIntegrityReady(p) { _integrityReady = p; }
function pushIntegrityFlag(flag) { integrityFlags.push(flag); }
function getCurrentSessionId() { return currentSessionId; }
function setCurrentSessionId(sid) { currentSessionId = sid; }
function getExamContext() { return examContext; }
function setExamContext(ctx) { examContext = ctx; }
function getStudentToken() { return studentToken; }
function setStudentToken(t) { studentToken = t; }
function getCalBiases() { return calBiases; }
function setCalBiases(b) { calBiases = b; }
function getPendingInviteToken() { return pendingInviteToken; }
function consumeInviteToken() { const t = pendingInviteToken; pendingInviteToken = null; return t; }
function getPowerBlockId() { return powerBlockId; }
function setPowerBlockId(id) { powerBlockId = id; }
function getIsKiosk() { return isKiosk; }

function setMonitorFns(start, stop) { startProcessMonitor = start; stopProcessMonitorFn = stop; }
function setPollingFns(start, stop) { startPollingFn = start; stopPollingFn = stop; }
function setPythonFns(start, stop, startCal, stopCal) {
  startPythonFn = start; stopPythonFn = stop;
  startCalibrationFn = startCal; stopCalibrationFn = stopCal;
}

function findLobbyHtml() {
  const fs = require('fs');
  const candidates = [
    path.join(__dirname, '..', 'renderer', 'lobby.html'),
    path.join(__dirname, '..', 'app', 'static', 'student.html'),
    path.join(process.resourcesPath || '', 'app', 'static', 'student.html'),
    path.join(process.resourcesPath || '', 'renderer', 'lobby.html'),
  ];
  for (const p of candidates) {
    try { if (fs.existsSync(p)) return p; } catch(e) {}
  }
  return candidates[0];
}

function createLobbyWindow() {
  if (lobbyWindow && !lobbyWindow.isDestroyed()) {
    lobbyWindow.show();
    lobbyWindow.focus();
    return lobbyWindow;
  }

  console.log('[Lobby] creating lobby window');
  lobbyWindow = new BrowserWindow({
    width: LOBBY_WIDTH, height: LOBBY_HEIGHT,
    minWidth: LOBBY_MIN_W, minHeight: LOBBY_MIN_H,
    fullscreen: false, fullscreenable: true,
    kiosk: false, alwaysOnTop: false,
    frame: true, resizable: true, movable: true,
    minimizable: true, maximizable: true,
    closable: true, autoHideMenuBar: true,
    title: 'Procta', backgroundColor: '#06080d',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, '..', 'lobby_preload.js'),
      contextIsolation: true, nodeIntegration: false, devTools: true,
      backgroundThrottling: false,
      offscreen: false,
    }
  });

  lobbyWindow.once('ready-to-show', () => {
    clearTimeout(lobbyShowFallback);
    lobbyWindow.show();
  });

  // Fallback: show the window after 5s even if ready-to-show never fires
  const lobbyShowFallback = setTimeout(() => {
    if (lobbyWindow && !lobbyWindow.isDestroyed()) {
      console.warn('[Lobby] ready-to-show timeout — forcing show');
      lobbyWindow.show();
    }
  }, 5000);

  const lobbyHtml = findLobbyHtml();
  console.log('[Lobby] loading:', lobbyHtml);
  lobbyWindow.loadFile(lobbyHtml);

  lobbyWindow.webContents.on('dom-ready', () => {
    lobbyWindow.webContents.insertCSS(
      'html,body{background:#06080d !important;color:#c8d1dc !important}'
    ).catch(() => {});
  });

  lobbyWindow.webContents.on('did-fail-load', (_, errorCode, errorDescription, validatedURL) => {
    if (errorCode === -3) return;
    console.error('[Lobby] load failed:', errorCode, errorDescription, validatedURL);
    clearTimeout(lobbyShowFallback);
    const offline = `<!DOCTYPE html><html><head><meta charset="utf-8"><title>Procta — Error</title>
      <style>body{margin:0;font-family:-apple-system,sans-serif;background:#0a0e1a;color:#cbd5e1;
        display:flex;align-items:center;justify-content:center;height:100vh;text-align:center;padding:24px}
        .box{max-width:480px;background:#0f1629;border:1px solid rgba(255,255,255,.06);
        border-radius:14px;padding:36px 32px}
        h1{color:#fff;font-size:20px;margin:0 0 10px}p{color:#94a3b8;font-size:13px;line-height:1.7;margin:0 0 10px}
        code{font-family:monospace;color:#60a5fa;font-size:11px;word-break:break-all;
        display:block;margin-top:8px;padding:8px 10px;background:rgba(255,255,255,.03);border-radius:6px}</style></head><body>
        <div class="box"><h1>Lobby failed to open</h1>
        <p>Couldn't load the local student dashboard bundle.</p>
        <code>${errorDescription || errorCode}\n${validatedURL || lobbyHtml}</code>
        <p style="margin-top:16px">Relaunch the app, or reinstall if the problem persists.</p></div></body></html>`;
    lobbyWindow.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(offline));
    lobbyWindow.show();
  });

  lobbyWindow.webContents.on('did-finish-load', () => {
    console.log('[Lobby] did-finish-load OK');
    if (pendingInviteToken) {
      try { lobbyWindow.webContents.send('invite-token-available', pendingInviteToken); }
      catch(e) { console.error('[Invite] failed to push token post-load:', e.message); }
    }
    checkGPU(lobbyWindow).then(flag => {
      if (flag) {
        integrityFlags.push(flag);
        console.log(`[Integrity] VM GPU detected: ${flag.details}`);
      }
    });
  });

  lobbyWindow.webContents.on('will-navigate', (e, url) => {
    try {
      const u = new URL(url);
      const ok = url.startsWith(SERVER_URL) || u.protocol === 'data:' ||
                 u.protocol === 'file:' || u.protocol === 'about:';
      if (!ok) e.preventDefault();
    } catch { e.preventDefault(); }
  });
  lobbyWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  lobbyWindow.on('closed', () => { lobbyWindow = null; });
  return lobbyWindow;
}

function createExamWindow() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.focus();
    return mainWindow;
  }

  isKiosk = KIOSK_ALLOWED;

  mainWindow = new BrowserWindow({
    fullscreen: isKiosk, kiosk: isKiosk, alwaysOnTop: isKiosk,
    resizable: !isKiosk, movable: !isKiosk, minimizable: !isKiosk,
    maximizable: !isKiosk, closable: !isKiosk, frame: !isKiosk,
    width: EXAM_WIDTH, height: EXAM_HEIGHT,
    autoHideMenuBar: true, backgroundColor: '#06080d',
    webPreferences: {
      preload: path.join(__dirname, '..', 'preload.js'),
      contextIsolation: true, nodeIntegration: false, devTools: !isKiosk,
      backgroundThrottling: false,
      offscreen: false,
      spellcheck: false,
    }
  });

  mainWindow.loadFile(path.join(__dirname, '..', 'renderer', 'index.html'));
  if (!isKiosk) mainWindow.webContents.openDevTools();

  mainWindow.webContents.on('will-navigate', (e, url) => {
    if (!url.startsWith(SERVER_URL) && !url.startsWith('file://')) e.preventDefault();
  });
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  mainWindow.webContents.on('devtools-opened', () => {
    if (isKiosk) mainWindow.webContents.closeDevTools();
  });
  mainWindow.webContents.on('render-process-gone', (_, details) => {
    console.log('[App] Renderer gone:', details.reason, '— reloading');
    try { if (mainWindow && !mainWindow.isDestroyed()) mainWindow.reload(); } catch(e) {}
  });

  try { clipboard.clear(); } catch(e) {}

  if (startProcessMonitor) startProcessMonitor();

  if (isKiosk) {
    mainWindow.on('blur',  () => { if (mainWindow && !mainWindow.isDestroyed()) mainWindow.focus(); });
    mainWindow.on('close', e => e.preventDefault());
    powerBlockId = powerSaveBlocker.start('prevent-display-sleep');

    const panicOk = globalShortcut.register(PANIC_SHORTCUT, async () => {
      if (!mainWindow || mainWindow.isDestroyed()) return;
      try {
        const confirmed = await mainWindow.webContents.executeJavaScript(`
          (function() {
            return confirm('PANIC UNLOCK\\n\\n' +
              'This closes the app and flags your session for your teacher to review.\\n\\n' +
              'Your work will NOT be submitted automatically.\\n\\nContinue?');
          })()
        `);
        if (!confirmed) return;
        await handlePanicUnlock('student-triggered');
      } catch(e) { console.error('[Panic] chord error:', e.message); }
    });
    if (!panicOk || !globalShortcut.isRegistered(PANIC_SHORTCUT)) {
      console.error(`[Panic] globalShortcut.register('${PANIC_SHORTCUT}') FAILED`);
    } else {
      console.log(`[Panic] chord armed: ${PANIC_SHORTCUT}`);
    }

    globalShortcut.registerAll(BLOCKED_SHORTCUTS, () => false);

    globalShortcut.register(EMERGENCY_SHORTCUT, () => {
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.executeJavaScript(`
          (function() {
            const code = prompt('Emergency exit — enter admin code:');
            window.proctor && window.proctor.adminExit(code);
          })()
        `);
      }
    });
  }
}

function releaseKiosk({ reopenLobby = true } = {}) {
  console.log('[Kiosk] releasing', reopenLobby ? '(→ lobby)' : '(→ quit)');
  isKiosk = false;

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
      winRef.destroy();
      console.log('[Kiosk] window destroyed');
    } catch(e) { console.error('[Kiosk] destroy error:', e.message); }
  }

  setTimeout(() => {
    if (winRef && !winRef.isDestroyed()) {
      console.error('[Kiosk] window survived destroy(); retrying');
      try { winRef.destroy(); } catch(e) {}
    }
  }, 500);

  try { if (stopPythonFn) stopPythonFn(); } catch(e) {}
  try { if (stopPollingFn) stopPollingFn(); } catch(e) {}
  try { if (stopProcessMonitorFn) stopProcessMonitorFn(); } catch(e) {}
  try { globalShortcut.unregisterAll(); } catch(e) {}
  if (powerBlockId !== null) {
    try { powerSaveBlocker.stop(powerBlockId); } catch(e) {}
    powerBlockId = null;
  }

  currentSessionId = null;
  examContext = null;
  studentToken = null;
  calBiases = null;

  if (reopenLobby) {
    setTimeout(() => createLobbyWindow(), 200);
  }
}

async function handlePanicUnlock(reason) {
  const sid = currentSessionId;
  if (sid && studentToken) {
    try {
      const ac = new AbortController();
      const timer = setTimeout(() => ac.abort(), 5000);
      await fetch(`${SERVER_URL}/event`, {
        method: 'POST', headers: { 'Content-Type': 'application/json',
          ...(studentToken ? { 'Authorization': `Bearer ${studentToken}` } : {}) },
        signal: ac.signal,
        body: JSON.stringify({ session_id: sid, event_type: 'panic_unlock',
          severity: 'high',
          details: `Panic unlock triggered (${reason}). Session left in_progress for teacher review.` }),
      });
      clearTimeout(timer);
    } catch(e) { console.error('[Panic] event post failed:', e.message); }
  }
  console.log(`[Panic] reason=${reason} — quitting app`);
  try { releaseKiosk({ reopenLobby: false }); }
  catch(e) { console.error('[Panic] releaseKiosk threw:', e.message); }

  try { if (lobbyWindow && !lobbyWindow.isDestroyed()) lobbyWindow.destroy(); } catch(e) {}
  try { app.quit(); } catch(e) { console.error('[Panic] app.quit:', e.message); }
  setTimeout(() => {
    console.error('[Panic] graceful quit did not take — app.exit(0)');
    try { app.exit(0); } catch(e) { process.exit(0); }
  }, 2000);
}

module.exports = {
  createLobbyWindow, createExamWindow, releaseKiosk, handlePanicUnlock,
  receiveInviteToken, consumeInviteToken, getPendingInviteToken,
  getMainWindow, setMainWindow, getLobbyWindow, setLobbyWindow,
  getIntegrityFlags, getIntegrityReady, setIntegrityReady, pushIntegrityFlag,
  getCurrentSessionId, setCurrentSessionId, getExamContext, setExamContext,
  getStudentToken, setStudentToken, getCalBiases, setCalBiases,
  getPowerBlockId, setPowerBlockId, getIsKiosk,
  setMonitorFns, setPollingFns, setPythonFns,
};
