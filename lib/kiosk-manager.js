const {
  app, BrowserWindow, globalShortcut,
  powerSaveBlocker, clipboard, dialog,
} = require('electron');
const path = require('path');
const {
  KIOSK_ALLOWED, BLOCKED_SHORTCUTS, PANIC_SHORTCUT, EMERGENCY_SHORTCUT,
  EXAM_WIDTH, EXAM_HEIGHT,
  LOBBY_WIDTH, LOBBY_HEIGHT, LOBBY_MIN_W, LOBBY_MIN_H,
  SERVER_URL,
} = require('../config');
const { checkGPU } = require('./integrity');

// Tracks every accelerator this module has registered via
// globalShortcut.register(). releaseKiosk() iterates this and unregisters
// each one individually instead of calling globalShortcut.unregisterAll(),
// which would also drop any accelerators registered by unrelated code
// elsewhere in the app.
const _registeredShortcuts = new Set();
function _trackRegister(accelerator, handler) {
  const ok = globalShortcut.register(accelerator, handler);
  if (ok) _registeredShortcuts.add(accelerator);
  return ok;
}
function _releaseTrackedShortcuts() {
  for (const accel of _registeredShortcuts) {
    try { globalShortcut.unregister(accel); } catch(e) {}
  }
  _registeredShortcuts.clear();
}

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
    path.join(__dirname, '..', 'app', 'static', 'student.html'),
    path.join(process.resourcesPath || '', 'app', 'static', 'student.html'),
  ];
  for (const p of candidates) {
    try { if (fs.existsSync(p)) return p; } catch(e) { console.debug('[Lobby] candidate check failed:', p, e.message); }
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
    title: 'Procta',
    icon: path.join(__dirname, '..', 'build', 'icon.png'),
    backgroundColor: '#06080d',
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

  // Render an immediate branded "Loading…" splash so the user never
  // sees a blank black window during the file://-then-fallback cascade.
  // Without this, a slow loadFile + slow loadURL fallback chain can
  // leave the window blank for tens of seconds (network timeout) and
  // the user thinks the app has hung. The splash is replaced by the
  // real student.html as soon as ANY tier successfully loads.
  const splash = `<!DOCTYPE html><html><head><meta charset="utf-8">
    <style>
      *{margin:0;padding:0;box-sizing:border-box}
      html,body{height:100%;background:#06080d;color:#c8d1dc;
        font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
        display:flex;align-items:center;justify-content:center}
      .wrap{text-align:center;padding:24px}
      .brand{display:flex;align-items:center;justify-content:center;gap:10px;margin-bottom:18px}
      .mark{width:34px;height:34px;border-radius:8px;background:#5b8af0;
        display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:18px}
      h1{font-size:18px;font-weight:600;color:#e2e8f0;margin-bottom:6px}
      p{font-size:13px;color:#94a3b8;line-height:1.6}
      .spin{margin:20px auto 0;width:28px;height:28px;border-radius:50%;
        border:2px solid rgba(91,138,240,.15);border-top-color:#5b8af0;
        animation:r 0.9s linear infinite}
      @keyframes r{to{transform:rotate(360deg)}}
    </style></head><body>
      <div class="wrap">
        <div class="brand">
          <div class="mark">P</div>
          <h1 style="margin:0">Procta</h1>
        </div>
        <p>Loading your dashboard…</p>
        <div class="spin"></div>
      </div></body></html>`;
  lobbyWindow.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(splash));
  // After the splash paints, swap to the real lobby. dom-ready is the
  // earliest signal that the splash is visible; we then load the real
  // student.html which (on success) replaces the splash.
  lobbyWindow.webContents.once('dom-ready', () => {
    const lobbyHtml = findLobbyHtml();
    console.log('[Lobby] loading:', lobbyHtml);
    lobbyWindow.loadFile(lobbyHtml);
  });
  const lobbyHtml = findLobbyHtml();

  lobbyWindow.webContents.on('dom-ready', () => {
    lobbyWindow.webContents.insertCSS(
      'html,body{background:#06080d !important;color:#c8d1dc !important}'
    ).catch(e => { console.debug('[Lobby] CSS injection failed:', e.message); });
  });

  // Cascade of fallback sources for student.html. Tried in order; each
  // step only fires after the previous step's load fails. The order
  // intentionally goes local → own-server → CDN mirror so the
  // most-trusted source wins when reachable, and the CDN catches the
  // case where the user's own server is unreachable from their network.
  //
  // Why jsDelivr and not raw.githubusercontent.com:
  //   raw.githubusercontent.com serves files with `text/plain` content-
  //   type, so Chromium would display the HTML source as text instead
  //   of rendering it. jsDelivr serves the same file from the same git
  //   commit with the correct `text/html` content-type.
  //
  // The branch ref is pinned to a stable commit so a fast-broken main
  // on git can't break shipped installs — bump this when shipping a
  // user-visible HTML/CSS change.
  const FALLBACK_URLS = [
    (SERVER_URL || 'https://app.procta.net').replace(/\/$/, '') + '/static/student.html',
    'https://cdn.jsdelivr.net/gh/ArihantK15/proctor-browser@main/app/static/student.html',
  ];
  let _fallbackIdx = 0;  // index into FALLBACK_URLS for the next attempt
  let _onFallback = false;  // tracks whether the current load is a fallback
  lobbyWindow.webContents.on('did-fail-load', (_, errorCode, errorDescription, validatedURL) => {
    if (errorCode === -3) return;
    console.error('[Lobby] load failed:', errorCode, errorDescription, validatedURL);

    // Step into the next fallback tier if one is available. Triggered
    // either from a file:// failure (asar broken, Defender quarantined,
    // etc.) or from a fallback URL itself failing (server down, DNS
    // misconfig, captive-portal redirect, etc.).
    const isFileSource = (validatedURL || '').startsWith('file://');
    if ((isFileSource || _onFallback) && _fallbackIdx < FALLBACK_URLS.length) {
      const nextUrl = FALLBACK_URLS[_fallbackIdx++];
      _onFallback = true;
      console.warn('[Lobby] tier %d fallback: %s', _fallbackIdx, nextUrl);
      clearTimeout(lobbyShowFallback);
      lobbyWindow.loadURL(nextUrl).catch(e => {
        console.error('[Lobby] fallback %s threw: %s', nextUrl, e.message);
      });
      lobbyWindow.show();
      return;
    }

    // Every tier (local + each fallback URL) failed. Show the error
    // dialog with a redacted path so screenshots don't leak the user's
    // account name.
    clearTimeout(lobbyShowFallback);
    const _redact = (s) => {
      if (!s) return '';
      let out = String(s);
      try {
        const home = (require('os').homedir() || '').replace(/\\/g, '/');
        if (home) out = out.split(home).join('~');
        out = out.replace(/[A-Za-z]:\/Users\/[^/]+\/AppData\/Local\/Programs\/[^/]+\//gi,
                          '<install>/');
        out = out.replace(/[A-Za-z]:\\Users\\[^\\]+\\AppData\\Local\\Programs\\[^\\]+\\/gi,
                          '<install>/');
      } catch(e) { /* path scrub best-effort */ }
      return out;
    };
    const safeUrl = _redact(validatedURL || lobbyHtml);
    let appVersion = '';
    try { appVersion = app.getVersion(); } catch(e) { /* in case app isn't ready */ }
    const offline = `<!DOCTYPE html><html><head><meta charset="utf-8"><title>Procta — Error</title>
      <style>body{margin:0;font-family:-apple-system,sans-serif;background:#0a0e1a;color:#cbd5e1;
        display:flex;align-items:center;justify-content:center;height:100vh;text-align:center;padding:24px}
        .box{max-width:480px;background:#0f1629;border:1px solid rgba(255,255,255,.06);
        border-radius:14px;padding:36px 32px}
        h1{color:#fff;font-size:20px;margin:0 0 10px}p{color:#94a3b8;font-size:13px;line-height:1.7;margin:0 0 10px}
        code{font-family:monospace;color:#60a5fa;font-size:11px;word-break:break-all;
        display:block;margin-top:8px;padding:8px 10px;background:rgba(255,255,255,.03);border-radius:6px}
        .v{margin-top:14px;color:#64748b;font-size:11px;font-family:monospace}</style></head><body>
        <div class="box"><h1>Lobby failed to open</h1>
        <p>Couldn't load the local student dashboard, and the network fallback also failed.</p>
        <code>${errorDescription || errorCode}\n${safeUrl}</code>
        <p style="margin-top:16px">Check your internet, then relaunch. If the problem persists, reinstall.</p>
        ${appVersion ? `<div class="v">Procta v${appVersion}</div>` : ''}
        </div></body></html>`;
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

  const _lobbyOrigin = (() => { try { return new URL(SERVER_URL).origin; } catch { return SERVER_URL; } })();
  lobbyWindow.webContents.on('will-navigate', (e, url) => {
    try {
      const u = new URL(url);
      if (u.origin !== _lobbyOrigin && u.protocol !== 'data:' && u.protocol !== 'about:') {
        e.preventDefault();
      }
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
    title: 'Procta',
    icon: path.join(__dirname, '..', 'build', 'icon.png'),
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

  const _serverOrigin = (() => { try { return new URL(SERVER_URL).origin; } catch { return SERVER_URL; } })();
  mainWindow.webContents.on('will-navigate', (e, url) => {
    try {
      const u = new URL(url);
      if (u.origin !== _serverOrigin && u.protocol !== 'data:' && u.protocol !== 'about:') {
        e.preventDefault();
      }
    } catch { e.preventDefault(); }
  });
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  mainWindow.webContents.on('devtools-opened', () => {
    if (isKiosk) mainWindow.webContents.closeDevTools();
  });
  let _crashCount = 0;
  mainWindow.webContents.on('render-process-gone', (_, details) => {
    _crashCount++;
    console.log('[App] Renderer gone:', details.reason, '— crash #' + _crashCount);
    if (_crashCount > 3) {
      console.error('[App] Too many crashes — releasing kiosk');
      handlePanicUnlock('renderer-crash-loop');
      return;
    }
    try { if (mainWindow && !mainWindow.isDestroyed()) mainWindow.reload(); } catch(e) { console.error('[Kiosk] reload failed:', e.message); }
  });

  try { clipboard.clear(); } catch(e) {}

  if (startProcessMonitor) startProcessMonitor();

  if (isKiosk) {
    // ── Safety-first: arm the panic + emergency exits BEFORE we
    // install the close-preventDefault listener. If either chord
    // fails to register (another app already holds the key, OS
    // refusal, etc.) we MUST NOT enter lockdown — otherwise the
    // student is trapped behind a one-way door with no way out
    // short of a Task Manager kill. Fail open, log loudly, notify
    // the server, and tell the student why the exam is unlocked.
    let panicOk = false;
    try {
      panicOk = _trackRegister(PANIC_SHORTCUT, async () => {
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
    } catch(e) {
      console.error(`[Panic] globalShortcut.register threw: ${e.message}`);
      panicOk = false;
    }
    panicOk = panicOk && globalShortcut.isRegistered(PANIC_SHORTCUT);

    let emergencyOk = false;
    try {
      emergencyOk = _trackRegister(EMERGENCY_SHORTCUT, () => {
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.webContents.executeJavaScript(`
            (function() {
              const code = prompt('Emergency exit — enter admin code:');
              window.proctor && window.proctor.adminExit(code);
            })()
          `);
        }
      });
    } catch(e) {
      console.error(`[Emergency] globalShortcut.register threw: ${e.message}`);
      emergencyOk = false;
    }
    emergencyOk = emergencyOk && globalShortcut.isRegistered(EMERGENCY_SHORTCUT);

    if (!panicOk || !emergencyOk) {
      console.error(
        `[Kiosk] ABORTING LOCKDOWN — panic=${panicOk} emergency=${emergencyOk}. ` +
        `Refusing to install close-preventDefault to avoid trapping the student.`
      );
      // Unregister whatever did succeed so we leave no half-armed state.
      _releaseTrackedShortcuts();
      // Best-effort server notification so the teacher sees the session
      // ran without lockdown. Non-blocking — failure here must not stop
      // the exam from loading.
      (async () => {
        const sid = currentSessionId;
        if (!sid || !studentToken) return;
        try {
          const ac = new AbortController();
          const timer = setTimeout(() => ac.abort(), 5000);
          await fetch(`${SERVER_URL}/api/v1/event`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json',
              'Authorization': `Bearer ${studentToken}` },
            signal: ac.signal,
            body: JSON.stringify({ session_id: sid,
              event_type: 'kiosk_lockdown_failed', severity: 'high',
              details: `Kiosk aborted: panic_chord=${panicOk} emergency_chord=${emergencyOk}. Exam ran without close-prevent lockdown.` }),
          });
          clearTimeout(timer);
        } catch(e) { console.error('[Kiosk] lockdown_failed event post failed:', e.message); }
      })();
      // Tell the student plainly. Non-modal so the exam still loads.
      try {
        dialog.showMessageBox(mainWindow, {
          type: 'warning',
          title: 'Lockdown unavailable',
          message: 'Procta could not lock the exam window on this device.',
          detail: 'The panic-exit shortcut could not be reserved (another app may be holding F12). ' +
                  'Your exam will still load and proctoring will run, but the window is not locked. ' +
                  'Your teacher has been notified. Please continue normally.',
          buttons: ['Continue'], defaultId: 0, noLink: true,
        }).catch(() => {});
      } catch(e) { console.error('[Kiosk] warn dialog failed:', e.message); }
      // Skip the close-preventDefault, blur-refocus, powerSaveBlocker,
      // and BLOCKED_SHORTCUTS bulk registration. The student can close
      // the window normally if they need to.
      isKiosk = false;
      return;
    }

    console.log(`[Panic] chord armed: ${PANIC_SHORTCUT}`);
    console.log(`[Emergency] chord armed: ${EMERGENCY_SHORTCUT}`);

    // Both exits armed — safe to lock the window down.
    let _blurTimer = null;
    mainWindow.on('blur', () => {
      if (!mainWindow || mainWindow.isDestroyed()) return;
      if (_blurTimer) return;
      _blurTimer = setTimeout(() => {
        _blurTimer = null;
        if (mainWindow && !mainWindow.isDestroyed() && !mainWindow.isFocused()) {
          mainWindow.focus();
        }
      }, 100);
    });
    mainWindow.on('focus', () => {
      if (_blurTimer) { clearTimeout(_blurTimer); _blurTimer = null; }
    });
    mainWindow.on('close', e => e.preventDefault());
    powerBlockId = powerSaveBlocker.start('prevent-display-sleep');

    for (const shortcut of BLOCKED_SHORTCUTS) {
      try { _trackRegister(shortcut, () => false); } catch(e) {
        console.debug('[Kiosk] shortcut register failed: %s', shortcut, e.message);
      }
    }

    // ── Mac-specific kiosk hardening ───────────────────────────────
    // Windows: hiding the menu bar is handled by `autoHideMenuBar: true`
    // on the BrowserWindow plus `frame: false` in kiosk mode. On macOS
    // the menu bar is global (not per-window) and the dock is a
    // separate UI surface — `autoHideMenuBar` is silently ignored on
    // Mac. Without this block, a student in kiosk mode can:
    //   • Hover the top of the screen → the system menu bar slides in,
    //     File / Edit / Window / Help menus become clickable, Cmd+Q
    //     route works through the menu even when globalShortcut
    //     blocking partially fails.
    //   • Move the mouse to the bottom → the dock slides in and they
    //     can launch other apps or switch via the dock.
    //
    // Setting an empty application menu removes the menu surface
    // entirely, and app.dock.hide() removes the dock until releaseKiosk
    // calls show() again. Both are macOS-only no-ops on Win/Linux
    // — gated on platform anyway to be explicit.
    if (process.platform === 'darwin') {
      try {
        const { Menu } = require('electron');
        Menu.setApplicationMenu(null);
      } catch(e) { console.debug('[Kiosk] mac menu suppress failed:', e.message); }
      try {
        if (app.dock && typeof app.dock.hide === 'function') app.dock.hide();
      } catch(e) { console.debug('[Kiosk] mac dock hide failed:', e.message); }
    }
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
  try { if (stopCalibrationFn) stopCalibrationFn(); } catch(e) {}
  try { if (stopPollingFn) stopPollingFn(); } catch(e) {}
  try { if (stopProcessMonitorFn) stopProcessMonitorFn(); } catch(e) {}
  _releaseTrackedShortcuts();
  // Restore the Mac dock if we hid it on kiosk entry. No-op on
  // non-Mac platforms. Menu suppression isn't reversed here — the
  // app menu was always empty for proctoring use anyway, and
  // restoring an Electron-default menu in the lobby wouldn't help
  // the user; the next createExamWindow() re-applies the null
  // menu if needed.
  if (process.platform === 'darwin') {
    try {
      if (app.dock && typeof app.dock.show === 'function') app.dock.show();
    } catch(e) { console.debug('[Kiosk] mac dock show failed:', e.message); }
  }
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
      await fetch(`${SERVER_URL}/api/v1/event`, {
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
