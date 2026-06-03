// scripts/electron-e2e.mjs — Electron integration smoke test.
//
// Drives the REAL packaged-shape app via the Chrome DevTools Protocol
// (Node's built-in fetch + WebSocket — no Playwright dependency) and asserts
// the things we historically only checked by hand:
//
//   1. The lobby window loads under sandbox:true and its contextBridge
//      (window.procta_native) is exposed — i.e. sandbox didn't silently
//      break lobby_preload (the exact regression we hit and verified once
//      manually). Also that the dashboard actually RENDERS (non-blank).
//   2. Launching the exam window (via the lobby bridge) opens a window
//      that loads renderer/index.html and exposes window.proctor — i.e.
//      the exam preload + window lifecycle work, and the exam renderer is
//      not a blank frame.
//
// Runs windowed (--no-kiosk + PROCTOR_DEBUG=1) so it never takes over the
// screen, and against NO backend (we assert window/preload lifecycle, not
// exam content). Self-contained: spawns Electron with a throwaway
// user-data-dir and a remote-debugging port, tears everything down after.
//
//   npm run test:e2e
//
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const electronPath = require('electron'); // path to the electron binary
const projectRoot = join(fileURLToPath(new URL('.', import.meta.url)), '..');
const PORT = 9242;

let child;
let userDataDir;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function cdpTargets() {
  try {
    const res = await fetch(`http://127.0.0.1:${PORT}/json/list`);
    return await res.json();
  } catch {
    return [];
  }
}

// Poll the CDP target list until a *page* whose url matches `rx` appears.
async function waitForTarget(rx, { timeoutMs = 25000, label = '' } = {}) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const list = await cdpTargets();
    const t = list.find((x) => x.type === 'page' && rx.test(x.url || ''));
    if (t && t.webSocketDebuggerUrl) return t;
    await sleep(400);
  }
  throw new Error(`timed out waiting for target ${label || rx} (debugging port ${PORT})`);
}

// Evaluate an expression in a target's page context, return the value.
function evaluate(target, expression) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    const id = 1;
    const timer = setTimeout(() => { try { ws.close(); } catch {} reject(new Error('CDP evaluate timeout')); }, 10000);
    ws.addEventListener('open', () => {
      ws.send(JSON.stringify({ id, method: 'Runtime.evaluate', params: { expression, returnByValue: true, awaitPromise: true } }));
    });
    ws.addEventListener('message', (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id !== id) return;
      clearTimeout(timer);
      try { ws.close(); } catch {}
      if (m.result && m.result.exceptionDetails) {
        reject(new Error('page threw: ' + (m.result.exceptionDetails.text || JSON.stringify(m.result.exceptionDetails))));
      } else {
        resolve(m.result && m.result.result ? m.result.result.value : undefined);
      }
    });
    ws.addEventListener('error', (e) => { clearTimeout(timer); reject(new Error('CDP ws error: ' + (e.message || e))); });
  });
}

before(async () => {
  userDataDir = mkdtempSync(join(tmpdir(), 'procta-e2e-'));
  child = spawn(
    electronPath,
    ['.', `--remote-debugging-port=${PORT}`, '--no-kiosk', `--user-data-dir=${userDataDir}`],
    { cwd: projectRoot, env: { ...process.env, PROCTOR_DEBUG: '1', SENTRY_DSN: '' }, stdio: 'ignore' },
  );
  // wait until the CDP endpoint is up
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    if ((await cdpTargets()).length) break;
    await sleep(400);
  }
});

after(async () => {
  try { child?.kill('SIGKILL'); } catch {}
  // child reaping is best-effort; also sweep any stragglers from this run
  await sleep(300);
  if (userDataDir) { try { rmSync(userDataDir, { recursive: true, force: true }); } catch {} }
});

test('lobby loads under sandbox and exposes its bridge (non-blank)', async () => {
  const lobby = await waitForTarget(/student\.html|procta-lobby/, { label: 'lobby' });
  const bridge = await evaluate(lobby, 'typeof window.procta_native');
  assert.equal(bridge, 'object', 'window.procta_native must exist under sandbox:true (lobby_preload regression guard)');

  const serverUrl = await evaluate(lobby, '(window.procta_native && window.procta_native.serverUrl) || null');
  assert.ok(serverUrl && /^https?:\/\//.test(serverUrl), `bridge.serverUrl should be a URL, got ${serverUrl}`);

  const bodyLen = await evaluate(lobby, 'document.body ? document.body.innerText.trim().length : 0');
  assert.ok(bodyLen > 20, `dashboard rendered content (got ${bodyLen} chars) — guards against a blank frame`);

  const controls = await evaluate(lobby, 'document.querySelectorAll("input,button").length');
  assert.ok(controls > 0, 'lobby rendered interactive controls');
});

test('launching the exam window opens a renderer with the proctor bridge', async () => {
  const lobby = await waitForTarget(/student\.html|procta-lobby/, { label: 'lobby' });
  // Drive the real lobby→exam path through the bridge (top-frame IPC).
  await evaluate(lobby, `window.procta_native.launchExam({ rollNumber: 'E2E-TEST', accessCode: '', examTitle: 'E2E' })`);

  // The exam window loads renderer/index.html (windowed, since --no-kiosk).
  const exam = await waitForTarget(/renderer\/index\.html|index\.html/, { label: 'exam window' });
  const proctorBridge = await evaluate(exam, 'typeof window.proctor');
  assert.equal(proctorBridge, 'object', 'window.proctor must exist in the exam renderer (preload.js under sandbox)');

  // It exposes the IPC surface the exam relies on.
  const hasApi = await evaluate(exam, 'typeof window.proctor.submitExam === "function" && typeof window.proctor.getQuestions === "function"');
  assert.equal(hasApi, true, 'exam bridge exposes submitExam/getQuestions');
});
