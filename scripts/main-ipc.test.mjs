// scripts/main-ipc.test.mjs — IPC handler contract tests for main.js
//
// main.js wires ~22 privileged IPC handlers (the trust boundary between the
// exam renderer and the main process). The most important contract is the
// frame-gate: every data/state handler must reject calls that did not come
// from the TOP frame of a window we created — otherwise an injected iframe (XSS
// in a question prompt, a malicious embed) could drive submit-exam / panic /
// validate-student. This had zero coverage.
//
// We load main.js under a mock `electron` (installed via a Module._load hook —
// node runs each test file in its own process, so the override is isolated)
// whose app.whenReady() never resolves, so the window bootstrap is skipped but
// the top-level ipcMain.handle registrations are still captured.
//
//   node --test scripts/main-ipc.test.mjs
//
import { test, describe, before } from 'node:test';
import assert from 'node:assert/strict';
import Module from 'node:module';

// ── Mock electron ─────────────────────────────────────────────────
const noop = () => {};
const fnProxy = () => new Proxy(noop, { get: () => fnProxy(), apply: () => undefined });

const handlers = new Map();   // channel → invoke handler (ipcMain.handle)
const listeners = new Map();  // channel → handler (ipcMain.on)

const ipcMain = {
  handle: (ch, fn) => handlers.set(ch, fn),
  handleOnce: (ch, fn) => handlers.set(ch, fn),
  on: (ch, fn) => listeners.set(ch, fn),
  removeHandler: noop, removeAllListeners: noop,
};

const app = new Proxy({
  requestSingleInstanceLock: () => true,        // must be truthy or main quits early
  whenReady: () => new Promise(() => {}),         // never resolves → skip bootstrap
  getVersion: () => '9.9.9-test',
  getPath: () => '/tmp/procta-test',
  getName: () => 'Procta',
  isReady: () => false,
  setAsDefaultProtocolClient: () => true,
  commandLine: { appendSwitch: noop, hasSwitch: () => false },
}, { get: (t, p) => (p in t ? t[p] : noop) });

class BrowserWindow {
  static getAllWindows() { return []; }
  static fromWebContents() { return null; }
}

const screen = new Proxy({
  getAllDisplays: () => [{}],
  getPrimaryDisplay: () => ({ workAreaSize: { width: 1280, height: 800 }, bounds: {} }),
}, { get: (t, p) => (p in t ? t[p] : noop) });

const electron = new Proxy({
  app, ipcMain, BrowserWindow, screen,
  globalShortcut: { register: () => true, unregister: noop, unregisterAll: noop, isRegistered: () => false },
  dialog: { showMessageBox: async () => ({ response: 0 }), showMessageBoxSync: () => 0, showErrorBox: noop },
  protocol: { registerSchemesAsPrivileged: noop, handle: noop },
  shell: { openExternal: async () => {} },
}, { get: (t, p) => (p in t ? t[p] : fnProxy()) });

// ── Install the mock + load main.js ───────────────────────────────
const _origLoad = Module._load;
Module._load = function (request, ...rest) {
  if (request === 'electron') return electron;
  if (request === 'electron-updater') return { autoUpdater: new Proxy({}, { get: () => noop }) };
  return _origLoad.call(this, request, ...rest);
};

before(() => {
  Module.createRequire(import.meta.url)('../main');   // registers the IPC handlers
});

const SUB_FRAME = { senderFrame: { parent: {}, url: 'http://evil.example/iframe' } };
const MAIN_FRAME = { senderFrame: { parent: null, url: 'file:///app/renderer/index.html' } };

const invoke = (ch, ev, ...args) => handlers.get(ch)(ev, ...args);

// Privileged handlers that must enforce the frame-gate. Includes the
// state-changing/destructive ones (kiosk panic-unlock, admin-exit, proctor
// start/stop, exam exit, lobby launch) — an iframe must never be able to drive
// a kiosk escape or terminate the exam — alongside the data handlers.
const GATED = ['get-integrity-flags', 'validate-student', 'get-questions',
               'log-event', 'submit-exam', 'get-events', 'start-calibration',
               'start-proctor', 'stop-proctor', 'panic-unlock', 'admin-exit',
               'exit-exam-to-lobby', 'lobby-launch-exam'];

describe('IPC frame-gate (security boundary)', () => {
  for (const ch of GATED) {
    test(`${ch} rejects a sub-frame caller`, async () => {
      assert.ok(handlers.has(ch), `handler ${ch} should be registered`);
      // `async () =>` so a SYNC handler's throw is surfaced as a rejection too
      // (some gated handlers are sync, e.g. stop-proctor / admin-exit).
      await assert.rejects(async () => invoke(ch, SUB_FRAME), /frame not allowed/i);
    });
  }

  test('a handler with no senderFrame is rejected', async () => {
    await assert.rejects(() => invoke('submit-exam', {}), /frame not allowed/i);
  });
});

describe('IPC contract registry', () => {
  test('the documented privileged channels are all registered', () => {
    const expected = [...GATED, 'get-app-version', 'get-exam-context'];
    for (const ch of expected) assert.ok(handlers.has(ch), `missing handler: ${ch}`);
  });
});

describe('submit-exam / get-events behaviour (main frame)', () => {
  const _realFetch = global.fetch;

  test('submit-exam returns the server JSON on success', async (t) => {
    t.after(() => { global.fetch = _realFetch; });
    global.fetch = async () => ({ ok: true, status: 200, json: async () => ({ score: 7, total: 10 }) });
    const r = await invoke('submit-exam', MAIN_FRAME, { session_id: 'S1', answers: {} });
    assert.deepEqual(r, { score: 7, total: 10 });
  });

  test('submit-exam throws with the status on a server error', async (t) => {
    t.after(() => { global.fetch = _realFetch; });
    global.fetch = async () => ({ ok: false, status: 503, text: async () => 'unavailable' });
    await assert.rejects(() => invoke('submit-exam', MAIN_FRAME, { session_id: 'S1' }), /503/);
  });

  test('get-events degrades to an empty list when the server is not OK', async (t) => {
    t.after(() => { global.fetch = _realFetch; });
    global.fetch = async () => ({ ok: false, status: 500 });
    const r = await invoke('get-events', MAIN_FRAME, 'S1');
    assert.deepEqual(r, { events: [] });
  });
});

test('restore Module._load', () => { Module._load = _origLoad; });
