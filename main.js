const { app, ipcMain, globalShortcut, screen, dialog } = require('electron');
const path = require('path');

// ── Sentry (optional — gated on SENTRY_DSN env / packaged config) ──
// @sentry/electron captures main-process AND renderer-process errors
// from a single init in the main process. Renderers automatically
// route their crashes through the main process via Electron IPC, so
// we don't need to call init() again inside renderer/.
//
// DSN is read from the SENTRY_DSN env var. When packaged, set it via
// the OS environment or bake it into config.js for the installer.
try {
  // Hardcoded fallback DSN for shipped installers — students/teachers
  // don't set env vars before launching the app. DSN is a project
  // identifier (not auth) so committing/embedding it is fine per
  // Sentry's data-management docs. The env-var override stays for
  // dev/local builds where you want errors going to a different
  // project (or disabled with SENTRY_DSN=).
  const SENTRY_DSN_DEFAULT = 'https://a2fbe3d1b9eeba6931f4a740f15b38a9@o4511494935478272.ingest.de.sentry.io/4511494981615696';
  const SENTRY_DSN = process.env.SENTRY_DSN !== undefined
    ? process.env.SENTRY_DSN
    : SENTRY_DSN_DEFAULT;
  if (SENTRY_DSN) {
    const Sentry = require('@sentry/electron/main');
    Sentry.init({
      dsn: SENTRY_DSN,
      environment: process.env.SENTRY_ENVIRONMENT || 'production',
      release: process.env.APP_VERSION || app.getVersion(),
      tracesSampleRate: 0.1,
      // Don't capture screenshots — proctoring sessions contain student
      // PII and exam content that we deliberately keep out of error
      // tracking. Stack traces alone are enough for triage.
      attachScreenshot: false,
    });
    console.log('[sentry] initialized for Electron main+renderer');
  }
} catch (e) {
  // @sentry/electron not installed (or init failed) — swallow so the
  // exam app still launches. Sentry is observability, not load-bearing.
  console.warn('[sentry] electron init skipped:', e.message);
}

const {
  SERVER_URL, ADMIN_CODE, INVITE_REGEX, BLOCKING_TYPES, THREATS,
} = require('./config');
const { extractInviteToken, authHeaders, fetchWithTimeout, _exec } = require('./lib/utils');
const { initAutoUpdater } = require('./lib/auto-update');
const { runIntegrityChecks } = require('./lib/integrity');
const {
  findPython, checkPackagesReady, runSetup,
  createSetupWindow, getSetupWindow, closeSetupWindow,
  startPython, stopPython, startCalibration, stopCalibration,
  reapOrphanProctors, ensureCameraAccess,
} = require('./lib/python-manager');
const { startPolling, stopPolling } = require('./lib/polling');

const {
  createLobbyWindow, createExamWindow, releaseKiosk, handlePanicUnlock,
  receiveInviteToken, consumeInviteToken, getPendingInviteToken,
  getMainWindow, getLobbyWindow, getIntegrityFlags, getIntegrityReady,
  setIntegrityReady, pushIntegrityFlag,
  getCurrentSessionId, setCurrentSessionId, getExamContext, setExamContext,
  getStudentToken, setStudentToken, getCalBiases, setCalBiases,
  getIsKiosk, setMonitorFns, setPollingFns, setPythonFns,
} = require('./lib/kiosk-manager');

// ── Wire up kiosk-manager with backend module references ──────────
// Threats and multi-monitor detection are reported at most once per
// (session, label) pair. Without dedup, a student with TeamViewer
// merely installed (not running maliciously) would emit a high-severity
// event every 30s for the whole exam — flooding the teacher dashboard,
// the events table, and the composite risk score. The Set is cleared
// when the monitor stops so the next session starts with a clean slate.
const _reportedThreats = new Set();
const { startProcessMonitor, stopProcessMonitor } = (() => {
  let _interval = null;
  return {
    startProcessMonitor: () => {
      if (_interval) return;
      console.log('[Monitor] starting continuous process monitoring');
      _reportedThreats.clear();
      _interval = setInterval(() => _scanProcesses(), 30000);
    },
    stopProcessMonitor: () => {
      if (_interval) { clearInterval(_interval); _interval = null; console.log('[Monitor] stopped'); }
      _reportedThreats.clear();
    },
  };
})();

async function _scanProcesses() {
  const isWin = process.platform === 'win32';
  const output = await _exec(isWin ? 'tasklist /fo csv /nh' : 'ps -eo comm', 8000);
  if (!output) return;
  const lower = output.toLowerCase();
  for (const { rx, label, type } of THREATS) {
    if (!rx.test(lower)) continue;
    const key = `${type}:${label}`;
    if (_reportedThreats.has(key)) continue;
    _reportedThreats.add(key);
    if (getCurrentSessionId() && getStudentToken()) {
      fetchWithTimeout(`${SERVER_URL}/api/v1/event`, {
        method: 'POST', headers: authHeaders(getStudentToken()),
        body: JSON.stringify({ session_id: getCurrentSessionId(),
          event_type: type, severity: 'high',
          details: `[Live scan] ${label} detected during exam` }),
      }, 10000).catch(() => {});
    }
    if (getMainWindow() && !getMainWindow().isDestroyed()) {
      getMainWindow().webContents.send('violation-detected', {
        type, severity: 'high',
        details: `[Live scan] ${label} detected during exam`,
      });
    }
    console.log(`[Monitor] THREAT: ${label} (${type})`);
  }
  try {
    const displays = screen.getAllDisplays();
    if (displays.length > 1 && getCurrentSessionId() && getStudentToken()) {
      const key = `multiple_monitors:${displays.length}`;
      if (!_reportedThreats.has(key)) {
        _reportedThreats.add(key);
        fetchWithTimeout(`${SERVER_URL}/api/v1/event`, {
          method: 'POST', headers: authHeaders(getStudentToken()),
          body: JSON.stringify({ session_id: getCurrentSessionId(),
            event_type: 'multiple_monitors', severity: 'medium',
            details: `[Live scan] ${displays.length} displays detected` }),
        }, 10000).catch(() => {});
      }
    }
  } catch(e) { console.error('[Monitor] _scanProcesses error:', e.message); }
}

setMonitorFns(startProcessMonitor, stopProcessMonitor);
setPollingFns(
  (sessionId, mainWindow, token, forceCb, violationCb) => {
    setStudentToken(token);
    setCurrentSessionId(sessionId);
    startPolling(sessionId, getMainWindow(), token, forceCb, violationCb);
  },
  stopPolling
);
setPythonFns(
  async (sessionId) => {
    setCurrentSessionId(sessionId);
    await startPython(sessionId, SERVER_URL, getStudentToken(), getCalBiases());
  },
  () => { stopPython(); stopPolling(); },
  async (sessionId) => {
    setCurrentSessionId(sessionId);
    await startCalibration(sessionId, SERVER_URL, getStudentToken(), getMainWindow());
  },
  stopCalibration
);

// ── Single-instance lock & protocol handler ───────────────────────
//
// Supported deep links:
//   procta://invite/<token>    open the lobby with an invite token
//                              pre-filled. Token: 8-128 chars,
//                              [A-Za-z0-9_-]. Anything else is
//                              treated as a "saw a procta:// URL we
//                              couldn't parse" event so we can show
//                              the user a clear message instead of
//                              silently dropping it.
//
// Entry paths the OS uses to hand us a URL:
//   - Windows: passed as process.argv on cold start; on warm start
//     the OS launches a 2nd instance which we intercept via
//     `second-instance`. Both routed through extractAndReceive().
//   - macOS:   `open-url` event fires on cold + warm starts.
//   - Linux:   removed — we no longer support Linux.
//
// All three paths log via the same `[Invite]` prefix with the
// originating source so we can debug failures from a single user's
// log file.

const PROCTA_SCHEME = 'procta';

/** Iterate args back-to-front (so the last `procta://` wins if more
 *  than one is passed) and route to receiveInviteToken or emit a
 *  malformed-URL signal so the lobby can render an error chip. */
function extractAndReceive(args, source) {
  try {
    let sawProctaUrl = false;
    for (let i = args.length - 1; i >= 0; i--) {
      const a = String(args[i] || '');
      if (a.toLowerCase().startsWith(`${PROCTA_SCHEME}://`)) {
        sawProctaUrl = true;
        // Plain "open the app" deep link (procta://open) — used by the web
        // registration success page to launch the installed app. It carries
        // no invite token, so route it straight to show+focus the lobby
        // rather than through the invite path (which would flag it as a
        // malformed invite). On cold start the lobby may not exist yet; the
        // normal launch will surface it, so a no-op here is fine.
        const rest = a.slice(`${PROCTA_SCHEME}://`.length).replace(/\/+$/, '').toLowerCase();
        if (rest === 'open' || rest === 'lobby') {
          const lobby = getLobbyWindow();
          if (lobby && !lobby.isDestroyed()) {
            if (lobby.isMinimized()) lobby.restore();
            lobby.show();
            lobby.focus();
          }
          return true;
        }
        const tok = extractInviteToken(a, INVITE_REGEX);
        if (tok) {
          receiveInviteToken(tok, source);
          return true;
        }
      }
    }
    if (sawProctaUrl) {
      // Saw a procta:// URL but no valid invite token in it. Could
      // be an old format, a typo'd link, or a deliberate bad input.
      // Log once and notify the lobby so the UI can prompt the user
      // to re-check their invite email.
      console.warn(`[Invite] malformed procta:// URL via ${source}`);
      const lobby = getLobbyWindow();
      if (lobby && !lobby.isDestroyed()) {
        try { lobby.webContents.send('invite-token-malformed', { source }); }
        catch(e) { console.error('[Invite] failed to notify lobby of malformed URL:', e.message); }
      }
    }
  } catch(e) {
    console.error('[Invite] %s parse error:', source, e.message);
  }
  return false;
}

const _gotSingleInstanceLock = app.requestSingleInstanceLock();
if (!_gotSingleInstanceLock) {
  // Don't die silently — a zombie first instance (or a still-running copy)
  // would otherwise make a relaunch look like "nothing happens". Tell the
  // user plainly before quitting this second instance.
  try {
    dialog.showErrorBox(
      'Procta is already running',
      'Another copy of Procta is already open on this computer.\n\n' +
      'Switch to the existing Procta window. If you don\'t see one, ' +
      'quit Procta from your Dock/Taskbar (or restart your computer), ' +
      'then open it again.');
  } catch(e) { /* dialog may be unavailable pre-ready — quit anyway */ }
  app.quit();
} else {
  app.on('second-instance', (_evt, argv) => {
    extractAndReceive(argv, 'second-instance');
    const lobby = getLobbyWindow();
    if (lobby && !lobby.isDestroyed()) {
      if (lobby.isMinimized()) lobby.restore();
      lobby.show();
      lobby.focus();
    }
  });
}

// ── Global crash handlers ─────────────────────────────────────────
// Without these, any unhandled throw on exam-start silently kills the
// main process — exactly the "app just shut off" the student saw — and,
// worse, can leave them trapped behind a kiosk window if the throw
// happened mid-lockdown. We log the error for diagnosis and, if an exam
// is in kiosk lockdown, FREE the student (handlePanicUnlock releases the
// window and quits cleanly). If no exam is locked we log and keep the
// lobby alive rather than tearing the app down over a stray rejection.
function _writeCrashLog(kind, err) {
  try {
    const fs = require('fs');
    const line = `[${new Date().toISOString()}] ${kind}: ` +
      `${(err && err.stack) || err}\n`;
    fs.appendFileSync(path.join(app.getPath('logs'), 'crash.log'), line);
  } catch(e) { /* logs dir may not exist pre-ready — best effort */ }
  // Constant format string (kind/err passed as args) — keeps Semgrep's
  // unsafe-formatstring rule satisfied without a suppression; both are
  // internal values but a literal format string is the correct shape.
  console.error('[crash] %s:', kind, (err && err.stack) || err);
}

let _handlingFatal = false;
function _handleFatal(kind, err) {
  _writeCrashLog(kind, err);
  let locked = false;
  try { locked = !!getIsKiosk(); } catch(e) {}
  const win = (() => { try { return getMainWindow(); } catch(e) { return null; } })();
  if (locked && win && !win.isDestroyed()) {
    if (_handlingFatal) return; // avoid re-entrancy if unlock path itself throws
    _handlingFatal = true;
    console.error('[crash] exam in lockdown — releasing student via panic unlock');
    Promise.resolve(handlePanicUnlock(kind)).catch(e =>
      console.error('[crash] panic unlock failed:', e && e.message));
  }
  // Not locked: deliberately do NOT quit — a stray exception/rejection
  // shouldn't kill a working lobby for the student.
}

process.on('uncaughtException', (err) => _handleFatal('uncaughtException', err));
process.on('unhandledRejection', (reason) => _handleFatal('unhandledRejection', reason));

// Child/GPU process death must be observed but must NOT tear down the app.
// A dead GPU process auto-recovers; a dead utility/renderer child is
// handled by its own owner (e.g. createExamWindow's render-process-gone).
app.on('child-process-gone', (_e, details) => {
  console.error('[crash] child-process-gone:', details && details.type,
    details && details.reason, 'exit=', details && details.exitCode);
  _writeCrashLog('child-process-gone',
    new Error(`${details && details.type} ${details && details.reason}`));
});
app.on('gpu-process-gone', (_e, details) => {
  console.error('[crash] gpu-process-gone:', details && details.reason);
  _writeCrashLog('gpu-process-gone',
    new Error(`gpu ${details && details.reason}`));
});

app.on('open-url', (event, url) => {
  event.preventDefault();
  extractAndReceive([url], 'open-url');
});

if (!app.isDefaultProtocolClient(PROCTA_SCHEME)) {
  try {
    // Dev mode: register electron itself + the script path as the
    // handler so `procta://...` clicks during development reach this
    // running process instead of trying to launch a packaged app
    // that doesn't exist yet.
    if (process.defaultApp && process.argv.length >= 2) {
      app.setAsDefaultProtocolClient(PROCTA_SCHEME, process.execPath, [path.resolve(process.argv[1])]);
    } else {
      app.setAsDefaultProtocolClient(PROCTA_SCHEME);
    }
  } catch(e) { console.error('[Invite] setAsDefaultProtocolClient failed:', e.message); }
}

// ── ELECTRON PERFORMANCE TUNING ──────────────────────────────────
// Disable GPU hardware acceleration to reduce VRAM/CPU overhead.
// The exam UI is text + CSS — no WebGL or canvas rendering needed.
// This cuts ~80-150MB of GPU process memory on Windows.
app.disableHardwareAcceleration();

// NOTE: previously also set `disable-gpu-sandbox` and
// `disable-site-isolation-trial` here to shave ~70 MB on low-end
// kiosks. Both were dropped after a security audit: Site Isolation
// is the Chromium primitive that contains Spectre-class cross-origin
// data leaks (renderer reads exam answers + camera frames), and
// the GPU sandbox is the only thing between a driver bug and full
// host compromise. The memory win wasn't worth the attack surface
// — the kiosk runs one origin and a few hundred MB of headroom.

// Reduce the renderer process idle time before it releases memory.
app.commandLine.appendSwitch('js-flags', '--max-old-space-size=256');

// ── Custom protocol for lobby assets ──────────────────────────────
// Why this exists: Electron's BrowserWindow.loadFile() turns a path
// inside app.asar into a file:// URL, and Chromium's file:// handler
// occasionally fails to read through Electron's asar fs patch on
// packaged builds. We saw this in shipped 2.3.12/2.3.13 — the lobby
// window stayed blank because loadFile fired did-fail-load on a
// student.html that genuinely existed in the asar.
//
// procta-lobby:// is a custom scheme that:
//   1. Reads the bundled file from the asar using Node's fs.readFile
//      (the patched-by-Electron path that DOES work reliably).
//   2. Hands the bytes back to the renderer with an explicit Content-
//      Type derived from the file extension — no MIME-sniffing edge
//      cases, no `text/plain` ambiguity that would let Chromium
//      refuse to render an .html.
//   3. Resolves relative paths inside the page naturally — when
//      student.html links `<link href="tokens.css">`, the renderer
//      requests procta-lobby://lobby/tokens.css and gets it from the
//      same handler.
//
// registerSchemesAsPrivileged MUST run before app.whenReady fires,
// otherwise protocol.handle below would throw.
const _path = require('path');
const _fsp = require('fs').promises;
require('electron').protocol.registerSchemesAsPrivileged([
  { scheme: 'procta-lobby', privileges: {
    standard: true, secure: true, supportFetchAPI: true,
    corsEnabled: true, allowServiceWorkers: false, bypassCSP: false,
  }},
]);

const _MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css':  'text/css; charset=utf-8',
  '.js':   'application/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg':  'image/svg+xml',
  '.png':  'image/png',
  '.ico':  'image/x-icon',
  '.woff': 'font/woff',
  '.woff2':'font/woff2',
};

function _registerLobbyProtocol() {
  require('electron').protocol.handle('procta-lobby', async (request) => {
    let u;
    try { u = new URL(request.url); }
    catch { return new Response('bad url', { status: 400 }); }
    // host 'exam' serves the kiosk exam renderer (renderer/); any other host
    // (e.g. 'lobby') serves the student dashboard (app/static/). BOTH are read
    // via fs.readFile — the asar-patched path that WORKS where the exam
    // window's old loadFile() ERR_FILE_NOT_FOUND'd on packaged Windows builds
    // (the same failure that moved the lobby onto this scheme).
    //   procta-lobby://lobby/student.html        → app/static/student.html
    //   procta-lobby://lobby/static/favicon.svg  → app/static/favicon.svg
    //   procta-lobby://exam/index.html           → renderer/index.html
    let rel = u.pathname.replace(/^\/+/, '');
    if (rel.startsWith('static/')) rel = rel.slice('static/'.length);
    // Clamp to a single component so a crafted URL can't escape the root
    // via ".." segments.
    const filename = rel.replace(/[\\/].*$/, '');
    if (!filename || filename === '..') {
      return new Response('not found', { status: 404 });
    }
    const root = u.host === 'exam' ? ['renderer'] : ['app', 'static'];
    const candidates = [
      _path.join(__dirname, ...root, filename),
      _path.join(process.resourcesPath || '', ...root, filename),
    ];
    for (const filepath of candidates) {
      try {
        const data = await _fsp.readFile(filepath);
        const ext = _path.extname(filename).toLowerCase();
        return new Response(data, {
          headers: {
            'Content-Type': _MIME[ext] || 'application/octet-stream',
            // Don't cache aggressively — packaged builds replace these
            // files on update and we want the new bundle to be picked
            // up immediately rather than a stale cache.
            'Cache-Control': 'no-cache',
          },
        });
      } catch { /* try the next candidate */ }
    }
    console.error('[procta-lobby] not found in any candidate:', filename);
    return new Response('not found', { status: 404 });
  });
}

// ── APP START ─────────────────────────────────────────────────────
app.whenReady().then(async () => {
  _registerLobbyProtocol();

  // Reap any proctor that survived a previous hard crash BEFORE we touch
  // Python or the camera. A crash skips before-quit/window-all-closed
  // cleanup, leaving an orphaned proctor.py holding the camera — which
  // makes the next exam's proctor fail to init and respawn-storm. Clear
  // it first so the relaunch starts clean.
  // Fire-and-forget: the reaper is async and yields immediately, so it no
  // longer blocks cold start. It still completes well before any proctor
  // spawns (which only happens at exam launch).
  reapOrphanProctors().catch(e => console.error('[Reaper] failed:', e.message));

  // Cold-start protocol activation: Windows launches us with the
  // procta:// URL as process.argv; macOS uses open-url instead.
  // Captured before we open any window so deeplink-invite tokens
  // survive the wait for setup.
  extractAndReceive(process.argv, 'argv');

  // ── Setup gate ──────────────────────────────────────────────────
  // Until setup is complete, we DO NOT open the lobby. The user
  // shouldn't be able to click around (try to sign in, start an
  // exam, etc.) while pip is still installing the AI packages in the
  // background. On a fresh install: setup window first → lobby
  // when done. On a re-launch with packages already there: very
  // brief "Checking…" splash → lobby. Either way one window at a
  // time, and the lobby never opens before setup is settled.
  // Setup runs on both desktop targets we ship: Windows (downloads the
  // python.org installer if needed) and macOS (uses the bundled
  // relocatable Python + a per-user venv). Linux is not a shipping
  // target, so it skips the gate and opens the lobby immediately.
  //
  // Gated on app.isPackaged: in dev (`npm start`, the smoke test) the
  // developer manages their own Python env, and we must NOT kick off a
  // multi-hundred-MB pip install into a throwaway venv on every launch.
  // This preserves the prior behaviour where unpackaged runs never ran
  // setup. Real installs always provision.
  const needsSetupGate = app.isPackaged
    && (process.platform === 'win32' || process.platform === 'darwin');
  let needSetup = false;
  if (needsSetupGate) {
    try {
      const python = await findPython();
      needSetup = !(python && await checkPackagesReady(python));
    } catch(e) { console.error('[Setup] readiness check threw:', e.message); }
  }

  if (needsSetupGate) {
    // Guard the setup-window creation: a throw here must NOT skip the
    // lobby open below. Worst case we run setup windowless, then still
    // reach the lobby.
    try { createSetupWindow(); } catch(e) { console.error('[Setup] createSetupWindow threw:', e.message); }
    try {
      // 16-min ceiling on the whole setup flow. Must stay ABOVE the
      // inner pip child's own 15-min timeout (python-manager runSetup),
      // otherwise this race would fire first, close the setup window and
      // open the lobby while pip is still running detached — letting a
      // student start an exam before packages finish. On an already-set-up
      // install runSetup short-circuits in ~1s; on a fresh install it
      // does the bundled pip + audio model download.
      await Promise.race([
        runSetup(),
        new Promise((_, reject) => setTimeout(() => reject(new Error('Setup timed out')), 960_000)),
      ]);
      await new Promise(r => setTimeout(r, needSetup ? 1500 : 400));
    } catch(e) { console.error('[Setup] Failed:', e); }
    try {
      if (getSetupWindow() && !getSetupWindow().isDestroyed()) closeSetupWindow();
    } catch(e) { console.error('[Setup] closeSetupWindow threw:', e.message); }
  }

  // Setup done (or skipped on unsupported platforms). Open the lobby now.
  // This is the one window the app cannot run without, so it gets its own
  // guard: the global crash handler logs an uncaught throw but deliberately
  // does NOT quit when no exam is locked — so without this catch a throw
  // here would leave a running process with no window (the silent "reopen
  // does nothing" hang). Surface a dialog and quit so the user can retry.
  try {
    createLobbyWindow();
  } catch(e) {
    console.error('[Boot] createLobbyWindow threw:', e.message);
    _writeCrashLog('boot-lobby-failed', e);
    try {
      dialog.showErrorBox('Procta could not start',
        'Procta hit an error while opening its window.\n\n' +
        'Please restart the app. If this keeps happening, reinstall the ' +
        'latest version of Procta or contact your examiner.');
    } catch(_) {}
    app.quit();
    return;
  }

  // Defer auto-updater to avoid blocking startup on slow networks.
  setTimeout(() => initAutoUpdater(getLobbyWindow(), getMainWindow), 3000);

  setIntegrityReady(runIntegrityChecks().then(flags => {
    flags.forEach(f => pushIntegrityFlag(f));
    console.log(`[Integrity] async checks complete: ${flags.length} flag(s)`);
  }).catch(e => {
    console.error('[Integrity] check failed:', e.message);
  }));
});

// Flag set by auto-update.js right before quitAndInstall() so
// before-quit knows to show the "Installing update" overlay instead
// of the silent shutdown that made the user think the app had hung.
let _quittingForUpdate = false;
function setQuittingForUpdate() { _quittingForUpdate = true; }
module.exports._setQuittingForUpdate = setQuittingForUpdate;

app.on('before-quit', () => {
  // If we're quitting to install an update, paint a clear overlay on
  // the lobby BEFORE we tear down the subprocesses. Without this the
  // app window goes blank → "Not responding" → NSIS pops → relaunch:
  // ~30s of dead screen that looks like a hang.
  if (_quittingForUpdate) {
    try {
      const lw = getLobbyWindow();
      if (lw && !lw.isDestroyed()) {
        lw.webContents.executeJavaScript(`(function(){
          var o=document.createElement('div');
          o.style.cssText='position:fixed;inset:0;background:#0a0e1a;color:#e2e8f0;'+
            'display:flex;align-items:center;justify-content:center;'+
            'flex-direction:column;z-index:999999;font-family:system-ui';
          o.innerHTML='<div style="width:48px;height:48px;border:3px solid #1e293b;'+
            'border-top-color:#3b82f6;border-radius:50%;animation:spin 1s linear infinite;'+
            'margin-bottom:18px"></div>'+
            '<div style="font-size:16px;font-weight:600;margin-bottom:6px">Installing update</div>'+
            '<div style="font-size:13px;color:#94a3b8">This takes about a minute. Please wait.</div>'+
            '<style>@keyframes spin{to{transform:rotate(360deg)}}</style>';
          document.body.appendChild(o);
        })()`).catch(() => {});
      }
    } catch(e) { /* best-effort */ }
  }
  stopPython();
  stopPolling();
  // Force-destroy the exam window on EVERY quit path. During an armed exam
  // two layered guards block a normal close — the kiosk 'close' preventDefault
  // (kiosk-manager) and the renderer 'beforeunload' preventDefault. Those exist
  // so a student can't close the exam, but they also cancel an app-level
  // app.quit() (auto-update quitAndInstall, OS/menu Cmd+Q): before-quit would
  // run, stop Python, then the quit would abort on the un-closable window —
  // leaving a half-dead, hung app. destroy() bypasses both guards, so the quit
  // always completes. Normal exam exits go through releaseKiosk (which also
  // destroys) and never reach here, so this is purely the external-quit safety net.
  try {
    const mw = getMainWindow();
    if (mw && !mw.isDestroyed()) mw.destroy();
  } catch(e) { /* best-effort */ }
  if (!_quittingForUpdate) {
    if (getLobbyWindow() && !getLobbyWindow().isDestroyed()) {
      getLobbyWindow().destroy();
    }
  }
  // On _quittingForUpdate we leave the window up showing the overlay
  // until electron-updater actually replaces the process.
});

app.on('window-all-closed', () => {
  stopPython();
  stopPolling();
  try { globalShortcut.unregisterAll(); } catch(e) {}
  app.quit();
});

// ── IPC HANDLERS ──────────────────────────────────────────────────
// All state-changing or data-returning handlers must validate that the
// invoking frame is the **top** frame of a window we created. Without
// this, a compromised renderer (XSS in a question prompt, a malicious
// embed) could call privileged handlers from a nested iframe.
function _assertMainFrame(event, name) {
  try {
    const f = event && event.senderFrame;
    if (!f) return false;
    // `parent === null` ⇒ top-level frame. Electron sets parent to a
    // WebFrameMain instance for child frames, so a strict null check
    // is what isolates "main page" from "any iframe".
    if (f.parent) {
      // `name` is one of ~15 hardcoded IPC channel strings; no user input.
      // nosemgrep: javascript.lang.security.audit.unsafe-formatstring.unsafe-formatstring
      console.warn('[IPC] rejected handler from sub-frame', { handler: name, url: f.url });
      return false;
    }
    return true;
  } catch (e) {
    // nosemgrep: javascript.lang.security.audit.unsafe-formatstring.unsafe-formatstring
    console.warn('[IPC] senderFrame check threw', { handler: name, error: e.message });
    return false;
  }
}

ipcMain.handle('get-integrity-flags', async (event) => {
  if (!_assertMainFrame(event, 'get-integrity-flags')) throw new Error('Frame not allowed');
  const ready = getIntegrityReady();
  if (ready) { try { await ready; } catch(e) { console.error('[Integrity] check failed:', e.message); } }
  return getIntegrityFlags().map(f => ({
    ...f,
    blocking: BLOCKING_TYPES.has(f.type) && f.severity === 'high',
  }));
});

ipcMain.handle('validate-student', async (event, roll, accessCode) => {
  if (!_assertMainFrame(event, 'validate-student')) throw new Error('Frame not allowed');
  const body = { roll_number: roll, access_code: accessCode || '' };
  if (getExamContext() && getExamContext().examId) body.exam_id = getExamContext().examId;
  if (getExamContext() && getExamContext().teacherId) body.teacher_id = getExamContext().teacherId;
  const r = await fetchWithTimeout(`${SERVER_URL}/api/v1/validate-student`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }, 30000);
  if (!r.ok) { const e = await r.json(); throw new Error(e.detail); }
  const data = await r.json();
  setStudentToken(data.token || null);
  return data;
});

ipcMain.handle('get-questions', async (event, sessionId) => {
  if (!_assertMainFrame(event, 'get-questions')) throw new Error('Frame not allowed');
  const qs = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : '';
  const r = await fetchWithTimeout(`${SERVER_URL}/api/v1/questions${qs}`,
    { headers: authHeaders(getStudentToken()) }, 30000);
  if (!r.ok) throw new Error('Could not load questions');
  return r.json();
});

ipcMain.handle('log-event', async (event, data) => {
  if (!_assertMainFrame(event, 'log-event')) throw new Error('Frame not allowed');
  try {
    await fetchWithTimeout(`${SERVER_URL}/api/v1/event`, {
      method: 'POST', headers: authHeaders(getStudentToken()),
      body: JSON.stringify(data),
    }, 10000);
  } catch(e) { console.error('[log-event]', e.message); }
});

ipcMain.handle('submit-exam', async (event, data) => {
  if (!_assertMainFrame(event, 'submit-exam')) throw new Error('Frame not allowed');
  const r = await fetchWithTimeout(`${SERVER_URL}/api/v1/submit-exam`, {
    method: 'POST', headers: authHeaders(getStudentToken()),
    body: JSON.stringify(data),
  }, 60000);
  if (!r.ok) {
    const errText = await r.text();
    console.error('[Submit] Server error:', r.status, errText);
    throw new Error(`Submission failed: ${r.status} ${errText}`);
  }
  return r.json();
});

ipcMain.handle('get-events', async (event, sessionId) => {
  if (!_assertMainFrame(event, 'get-events')) throw new Error('Frame not allowed');
  const r = await fetchWithTimeout(`${SERVER_URL}/api/v1/events/${encodeURIComponent(sessionId)}`,
    { headers: authHeaders(getStudentToken()) }, 15000);
  if (!r.ok) return { events: [] };
  return r.json();
});

ipcMain.handle('start-calibration', async (event, data) => {
  if (!_assertMainFrame(event, 'start-calibration')) throw new Error('Frame not allowed');
  const sessionId = data && data.sessionId;
  if (sessionId) setCurrentSessionId(sessionId);
  await startCalibration(sessionId, SERVER_URL, getStudentToken(), getMainWindow());
  return { started: true };
});

ipcMain.handle('stop-calibration', (event, data) => {
  if (!_assertMainFrame(event, 'stop-calibration')) throw new Error('Frame not allowed');
  const biases = data && data.biases;
  if (biases) {
    setCalBiases(biases);
    console.log('[CAL] Biases received:', JSON.stringify(getCalBiases()));
  }
  stopCalibration();
  return { stopped: true };
});

ipcMain.handle('start-proctor', async (event, data) => {
  if (!_assertMainFrame(event, 'start-proctor')) throw new Error('Frame not allowed');
  const sessionId = data && data.sessionId;
  if (sessionId) setCurrentSessionId(sessionId);
  await startPython(sessionId, SERVER_URL, getStudentToken(), getCalBiases());
  return { started: true };
});

ipcMain.handle('start-polling', (event, data) => {
  if (!_assertMainFrame(event, 'start-polling')) throw new Error('Frame not allowed');
  const sessionId = data && data.sessionId;
  if (sessionId) setCurrentSessionId(sessionId);
  startPolling(sessionId, getMainWindow(), getStudentToken(),
    () => { if (getMainWindow() && !getMainWindow().isDestroyed()) getMainWindow().webContents.send('force-submit'); },
    (evt) => {
      if (getMainWindow() && !getMainWindow().isDestroyed()) {
        getMainWindow().webContents.send('violation-detected', {
          type: evt.type, details: evt.details, severity: evt.severity,
        });
      }
    }
  );
  return { polling: true };
});

ipcMain.handle('stop-proctor', (event) => {
  if (!_assertMainFrame(event, 'stop-proctor')) throw new Error('Frame not allowed');
  stopPython();
  stopPolling();
  return { stopped: true };
});

ipcMain.handle('consume-invite-token', (event) => {
  if (!_assertMainFrame(event, 'consume-invite-token')) throw new Error('Frame not allowed');
  return consumeInviteToken();
});

ipcMain.handle('get-exam-context', (event) => {
  if (!_assertMainFrame(event, 'get-exam-context')) throw new Error('Frame not allowed');
  return getExamContext();
});

ipcMain.handle('get-server-url', (event) => {
  if (!_assertMainFrame(event, 'get-server-url')) throw new Error('Frame not allowed');
  return SERVER_URL;
});

// App version for the on-screen "Procta vX.Y.Z" badge (lobby + exam). A
// non-sensitive string, so no frame gate — handy for support + spotting
// which build a student is on (e.g. when an update hasn't landed yet).
ipcMain.handle('get-app-version', () => { try { return app.getVersion(); } catch { return ''; } });

ipcMain.handle('lobby-launch-exam', async (event, ctx) => {
  if (!_assertMainFrame(event, 'lobby-launch-exam')) throw new Error('Frame not allowed');
  if (!ctx || !ctx.rollNumber) return { ok: false, error: 'Missing roll number' };
  setExamContext({
    rollNumber: String(ctx.rollNumber).trim().toUpperCase(),
    accessCode: String(ctx.accessCode || '').trim().toUpperCase(),
    examTitle: ctx.examTitle || '',
    teacherId: ctx.teacherId || null,
    examId: ctx.examId || null,
  });
  console.log('[Lobby] launch exam:', getExamContext());

  // Camera pre-flight (macOS TCC). If the OS denies camera access the
  // proctor would throw on VideoCapture and could take the exam-start
  // flow down with it. Catch it here, BEFORE the exam window arms, and
  // surface a clear, escapable message — keep the student in the lobby.
  try {
    const cam = await ensureCameraAccess();
    if (cam && !cam.ok) {
      const lobby = getLobbyWindow();
      try {
        await dialog.showMessageBox(lobby && !lobby.isDestroyed() ? lobby : undefined, {
          type: 'warning',
          title: 'Camera access needed',
          message: 'Procta can\'t access your camera.',
          detail: 'Proctoring requires your camera. Open System Settings → ' +
            'Privacy & Security → Camera, enable Procta, then start the exam again.',
          buttons: ['OK'], defaultId: 0, noLink: true,
        });
      } catch(e) { /* dialog best-effort */ }
      return { ok: false, error: 'camera-denied' };
    }
  } catch(e) { console.error('[Camera] preflight threw:', e.message); }

  if (getLobbyWindow() && !getLobbyWindow().isDestroyed()) {
    try { getLobbyWindow().hide(); } catch(e) {}
  }
  createExamWindow();
  return { ok: true };
});

ipcMain.handle('panic-unlock', async (event, payload) => {
  if (!_assertMainFrame(event, 'panic-unlock')) throw new Error('Frame not allowed');
  await handlePanicUnlock((payload && payload.reason) || 'renderer-triggered');
  return { ok: true };
});

ipcMain.handle('exit-exam-to-lobby', (event) => {
  if (!_assertMainFrame(event, 'exit-exam-to-lobby')) throw new Error('Frame not allowed');
  releaseKiosk({ reopenLobby: true });
  return { ok: true };
});

ipcMain.handle('admin-exit', (event, code) => {
  if (!_assertMainFrame(event, 'admin-exit')) throw new Error('Frame not allowed');
  if (code === 'AUTO_CLOSE') {
    console.log('[admin-exit] AUTO_CLOSE received');
    const winRef = getMainWindow();
    try { releaseKiosk({ reopenLobby: true }); }
    catch(e) { console.error('[admin-exit] releaseKiosk threw:', e.message); }
    setTimeout(() => {
      if (winRef && !winRef.isDestroyed()) {
        console.error('[admin-exit] window still alive after releaseKiosk; force-destroying');
        try { winRef.destroy(); } catch(e) {}
      }
      if (getLobbyWindow() && !getLobbyWindow().isDestroyed()) {
        try { getLobbyWindow().show(); getLobbyWindow().focus(); } catch(e) {}
      } else {
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
