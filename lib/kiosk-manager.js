const {
  app, BrowserWindow, globalShortcut,
  powerSaveBlocker, clipboard, dialog, screen,
} = require('electron');
const path = require('path');
const os = require('os');
const fs = require('fs');
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

// Best-effort attempt to disable Mission Control / Spaces system-wide for
// the logged-in macOS user, researched 2026-07-04. Trackpad swipe gestures
// for Mission Control/Space-switching can't be intercepted by any app-level
// API (see config.js's _BLOCKED_MAC comment) — but `mcx-expose-disabled` is
// a per-user cfprefsd preference (`com.apple.dock` domain, no MDM/root/
// entitlement required) that tells the Dock process — which owns Mission
// Control/Expose activation for EVERY trigger method (hot corner, F3,
// Control+arrows, AND the trackpad gesture) — not to respond at all. If it
// takes effect, this closes the trackpad-gesture gap at the source instead
// of trying to intercept the resulting action.
// Evidence is genuinely mixed on how reliably this behaves across macOS
// versions (some community reports of it not applying cleanly on newer
// releases) — could not verify firsthand without macOS hardware access in
// this environment. Treat this as an ADDITIONAL best-effort layer, not a
// replacement for the existing keyboard-shortcut blocking or the
// blur/focus refocus watcher — if it silently no-ops on a given macOS
// version, those remain the real defense. Never let a failure here affect
// the exam: always wrapped, always logged, never thrown.
function _setMacMissionControlDisabled(disabled) {
  if (process.platform !== 'darwin') return;
  try {
    const { execFileSync } = require('child_process');
    if (disabled) {
      execFileSync('defaults', ['write', 'com.apple.dock', 'mcx-expose-disabled', '-bool', 'TRUE'], { stdio: 'ignore' });
    } else {
      // Check first — restoring is called defensively (every kiosk
      // release AND once at app startup, for crash recovery), and
      // `killall Dock` visibly restarts the Dock for every user on every
      // launch if done unconditionally. Only touch anything if the key is
      // actually set, so a normal launch/exit where it was never disabled
      // causes zero Dock disruption.
      let current = '';
      try {
        current = execFileSync('defaults', ['read', 'com.apple.dock', 'mcx-expose-disabled'], { stdio: ['ignore', 'pipe', 'ignore'] })
          .toString().trim();
      } catch(e) { return; } // key not set — nothing to restore, no Dock restart
      if (current !== '1' && current.toLowerCase() !== 'true') return;
      execFileSync('defaults', ['delete', 'com.apple.dock', 'mcx-expose-disabled'], { stdio: 'ignore' });
    }
    // The Dock owns Mission Control activation, so it only picks up the
    // preference change after restarting. This also resets any dock-hide
    // state the caller applied via app.dock.hide() — callers must
    // re-apply that AFTER calling this, not before.
    execFileSync('killall', ['Dock'], { stdio: 'ignore' });
  } catch(e) {
    console.debug('[Kiosk] mac Mission Control %s failed: %s', disabled ? 'disable' : 'restore', e.message);
  }
}

// ── Mission Control watchdog (crash-safety net) ─────────────────────
// Explicitly authorized 2026-07-04 after flagging the tradeoff: this
// installs a per-user launchd LaunchAgent (~/Library/LaunchAgents), a
// persistence mechanism that runs shell commands independent of whether
// this app process is even alive. Justification: _setMacMissionControlDisabled's
// restore call only runs from THIS process's own JS (releaseKiosk,
// before-quit, startup) — if the process is killed outright (SIGKILL,
// Force Quit via Activity Monitor, power loss, macOS crash, laptop lid
// closed until battery dies), none of that JS ever runs, and the
// student's Mission Control stays disabled indefinitely until they
// happen to relaunch Procta. The exam ending must not depend on the app
// surviving to clean up after itself.
//
// Design: arm schedules a job that fires WATCHDOG_SECS after being
// loaded (independent of this process), restores Mission Control, then
// deletes and unloads itself — a one-shot, self-cleaning launchd job.
// Every normal release path (releaseKiosk, before-quit, startup) disarms
// it immediately, so it only ever actually fires in the crash scenario
// it exists for. (`at`/atrun was considered and rejected: disabled by
// default on macOS, and re-enabling it needs sudo plus a Full Disk
// Access grant — not something to ask a student's personal, unmanaged
// laptop to do. launchd needs neither.)
const _WATCHDOG_LABEL = 'net.procta.missioncontrol-watchdog';
const _WATCHDOG_SECS = 6 * 60 * 60; // generous ceiling past any real exam length
function _watchdogPaths() {
  const dir = path.join(os.homedir(), 'Library', 'LaunchAgents');
  return { dir, plist: path.join(dir, `${_WATCHDOG_LABEL}.plist`) };
}
function _armMissionControlWatchdog() {
  if (process.platform !== 'darwin') return;
  try {
    const { execFileSync } = require('child_process');
    const { dir, plist } = _watchdogPaths();
    fs.mkdirSync(dir, { recursive: true });
    // The job's own script deletes this plist and unloads itself the
    // moment it fires — a standard, widely-used "run once after a delay,
    // self-cleaning" launchd pattern. StartInterval (not RunAtLoad) means
    // it fires WATCHDOG_SECS after being loaded, not immediately.
    const script = `defaults delete com.apple.dock mcx-expose-disabled 2>/dev/null; killall Dock 2>/dev/null; rm -f '${plist}'; launchctl bootout gui/$(id -u)/${_WATCHDOG_LABEL} 2>/dev/null`;
    const xml = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>${_WATCHDOG_LABEL}</string>
  <key>ProgramArguments</key><array><string>/bin/sh</string><string>-c</string><string>${script}</string></array>
  <key>StartInterval</key><integer>${_WATCHDOG_SECS}</integer>
  <key>RunAtLoad</key><false/>
</dict></plist>
`;
    fs.writeFileSync(plist, xml, { mode: 0o644 });
    // Unload any stale prior copy first (harmless if none exists) so a
    // reload always picks up a fresh timer rather than an old one.
    try { execFileSync('launchctl', ['unload', plist], { stdio: 'ignore' }); } catch(e) {}
    execFileSync('launchctl', ['load', '-w', plist], { stdio: 'ignore' });
  } catch(e) {
    console.debug('[Kiosk] mac Mission Control watchdog arm failed:', e.message);
  }
}
function _disarmMissionControlWatchdog() {
  if (process.platform !== 'darwin') return;
  try {
    const { execFileSync } = require('child_process');
    const { plist } = _watchdogPaths();
    try { execFileSync('launchctl', ['unload', plist], { stdio: 'ignore' }); } catch(e) {}
    try { fs.unlinkSync(plist); } catch(e) {}
  } catch(e) {
    console.debug('[Kiosk] mac Mission Control watchdog disarm failed:', e.message);
  }
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
function setPollingFns(stop) { stopPollingFn = stop; }
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
  let _gpuChecked = false;  // GPU probe runs once per lobby, not per reload
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
      contextIsolation: true, nodeIntegration: false, sandbox: true, devTools: !app.isPackaged,
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
  // After the splash paints, swap to the real lobby. We use the custom
  // procta-lobby:// scheme registered in main.js — it reads the
  // bundled file via Node's fs (the patched-by-Electron asar path that
  // works reliably) and serves with explicit text/html. Previously this
  // call was loadFile() against a file:// URL inside the asar, which
  // intermittently failed on packaged builds (the 2.3.12/2.3.13 blank-
  // window symptom) for reasons we couldn't trace remotely.
  lobbyWindow.webContents.once('dom-ready', () => {
    console.log('[Lobby] loading procta-lobby://lobby/student.html');
    lobbyWindow.loadURL('procta-lobby://lobby/student.html');
  });

  lobbyWindow.webContents.on('dom-ready', () => {
    lobbyWindow.webContents.insertCSS(
      'html,body{background:#06080d !important;color:#c8d1dc !important}'
    ).catch(e => { console.debug('[Lobby] CSS injection failed:', e.message); });
  });

  // Network fallback — fires only if the custom-protocol load itself
  // throws (e.g. the asar is so broken Node can't read it). The own-
  // server URL is the only remaining tier: jsDelivr was removed
  // because it serves .html files as text/plain with X-Content-Type-
  // Options: nosniff, so Chromium refuses to render — the user saw
  // raw HTML source. github.io (Pages) or a release-asset URL would
  // serve correctly but require manual setup outside this fix's
  // scope.
  const FALLBACK_URLS = [
    (SERVER_URL || 'https://app.procta.net').replace(/\/$/, '') + '/static/student.html',
  ];
  let _fallbackIdx = 0;
  let _onFallback = false;
  lobbyWindow.webContents.on('did-fail-load', (_, errorCode, errorDescription, validatedURL) => {
    if (errorCode === -3) return;
    console.error('[Lobby] load failed:', errorCode, errorDescription, validatedURL);

    // Step into the next fallback tier if one is available. Triggered
    // either by the custom-protocol load failing (asar genuinely
    // unreadable) or by a previous fallback URL itself failing.
    const isLobbySource = (validatedURL || '').startsWith('procta-lobby://');
    if ((isLobbySource || _onFallback) && _fallbackIdx < FALLBACK_URLS.length) {
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
    const safeUrl = _redact(validatedURL || 'procta-lobby://lobby/student.html');
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
    // did-finish-load fires on EVERY (re)load of the lobby — re-login,
    // offline retry, navigation. Guard so the GPU probe runs (and pushes its
    // flag) at most once, otherwise the same finding stacks up and the student
    // sees duplicate "Virtual Machine" entries on the block screen.
    if (!_gpuChecked) {
      _gpuChecked = true;
      checkGPU(lobbyWindow).then(flag => {
        if (flag) {
          integrityFlags.push(flag);
          console.log(`[Integrity] VM GPU detected: ${flag.details}`);
        }
      });
    }
  });

  const _lobbyOrigin = (() => { try { return new URL(SERVER_URL).origin; } catch { return SERVER_URL; } })();
  lobbyWindow.webContents.on('will-navigate', (e, url) => {
    try {
      const u = new URL(url);
      // Allow the lobby renderer's own scheme (it is served via
      // procta-lobby://lobby) alongside the server origin + data/about —
      // mirrors the exam window's guard. Without procta-lobby: here, a
      // same-scheme top-frame navigation inside the lobby (the lobby was
      // moved onto this scheme; this guard predated that) would be blocked.
      if (u.origin !== _lobbyOrigin && u.protocol !== 'data:'
          && u.protocol !== 'about:' && u.protocol !== 'procta-lobby:') {
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

  // `isKiosk` is the INTENDED lockdown state. We deliberately construct
  // the window UNARMED (closable, not-kiosk, not-always-on-top) and only
  // apply the lockdown at runtime once content has actually loaded
  // (did-finish-load). If the exam HTML fails to load (corrupt/unsigned
  // asar, missing file) we must never end up with a black, unclosable,
  // always-on-top fullscreen window the student can't escape — the panic
  // chord needs a live renderer for confirm() and would be useless there.
  // `frame` is the one property that can't be toggled after construction,
  // so we set it up-front; a frameless-but-closable window is safe.
  isKiosk = KIOSK_ALLOWED;

  // Tier 1.1 — refuse-to-start when packaged and kiosk flag is off
  if (app.isPackaged && !KIOSK_ALLOWED) {
    console.error('[Kiosk] REFUSE: packaged build with KIOSK_ALLOWED=false — blocking exam');
    const locked = mainWindow;
    if (!locked || locked.isDestroyed()) {
      const block = new BrowserWindow({
        width: EXAM_WIDTH, height: EXAM_HEIGHT,
        frame: true, closable: true,
        title: 'Procta', autoHideMenuBar: true, backgroundColor: '#06080d',
        show: false,
        webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
      });
      block.setContentProtection(true);
      block.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(
        '<!DOCTYPE html><html><head><meta charset="utf-8"><title>Procta</title>' +
        '<style>body{margin:0;font-family:-apple-system,sans-serif;background:#0a0e1a;' +
        'color:#cbd5e1;display:flex;align-items:center;justify-content:center;' +
        'height:100vh;text-align:center;padding:24px}.box{max-width:440px;' +
        'background:#0f1629;border:1px solid rgba(255,255,255,.06);border-radius:14px;' +
        'padding:36px 32px}h1{color:#fff;font-size:18px;margin:0 0 10px}' +
        'p{color:#94a3b8;font-size:13px;line-height:1.7;margin:0}</style></head><body>' +
        '<div class="box"><h1>Secure browser required</h1>' +
        '<p>Procta did not start in secure-browser mode. Please restart Procta normally ' +
        'from your Applications folder or Start menu.<br><br>' +
        'If the problem persists, reinstall the latest version of Procta.</p></div></body></html>'
      ));
      block.once('ready-to-show', () => block.show());
      mainWindow = block;
    }
    return;
  }

  let _lockdownArmed = false;
  let _loadFailed = false;

  mainWindow = new BrowserWindow({
    fullscreen: false, kiosk: false, alwaysOnTop: false,
    resizable: true, movable: true, minimizable: true,
    maximizable: true, closable: true, frame: !isKiosk,
    show: false,
    width: EXAM_WIDTH, height: EXAM_HEIGHT,
    title: 'Procta',
    icon: path.join(__dirname, '..', 'build', 'icon.png'),
    autoHideMenuBar: true, backgroundColor: '#06080d',
    webPreferences: {
      preload: path.join(__dirname, '..', 'preload.js'),
      contextIsolation: true, nodeIntegration: false, sandbox: true,
      devTools: !app.isPackaged,
      webviewTag: false,
      backgroundThrottling: false,
      offscreen: false,
      spellcheck: false,
    }
  });

  // Windows: this genuinely works — SetWindowDisplayAffinity blacks the
  // window out at the compositor level for ANY capture method (screen
  // recorder, remote-desktop tool, browser screen-share), known or not.
  //
  // macOS: researched 2026-07-04 — setContentProtection does NOT work at
  // all here, confirmed by multiple still-open Electron issues (#31787,
  // #14415, #38994, #48258): the window remains fully visible to native
  // screen recording, AnyDesk/Splashtop/TeamViewer, and Zoom/Teams/Tencent
  // Meeting screen-share, despite this call succeeding with no error.
  // There is no public macOS API that gives Windows' compositor-level
  // guarantee — Apple's own Screen Recording protections are for
  // *permission-gating* an app's own capture, not for making another
  // app's window invisible to a third-party capturer.
  //
  // Net effect: on macOS, the ONLY defense against screen capture/sharing
  // during an exam is config.js's THREATS process-name detection (catches
  // KNOWN tools by process name — AnyDesk, TeamViewer, OBS, Zoom, etc.).
  // That's an allowlist-style, detection-only defense, structurally weaker
  // than Windows' capture-level block: it misses unlisted/custom capture
  // tools, macOS's own Cmd+Shift+5 recorder (see config.js, also unblocked
  // there), and doesn't prevent capture, only flags a known tool's
  // presence. Real mitigation path is DETECTION of active capture via
  // `systemPreferences.subscribeNotification(
  // 'com.apple.screenIsBeingCapturedDidChange', ...)` — tracked as
  // task_a84b887a, not yet implemented. This is a platform ceiling, not a
  // bug in this call — keeping setContentProtection(true) here is still
  // correct (free on Windows, harmless no-op on Mac).
  mainWindow.setContentProtection(true);

  // PROCTOR_E2E_FORCE_EXAM_LOAD_FAIL: test-only hook that points the exam
  // window at a non-existent file so the e2e harness can verify the
  // did-fail-load fail-OPEN path (escapable error page, never a trapped
  // black screen). Inert in production — the var is never set there.
  // Load the exam renderer via the procta-lobby:// scheme (host 'exam' →
  // renderer/index.html), NOT loadFile(): loadFile against the asar
  // ERR_FILE_NOT_FOUND'd on packaged Windows builds (the exact failure that
  // moved the lobby onto this scheme; the exam window had been left behind).
  // The fs-read protocol handler is the reliable path and gives a stable
  // origin for the renderer's IPC + server fetches. The E2E hook keeps using
  // loadFile to a missing file so it still exercises the did-fail-load
  // fail-open path.
  if (process.env.PROCTOR_E2E_FORCE_EXAM_LOAD_FAIL) {
    mainWindow.loadFile(path.join(__dirname, '..', 'renderer', '__e2e_nonexistent__.html'))
      .catch(e => console.error('[Exam] loadFile threw:', e.message));
  } else {
    mainWindow.loadURL('procta-lobby://exam/index.html')
      .catch(e => console.error('[Exam] loadURL threw:', e.message));
  }
  if (!app.isPackaged && !isKiosk) mainWindow.webContents.openDevTools();

  const _serverOrigin = (() => { try { return new URL(SERVER_URL).origin; } catch { return SERVER_URL; } })();
  mainWindow.webContents.on('will-navigate', (e, url) => {
    try {
      const u = new URL(url);
      // Allow the exam renderer's own scheme (it is served via
      // procta-lobby://exam) alongside the server origin + data/about.
      if (u.origin !== _serverOrigin && u.protocol !== 'data:'
          && u.protocol !== 'about:' && u.protocol !== 'procta-lobby:') {
        e.preventDefault();
      }
    } catch { e.preventDefault(); }
  });
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));

  // Tier 1.6 — block context menu in production
  if (app.isPackaged) {
    mainWindow.webContents.on('context-menu', (e) => e.preventDefault());
  }

  mainWindow.webContents.on('devtools-opened', () => {
    if (app.isPackaged || isKiosk) mainWindow.webContents.closeDevTools();
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

  // ── Load FAILED → fail OPEN, never lock ───────────────────────────
  // If the exam HTML can't load we must NOT arm kiosk. The window was
  // constructed closable/un-kiosked, so we just show a plain escapable
  // error screen and notify the server (non-blocking). No always-on-top
  // black screen, no close-preventDefault — the student can quit.
  mainWindow.webContents.on('did-fail-load', (_e, errorCode, errorDescription, validatedURL, isMainFrame) => {
    if (errorCode === -3) return; // aborted (e.g. superseded by reload)
    // Only a MAIN-FRAME failure means the exam itself couldn't load. A
    // sub-frame failure (if an iframe is ever embedded) must NOT tear down
    // a live, armed exam into the escapable error page — that would be an
    // escape hatch a student could trigger on purpose. Ignore non-main frames.
    if (isMainFrame === false) {
      console.warn('[Exam] sub-frame load failed (ignored):', errorCode, errorDescription, validatedURL);
      return;
    }
    console.error('[Exam] load failed:', errorCode, errorDescription, validatedURL);
    _lockdownArmed = false;
    _loadFailed = true; // so the error page's own did-finish-load won't arm kiosk
    // Notify the server so the teacher sees the build failed to load.
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
            event_type: 'exam_load_failed', severity: 'high',
            details: `Exam window failed to load (${errorCode} ${errorDescription}). Kiosk NOT armed.` }),
        });
        clearTimeout(timer);
      } catch(e) { console.error('[Exam] load_failed event post failed:', e.message); }
    })();
    let appVersion = '';
    try { appVersion = app.getVersion(); } catch(e) {}
    const errPage = `<!DOCTYPE html><html><head><meta charset="utf-8"><title>Procta — Error</title>
      <style>body{margin:0;font-family:-apple-system,sans-serif;background:#0a0e1a;color:#cbd5e1;
        display:flex;align-items:center;justify-content:center;height:100vh;text-align:center;padding:24px}
        .box{max-width:480px;background:#0f1629;border:1px solid rgba(255,255,255,.06);
        border-radius:14px;padding:36px 32px}
        h1{color:#fff;font-size:20px;margin:0 0 10px}p{color:#94a3b8;font-size:13px;line-height:1.7;margin:0 0 10px}
        code{font-family:monospace;color:#60a5fa;font-size:11px;display:block;margin-top:8px;
        padding:8px 10px;background:rgba(255,255,255,.03);border-radius:6px}
        .v{margin-top:14px;color:#64748b;font-size:11px;font-family:monospace}</style></head><body>
        <div class="box"><h1>This exam build couldn't load</h1>
        <p>Procta couldn't open the exam window. Please reinstall the latest version of Procta and try again, or contact your examiner.</p>
        <code>${errorDescription || errorCode}</code>
        <p style="margin-top:16px">You can close this window normally.</p>
        ${appVersion ? `<div class="v">Procta v${appVersion}</div>` : ''}
        </div></body></html>`;
    try {
      mainWindow.setClosable(true);
      mainWindow.setKiosk(false);
      mainWindow.setAlwaysOnTop(false);
      mainWindow.setFullScreen(false);
      mainWindow.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(errPage));
      mainWindow.show();
    } catch(e) { console.error('[Exam] error-page render failed:', e.message); }
  });

  // ── Content loaded OK → NOW arm the lockdown ──────────────────────
  // Only once we have a live renderer do we lock the window. This is the
  // single place kiosk is armed; a failed load never reaches here.
  mainWindow.webContents.on('did-finish-load', () => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    mainWindow.show();
    if (_loadFailed) return;     // showing the escapable error page — never arm
    if (!isKiosk) return;        // dev build / kiosk disabled — nothing to arm
    if (_lockdownArmed) return;  // reload after a crash — already locked
    _armExamLockdown();
  });

  // Fail-open visibility guard. Because we construct the window with
  // show:false and only reveal it on did-finish-load / did-fail-load,
  // a renderer that wedges so badly that NEITHER event ever fires would
  // otherwise leave the student staring at an invisible window — the
  // very "black screen" failure this work is meant to kill. If nothing
  // has shown the window within 12s, show it anyway WITHOUT arming kiosk
  // (no live renderer ⇒ no panic chord ⇒ must stay escapable). A normal
  // load shows in well under a second, so this never affects the happy
  // path; did-finish-load, if it later fires, still arms lockdown.
  const _showFallback = setTimeout(() => {
    try {
      if (mainWindow && !mainWindow.isDestroyed() && !mainWindow.isVisible()) {
        console.error('[Exam] no load event after 12s — showing window unarmed (fail-open)');
        mainWindow.show();
      }
    } catch(e) { console.error('[Exam] show-fallback failed:', e.message); }
  }, 12000);
  _showFallback.unref && _showFallback.unref();
  mainWindow.on('closed', () => { try { clearTimeout(_showFallback); } catch(e) {} });

  try { clipboard.clear(); } catch(e) {}

  if (startProcessMonitor) startProcessMonitor();

  const _armExamLockdown = () => {
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

    // Both exits armed — safe to lock the window down. Apply the kiosk
    // armor at RUNTIME now (it was deliberately not set at construction
    // so a failed load couldn't trap the student).
    try { mainWindow.setKiosk(true); }       catch(e) { console.error('[Kiosk] setKiosk:', e.message); }
    try { mainWindow.setFullScreen(true); }  catch(e) { console.error('[Kiosk] setFullScreen:', e.message); }
    try { mainWindow.setAlwaysOnTop(true); } catch(e) { console.error('[Kiosk] setAlwaysOnTop:', e.message); }
    try { mainWindow.setClosable(false); }   catch(e) { console.error('[Kiosk] setClosable:', e.message); }
    _lockdownArmed = true;

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
    //     File / Edit / Window / Help menus become clickable, giving a
    //     second, mouse-driven Quit route on top of the raw Cmd+Q key.
    //   • Move the mouse to the bottom → the dock slides in and they
    //     can launch other apps or switch via the dock.
    //
    // Setting an empty application menu removes the menu surface
    // entirely, and app.dock.hide() removes the dock until releaseKiosk
    // calls show() again. Both are macOS-only no-ops on Win/Linux
    // — gated on platform anyway to be explicit.
    //
    // NOTE: removing the menu does NOT stop the raw Cmd+Q keystroke —
    // Cocoa routes that to NSApplication terminate: independent of any
    // menu item existing (electron/electron#13440). The real backstop for
    // the keystroke is the kiosk-state check in main.js's `before-quit`
    // handler; this block only removes the mouse-driven fallback.
    if (process.platform === 'darwin') {
      try {
        const { Menu } = require('electron');
        Menu.setApplicationMenu(null);
      } catch(e) { console.debug('[Kiosk] mac menu suppress failed:', e.message); }
      // Disable Mission Control BEFORE hiding the dock — killall Dock
      // (inside this call) restarts the Dock process, which would
      // otherwise clobber the dock-hide state applied below.
      _setMacMissionControlDisabled(true);
      // Crash-safety net — see _armMissionControlWatchdog's own comment.
      // Armed every time lockdown arms; every release path disarms it.
      _armMissionControlWatchdog();
      try {
        if (app.dock && typeof app.dock.hide === 'function') app.dock.hide();
      } catch(e) { console.debug('[Kiosk] mac dock hide failed:', e.message); }
    }
  };
}

// KNOWN OPEN GAP, researched 2026-07-04, not fixed here — macOS
// notification banners (Messages, Mail, Calendar, etc.) can show message
// PREVIEW TEXT as an overlay even while another app is fullscreen, unlike
// Windows where fullscreen-exclusive apps get notifications auto-
// suppressed more reliably. macOS DOES have its own native "auto-Do-Not-
// Disturb-for-fullscreen-apps" behavior, but whether Electron's kiosk+
// fullscreen combination (setKiosk(true) + setFullScreen(true), both used
// above) actually triggers that native detection the same way a truly
// native fullscreen macOS app does is genuinely unclear from available
// research — not verified either way without real hardware.
// Did NOT attempt to force Do Not Disturb/Focus mode programmatically as
// a mitigation: unlike mcx-expose-disabled (clear, stable command syntax,
// only the cross-version RELIABILITY was uncertain), the DND/Focus
// command-line APIs are documented as actually broken/removed since
// macOS Big Sur (2020) — the syntax itself, not just its effect, is
// unreliable. Implementing an unverified fix on top of already-broken
// tooling was judged worse than no fix — same reasoning as the Tor
// Browser detection decision earlier in this audit. If this needs
// closing, the next step is real-hardware testing of whether a
// notification banner actually appears during an armed exam before
// deciding whether a fix is even necessary.

// KNOWN OPEN GAP #2, researched 2026-07-04, not fixed here — Launchpad
// (F4 key or 4-finger pinch gesture) is a SEPARATE app-switching surface
// from Mission Control, arguably worse: it directly launches any
// installed app rather than just switching windows. Not covered by
// _setMacMissionControlDisabled (that's Mission Control/Exposé only) or
// by anything in config.js's blocked-shortcut lists.
// Found real, documented command syntax for the gesture half —
// `defaults write com.apple.AppleMultitouchTrackpad
// TrackpadFourFingerPinchGesture -bool false` — but did NOT implement it:
// unlike Mission Control (a `com.apple.dock` preference that a plain
// `killall Dock` reliably picks up), trackpad-driver preferences under
// `com.apple.AppleMultitouchTrackpad` reportedly need the WHOLE user
// session restarted (`launchctl bootout user/$(id -u)`) to take effect
// live, not just a Dock restart — that would risk killing Procta's own
// process mid-exam, a categorically worse outcome than the gap it would
// fix. Not attempted without solid confirmation this is actually safe.
// UPDATE 2026-07-04 — the F4 key IS separately coverable, and unlike the
// trackpad-gesture path above, this one has solid evidence: F4/Launchpad
// is `com.apple.symbolichotkeys` entry 160 (`AppleSymbolicHotKeys`), and
// changes to that domain apply live — WITHOUT logout/session-restart —
// via the real (if undocumented-by-Apple) system utility
// `/System/Library/PrivateFrameworks/SystemAdministration.framework/
// Resources/activateSettings -u`, confirmed present on this machine.
// Multiple independent sources (a dedicated blog post specifically about
// applying this instantly, plus real-world dotfiles scripts) converge on:
//   defaults write com.apple.symbolichotkeys AppleSymbolicHotKeys \
//     -dict-add 160 "{enabled = 0; value = { parameters = (65535, 131, 0); type = 'standard'; }; }"
//   activateSettings -u
// NOT implemented yet — attempted a live verification test on real
// hardware and it was correctly blocked pending explicit authorization
// for the same reason the Mission Control watchdog needed it (a live,
// persistent system-preference modification). Same shape as that one:
// arm at lockdown, restore at every release path, extend the existing
// crash-safety watchdog to also restore key 160. Ask for authorization
// and verify live before implementing, same process as before.

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
    // Restore Mission Control unconditionally — safe even if it was never
    // disabled (see _setMacMissionControlDisabled). Runs on EVERY release
    // path (normal exit, panic/emergency unlock both call releaseKiosk
    // before quitting) so a student never loses Mission Control outside
    // the exam itself.
    _setMacMissionControlDisabled(false);
    _disarmMissionControlWatchdog();
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
  // Exposed so main.js can defensively restore Mission Control on any
  // quit path that doesn't go through releaseKiosk() (e.g. an
  // auto-update quit), and once at app startup in case a prior session
  // crashed before cleanup ran.
  setMacMissionControlDisabled: _setMacMissionControlDisabled,
  disarmMissionControlWatchdog: _disarmMissionControlWatchdog,
};
