const { app, ipcMain, powerSaveBlocker, globalShortcut } = require('electron');
const path = require('path');
const {
  SERVER_URL, ADMIN_CODE, INVITE_REGEX, BLOCKING_TYPES,
} = require('./config');
const { extractInviteToken, authHeaders } = require('./lib/utils');
const { initAutoUpdater } = require('./lib/auto-update');
const { runIntegrityChecks } = require('./lib/integrity');
const {
  findPython, checkPackagesReady, runWindowsSetup,
  createSetupWindow, getSetupWindow, closeSetupWindow,
  startPython, stopPython, startCalibration, stopCalibration,
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
const { startProcessMonitor, stopProcessMonitor } = (() => {
  let _interval = null;
  return {
    startProcessMonitor: () => {
      if (_interval) return;
      console.log('[Monitor] starting continuous process monitoring');
      _interval = setInterval(() => _scanProcesses(), 30000);
    },
    stopProcessMonitor: () => {
      if (_interval) { clearInterval(_interval); _interval = null; console.log('[Monitor] stopped'); }
    },
  };
})();

async function _scanProcesses() {
  const { THREATS } = require('./config');
  const { _exec } = require('./lib/utils');
  const isWin = process.platform === 'win32';
  const output = await _exec(isWin ? 'tasklist /fo csv /nh' : 'ps -eo comm', 8000);
  if (!output) return;
  const lower = output.toLowerCase();
  for (const { rx, label, type } of THREATS) {
    if (rx.test(lower)) {
      if (getCurrentSessionId() && getStudentToken()) {
        fetch(`${SERVER_URL}/event`, {
          method: 'POST', headers: authHeaders(getStudentToken()),
          body: JSON.stringify({ session_id: getCurrentSessionId(),
            event_type: type, severity: 'high',
            details: `[Live scan] ${label} detected during exam` }),
        }).catch(() => {});
      }
      if (getMainWindow() && !getMainWindow().isDestroyed()) {
        getMainWindow().webContents.send('violation-detected', {
          type, severity: 'high',
          details: `[Live scan] ${label} detected during exam`,
        });
      }
      console.log(`[Monitor] THREAT: ${label} (${type})`);
    }
  }
  try {
    const { screen } = require('electron');
    const displays = screen.getAllDisplays();
    if (displays.length > 1 && getCurrentSessionId() && getStudentToken()) {
      fetch(`${SERVER_URL}/event`, {
        method: 'POST', headers: authHeaders(getStudentToken()),
        body: JSON.stringify({ session_id: getCurrentSessionId(),
          event_type: 'multiple_monitors', severity: 'medium',
          details: `[Live scan] ${displays.length} displays detected` }),
      }).catch(() => {});
    }
  } catch(e) {}
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

// ── Single-instance lock & protocol (must be before whenReady) ────
const _gotSingleInstanceLock = app.requestSingleInstanceLock();
if (!_gotSingleInstanceLock) {
  app.quit();
} else {
  app.on('second-instance', (_evt, argv) => {
    try {
      for (let i = argv.length - 1; i >= 0; i--) {
        const tok = extractInviteToken(argv[i], INVITE_REGEX);
        if (tok) { receiveInviteToken(tok, 'second-instance'); break; }
      }
    } catch(e) { console.error('[Invite] second-instance parse error:', e.message); }
    if (getLobbyWindow() && !getLobbyWindow().isDestroyed()) {
      if (getLobbyWindow().isMinimized()) getLobbyWindow().restore();
      getLobbyWindow().show();
      getLobbyWindow().focus();
    }
  });
}

app.on('open-url', (event, url) => {
  event.preventDefault();
  const tok = extractInviteToken(url, INVITE_REGEX);
  if (tok) receiveInviteToken(tok, 'open-url');
});

if (!app.isDefaultProtocolClient('procta')) {
  try {
    if (process.defaultApp && process.argv.length >= 2) {
      app.setAsDefaultProtocolClient('procta', process.execPath, [path.resolve(process.argv[1])]);
    } else {
      app.setAsDefaultProtocolClient('procta');
    }
  } catch(e) { console.error('[Invite] setAsDefaultProtocolClient failed:', e.message); }
}

// ── ELECTRON PERFORMANCE TUNING ──────────────────────────────────
// Disable GPU hardware acceleration to reduce VRAM/CPU overhead.
// The exam UI is text + CSS — no WebGL or canvas rendering needed.
// This cuts ~80-150MB of GPU process memory on Windows.
app.disableHardwareAcceleration();

// Disable GPU sandbox — saves another ~30MB on the GPU process.
app.commandLine.appendSwitch('disable-gpu-sandbox');

// Disable Site Isolation (saves ~40MB per renderer on low-end devices).
app.commandLine.appendSwitch('disable-site-isolation-trial');

// Reduce the renderer process idle time before it releases memory.
app.commandLine.appendSwitch('js-flags', '--max-old-space-size=256');

// ── APP START ─────────────────────────────────────────────────────
app.whenReady().then(async () => {
  createLobbyWindow();

  try {
    for (let i = process.argv.length - 1; i >= 0; i--) {
      const tok = extractInviteToken(process.argv[i], INVITE_REGEX);
      if (tok) { receiveInviteToken(tok, 'argv'); break; }
    }
  } catch(e) { console.error('[Invite] argv parse error:', e.message); }

  // Defer auto-updater to avoid blocking startup on slow networks
  setTimeout(() => initAutoUpdater(getLobbyWindow(), getMainWindow), 3000);

  setIntegrityReady(runIntegrityChecks().then(flags => {
    flags.forEach(f => pushIntegrityFlag(f));
    console.log(`[Integrity] async checks complete: ${flags.length} flag(s)`);
  }).catch(e => {
    console.error('[Integrity] check failed:', e.message);
  }));

  if (process.platform === 'win32') {
    setTimeout(async () => {
      try {
        const python = await findPython();
        const packagesOk = python && await checkPackagesReady(python);
        if (!packagesOk) {
          createSetupWindow();
          try {
            // Timeout the entire setup flow at 10 minutes
            await Promise.race([
              runWindowsSetup(),
              new Promise((_, reject) => setTimeout(() => reject(new Error('Setup timed out')), 600_000)),
            ]);
            await new Promise(r => setTimeout(r, 2000));
          } catch(e) { console.error('[Setup] Failed:', e); }
          if (getSetupWindow() && !getSetupWindow().isDestroyed()) closeSetupWindow();
        } else {
          console.log('[Setup] Python ready, skipping setup');
        }
      } catch(e) { console.error('[Setup] Error:', e); }
    }, 500);
  }
});

app.on('before-quit', () => {
  stopPython();
  stopPolling();
  if (getLobbyWindow() && !getLobbyWindow().isDestroyed()) {
    getLobbyWindow().destroy();
  }
});

app.on('window-all-closed', () => {
  stopPython();
  stopPolling();
  try { globalShortcut.unregisterAll(); } catch(e) {}
  app.quit();
});

// ── IPC HANDLERS ──────────────────────────────────────────────────
ipcMain.handle('get-integrity-flags', async () => {
  const ready = getIntegrityReady();
  if (ready) { try { await ready; } catch(e) {} }
  return getIntegrityFlags().map(f => ({
    ...f,
    blocking: BLOCKING_TYPES.has(f.type) && f.severity === 'high',
  }));
});

ipcMain.handle('validate-student', async (_, roll, accessCode) => {
  const body = { roll_number: roll, access_code: accessCode || '' };
  if (getExamContext() && getExamContext().examId) body.exam_id = getExamContext().examId;
  const r = await fetch(`${SERVER_URL}/api/validate-student`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) { const e = await r.json(); throw new Error(e.detail); }
  const data = await r.json();
  setStudentToken(data.token || null);
  return data;
});

ipcMain.handle('get-questions', async (_, sessionId) => {
  const qs = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : '';
  const r = await fetch(`${SERVER_URL}/api/questions${qs}`,
    { headers: authHeaders(getStudentToken()) });
  if (!r.ok) throw new Error('Could not load questions');
  return r.json();
});

ipcMain.handle('log-event', async (_, data) => {
  try {
    await fetch(`${SERVER_URL}/event`, {
      method: 'POST', headers: authHeaders(getStudentToken()),
      body: JSON.stringify(data),
    });
  } catch(e) { console.error('[log-event]', e.message); }
});

ipcMain.handle('submit-exam', async (_, data) => {
  const r = await fetch(`${SERVER_URL}/api/submit-exam`, {
    method: 'POST', headers: authHeaders(getStudentToken()),
    body: JSON.stringify(data),
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
    { headers: authHeaders(getStudentToken()) });
  if (!r.ok) return { events: [] };
  return r.json();
});

ipcMain.handle('start-calibration', async (_, data) => {
  const sessionId = data && data.sessionId;
  if (sessionId) setCurrentSessionId(sessionId);
  await startCalibration(sessionId, SERVER_URL, getStudentToken(), getMainWindow());
  return { started: true };
});

ipcMain.handle('stop-calibration', (_, data) => {
  const biases = data && data.biases;
  if (biases) {
    setCalBiases(biases);
    console.log('[CAL] Biases received:', JSON.stringify(getCalBiases()));
  }
  stopCalibration();
  return { stopped: true };
});

ipcMain.handle('start-proctor', async (_, data) => {
  const sessionId = data && data.sessionId;
  if (sessionId) setCurrentSessionId(sessionId);
  await startPython(sessionId, SERVER_URL, getStudentToken(), getCalBiases());
  return { started: true };
});

ipcMain.handle('start-polling', (_, data) => {
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

ipcMain.handle('stop-proctor', () => {
  stopPython();
  stopPolling();
  return { stopped: true };
});

ipcMain.handle('consume-invite-token', () => consumeInviteToken());

ipcMain.handle('get-exam-context', () => getExamContext());

ipcMain.handle('get-server-url', () => SERVER_URL);

ipcMain.handle('lobby-launch-exam', async (_, ctx) => {
  if (!ctx || !ctx.rollNumber) return { ok: false, error: 'Missing roll number' };
  setExamContext({
    rollNumber: String(ctx.rollNumber).trim().toUpperCase(),
    accessCode: String(ctx.accessCode || '').trim().toUpperCase(),
    examTitle: ctx.examTitle || '',
    teacherId: ctx.teacherId || null,
    examId: ctx.examId || null,
  });
  console.log('[Lobby] launch exam:', getExamContext());
  if (getLobbyWindow() && !getLobbyWindow().isDestroyed()) {
    try { getLobbyWindow().hide(); } catch(e) {}
  }
  createExamWindow();
  return { ok: true };
});

ipcMain.handle('panic-unlock', async (_, payload) => {
  await handlePanicUnlock((payload && payload.reason) || 'renderer-triggered');
  return { ok: true };
});

ipcMain.handle('exit-exam-to-lobby', () => {
  releaseKiosk({ reopenLobby: true });
  return { ok: true };
});

ipcMain.handle('admin-exit', (_, code) => {
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
