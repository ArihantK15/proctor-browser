const {
  app, BrowserWindow, globalShortcut,
  powerSaveBlocker, clipboard, dialog, screen,
  desktopCapturer, systemPreferences,
} = require('electron');
const path = require('path');
const fs = require('fs');
const {
  KIOSK_ALLOWED, BLOCKED_SHORTCUTS, PANIC_SHORTCUT, EMERGENCY_SHORTCUT,
  EXAM_WIDTH, EXAM_HEIGHT,
  LOBBY_WIDTH, LOBBY_HEIGHT, LOBBY_MIN_W, LOBBY_MIN_H,
  SERVER_URL,
} = require('../config');
const { checkGPU } = require('./integrity');
const { browserUserAgent } = require('./utils');

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

// TESTED AND REVERTED, 2026-07-04: disabling Mission Control
// (`com.apple.dock mcx-expose-disabled`) and Launchpad
// (`com.apple.symbolichotkeys` entry 160, applied via the real
// `activateSettings -u` utility) both looked promising — clear command
// syntax, applied cleanly to the preference files, verified via
// PlistBuddy/defaults read — but a real physical test on real hardware
// (three-finger swipe, F3, F4, four-finger pinch) confirmed BOTH still
// open normally despite the "disabled" preference state. Whatever
// actually gates Mission Control/Launchpad activation on current macOS
// doesn't consult these preferences the way older documentation/dotfiles
// scripts assumed. Also had a launchd crash-safety watchdog built for
// this (since removed along with it) — pointless without a working arm.
// Removed entirely rather than leave misleading, confirmed-non-functional
// code that LOOKS like protection. See config.js's _BLOCKED_MAC comment
// for the current, honest state of the trackpad-gesture gap (uncoverable
// by any means found so far).

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
// Set by createLobbyWindow() to its own retry closure (fresh each time the
// lobby window is (re)created). retryLobbyLoad() is the module-level entry
// point the "Retry now" button's IPC call and lobby-preload both use —
// see createLobbyWindow's did-fail-load handler for why this exists (a
// transient origin outage used to leave the lobby permanently stuck on a
// static error page with no way back in except relaunching the app).
let _lobbyRetryFn = null;
function retryLobbyLoad() { if (_lobbyRetryFn) _lobbyRetryFn(); }
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
  // See lib/utils.js's browserUserAgent() doc comment — Electron's default
  // UA (with its own app name + "Electron/x.y.z" token) gets flagged by
  // Cloudflare bot-management on this window's login/Turnstile requests.
  // Must be set before the first loadURL below.
  lobbyWindow.webContents.setUserAgent(browserUserAgent());

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
  let _autoRetryTimer = null;
  // Real bug (2026-07-06): a transient origin outage (self-heal cron
  // recovers it server-side within ~2 minutes) used to leave an
  // already-open lobby permanently stuck on the static error page below —
  // did-fail-load only ever fires forward through the tiers once, so
  // there was no way back in short of fully relaunching the app, even
  // though the server was healthy again seconds later. _attemptLobbyLoad
  // resets the tier state and tries again from scratch; both the
  // auto-retry timer and the error page's "Retry now" button
  // (lobby_preload.js → main.js's lobby-retry-load) call this same entry
  // point via retryLobbyLoad().
  function _attemptLobbyLoad() {
    clearTimeout(_autoRetryTimer);
    _fallbackIdx = 0;
    _onFallback = false;
    console.log('[Lobby] loading procta-lobby://lobby/student.html');
    lobbyWindow.loadURL('procta-lobby://lobby/student.html');
  }
  _lobbyRetryFn = _attemptLobbyLoad;

  lobbyWindow.webContents.once('dom-ready', _attemptLobbyLoad);

  lobbyWindow.webContents.on('dom-ready', () => {
    lobbyWindow.webContents.insertCSS(
      'html,body{background:#06080d !important;color:#c8d1dc !important}'
    ).catch(e => { console.debug('[Lobby] CSS injection failed:', e.message); });
  });

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
    const RETRY_SECS = 20;
    const offline = `<!DOCTYPE html><html><head><meta charset="utf-8"><title>Procta — Error</title>
      <style>body{margin:0;font-family:-apple-system,sans-serif;background:#0a0e1a;color:#cbd5e1;
        display:flex;align-items:center;justify-content:center;height:100vh;text-align:center;padding:24px}
        .box{max-width:480px;background:#0f1629;border:1px solid rgba(255,255,255,.06);
        border-radius:14px;padding:36px 32px}
        h1{color:#fff;font-size:20px;margin:0 0 10px}p{color:#94a3b8;font-size:13px;line-height:1.7;margin:0 0 10px}
        code{font-family:monospace;color:#60a5fa;font-size:11px;word-break:break-all;
        display:block;margin-top:8px;padding:8px 10px;background:rgba(255,255,255,.03);border-radius:6px}
        button{margin-top:16px;padding:10px 22px;border-radius:8px;border:none;
        background:#5b8af0;color:#fff;font-size:13px;font-weight:600;cursor:pointer;
        font-family:inherit}
        button:hover{background:#4a79df}
        .v{margin-top:14px;color:#64748b;font-size:11px;font-family:monospace}</style></head><body>
        <div class="box"><h1>Lobby failed to open</h1>
        <p>Couldn't load the local student dashboard, and the network fallback also failed. This
        can happen during a brief server hiccup — it usually clears within a couple of minutes.</p>
        <code>${errorDescription || errorCode}\n${safeUrl}</code>
        <button id="retry-btn">Retry now</button>
        <p style="margin-top:12px" id="auto-retry-note">Retrying automatically in <span id="retry-count">${RETRY_SECS}</span>s…</p>
        <p>If this keeps happening, check your internet, then relaunch. If the problem persists, reinstall.</p>
        ${appVersion ? `<div class="v">Procta v${appVersion}</div>` : ''}
        </div>
        <script>
          // Not subject to the FastAPI app's CSP — this is a local data:
          // URL the server never sees, so an inline script is fine here
          // (unlike every page actually served over HTTP by the app).
          document.getElementById('retry-btn').addEventListener('click', () => {
            window.procta_native && window.procta_native.retryLoad && window.procta_native.retryLoad();
          });
          let n = ${RETRY_SECS};
          const el = document.getElementById('retry-count');
          const timer = setInterval(() => {
            n -= 1;
            if (el) el.textContent = String(Math.max(n, 0));
            if (n <= 0) clearInterval(timer);
          }, 1000);
        </script>
        </body></html>`;
    lobbyWindow.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(offline));
    lobbyWindow.show();
    // Auto-retry once — if THIS also fails, did-fail-load fires again and
    // reschedules another RETRY_SECS timer, so a longer outage just keeps
    // trying quietly in the background rather than giving up permanently.
    clearTimeout(_autoRetryTimer);
    _autoRetryTimer = setTimeout(() => {
      if (lobbyWindow && !lobbyWindow.isDestroyed()) _attemptLobbyLoad();
    }, RETRY_SECS * 1000);
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
  // See createLobbyWindow's identical call + lib/utils.js's
  // browserUserAgent() doc comment — same Cloudflare bot-management
  // concern applies here (heartbeat/save-answers/submit-exam all fetch
  // from this window). Must be set before the first loadURL below.
  mainWindow.webContents.setUserAgent(browserUserAgent());

  // Windows: this genuinely works — SetWindowDisplayAffinity blacks the
  // window out at the compositor level for ANY capture method (screen
  // recorder, remote-desktop tool, browser screen-share), known or not.
  //
  // macOS: researched 2026-07-04, re-confirmed 2026-07-04 — setContentProtection
  // does NOT work at all here, confirmed by multiple still-open Electron
  // issues (#31787, #14415, #38994, #48258): the window remains fully
  // visible to native screen recording, AnyDesk/Splashtop/TeamViewer, and
  // Zoom/Teams/Tencent Meeting screen-share, despite this call succeeding
  // with no error.
  //
  // This is NOT an Electron implementation bug and a native rewrite would
  // NOT fix it: Electron's setContentProtection on macOS already does
  // exactly what a hand-written native module would do — sets the
  // NSWindow's sharingType to NSWindowSharingNone (confirmed via
  // Electron's own source/issue discussion, e.g. #46886). The actual
  // blocker is one level up: Apple's modern ScreenCaptureKit framework
  // (macOS 12+, the API most current screen recorders/sharing tools now
  // use) DELIBERATELY IGNORES NSWindow.sharingType entirely — confirmed
  // via a WebRTC bug report (issues.webrtc.org #41480865) hitting the
  // identical wall from a completely different codebase, and Tauri's own
  // tracker (tauri-apps/tauri#14200) describing it as "no known
  // workaround." This is Apple's deliberate platform architecture, not a
  // gap anyone has patched around — there is no public API, native or
  // otherwise, that restores Windows' compositor-level guarantee on
  // current macOS.
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

    // This watcher is the best available defense against every OS-reserved
    // switching surface this audit found genuinely uninterceptable
    // (Cmd+Tab, Cmd+Space, Mission Control, Launchpad — see config.js and
    // the removed-fix comments above) — all of them steal key-window
    // status from the exam the instant they activate, which fires 'blur'
    // regardless of the specific cause.
    //
    // IMPORTANT, empirically confirmed on real hardware 2026-07-04 (via
    // scripts/blur-test-window.mjs — a standalone diagnostic, not part of
    // the app): calling win.focus() reliably wins the fight against an
    // ORDINARY app window (Cmd+Tab to Terminal, Finder, etc. — confirmed
    // via isFocused()===true within ~150ms every time). It does NOT
    // reliably dismiss Mission Control or Launchpad's own full-screen
    // overlay — those are WindowServer-level surfaces above ordinary
    // window focus arbitration, and focus() only came back after the
    // student manually returned, sometimes 3-8+ seconds later. This is
    // the same category of platform limit as setContentProtection/
    // ScreenCaptureKit above — no API found (here or anywhere researched
    // this session) forces those overlays to dismiss. Still call focus()
    // unconditionally — it's free, and it IS the real defense for the
    // app-switching case.
    //
    // Since we can't reliably force the overlay itself away, the honest
    // fallback is: log accurately. Report on FOCUS regain (not on blur),
    // with the ACTUAL duration attached — a 100ms blur (Cmd+Tab and back)
    // and an 8-second one (browsing Launchpad) are very different signal
    // for a teacher, and only measuring at focus-return captures that.
    // Severity scales with duration since dwell time is the best proxy
    // available for "did they actually do something over there."
    // Debounce delay shortened 2026-07-04 from 100ms to 0ms (still
    // debounced via _blurTimer — this just removes the artificial extra
    // wait) after Arihant verified on real hardware that the refocus
    // completes reliably in 14-26ms at this setting, across many repeated
    // Mission Control/Launchpad/app-switch attempts, with zero missed
    // refocuses (see scripts/blur-test-window.mjs, an untracked local
    // diagnostic used for the live verification, not part of the app).
    // Screen-capture-while-blurred, added 2026-07-04 at Arihant's request:
    // while the exam window is out of focus, grab a screenshot every 2s
    // and upload it as evidence tied to a violation — direct visual proof
    // of what the student was looking at, not just "a blur happened."
    // Reuses the EXISTING /api/v1/analyze-frame endpoint (same one
    // proctor.py posts webcam violation frames to) rather than a new
    // backend route — same disk-save + S3 dual-write + evidence-linkage
    // pipeline, zero backend changes needed (verified against
    // app/routers/exam.py's FrameIn model and _save_frame: expects plain
    // base64 JPEG in `frame`, no data-URL prefix).
    //
    // Capped at MAX_BLUR_CAPTURES (8 shots = 16s of a single continuous
    // blur) rather than truly unbounded "every 2s for as long as they're
    // away" — analyze-frame is rate-limited server-side to 30/minute
    // shared with real webcam violation uploads from proctor.py; an
    // uncapped loop during one long blur would burn the ENTIRE budget
    // and could cause a genuine camera-detected violation to get dropped
    // at the worst possible moment. 8 shots is enough to show the pattern
    // (what they were doing, that it continued) without starving the
    // channel that actually matters most.
    //
    // macOS requires the separate "Screen Recording" TCC permission
    // (distinct from camera/mic) for desktopCapturer — unlike camera/mic,
    // there is NO programmatic re-prompt for this on mac (Apple only
    // allows a one-time System Settings toggle), so this fails open and
    // silent if not granted, exactly like every other best-effort check
    // in this file. Windows has no equivalent permission gate.
    const MAX_BLUR_CAPTURES = 8;
    const BLUR_CAPTURE_INTERVAL_MS = 2000;
    let _blurCaptureTimer = null;
    let _blurCaptureCount = 0;
    async function _captureScreenEvidence() {
      const sid = currentSessionId;
      if (!sid || !studentToken) return;
      try {
        if (process.platform === 'darwin') {
          const status = systemPreferences.getMediaAccessStatus('screen');
          if (status !== 'granted') return; // no re-prompt possible on mac — fail silent
        }
        const sources = await desktopCapturer.getSources({
          types: ['screen'],
          thumbnailSize: { width: 1024, height: 640 }, // keeps JPEG well under analyze-frame's ~375KB decoded cap
        });
        if (!sources || !sources.length) return;
        const jpegBuf = sources[0].thumbnail.toJPEG(70);
        const ac = new AbortController();
        const timer = setTimeout(() => ac.abort(), 5000);
        await fetch(`${SERVER_URL}/api/v1/analyze-frame`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json',
            'Authorization': `Bearer ${studentToken}` },
          signal: ac.signal,
          body: JSON.stringify({
            session_id: sid,
            frame: jpegBuf.toString('base64'),
            timestamp: new Date().toISOString(),
            // Must match the violation_type posted below on focus-regain
            // (window_focus_lost), NOT a distinct label — the scorecard's
            // evidence matcher (match_screenshot_for_violation in
            // app/services/sessions.py) links a screenshot to a violation
            // by exact `evt_<violation_type>_<ts>.jpg` prefix match. A
            // different label here would save the image but the PDF would
            // never find it.
            event_type: 'window_focus_lost',
          }),
        });
        clearTimeout(timer);
      } catch(e) { console.error('[Kiosk] blur screen-capture failed:', e.message); }
    }

    let _blurTimer = null;
    let _blurStartedAt = null;
    mainWindow.on('blur', () => {
      if (!mainWindow || mainWindow.isDestroyed()) return;
      if (!_blurStartedAt) _blurStartedAt = Date.now();
      if (!_blurCaptureTimer) {
        _blurCaptureCount = 0;
        _captureScreenEvidence();
        _blurCaptureCount++;
        _blurCaptureTimer = setInterval(() => {
          if (_blurCaptureCount >= MAX_BLUR_CAPTURES) {
            clearInterval(_blurCaptureTimer);
            _blurCaptureTimer = null;
            return;
          }
          _captureScreenEvidence();
          _blurCaptureCount++;
        }, BLUR_CAPTURE_INTERVAL_MS);
      }
      if (_blurTimer) return;
      _blurTimer = setTimeout(() => {
        _blurTimer = null;
        if (mainWindow && !mainWindow.isDestroyed() && !mainWindow.isFocused()) {
          // Refined 2026-07-04: Arihant's real-hardware test showed plain
          // window.focus() only reliably won against Terminal, NOT
          // actively-used regular apps (Chrome, Safari, Outlook) — macOS
          // has a default focus-stealing PREVENTION mechanism that
          // protects whichever app the user is actually engaging with,
          // and BrowserWindow.focus() respects it. `app.focus({steal:
          // true})` operates at the whole-application level and is the
          // documented, correct way to override that protection (macOS-
          // only option; Electron's own docs say "use sparingly," which
          // this qualifies for — an armed exam actively fighting for
          // focus is exactly the deliberate-override case it exists for).
          // VERIFIED live: reliable across Chrome/Safari/Outlook/Terminal,
          // 14-26ms consistently, in the same diagnostic session above.
          if (process.platform === 'darwin') {
            try { app.focus({ steal: true }); } catch(e) { console.debug('[Kiosk] app.focus steal failed:', e.message); }
          }
          // Windows has its OWN, separate focus-stealing protection
          // (ForegroundLockTimeout), and researched 2026-07-04 whether a
          // Windows equivalent of mac's app.focus({steal:true}) exists.
          // Conclusion: no safe one does, for a specific, confirmed reason
          // — not just "untested":
          //   - AllowSetForegroundWindow (the official Win32 API, wrapped
          //     by e.g. the windows-foreground-love npm package) grants a
          //     one-time allowance, but per Microsoft's own docs that
          //     grant is invalidated "the next time the user generates
          //     input... unless the input is directed at that process."
          //     The user's own switch-away action IS that input event —
          //     by the time our blur handler fires, any grant we'd taken
          //     out in advance is already gone. This isn't a timing
          //     nuance to route around; the API is structurally the
          //     wrong tool for "reclaim focus after the user already
          //     switched away," which is exactly our trigger.
          //   - The other option, AttachThreadInput (electron/electron
          //     PR #10783), was explicitly REJECTED by Electron's own
          //     maintainers as "problematic... might be unstable" and
          //     never merged into core — not something to hand-roll into
          //     a proctoring app where a crash/hang mid-exam is worse
          //     than the gap it would close.
          // moveTop() is kept as a harmless, free z-order nudge (does not
          // grab keyboard focus, can't make anything worse), but this is
          // a confirmed, not just unverified, platform gap for Windows —
          // same honest treatment as the mac Mission Control/Launchpad
          // overlay finding. Ordinary Windows apps switched to via
          // Alt+Tab are NOT protected by ForegroundLockTimeout the same
          // way — that mechanism specifically shields the CURRENT
          // foreground app from OTHER apps stealing it, so plain
          // mainWindow.focus() below already has a real chance of working
          // for the common case; this only affects apps that actively
          // resist being displaced.
          if (process.platform === 'win32') {
            try { mainWindow.moveTop(); } catch(e) { console.debug('[Kiosk] moveTop failed:', e.message); }
          }
          mainWindow.focus();
        }
      }, 0);
    });
    mainWindow.on('focus', () => {
      if (_blurTimer) { clearTimeout(_blurTimer); _blurTimer = null; }
      if (_blurCaptureTimer) { clearInterval(_blurCaptureTimer); _blurCaptureTimer = null; }
      if (_blurStartedAt) {
        const durationMs = Date.now() - _blurStartedAt;
        _blurStartedAt = null;
        const sid = currentSessionId;
        if (sid && studentToken) {
          const severity = durationMs > 2000 ? 'high' : durationMs > 500 ? 'medium' : 'low';
          // One extra capture right at focus-regain, timestamped to land
          // inside match_screenshot_for_violation's ±5s window around the
          // violation row created below. Without this, a blur longer than
          // ~19s (8 periodic shots every 2s = 16s, capped) has no capture
          // left within 5s of the violation's created_at, so the scorecard
          // PDF would show the violation with zero evidence even though
          // screenshots were taken earlier during the same blur.
          _captureScreenEvidence();
          (async () => {
            try {
              const ac = new AbortController();
              const timer = setTimeout(() => ac.abort(), 5000);
              await fetch(`${SERVER_URL}/api/v1/event`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json',
                  'Authorization': `Bearer ${studentToken}` },
                signal: ac.signal,
                body: JSON.stringify({ session_id: sid,
                  // window_focus_lost (not a bespoke label) — this is the
                  // existing violation type app/services/risk.py,
                  // false_positive.py, scorecard.py, admin_scorecards.py,
                  // and dashboard-app.js already carry weights/labels/icons
                  // for. A different string here would silently score 0
                  // risk weight and show up unlabeled in the PDF.
                  event_type: 'window_focus_lost', severity,
                  details: `Exam window lost focus for ${durationMs}ms (app switch, Mission Control, Launchpad, Spotlight, or similar).` }),
              });
              clearTimeout(timer);
            } catch(e) { console.error('[Kiosk] blur event post failed:', e.message); }
          })();
        }
      }
    });
    // Belt-and-suspenders: if the window is destroyed while a blur-capture
    // loop is mid-flight (exam ends/submits while the student happens to
    // be tabbed away), stop it — otherwise it'd keep firing for up to
    // MAX_BLUR_CAPTURES more shots against a session that's already over.
    mainWindow.on('closed', () => {
      if (_blurCaptureTimer) { clearInterval(_blurCaptureTimer); _blurCaptureTimer = null; }
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
//
// UPDATE 2026-07-04, re-researched at Arihant's request (wants
// notifications suppressed on BOTH platforms as a distraction issue,
// not just a proctoring one): macOS Monterey+ DOES have a real, current,
// Apple-sanctioned path — the built-in `/usr/bin/shortcuts run "<name>"`
// CLI can trigger the Shortcuts app's own Focus-mode automation, which is
// the actual supported mechanism (not a reverse-engineered defaults key).
// NOT implemented: it requires a specific named Shortcut to already
// exist in the user's Shortcuts app — Procta can't create one
// standalone, it would need either a documented one-time user setup
// step or shipping+importing a .shortcut file, both bigger scope than a
// quick fix. Flagging as the real path forward, not attempting it here.
//
// WINDOWS SIDE: researched Focus Assist toggling — evidence is JUST AS
// weak as the mac DND case, not better. Found conflicting registry
// mechanisms (`HKCU\...\Notifications\Settings\NOC_GLOBAL_SETTINGS` vs a
// separate `HKCU\...\CloudStore\...\quiethourssettings\Current` binary
// blob) and a `Set-NotificationSetting` PowerShell cmdlet that reportedly
// "activates immediately" — but the SAME sources also say you need to
// restart the per-user `WpnUserService_*` Windows service (or log out,
// or restart Explorer) for the change to actually apply, which is the
// same session-disruption risk class already declined for the Launchpad
// trackpad-gesture path and the mac `Scancode Map` PrintScreen fix. Not
// implemented — no Windows hardware in this environment to verify which
// of the conflicting mechanisms (if any) actually applies live anyway.
//
// CONCLUSION 2026-07-04: Arihant asked to try just the "basic" classic
// DND toggle live and drop it for both platforms if it doesn't work —
// tested `defaults write com.apple.notificationcenterui doNotDisturb
// -boolean true` directly on this machine (macOS 26.5.1) and it fails
// outright: "Could not write domain ... exiting" — not silently
// ignored, an actual sandbox/permission error. This matches the search
// caveat that this specific command only ever applied to Monterey
// (12.x); current macOS has fully moved past it. DROPPED for macOS.
// Windows dropped too, per the same instruction, given no hardware here
// to test its "basic" equivalent and the evidence being equally weak.
// The Shortcuts-automation path above remains the only real option if
// this is revisited later — not attempted, bigger scope than "basic."

// KNOWN OPEN GAP #2 — Launchpad (F4 key or 4-finger pinch gesture) is a
// SEPARATE app-switching surface from Mission Control, arguably worse:
// it directly launches any installed app rather than just switching
// windows. Not covered by anything in config.js's blocked-shortcut
// lists. The trackpad-pinch half was never attempted — `com.apple.
// AppleMultitouchTrackpad TrackpadFourFingerPinchGesture` reportedly
// needs a full user-session restart to take live effect, too risky to
// try (could kill Procta mid-exam). The F4-key half (`com.apple.
// symbolichotkeys` entry 160, applied via `activateSettings -u`) WAS
// implemented and authorized 2026-07-04, but a real physical F4 test on
// real hardware confirmed Launchpad still opens anyway despite the
// preference being correctly set — reverted (see the "TESTED AND
// REVERTED" comment near the top of this file). Genuinely uncoverable
// by any means found so far, on either the key or gesture side.

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
  retryLobbyLoad,
};
