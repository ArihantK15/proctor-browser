const path = require('path');
const fs = require('fs');
const os = require('os');
const crypto = require('crypto');
const { spawn, spawnSync, exec } = require('child_process');
const https = require('https');
const { app } = require('electron');
const {
  getPythonCandidates, PIP_PACKAGES,
  SETUP_WIDTH, SETUP_HEIGHT,
} = require('../config');

let resolvedPython = null;
let pythonProcess = null;
let pythonStarting = false;
let pythonShouldRun = false;
let calProcess = null;
let setupWindow = null;

// ── Restart-storm guard ───────────────────────────────────────────
// A proctor that can never initialise (e.g. the camera is held by an
// orphan from a previous crash) would otherwise respawn forever via the
// 'close' handler, thrashing CPU and freezing the machine on reopen. We
// cap restarts to _RESTART_MAX inside a rolling _RESTART_WINDOW_MS and
// then give up, surfacing a 'proctor-failed' IPC to every open window.
let _restartTimes = [];
const _RESTART_WINDOW_MS = 60000;
const _RESTART_MAX = 5;

// Pure decision for the 'close' handler below: given the prior restart
// timestamps and the current time, prune anything outside the rolling window,
// then decide whether this restart would be the (_RESTART_MAX + 1)-th inside
// it. Returns { giveUp, times } where `times` is the new restart-times array to
// store (empty on give-up, prior-pruned + now on a permitted restart).
// Extracted so the storm threshold is unit-testable without driving the whole
// spawn lifecycle — the close handler is a thin wrapper around this.
function _restartStormDecision(restartTimes, now) {
  const kept = restartTimes.filter(t => now - t < _RESTART_WINDOW_MS);
  if (kept.length >= _RESTART_MAX) {
    return { giveUp: true, times: [] };
  }
  kept.push(now);
  return { giveUp: false, times: kept };
}

// ── Orphan tracking ───────────────────────────────────────────────
// Every spawned proctor/calibration pid is appended to a pidfile so a
// later launch can reap survivors of a hard crash (which skips
// before-quit/window-all-closed cleanup). Children are spawned detached
// (POSIX) so they are their own process-group leader and can be killed
// as a group with kill(-pid).
function _pidFilePath() {
  try { return path.join(app.getPath('userData'), 'proctor.pids'); }
  catch(e) { return path.join(os.tmpdir(), 'proctor.pids'); }
}

function _recordPid(pid) {
  if (!pid) return;
  try { fs.appendFileSync(_pidFilePath(), pid + '\n'); }
  catch(e) { console.warn('[AI] pidfile append failed:', e.message); }
}

function _unrecordPid(pid) {
  if (!pid) return;
  try {
    const fp = _pidFilePath();
    if (!fs.existsSync(fp)) return;
    const remaining = fs.readFileSync(fp, 'utf8')
      .split('\n').map(s => s.trim()).filter(Boolean)
      .filter(p => p !== String(pid));
    if (remaining.length) fs.writeFileSync(fp, remaining.join('\n') + '\n');
    else { try { fs.unlinkSync(fp); } catch(e) {} }
  } catch(e) { console.warn('[AI] pidfile prune failed:', e.message); }
}

// Last server context from startPython, so the failure/restart paths can
// post a privacy-safe diagnostic even when the proctor process never came
// up (the cases where proctor.py's own event pipeline can't report). Holds
// only what's needed to reach POST /api/v1/event — no media, no PII.
let _lastServerCtx = null; // { serverUrl, studentToken, sessionId }

// Derive the POST /api/v1/event target from the base server URL, the same
// shape proctor.py builds. Returns null if the URL can't be parsed.
function _eventUrl(serverUrl) {
  try {
    const u = new URL(serverUrl);
    return `${u.protocol}//${u.host}/api/v1/event`;
  } catch (e) { return null; }
}

// Best-effort, fire-and-forget POST of a METADATA-ONLY diagnostic event
// through the existing /api/v1/event pipeline (same EventIn shape the
// proctor uses). Reused by the proctor-failed + restart paths so that a
// proctor which never boots — and therefore can't self-report — is still
// visible server-side. NEVER include frames, audio, tokens, or identity:
// `details` is a small JSON blob of OS/arch/version/exit-code metadata.
function _postDiagnostic(eventType, severity, detailsObj) {
  try {
    const ctx = _lastServerCtx;
    if (!ctx || !ctx.serverUrl) return;
    const url = _eventUrl(ctx.serverUrl);
    if (!url) return;
    const { authHeaders, fetchWithTimeout } = require('./utils');
    const payload = {
      session_id: ctx.sessionId || 'unknown',
      event_type: eventType,
      severity,
      details: JSON.stringify({
        os: process.platform, arch: process.arch,
        electron: process.versions.electron, ...detailsObj,
      }),
    };
    fetchWithTimeout(url, {
      method: 'POST',
      headers: authHeaders(ctx.studentToken),
      body: JSON.stringify(payload),
    }, 5000).catch(() => { /* offline / server down — best-effort */ });
  } catch (e) { /* never let diagnostics break the failure path */ }
}

// Notify every open renderer that proctoring gave up, so the student
// sees a clear message instead of an invisible respawn storm. Uses
// BrowserWindow.getAllWindows() to avoid a circular require on
// kiosk-manager. Also posts a privacy-safe `proctor_failed` diagnostic so
// "we gave up" is visible server-side, not just as a local banner.
function _proctorFailedNotify(detail) {
  try {
    const { BrowserWindow } = require('electron');
    for (const w of BrowserWindow.getAllWindows()) {
      if (w && !w.isDestroyed()) {
        try { w.webContents.send('proctor-failed', { detail }); } catch(e) {}
      }
    }
  } catch(e) { /* best-effort */ }
  // `detail` is a fixed, human-readable banner string (no PII) — safe to
  // forward as metadata so support can correlate the give-up server-side.
  _postDiagnostic('proctor_failed', 'high', { detail });
}

// Best-effort fetch of a pid's full command line, for the reaper's
// identity check. Returns the command-line string, or null if it can't
// be determined (tool missing / errored / pid already gone) so callers
// can decide how to treat the unknown. POSIX uses `ps`; Windows uses
// Get-CimInstance (same approach the integrity check moved to — wmic is
// deprecated/absent on newer Windows).
function _pidCmdline(pid) {
  try {
    if (process.platform === 'win32') {
      const r = spawnSync('powershell', ['-NoProfile', '-NonInteractive', '-Command',
        `(Get-CimInstance Win32_Process -Filter "ProcessId=${pid}").CommandLine`],
        { encoding: 'utf8', timeout: 8000 });
      if (r.status === 0 && typeof r.stdout === 'string' && r.stdout.trim()) return r.stdout;
      return null;
    }
    const r = spawnSync('ps', ['-p', String(pid), '-o', 'command='],
      { encoding: 'utf8', timeout: 5000 });
    if (r.status === 0 && typeof r.stdout === 'string' && r.stdout.trim()) return r.stdout;
    return null;
  } catch(e) {
    return null;
  }
}

// True only when the live pid's command line clearly belongs to OUR
// proctor (`python …/proctor.py`). Used to guard the reaper against PID
// reuse: the pidfile survives reboots, so a recorded pid can later belong
// to an unrelated process. If the command line can't be read at all we
// return true (fail OPEN) — leaving a real camera-holding orphan alive is
// worse than the rare recycled-kill, and `ps`/PowerShell are effectively
// always present.
function _pidIsOurProctor(pid) {
  const cmd = _pidCmdline(pid);
  if (cmd === null) return true;       // undeterminable → fail open
  return /proctor\.py/i.test(cmd);
}

// Kill a pid that is NOT one of our live handles (used by the reaper).
// POSIX: group-kill only (kill(-pid)) — a recycled bare pid is unlikely
// to also be a group leader with that pgid, so this won't nuke an
// unrelated process. Windows: taskkill the whole tree.
function _reapKill(pid) {
  try {
    if (process.platform === 'win32') {
      spawnSync('taskkill', ['/pid', String(pid), '/f', '/t'], { timeout: 5000 });
    } else {
      try { process.kill(-pid, 'SIGKILL'); } catch(e) {}
    }
  } catch(e) { console.error('[AI] reap kill failed:', e.message); }
}

// Read the pidfile on startup and kill any proctor that survived a
// crash, then clear the file. Cross-platform; no broad `pkill python`
// (would nuke unrelated Python). Safe to call before any spawn.
// Async so the caller can fire-and-forget it (`reapOrphanProctors().catch`)
// instead of blocking the cold-start path. The function yields at the first
// `await` below, so the boot sequence (lobby creation) proceeds immediately;
// the rare `_pidIsOurProctor`/`_reapKill` spawnSync (only hit when a live
// orphan pid is found) then runs off the critical path. The proctor only
// starts at exam launch, long after this completes — so nothing races it.
async function reapOrphanProctors() {
  let fp;
  try { fp = _pidFilePath(); } catch(e) { return; }
  let pids = [];
  try {
    try { await fs.promises.access(fp); } catch(_) { return; }  // no pidfile → nothing to reap
    const raw = await fs.promises.readFile(fp, 'utf8');
    pids = raw.split('\n')
      .map(s => parseInt(s.trim(), 10))
      .filter(n => Number.isInteger(n) && n > 0 && n !== process.pid);
  } catch(e) { console.warn('[AI] reaper: pidfile read failed:', e.message); return; }
  for (const pid of pids) {
    let alive = false;
    try { process.kill(pid, 0); alive = true; } catch(e) { alive = false; }
    if (alive) {
      // Guard against PID reuse: the pidfile outlives reboots, so a live
      // pid here may be an unrelated process that recycled our number.
      // Only kill it if its command line still looks like our proctor.
      if (!_pidIsOurProctor(pid)) {
        console.warn('[AI] reaper: pid', pid, 'is alive but not our proctor (recycled) — skipping');
        continue;
      }
      console.warn('[AI] reaper: killing orphan proctor pid', pid);
      _reapKill(pid);
    }
  }
  try { await fs.promises.unlink(fp); } catch(e) {}
}

// ── Camera pre-flight (macOS TCC) ─────────────────────────────────
// On macOS the camera is gated by TCC. If permission is denied/restricted
// the proctor throws on cv2.VideoCapture and the whole exam-start flow
// can take an unhandled error. Check (and prompt if undetermined) BEFORE
// the exam window arms so we can surface an escapable message instead.
// Returns { ok, status }. Fail-open on any preflight bug — never block an
// exam because the check itself errored.
async function ensureCameraAccess() {
  if (process.platform !== 'darwin') return { ok: true, status: 'granted' };
  let systemPreferences;
  try { ({ systemPreferences } = require('electron')); }
  catch(e) { return { ok: true, status: 'unknown' }; }
  try {
    const status = systemPreferences.getMediaAccessStatus('camera');
    if (status === 'granted') return { ok: true, status };
    if (status === 'not-determined') {
      const granted = await systemPreferences.askForMediaAccess('camera');
      return { ok: !!granted, status: granted ? 'granted' : 'denied' };
    }
    return { ok: false, status }; // denied | restricted
  } catch(e) {
    console.warn('[Camera] preflight failed:', e.message);
    return { ok: true, status: 'unknown' };
  }
}

function _exec(cmd, timeout = 8000) {
  return new Promise(resolve => {
    exec(cmd, { encoding: 'utf8', timeout }, (err, stdout) => {
      resolve(err ? '' : stdout);
    });
  });
}

// ── Env scrubber ──────────────────────────────────────────────────
// Returns a shallow copy of `env` with well-known secret / credential
// vars stripped. Applied to process.env before it is forwarded to a
// spawned proctor child. Build-time signing secrets (CSC_*), notarization
// passwords (APPLE_*), update tokens (GH_TOKEN/GITHUB_TOKEN), backend
// DB / service credentials, and similar are pruned so a compromised
// child can't read them out of its own environ. The proctor itself only
// reads PROCTOR_* + a small set of OS path/locale vars, none of which
// match these patterns.
const _SECRET_ENV_DENYLIST = new Set([
  // Code-signing & notarization (electron-builder)
  'CSC_LINK', 'CSC_KEY_PASSWORD',
  'CSC_INSTALLER_LINK', 'CSC_INSTALLER_KEY_PASSWORD',
  'APPLE_ID', 'APPLE_APP_SPECIFIC_PASSWORD', 'APPLE_TEAM_ID',
  'APPLE_ISSUER_ID', 'APPLE_API_KEY', 'APPLE_API_KEY_ID',
  'WIN_CSC_LINK', 'WIN_CSC_KEY_PASSWORD',
  // CI / release tokens
  'GH_TOKEN', 'GITHUB_TOKEN', 'NPM_TOKEN', 'NODE_AUTH_TOKEN',
  // Backend / DB secrets that a build/dev shell may have exported
  'DATABASE_URL', 'POSTGRES_PASSWORD', 'POSTGRES_USER',
  'REDIS_URL', 'REDIS_PASSWORD',
  'SUPABASE_URL', 'SUPABASE_KEY', 'SUPABASE_ANON_KEY',
  'SUPABASE_SERVICE_KEY', 'SUPABASE_SERVICE_ROLE_KEY',
  'SUPABASE_JWT_SECRET',
  'SENTRY_AUTH_TOKEN', 'SENTRY_DSN',
  'STRIPE_SECRET_KEY', 'STRIPE_WEBHOOK_SECRET',
  'RESEND_API_KEY', 'EMAIL_API_KEY', 'SMTP_PASSWORD',
  'TURNSTILE_SECRET_KEY',
  'OPENAI_API_KEY', 'ANTHROPIC_API_KEY',
  'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN',
  'GCP_SERVICE_ACCOUNT_KEY', 'GOOGLE_APPLICATION_CREDENTIALS',
]);
// Pattern-based catch-all for anything that looks like a secret. The
// passthrough below keeps PROCTOR_* vars intact (e.g., PROCTOR_JWT_TOKEN
// matches _TOKEN$ but is a first-party var the proctor expects). OS path
// / locale / Python config vars don't match these patterns and are kept
// as-is.
const _SECRET_NAME_RX = /(_SECRET|_TOKEN|_PASSWORD|_API_KEY|_PRIVATE_KEY)$/i;
function _scrubSecretsFromEnv(env) {
  const out = {};
  for (const k of Object.keys(env)) {
    if (_SECRET_ENV_DENYLIST.has(k)) continue;
    // PROCTOR_* is our own namespace — never scrub. New PROCTOR_*_TOKEN
    // or PROCTOR_*_SECRET vars added later must still reach the child.
    if (!k.startsWith('PROCTOR_') && _SECRET_NAME_RX.test(k)) continue;
    out[k] = env[k];
  }
  return out;
}

function _exists(p) {
  try { return !!p && fs.existsSync(p); } catch { return false; }
}

// ── Bundled relocatable Python + per-user venv ────────────────────
// macOS can't silently install Python the way Windows downloads the
// python.org .exe, and it must NOT pip-install into the system python3
// (PEP 668 "externally managed"). So on macOS we ship a relocatable
// python-build-standalone runtime inside the .app (Resources/
// python-runtime/<arch>/) and, at first launch, create a user-writable
// venv under userData — never inside the signed .app bundle. Packages
// install into that venv exactly like the Windows flow.
function _macArchDir() {
  // matches the directory names bundle-python.js writes
  return process.arch === 'arm64' ? 'aarch64-apple-darwin' : 'x86_64-apple-darwin';
}

// Path to the relocatable interpreter bundled in the packaged app, or
// null if absent (dev builds ship an empty python-runtime/ placeholder).
function getBundledPython() {
  const res = process.resourcesPath || '';
  if (process.platform === 'win32') {
    const p = path.join(res, 'python', 'python.exe');
    return _exists(p) ? p : null;
  }
  if (process.platform === 'darwin') {
    const p = path.join(res, 'python-runtime', _macArchDir(), 'bin', 'python3');
    return _exists(p) ? p : null;
  }
  return null;
}

function getVenvDir() {
  // app may be undefined in some non-Electron test contexts; guard.
  const base = (app && typeof app.getPath === 'function')
    ? app.getPath('userData')
    : os.tmpdir();
  return path.join(base, 'py-venv');
}

function getVenvPython() {
  const dir = getVenvDir();
  return process.platform === 'win32'
    ? path.join(dir, 'Scripts', 'python.exe')
    : path.join(dir, 'bin', 'python3');
}

// Create the venv from `basePython` if it doesn't already exist. Uses
// the default --symlinks behaviour on POSIX so venv/bin/python3 points
// at the (signed) bundled interpreter — required on Apple Silicon where
// the executed binary must carry a valid signature. Returns the venv
// python path on success, or null on failure.
async function ensureVenv(basePython) {
  if (!basePython) return null;
  const venvPy = getVenvPython();
  if (_exists(venvPy)) return venvPy;
  await new Promise(resolve => {
    const child = spawn(basePython, ['-m', 'venv', getVenvDir()],
      { timeout: 120000, stdio: 'ignore' });
    child.on('exit', () => resolve());
    child.on('error', () => resolve());
  });
  return _exists(venvPy) ? venvPy : null;
}

async function findPython() {
  if (resolvedPython) return resolvedPython;

  // Prefer the per-user venv once it exists. On macOS this is where we
  // install packages (see runSetup); on Windows a venv is never created
  // so this check is a harmless no-op. Returning it here means the
  // readiness probe and the proctor spawn both use the right env.
  const venvPy = getVenvPython();
  if (_exists(venvPy)) {
    resolvedPython = venvPy;
    return venvPy;
  }

  const isWin = process.platform === 'win32';
  const candidates = getPythonCandidates();

  for (const p of candidates) {
    try {
      if (p.includes('/') || p.includes('\\')) {
        if (fs.existsSync(p)) {
          resolvedPython = p;
          return p;
        }
      }
    } catch(e) { console.debug('[Python] candidate path check failed:', p, e.message); }
  }

  for (const cmd of (isWin ? ['python','py','python3'] : ['python3','python'])) {
    const output = await _exec(`${cmd} --version`, 3000);
    if (output) {
      resolvedPython = cmd;
      return cmd;
    }
  }

  return null;
}

function getScriptPath() {
  const candidates = [
    path.join(process.resourcesPath || '', 'proctor.py'),
    path.join(__dirname, '..', 'proctor.py'),
    path.join(app.getAppPath(), 'proctor.py'),
  ];
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  return path.join(__dirname, '..', 'proctor.py');
}

// ── Readiness marker ──────────────────────────────────────────────
// The full import probe (checkPackagesReady) spawns Python and imports
// 11 native packages (cv2/onnxruntime/insightface/…), which on a cold
// filesystem can take many seconds — and it ran on EVERY launch. We
// cache a "packages are ready" marker keyed on everything that would
// invalidate it, so steady-state launches skip the probe entirely.
//   key = app version + PIP_PACKAGES set + interpreter path + its
//         mtime + size  → any package change, app upgrade, or Python
//         swap flips the key and forces a re-probe/re-install.
// The marker is written ONLY after a green probe (so a half-install
// never produces one) and cleared on a red probe (self-heal).
function _pkgMarkerPath() {
  try { return path.join(app.getPath('userData'), 'pkg-ready.json'); }
  catch (_) { return null; }
}
function _pkgReadyKey(python) {
  try {
    const st = fs.statSync(python);
    return crypto.createHash('sha256')
      .update(String(app.getVersion()))
      .update('\0').update(JSON.stringify(PIP_PACKAGES))
      .update('\0').update(String(python))
      .update('\0').update(String(st.mtimeMs))
      .update('\0').update(String(st.size))
      .digest('hex');
  } catch (_) { return null; }
}
function isPackagesMarkedReady(python) {
  const mp = _pkgMarkerPath(); const key = _pkgReadyKey(python);
  if (!mp || !key) return false;
  try {
    const saved = JSON.parse(fs.readFileSync(mp, 'utf8'));
    return !!saved && saved.key === key;
  } catch (_) { return false; }
}
function _writePkgReadyMarker(python) {
  const mp = _pkgMarkerPath(); const key = _pkgReadyKey(python);
  if (!mp || !key) return;
  try { fs.writeFileSync(mp, JSON.stringify({ key, at: new Date().toISOString() })); }
  catch (_) {}
}
function clearPkgReadyMarker() {
  const mp = _pkgMarkerPath(); if (!mp) return;
  try { fs.unlinkSync(mp); } catch (_) {}
}

// Cache-aware readiness check. If the marker matches, returns true
// WITHOUT spawning the heavy import probe. Otherwise runs the real
// probe (which (re)writes/clears the marker via checkPackagesReady).
async function checkPackagesReadyCached(python) {
  if (isPackagesMarkedReady(python)) return true;
  return checkPackagesReady(python);
}

async function checkPackagesReady(python) {
  // Full import-readiness probe. MUST stay in sync with PIP_PACKAGES
  // in config.js — every package the proctor needs at runtime is
  // imported once in a single subprocess. Atomic: any missing package
  // → false → the setup flow runs. The four-package check this
  // replaced passed silently when a half-completed install had cv2 +
  // uniface but not vosk, leaving audio + object detection silently
  // disabled with no log signal.
  //
  // Object detection now runs YOLOv8n on onnxruntime (a bundled
  // weights/yolov8n.onnx) — we no longer install or import ultralytics
  // (and therefore no torch). `import onnxruntime` below covers it.
  //
  // Import names differ from pip names in two places:
  //   pip "opencv-python"   → import cv2
  //   pip "websocket-client" → import websocket
  // PIP_PACKAGES "numpy" / "requests" are transitive of cv2 but checked
  // explicitly so a corrupted numpy install (rare but happens after a
  // Python version swap) is caught here.
  return new Promise(resolve => {
    const importLine = [
      'import cv2',
      'import numpy',
      'import requests',
      'import uniface',
      'import onnxruntime',
      'import sounddevice',
      'import vosk',
      'import python_speech_features',
      'import insightface',
      'import websocket',
      'import psutil',
    ].join('; ');
    // 30s ceiling — generous headroom for first-invocation import cost
    // of the heavier native packages (onnxruntime, insightface) on a
    // cold filesystem. Single subprocess keeps the check fast AND atomic.
    const child = spawn(python, ['-c', importLine], { timeout: 30000 });
    let stderr = '';
    if (child.stderr) child.stderr.on('data', d => { stderr += d.toString(); });
    child.on('exit', code => {
      if (code !== 0 && stderr) {
        // Surface which package is missing so the setup window's
        // status text isn't just "checking..." for 30 seconds with
        // no diagnostic. Trims to first ImportError line.
        const firstErr = stderr.split('\n').find(l => l.includes('ImportError') || l.includes('ModuleNotFoundError'));
        if (firstErr) console.warn('[Setup] package check failed:', firstErr.trim());
      }
      // Persist (or invalidate) the readiness marker so the next launch
      // can skip this probe — see checkPackagesReadyCached.
      if (code === 0) _writePkgReadyMarker(python);
      else clearPkgReadyMarker();
      resolve(code === 0);
    });
    child.on('error', () => { clearPkgReadyMarker(); resolve(false); });
  });
}

function downloadFile(url, dest) {
  return new Promise((resolve, reject) => {
    const file = fs.createWriteStream(dest);
    const req  = https.get(url, res => {
      if (res.statusCode === 302 || res.statusCode === 301) {
        file.close();
        fs.unlink(dest, () => {});
        downloadFile(res.headers.location, dest).then(resolve, reject);
        return;
      }
      if (res.statusCode < 200 || res.statusCode >= 300) {
        file.close();
        fs.unlink(dest, () => {});
        reject(new Error(`Download failed: HTTP ${res.statusCode}`));
        res.resume();
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

function createSetupWindow() {
  const { BrowserWindow } = require('electron');
  setupWindow = new BrowserWindow({
    width: SETUP_WIDTH, height: SETUP_HEIGHT,
    frame: true, resizable: false, alwaysOnTop: true,
    title: 'Procta',
    icon: path.join(__dirname, '..', 'build', 'icon.png'),
    backgroundColor: '#0d1117',
    webPreferences: {
      preload: path.join(__dirname, '..', 'setup-preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      devTools: false,
      backgroundThrottling: false,
      spellcheck: false,
    }
  });

  const html = `<!DOCTYPE html>
<html>
<head><meta charset="utf-8">
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{font-family:-apple-system,'Segoe UI',sans-serif;background:#0d1117;color:#c9d1d9;
       display:flex;align-items:center;justify-content:center;height:100vh;
       flex-direction:column;padding:32px;text-align:center}
  h2{color:#58a6ff;margin-bottom:8px;font-size:18px}
  p{color:#8b949e;font-size:13px;margin-bottom:20px}
  /* Real progress bar — replaces the previous indeterminate spinner.
     Two-layer track: outer dim rail + inner accent fill driven by
     the progress IPC events from python-manager.js. */
  .bar-wrap{width:100%;max-width:480px;margin:0 auto 12px}
  .bar-track{position:relative;height:10px;background:#1c2230;
             border:1px solid #30363d;border-radius:6px;overflow:hidden}
  .bar-fill{position:absolute;left:0;top:0;bottom:0;width:0%;
            background:linear-gradient(90deg,#3b82f6,#60a5fa);
            transition:width 220ms ease;border-radius:5px}
  /* Indeterminate stripes while bar-fill width is 0 — used at very
     start before the first progress event lands. */
  .bar-fill.indet{width:100%;background:
    repeating-linear-gradient(45deg,#1f2937,#1f2937 8px,#374151 8px,#374151 16px);
    animation:slide 1s linear infinite}
  @keyframes slide{0%{background-position:0 0}100%{background-position:32px 0}}
  .bar-meta{display:flex;justify-content:space-between;margin-top:6px;
            font-size:11px;color:#6b7280;font-family:monospace}
  .current{margin-top:14px;font-size:13px;color:#e2e8f0;font-weight:500;
           min-height:18px}
  .log{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px;
       width:100%;max-height:180px;overflow-y:auto;font-size:11px;
       font-family:monospace;color:#3fb950;text-align:left;line-height:1.6;
       margin-top:14px}
</style></head>
<body>
  <h2 id="title">Procta</h2>
  <p id="subtitle">Preparing the AI exam environment…</p>
  <div class="bar-wrap">
    <div class="bar-track"><div class="bar-fill indet" id="bar"></div></div>
    <div class="bar-meta">
      <span id="step-label">Starting…</span>
      <span id="step-count"></span>
    </div>
  </div>
  <div class="current" id="current"></div>
  <div class="log" id="log">Starting setup…\n</div>
  <script>
    // Use the contextBridge-exposed setupApi, NOT require('electron')
    // — this window runs with contextIsolation:true + nodeIntegration:false
    // so direct require() returns undefined. v2.3.5's progress bar was
    // broken for exactly this reason; v2.3.7 routes through setup-preload.
    const bar = document.getElementById('bar');
    const stepLabel = document.getElementById('step-label');
    const stepCount = document.getElementById('step-count');
    const current = document.getElementById('current');
    const log = document.getElementById('log');
    if (window.setupApi && typeof window.setupApi.onSetupStatus === 'function') {
      window.setupApi.onSetupStatus((msg) => {
        log.appendChild(document.createTextNode(msg + '\\n'));
        log.scrollTop = log.scrollHeight;
        current.textContent = String(msg || '').replace(/^\\s+/, '');
      });
    }
    if (window.setupApi && typeof window.setupApi.onSetupProgress === 'function') {
      window.setupApi.onSetupProgress((p) => {
        // p = { step:0..total, total, label?:string }
        if (!p || typeof p.step !== 'number' || typeof p.total !== 'number') return;
        bar.classList.remove('indet');
        const pct = p.total > 0 ? Math.max(0, Math.min(100, (p.step / p.total) * 100)) : 0;
        bar.style.width = pct.toFixed(1) + '%';
        if (p.label) stepLabel.textContent = p.label;
        stepCount.textContent = p.step + ' / ' + p.total;
      });
    }
    if (window.setupApi && typeof window.setupApi.onSetupMode === 'function') {
      const title = document.getElementById('title');
      const subtitle = document.getElementById('subtitle');
      window.setupApi.onSetupMode((mode) => {
        if (mode === 'recheck') {
          if (title) title.textContent = 'Checking for updates';
          if (subtitle) subtitle.textContent = 'Verifying Procta is ready to go…';
        } else if (mode === 'fresh') {
          if (title) title.textContent = 'Setting up Procta';
          if (subtitle) subtitle.textContent = 'First-time install. Takes a few minutes — leave this window open.';
        }
      });
    }
  </script>
</body>
</html>`;

  // Use a crypto-random suffix so a hostile process on the same
  // machine can't pre-create a symlink at the predictable path
  // (CodeQL js/insecure-temporary-file). `wx` flag fails fast if
  // the file already exists, defeating the TOCTOU race outright.
  const tmpHtml = path.join(
    os.tmpdir(),
    `proctor_setup_${crypto.randomBytes(8).toString('hex')}.html`,
  );
  fs.writeFileSync(tmpHtml, html, { flag: 'wx' });
  setupWindow.loadFile(tmpHtml);
  setupWindow.setMenuBarVisibility(false);
}

// Strip CR/LF/NUL from internally-generated status strings before
// they land in the renderer console — defends against an upstream
// caller accidentally interpolating a tainted value into `msg`
// (CodeQL js/log-injection). The message itself is internal but
// the helper centralises the rule so future callers can't regress.
function _scrubMsg(msg) {
  return String(msg == null ? '' : msg).replace(/[\r\n\x00]/g, ' ');
}

function sendSetupStatus(msg) {
  const safeMsg = _scrubMsg(msg);
  console.log('[Setup]', safeMsg);
  if (setupWindow && !setupWindow.isDestroyed()) {
    setupWindow.webContents.send('setup-status', safeMsg);
  }
}

// Drives the visual progress bar in the setup window. Step counts are
// computed from the same arithmetic used by the install loop so the
// bar reflects actual work done rather than wall-clock estimate.
function sendSetupProgress(step, total, label) {
  if (setupWindow && !setupWindow.isDestroyed()) {
    setupWindow.webContents.send('setup-progress',
      { step, total, label: _scrubMsg(label || '') });
  }
}

// Tells the setup window whether this is a fresh install (`'fresh'`)
// or a re-launch where packages are already there (`'recheck'`). The
// renderer uses this to swap "Setting up Procta" → "Checking for
// updates" and adjust the subtitle accordingly.
function sendSetupMode(mode) {
  if (setupWindow && !setupWindow.isDestroyed()) {
    setupWindow.webContents.send('setup-mode', String(mode || ''));
  }
}

async function runSetup() {
  // Bigger UX change: bundle ALL pip packages into a single pip call
  // instead of installing them one at a time. pip resolves once and
  // downloads in parallel internally — typically 3-5x faster on the
  // same network than serial installs, and a fraction of the resolver
  // overhead. Since dropping ultralytics/torch the install is far
  // lighter (onnxruntime + insightface + opencv are now the largest),
  // but the single-pass shape is still the faster, atomic choice.
  //
  // Progress bar: 4 phases.
  //   1. Python ready
  //   2. Install AI packages (bundled)
  //   3. Verify imports
  //   4. Download speech models
  const STEP_TOTAL = 4;
  let stepDone = 0;
  const step = (label) => sendSetupProgress(++stepDone, STEP_TOTAL, label);

  sendSetupProgress(0, STEP_TOTAL, 'Finding Python…');
  let python = await findPython();

  if (process.platform === 'darwin') {
    // ── Baked-bundle fast path (the deterministic, offline default) ──
    // CI installs this arch's wheels straight into the signed bundle's
    // site-packages (bundle-python.js runMac + afterPack signs the .so),
    // so a fresh install does ZERO first-launch pip and needs no network.
    // If the bundled interpreter already imports every package, use it
    // directly and finish — no venv, no pip. The venv+pip path below is
    // now only the dev / un-baked fallback.
    const bundled = getBundledPython();
    if (bundled && await checkPackagesReadyCached(bundled)) {
      resolvedPython = bundled;
      sendSetupMode('recheck');
      sendSetupStatus('AI packages bundled — no install needed.');
      sendSetupProgress(STEP_TOTAL, STEP_TOTAL, 'Ready');
      return true;
    }
    // macOS fallback: never pip into the system python3 (PEP 668) and
    // never write inside the signed .app. Use the bundled relocatable
    // interpreter (dev fallback: whatever findPython turned up) to
    // build a user-writable venv under userData, then install into it.
    const venvPy = getVenvPython();
    if (_exists(venvPy)) {
      python = venvPy;
    } else {
      const base = getBundledPython() || python;
      if (!base) {
        sendSetupStatus('Python runtime missing — AI proctoring unavailable.');
        step('Python ready');
        return false;
      }
      sendSetupStatus('Preparing Python environment…');
      python = await ensureVenv(base);
      if (!python) {
        sendSetupStatus('Could not create Python environment — AI features limited.');
        step('Python ready');
        return false;
      }
    }
    resolvedPython = python;
    sendSetupStatus(`Python ready: ${python}`);
  } else if (!python) {
    // Windows: download + silent-install python.org if none is present.
    sendSetupStatus('Python not found. Downloading Python 3.11…');
    sendSetupProgress(0, STEP_TOTAL, 'Downloading Python 3.11…');
    const installerPath = path.join(os.tmpdir(), 'python_installer.exe');
    try {
      await downloadFile(
        'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe',
        installerPath
      );
      sendSetupStatus('Installing Python 3.11 silently…');
      const r = spawnSync(installerPath,
        ['/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_pip=1'],
        { timeout: 300000 });
      if (r.status === 0) {
        sendSetupStatus('Python installed.');
        resolvedPython = null;
        python = await findPython();
      } else {
        sendSetupStatus('Python install failed. Trying pip packages anyway…');
        python = 'python';
      }
    } catch(e) {
      sendSetupStatus(`Download failed: ${e.message}`);
      python = 'python';
    }
  } else {
    sendSetupStatus(`Python found: ${python}`);
  }
  step('Python ready');

  if (await checkPackagesReadyCached(python)) {
    // Already-installed path — flip the window into "Checking for
    // updates" mode and finish fast. The renderer swaps the H2.
    sendSetupMode('recheck');
    sendSetupStatus('All AI packages already installed.');
    sendSetupProgress(STEP_TOTAL, STEP_TOTAL, 'Ready');
    return true;
  }

  sendSetupMode('fresh');
  sendSetupStatus(`Installing AI packages in one pass — this can take a few minutes. Do NOT close this window.`);
  const setupStart = Date.now();

  // Single bundled pip call. --prefer-binary skips slow source builds
  // when a wheel exists; --no-warn-script-location quietens noise.
  const pipArgs = ['-m', 'pip', 'install', '--quiet', '--prefer-binary',
                   '--no-warn-script-location', ...PIP_PACKAGES];

  // Heartbeat so the setup window doesn't look frozen during the
  // multi-minute package download (onnxruntime + insightface + opencv
  // are now the largest). Updates the bar's "current" line every
  // 4 s with elapsed wall-clock time. The bar fill stays at the
  // step-2 mark until pip exits — partial progress inside pip isn't
  // observable from outside.
  const heartbeat = setInterval(() => {
    const elapsed = Math.round((Date.now() - setupStart) / 1000);
    sendSetupStatus(`  Installing AI packages — ${elapsed}s elapsed (large packages download in parallel)`);
  }, 4000);

  const pipOk = await new Promise(resolve => {
    // 15-minute ceiling. Since dropping torch the install is a few
    // hundred MB, so on typical wifi this finishes in 1-3 min; the
    // ceiling is generous headroom for slow demo networks. If pip times
    // out the user retries with a hotspot. Better than letting it hang
    // forever with no signal. NOTE: the main-process setup gate
    // (main.js) sets its outer race ABOVE this, so this child timeout
    // is always the one that fires first on a genuinely stuck install.
    const child = spawn(python, pipArgs, { timeout: 15 * 60 * 1000, stdio: 'ignore' });
    child.on('exit', code => resolve(code === 0));
    child.on('error', () => resolve(false));
  });
  clearInterval(heartbeat);
  const totalSecs = Math.round((Date.now() - setupStart) / 1000);
  sendSetupStatus(pipOk
    ? `Package install done (${totalSecs}s).`
    : `Package install ended with errors (${totalSecs}s). Some AI features may be limited.`);
  step('AI packages installed');

  const ready = await checkPackagesReady(python);
  sendSetupStatus(ready ?
    'All packages verified.' :
    'Some packages still missing — AI features may be limited');
  step('Verified packages');

  // Phase 1.5 — the on-device audio models (Vosk en-IN + hi-IN + Silero
  // VAD) are baked into <resources>/weights at build time, so a packaged
  // install needs NO download (offline + deterministic). Only fall back to
  // the first-launch download when they are NOT bundled (dev runs from a
  // checkout, or a build that skipped the bundle step). Non-fatal either
  // way: a miss leaves the proctor on the RMS-only voice path, never blocks
  // the exam start.
  if (audioModelsPresent(bundledWeightsDir())) {
    console.log('[setup] audio models bundled in app — skipping download.');
    sendSetupStatus('Speech models bundled — ready.');
  } else {
    try {
      await downloadAudioModels(python);
    } catch(e) {
      console.warn('[setup] audio model download failed (proctor falls back to RMS-only):', e.message);
    }
  }
  step('Speech models ready');
  return ready;
}

// ── Background setup state machine ────────────────────────────────
// The lobby opens IMMEDIATELY at boot (it needs no Python); setup runs
// in the background and exam-start is gated on isSetupReady(). This is
// the single source of truth the lobby reads via `get-setup-state` and
// is pushed (the `setup-state` IPC event) on every transition.
//   phase: idle → checking → (ready | installing → ready/error)
let _setupState = { phase: 'idle', ready: false, label: '', pct: 0 };
let _setupStarted = false;
function getSetupState() { return { ..._setupState }; }
function isSetupReady() { return _setupState.ready === true; }
function _setSetup(patch, getLobbyWin) {
  _setupState = { ..._setupState, ...patch };
  try {
    const w = typeof getLobbyWin === 'function' ? getLobbyWin() : null;
    if (w && !w.isDestroyed()) w.webContents.send('setup-state', getSetupState());
  } catch (_) {}
}

// Run the (possibly multi-minute) Python/package provisioning OFF the
// cold-start path. On a warm launch the cached readiness marker resolves
// this to `ready` in ~0ms with no window. On a fresh install it surfaces
// the existing setup window's progress while the lobby stays usable — the
// Start-exam button is gated on isSetupReady() until this finishes.
// Idempotent: safe to call once at boot.
async function startSetupInBackground(getLobbyWin) {
  if (_setupStarted) return;
  _setupStarted = true;
  // E2E hook: pin the state to "not ready" so the harness can verify the
  // exam-start gate refuses to launch while setup is in progress (the
  // safety invariant). Test-only — never set in production.
  if (process.env.PROCTOR_E2E_FORCE_SETUP_NOT_READY === '1') {
    _setSetup({ phase: 'installing', ready: false, label: 'Preparing the AI exam environment…', pct: 5 }, getLobbyWin);
    return;
  }
  // Dev / unsupported platforms manage their own env — treat as ready so
  // exam-start is never blocked there (mirrors the old needsSetupGate).
  if (!app.isPackaged || !(process.platform === 'win32' || process.platform === 'darwin')) {
    _setSetup({ phase: 'ready', ready: true, label: 'Ready', pct: 100 }, getLobbyWin);
    return;
  }
  try {
    _setSetup({ phase: 'checking', ready: false, label: 'Checking AI environment…', pct: 0 }, getLobbyWin);
    const python = await findPython();
    if (python && await checkPackagesReadyCached(python)) {
      _setSetup({ phase: 'ready', ready: true, label: 'Ready', pct: 100 }, getLobbyWin);
      return;
    }
    // Fresh / incomplete install — show the setup window for the install
    // progress and provision while the lobby is open.
    _setSetup({ phase: 'installing', ready: false, label: 'Preparing the AI exam environment…', pct: 5 }, getLobbyWin);
    try { createSetupWindow(); } catch (e) { console.error('[Setup] createSetupWindow threw:', e.message); }
    let ok = false;
    try {
      // 16-min ceiling stays ABOVE runSetup's inner 15-min pip timeout so
      // the inner child is the one that fires first on a genuinely stuck
      // install (same invariant as the old main-process gate).
      ok = await Promise.race([
        runSetup(),
        new Promise((_, reject) => setTimeout(() => reject(new Error('Setup timed out')), 960_000)),
      ]);
    } catch (e) { console.error('[Setup] background setup failed:', e.message); ok = false; }
    try { if (getSetupWindow() && !getSetupWindow().isDestroyed()) closeSetupWindow(); } catch (e) {}
    _setSetup(ok
      ? { phase: 'ready', ready: true, label: 'Ready', pct: 100 }
      : { phase: 'error', ready: false, label: 'AI setup did not finish — reinstall Procta if exams fail to start.', pct: 100 },
      getLobbyWin);
  } catch (e) {
    console.error('[Setup] background setup threw:', e.message);
    _setSetup({ phase: 'error', ready: false, label: 'AI setup failed', pct: 100 }, getLobbyWin);
  }
}

// Writable per-user directory for the on-device speech models (Vosk
// en-IN + hi-IN + Silero VAD). These are pulled at first run, NOT
// bundled, so they must land somewhere the OS lets us write. The bundled
// <resources>/weights dir is READ-ONLY on a signed macOS .app and on
// Windows Program Files, which is exactly why the old download (which
// extracted into <resources>/weights) failed with the user-visible
// "skipping keyword / could not download" message. userData is always
// writable per-user on both platforms.
function audioWeightsDir() {
  return path.join(app.getPath('userData'), 'weights');
}

// The READ-ONLY weights dir shipped inside the packaged app
// (<resources>/weights). As of Phase 1.5 the Vosk en/hi + Silero VAD
// models are baked here at build time (CI runs download_audio_models.py
// before electron-builder), so a fresh install needs ZERO first-launch
// download — fully offline + deterministic. In dev this points at
// electron's own resources dir (no audio models), so the userData copy
// from a first-run download is used instead.
function bundledWeightsDir() {
  return path.join(process.resourcesPath || '', 'weights');
}

// Names the three on-device speech models are stored under. Single list
// reused by audioModelsPresent() + resolveAudioModel() so the bundled and
// runtime sides can't drift. Mirrors download_audio_models.py MODELS[].dir.
const AUDIO_MODEL_NAMES = [
  'vosk-model-small-en-in-0.4',
  'vosk-model-small-hi-0.22',
  'silero_vad.onnx',
];

// True iff every speech model is present + non-empty under `dir`. Used to
// decide whether the bundled copy makes the first-launch download a no-op.
function audioModelsPresent(dir) {
  if (!dir) return false;
  return AUDIO_MODEL_NAMES.every((n) => {
    try {
      const p = path.join(dir, n);
      const st = fs.statSync(p);
      return st.isFile() ? st.size > 0 : fs.readdirSync(p).length > 0;
    } catch { return false; }
  });
}

// Resolve one speech-model path. A user-downloaded copy in userData/weights
// OVERRIDES the bundled copy under <resources>/weights (lets a newer model
// land without a reinstall); otherwise the bundled copy is used; otherwise
// we point at the userData path where a first-run download would land.
// audio_processor degrades to the RMS-only voice path if the final path is
// absent, so this never blocks the exam.
function resolveAudioModel(name) {
  const userPath = path.join(audioWeightsDir(), name);
  const bundledPath = path.join(bundledWeightsDir(), name);
  try { if (fs.existsSync(userPath)) return userPath; } catch { /* fall through */ }
  try { if (fs.existsSync(bundledPath)) return bundledPath; } catch { /* fall through */ }
  return userPath;
}

async function downloadAudioModels(python) {
  // Cross-platform via Python (urllib + zipfile from stdlib). Python
  // is guaranteed to exist by the time we get here — runSetup() just
  // pip-installed packages into it. Avoids two pitfalls:
  //   1. Bash isn't on Windows.
  //   2. ELECTRON_RUN_AS_NODE is disabled by our security fuses.
  const candidates = [
    path.join(__dirname, '..', 'scripts', 'download_audio_models.py'),
    path.join(process.resourcesPath || '', 'scripts', 'download_audio_models.py'),
  ];
  const script = candidates.find(p => { try { return fs.existsSync(p); } catch { return false; } });
  if (!script) {
    console.warn('[setup] audio model script not found — skipping');
    return;
  }
  if (!python) {
    console.warn('[setup] no python — skipping audio model download');
    return;
  }
  const weightsDir = audioWeightsDir();
  try { fs.mkdirSync(weightsDir, { recursive: true }); }
  catch(e) { console.warn('[setup] could not create audio weights dir:', e.message); }

  sendSetupStatus('Downloading speech models (first run only)…');
  await new Promise(resolve => {
    // 5-minute total cap. Fast no-op on a cache hit (no network).
    // PROCTA_WEIGHTS_DIR redirects the extraction into the writable
    // per-user dir instead of the read-only bundle. We capture stdout +
    // stderr (NOT stdio:'ignore') so a failure is diagnosable from the
    // logs / crash.log instead of an opaque "could not download".
    const child = spawn(python, [script], {
      timeout: 300000,
      env: { ...process.env, PROCTA_WEIGHTS_DIR: weightsDir },
    });
    let tail = '';
    const capture = d => {
      const s = d.toString();
      tail = (tail + s).slice(-2000); // keep last ~2 KB for the failure log
      console.log('[audio-models]', s.trim());
    };
    if (child.stdout) child.stdout.on('data', capture);
    if (child.stderr) child.stderr.on('data', capture);
    child.on('exit', code => {
      if (code === 0) {
        sendSetupStatus('Speech models ready.');
      } else {
        console.error(
          `[setup] audio model download exited code=${code}. Last output:\n${tail}`);
        sendSetupStatus('Speech models could not download — voice keywords disabled.');
      }
      resolve();
    });
    child.on('error', err => {
      console.error('[setup] audio model download spawn error:', err.message);
      sendSetupStatus('Speech models skipped.');
      resolve();
    });
  });
}

async function startPython(sessionId, serverUrl, studentToken, calBiases) {
  pythonShouldRun = true;
  // Stash server context up front so the failure paths below (including the
  // "no python found" / "script missing" early returns) can post a
  // privacy-safe diagnostic. Metadata only — no media or identity.
  _lastServerCtx = { serverUrl, studentToken, sessionId };
  if (pythonStarting || (pythonProcess && !pythonProcess.killed)) {
    console.log('[AI] Proctor already running — skipping duplicate start');
    return;
  }
  pythonStarting = true;
  const python = await findPython();
  const script = getScriptPath();

  console.log('[AI] Python:', python);
  console.log('[AI] Script:', script);
  console.log('[AI] Script exists:', fs.existsSync(script));

  if (!python) {
    console.error('[AI] No Python found — AI proctoring disabled');
    pythonStarting = false;
    _proctorFailedNotify(
      'Proctoring could not start — the AI engine is missing. ' +
      'Reinstall the latest Procta, then restart the exam.');
    return false;
  }
  if (!fs.existsSync(script)) {
    console.error('[AI] proctor.py not found');
    pythonStarting = false;
    _proctorFailedNotify(
      'Proctoring could not start — a required component is missing. ' +
      'Reinstall the latest Procta, then restart the exam.');
    return false;
  }
  if (!pythonShouldRun) { pythonStarting = false; return false; }

  const evidenceDir = path.join(app.getPath('userData'), 'evidence');
  try { fs.mkdirSync(evidenceDir, { recursive: true }); } catch(e) { console.warn('[AI] evidence dir creation failed:', e.message); }

  // Scrub well-known secret/credential env vars before handing process.env
  // to the proctor child. The proctor only needs PROCTOR_*, plus OS path
  // / locale vars to find Python libs and tempdirs. Stripping these
  // shrinks the blast radius if proctor.py is ever compromised, and stops
  // notarization/build secrets that happen to be set in dev shells from
  // leaking into a forked process.
  const sanitizedParentEnv = _scrubSecretsFromEnv(process.env);

  const envVars = {
    ...sanitizedParentEnv,
    // Force CPython UTF-8 mode so stdout/stderr (pipes back to us) are UTF-8,
    // not the Windows ANSI code page. Without it the proctor's ✅/❌/🎯 status
    // prints raise UnicodeEncodeError on cp1252 and hard-crash the daemon.
    // proctor.py also self-reconfigures, but setting this covers any
    // import-time/library output that races ahead of that line.
    PYTHONUTF8:                      '1',
    PROCTOR_SESSION_ID:              sessionId,
    // Base URL only — proctor.py builds the /api/v1/event POST target
    // itself. The old "${serverUrl}/event" form is still accepted by
    // proctor.py for backward compat, but every URL it derived from
    // that (HEARTBEAT_URL, system-check, ws upgrade) routed wrong
    // because of an rstrip bug. Pass the base and let proctor.py
    // normalise.
    PROCTOR_SERVER_URL:              serverUrl,
    PROCTOR_EVIDENCE_DIR:            evidenceDir,
    PROCTOR_JWT_TOKEN:               studentToken || '',
    PROCTOR_SKIP_ENROLLMENT:         '1',
    PROCTOR_WRONG_PERSON_THRESHOLD:  '0.25',
    PROCTOR_VOICE_THRESHOLD:         '0.035',
  };

  // Point the on-device audio path at the speech models. resolveAudioModel()
  // prefers the bundled <resources>/weights copy (baked at build time, Phase
  // 1.5) and lets a user-downloaded userData/weights copy override it. Without
  // these, audio_processor defaults to <module>/weights (the wrong dir) and
  // keyword spotting silently stays disabled. audio_processor.available()
  // degrades to the RMS-only voice path if a given file/dir is absent, so
  // setting these unconditionally is safe even when nothing was bundled.
  envVars.PROCTOR_VOSK_EN_MODEL = resolveAudioModel('vosk-model-small-en-in-0.4');
  envVars.PROCTOR_VOSK_HI_MODEL = resolveAudioModel('vosk-model-small-hi-0.22');
  envVars.PROCTOR_SILERO_VAD    = resolveAudioModel('silero_vad.onnx');

  if (calBiases) {
    envVars.PROCTOR_GAZE_YAW_BIAS   = String(calBiases.gaze_yaw);
    envVars.PROCTOR_GAZE_PITCH_BIAS = String(calBiases.gaze_pitch);
    envVars.PROCTOR_HEAD_YAW_BIAS   = String(calBiases.head_yaw);
    envVars.PROCTOR_HEAD_PITCH_BIAS = String(calBiases.head_pitch);
    if (calBiases.gaze_yaw_range != null) {
      envVars.PROCTOR_GAZE_YAW_RANGE   = String(calBiases.gaze_yaw_range);
      envVars.PROCTOR_GAZE_PITCH_RANGE = String(calBiases.gaze_pitch_range);
      envVars.PROCTOR_HEAD_YAW_RANGE   = String(calBiases.head_yaw_range);
      envVars.PROCTOR_HEAD_PITCH_RANGE = String(calBiases.head_pitch_range);
    }
  }

  // detached on POSIX makes the child its own process-group leader so
  // stopPython can kill the whole group with kill(-pid). We keep the
  // reference (no unref) and keep draining stdout/stderr. On Windows we
  // do NOT detach (it can flash a console window); taskkill /t handles
  // the tree there instead.
  // Bind everything to the closure-local `proc`, not the module-level
  // `pythonProcess`, so the close handler can't act on a freshly-spawned
  // replacement if a restart raced in between (same class of bug the
  // stopPython `dying` snapshot guards against).
  const proc = spawn(python, [script], {
    env: envVars,
    detached: process.platform !== 'win32',
  });
  pythonProcess = proc;
  pythonStarting = false;
  _recordPid(proc.pid);

  proc.stdout.on('data', d => console.log('[AI]', d.toString().trim()));
  proc.stderr.on('data', d => console.error('[AI]', d.toString().trim()));
  proc.on('close', code => {
    console.log('[AI] Exited:', code);
    _unrecordPid(proc.pid);
    if (pythonProcess === proc) pythonProcess = null;
    // code !== 0 covers both genuine error exits and signal kills (null).
    if (code !== 0 && pythonShouldRun) {
      const { giveUp, times } = _restartStormDecision(_restartTimes, Date.now());
      _restartTimes = times;
      if (giveUp) {
        console.error(
          `[AI] Restart storm — ${_RESTART_MAX} restarts in ` +
          `${_RESTART_WINDOW_MS / 1000}s. Giving up to avoid a respawn loop.`);
        pythonShouldRun = false;
        _proctorFailedNotify(
          'Proctoring could not start after several attempts. ' +
          'Close any app using your camera, then restart the exam.');
        return;
      }
      const delay = code === null ? 5000 : 3000;
      console.log(
        `[AI] Unexpected exit (code=${code}) — restarting in ${delay / 1000}s ` +
        `(attempt ${_restartTimes.length}/${_RESTART_MAX})`);
      // Privacy-safe restart telemetry: attempt N + exit code, metadata only.
      _postDiagnostic('restart_attempt', 'low',
        { attempt: _restartTimes.length, max: _RESTART_MAX, exit_code: code });
      const restartTimer = setTimeout(() => {
        if (pythonShouldRun) {
          startPython(sessionId, serverUrl, studentToken, calBiases);
        }
      }, delay);
      restartTimer.unref();
    }
  });
  proc.on('error', err => console.error('[AI] Spawn error:', err.message));
  return true;
}

function stopPython() {
  pythonShouldRun = false;
  _restartTimes = [];
  if (pythonProcess) {
    // Capture the doomed process in a local so the kill fallback below
    // can only ever target this specific process. Without this, the
    // module-level `pythonProcess` could be reassigned to a freshly-spawned
    // proctor before the 2s timer fires — and `pythonProcess.kill()` would
    // then silently kill the new process.
    const dying = pythonProcess;
    const dyingPid = dying.pid;
    pythonProcess = null;
    try {
      if (process.platform === 'win32') {
        spawnSync('taskkill', ['/pid', String(dyingPid), '/f', '/t'],
          { timeout: 5000 });
      } else {
        // Send SIGINT first so Python can run its finally blocks (cap.release)
        dying.kill('SIGINT');
        // Escalate to a whole-process-group SIGKILL if THIS process is
        // still alive after 2s. A proctor wedged in an onnxruntime import
        // ignores SIGINT/SIGTERM; the group kill (negative pid, possible
        // because we spawned detached) guarantees it and any child it
        // forked are gone — no orphan left holding the camera.
        const fallbackTimer = setTimeout(() => {
          let alive = false;
          try { process.kill(dyingPid, 0); alive = true; } catch(e) {}
          if (alive) {
            try { process.kill(-dyingPid, 'SIGKILL'); }
            catch(e) { try { dying.kill('SIGKILL'); } catch(e2) {} }
          }
        }, 2000);
        fallbackTimer.unref();
      }
    } catch(e) { console.error('[AI] Stop error:', e.message); }
    _unrecordPid(dyingPid);
  }
}

async function startCalibration(sessionId, serverUrl, studentToken, mainWindow) {
  stopCalibration();

  const sendCalReading = (data) => {
    try {
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('cal-reading', data);
      }
    } catch (e) {
      console.debug('[CAL] send failed:', e.message);
    }
  };

  const python = await findPython();
  const script = getScriptPath();
  if (!python || !fs.existsSync(script)) {
    console.error('[CAL] Python or script not found — calibration unavailable');
    sendCalReading({
      error: 'calibration_unavailable',
      detail: 'Python runtime or proctor script was not found.'
    });
    return;
  }

  // Closure-local `proc` so the close handler can't null a replacement.
  const proc = spawn(python, [script], {
    detached: process.platform !== 'win32',
    env: {
      ..._scrubSecretsFromEnv(process.env),
      // See startPython: UTF-8 mode keeps cp1252 from crashing the proctor's
      // emoji status prints on Windows (the bug that stranded calibration).
      PYTHONUTF8:               '1',
      PROCTOR_SESSION_ID:       sessionId,
      PROCTOR_SERVER_URL:       serverUrl,
      PROCTOR_JWT_TOKEN:        studentToken || '',
      PROCTOR_CALIBRATION_MODE: '1',
      PROCTOR_SKIP_ENROLLMENT:  '1',
      PROCTOR_HEADLESS:         '1',
    }
  });
  calProcess = proc;
  const _calPid = proc.pid;
  _recordPid(_calPid);
  let sawCalReading = false;
  const stderrTail = [];

  sendCalReading({ status: 'starting' });

  proc.stdout.on('data', d => {
    const lines = d.toString().split('\n');
    for (const line of lines) {
      const trimmed = line.trim();
      if (trimmed.startsWith('CAL:')) {
        try {
          const reading = JSON.parse(trimmed.slice(4));
          sawCalReading = true;
          sendCalReading(reading);
        } catch (e) { console.debug('[CAL] malformed JSON:', e.message); }
      } else if (trimmed) {
        console.log('[CAL]', trimmed);
      }
    }
  });
  proc.stderr.on('data', d => {
    const text = d.toString().trim();
    if (text) {
      stderrTail.push(text);
      while (stderrTail.length > 6) stderrTail.shift();
    }
    console.error('[CAL]', text);
  });
  proc.on('close', code => {
    console.log('[CAL] Exited:', code);
    if (!sawCalReading) {
      sendCalReading({
        error: 'calibration_process_exited',
        detail: stderrTail.join('\n').slice(-700) ||
          `Calibration process exited before camera readings arrived (code ${code}).`
      });
    }
    _unrecordPid(_calPid);
    if (calProcess === proc) calProcess = null;
  });
  proc.on('error', err => {
    console.error('[CAL] Spawn error:', err.message);
    sendCalReading({ error: 'calibration_spawn_failed', detail: err.message });
  });
  console.log('[CAL] Calibration proctor started');
}

function stopCalibration() {
  if (calProcess) {
    // Same race fix as stopPython: snapshot the process so the 2s
    // SIGTERM fallback can't hit a freshly-started calibration proctor.
    const dying = calProcess;
    const dyingPid = dying.pid;
    calProcess = null;
    try {
      if (process.platform === 'win32') {
        spawnSync('taskkill', ['/pid', String(dyingPid), '/f', '/t'],
          { timeout: 5000 });
      } else {
        dying.kill('SIGINT');
        // Same group-kill escalation as stopPython: guarantee the
        // calibration proctor is gone so it can't keep the camera.
        const fallbackTimer = setTimeout(() => {
          let alive = false;
          try { process.kill(dyingPid, 0); alive = true; } catch(e) {}
          if (alive) {
            try { process.kill(-dyingPid, 'SIGKILL'); }
            catch(e) { try { dying.kill('SIGKILL'); } catch(e2) {} }
          }
        }, 2000);
        fallbackTimer.unref();
      }
    } catch (e) { console.error('[CAL] Stop error:', e.message); }
    _unrecordPid(dyingPid);
  }
}

function getSetupWindow() { return setupWindow; }
function closeSetupWindow() {
  if (setupWindow && !setupWindow.isDestroyed()) setupWindow.close();
  setTimeout(() => cleanupTempHtml(), 1000);
}

function cleanupTempHtml() {
  // Temp files from createSetupWindow() use predictable prefix + random suffix
  // in os.tmpdir(). Clean up any leftover files so they don't accumulate.
  try {
    const tmpDir = os.tmpdir();
    const prefix = 'proctor_setup_';
    for (const f of fs.readdirSync(tmpDir)) {
      if (f.startsWith(prefix) && f.endsWith('.html')) {
        const fp = path.join(tmpDir, f);
        try { fs.unlinkSync(fp); } catch(e) { console.debug('[Setup] tmp cleanup:', e.message); }
      }
    }
  } catch(e) { console.debug('[Setup] tmpdir scan:', e.message); }
}

// ── Pre-exam System Check (Phase 1.4) ─────────────────────────────
// A proactive, on-device readiness probe a student can run BEFORE exam
// day. It exercises the full LOCAL pipeline and returns a green/red
// summary per component — Python, AI packages, every model (via
// proctor.py --selftest), camera (macOS TCC), microphone, and speech
// models. 100% on-device: nothing here ships frames, audio, identity, or
// file paths off the machine. The caller (main.js IPC) may post ONLY the
// per-component status summary (OS/arch/version/statuses/tier) as a
// `system_check` diagnostic event — keep that boundary intact, it's the
// privacy selling point. Every sub-check is wrapped so a probe bug can
// never throw into / break the lobby.

// macOS microphone permission, mirroring ensureCameraAccess(). The mic is
// OPTIONAL — audio_processor degrades to the RMS-only voice path without
// it — so a denial is a 'warn', never a hard block. Non-darwin returns
// 'unknown' (Windows/Linux don't gate the mic the way macOS TCC does).
async function ensureMicAccess() {
  if (process.platform !== 'darwin') return { ok: true, status: 'unknown' };
  let systemPreferences;
  try { ({ systemPreferences } = require('electron')); }
  catch (e) { return { ok: true, status: 'unknown' }; }
  try {
    const status = systemPreferences.getMediaAccessStatus('microphone');
    if (status === 'granted') return { ok: true, status };
    if (status === 'not-determined') {
      const granted = await systemPreferences.askForMediaAccess('microphone');
      return { ok: !!granted, status: granted ? 'granted' : 'denied' };
    }
    return { ok: false, status }; // denied | restricted
  } catch (e) {
    console.warn('[Mic] preflight failed:', e.message);
    return { ok: true, status: 'unknown' };
  }
}

// Spawn `proctor.py --selftest`, parse its single `SELFTEST:` JSON line.
// Resolves { ok:true, report } once the report parses — REGARDLESS of exit
// code (exit 1 just signals a degraded tier, still useful to surface) — or
// { ok:false, error } if Python produced no parseable report at all. Never
// throws.
function runSelfTest(python, timeoutMs = 60000) {
  return new Promise(resolve => {
    let script;
    try { script = getScriptPath(); } catch (e) { return resolve({ ok: false, error: 'script-missing' }); }
    if (!python || !script || !fs.existsSync(script)) {
      return resolve({ ok: false, error: 'python-or-script-missing' });
    }
    // Mirror startPython's audio env so the report's audio_models flags
    // reflect the REAL (bundled / user-downloaded) model paths. No session,
    // JWT, server, or evidence env — --selftest does no camera, no
    // proctoring loop, and no event POST.
    const env = {
      ..._scrubSecretsFromEnv(process.env),
      PYTHONUTF8: '1',
      PROCTOR_VOSK_EN_MODEL: resolveAudioModel('vosk-model-small-en-in-0.4'),
      PROCTOR_VOSK_HI_MODEL: resolveAudioModel('vosk-model-small-hi-0.22'),
      PROCTOR_SILERO_VAD:    resolveAudioModel('silero_vad.onnx'),
    };
    let out = '';
    let done = false;
    const finish = (v) => { if (!done) { done = true; resolve(v); } };
    let child;
    try {
      child = spawn(python, [script, '--selftest'], { timeout: timeoutMs, env });
    } catch (e) { return finish({ ok: false, error: 'spawn-failed' }); }
    if (child.stdout) child.stdout.on('data', d => { out += d.toString(); });
    // stderr carries import/log noise from the model loaders — drain it so
    // the pipe never back-pressures, but it's not part of the report.
    if (child.stderr) child.stderr.on('data', () => {});
    child.on('exit', () => {
      const line = out.split('\n').find(l => l.startsWith('SELFTEST:'));
      if (!line) return finish({ ok: false, error: 'no-report' });
      try { finish({ ok: true, report: JSON.parse(line.slice('SELFTEST:'.length)) }); }
      catch (e) { finish({ ok: false, error: 'bad-report' }); }
    });
    child.on('error', () => finish({ ok: false, error: 'spawn-error' }));
  });
}

// Pure mapping: raw probe inputs → the green/red System Check result.
// Kept side-effect-free (no spawn, no electron, no fs) so it is unit
// testable in plain node — runSystemCheck() below is the thin I/O shell
// that gathers `inputs` and delegates here. Inputs:
//   python  : truthy interpreter path | null
//   pkgOk   : bool (checkPackagesReady result)
//   selftest: { ok:true, report } | { ok:false, error } | null (skipped)
//   camera  : { ok, status } from ensureCameraAccess
//   mic     : { ok, status } from ensureMicAccess
//   audioOk : bool (speech models present)
//   at      : ISO timestamp
function _buildSystemCheckResult(inputs) {
  const { python, pkgOk, selftest, camera, mic, audioOk, at } = inputs;
  const components = {};

  components.python = python
    ? { status: 'green', detail: 'Interpreter found' }
    : { status: 'red', detail: 'No Python runtime found' };

  components.packages = pkgOk
    ? { status: 'green', detail: 'All AI packages import cleanly' }
    : { status: 'red', detail: python ? 'One or more AI packages are missing' : 'No Python to probe' };

  // Models via --selftest — only meaningful once the packages import.
  let tier = null;
  if (python && pkgOk) {
    if (selftest && selftest.ok && selftest.report) {
      const r = selftest.report;
      tier = (r.proctoring && r.proctoring.tier) || null;
      const missing = (r.proctoring && r.proctoring.missing) || [];
      let status = 'green', detail = 'All detectors loaded (full proctoring)';
      if (tier === 'reduced') { status = 'warn'; detail = 'Reduced proctoring — some detectors unavailable'; }
      else if (tier === 'minimal') { status = 'warn'; detail = 'Minimal proctoring — face detection unavailable'; }
      components.models = {
        status, detail, tier, missing,
        modelFlags: r.models || {},
        audioModels: r.audio_models || {},
        modelErrors: r.model_errors || {},
      };
    } else {
      components.models = { status: 'warn', detail: 'Could not run the model self-test', error: (selftest && selftest.error) || 'unknown' };
    }
  } else {
    components.models = { status: 'skip', detail: 'Skipped — AI packages not ready' };
  }

  const cam = camera || { ok: true, status: 'unknown' };
  components.camera = cam.ok
    ? { status: 'green', detail: cam.status === 'unknown' ? 'Available' : 'Access granted' }
    : { status: 'red', detail: 'Camera access denied — proctoring needs your camera' };

  // Microphone — OPTIONAL (degrades to RMS-only), so a denial is a warn.
  const m = mic || { ok: true, status: 'unknown' };
  components.mic = m.ok
    ? { status: 'green', detail: m.status === 'unknown' ? 'Available' : 'Access granted' }
    : { status: 'warn', detail: 'Microphone denied — voice keyword spotting limited' };

  // Speech models present — OPTIONAL (bundled at build time, Phase 1.5).
  components.audio = audioOk
    ? { status: 'green', detail: 'Speech models present' }
    : { status: 'warn', detail: 'Speech models absent — voice keywords use RMS-only fallback' };

  // Overall readiness: the exam can START as long as Python + packages +
  // camera are green. Model tier (reduced/minimal), mic, and speech models
  // NEVER flip ok=false — graceful degradation (Phase 1.6) keeps the exam
  // running with whatever loaded; they surface as warnings only.
  const ok = components.python.status === 'green'
    && components.packages.status === 'green'
    && components.camera.status === 'green';

  // Metadata-ONLY summary for optional server telemetry. OS/arch/version +
  // per-component status strings + tier. NO identity, media, or file paths.
  const summary = {
    os: process.platform, arch: process.arch,
    electron: process.versions.electron,
    ok, tier,
    components: Object.fromEntries(
      Object.entries(components).map(([k, v]) => [k, v.status])),
  };

  return { ok, at, tier, components, summary };
}

async function runSystemCheck() {
  const at = new Date().toISOString();

  // 1) Python interpreter
  let python = null;
  try { python = await findPython(); } catch (e) { python = null; }

  // 2) AI packages — run the REAL import probe (not the cached marker) so
  //    the check reflects the live state of the machine right now.
  let pkgOk = false;
  if (python) {
    try { pkgOk = await checkPackagesReady(python); } catch (e) { pkgOk = false; }
  }

  // 3) Models via --selftest — only meaningful once the packages import.
  let selftest = null;
  if (python && pkgOk) {
    selftest = await runSelfTest(python);
  }

  // 4) Camera (macOS TCC; granted by default elsewhere).
  let camera = { ok: true, status: 'unknown' };
  try { camera = await ensureCameraAccess(); } catch (e) { camera = { ok: true, status: 'unknown' }; }

  // 5) Microphone — OPTIONAL (degrades to RMS-only), so a denial is a warn.
  let mic = { ok: true, status: 'unknown' };
  try { mic = await ensureMicAccess(); } catch (e) { mic = { ok: true, status: 'unknown' }; }

  // 6) Speech models present — OPTIONAL (bundled at build time, Phase 1.5).
  let audioOk = false;
  try { audioOk = audioModelsPresent(bundledWeightsDir()) || audioModelsPresent(audioWeightsDir()); }
  catch (e) { audioOk = false; }

  return _buildSystemCheckResult({ python, pkgOk, selftest, camera, mic, audioOk, at });
}

module.exports = {
  findPython, getScriptPath, checkPackagesReady, checkPackagesReadyCached,
  clearPkgReadyMarker,
  startSetupInBackground, getSetupState, isSetupReady,
  // runSetup is the cross-platform entry point; runWindowsSetup kept as a
  // back-compat alias so any external caller / older code still resolves.
  runSetup, runWindowsSetup: runSetup,
  getBundledPython, getVenvPython, ensureVenv,
  createSetupWindow, getSetupWindow, closeSetupWindow,
  cleanupTempHtml,
  startPython, stopPython,
  startCalibration, stopCalibration,
  reapOrphanProctors, ensureCameraAccess,
  ensureMicAccess, runSelfTest, runSystemCheck, _buildSystemCheckResult,
  _restartStormDecision, _scrubSecretsFromEnv,
};
