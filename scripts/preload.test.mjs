// scripts/preload.test.mjs — contextBridge exposure contract for preload.js
//
// preload.js is the ONLY bridge between the sandboxed exam renderer and the
// privileged main process — contextIsolation means the renderer literally
// cannot reach ipcRenderer/require/node globals except through whatever this
// file explicitly exposes via contextBridge.exposeInMainWorld. It had zero
// test coverage (5 bugfixes in the last 180 days per repowise) despite being
// exactly the kind of file where a channel-name typo or an accidentally-
// leaked raw ipcRenderer reference is a real security regression, not just a
// broken feature.
//
//   node --test scripts/preload.test.mjs
//
import { test, describe, before } from 'node:test';
import assert from 'node:assert/strict';
import Module from 'node:module';

const invokeCalls = [];
const sendSyncCalls = [];
const onCalls = [];
const onceCalls = [];
const removeAllListenersCalls = [];

const ipcRenderer = {
  invoke: (channel, ...args) => { invokeCalls.push({ channel, args }); return Promise.resolve({ channel, args }); },
  sendSync: (channel, ...args) => { sendSyncCalls.push({ channel, args }); return `sync:${channel}`; },
  on: (channel, fn) => onCalls.push({ channel, fn }),
  once: (channel, fn) => onceCalls.push({ channel, fn }),
  removeAllListeners: (channel) => removeAllListenersCalls.push(channel),
};

let exposedKey = null;
let exposedApi = null;
const contextBridge = {
  exposeInMainWorld: (key, api) => { exposedKey = key; exposedApi = api; },
};

const _origLoad = Module._load;
Module._load = function (request, ...rest) {
  if (request === 'electron') return { contextBridge, ipcRenderer };
  return _origLoad.call(this, request, ...rest);
};

before(async () => {
  await import('../preload.js');
  Module._load = _origLoad;
});

describe('contextBridge exposure', () => {
  test('exposes exactly one namespace: "proctor"', () => {
    assert.equal(exposedKey, 'proctor');
    assert.ok(exposedApi);
  });

  test('never leaks the raw ipcRenderer or a require function to the renderer', () => {
    for (const [name, value] of Object.entries(exposedApi)) {
      assert.notEqual(value, ipcRenderer, `${name} leaks the raw ipcRenderer object`);
      assert.notEqual(typeof value, 'undefined', `${name} is undefined`);
      assert.ok(['function'].includes(typeof value), `${name} is not a function`);
    }
  });
});

describe('IPC channel wiring — regression guard against channel-name typos', () => {
  test('getServerUrl uses sendSync (NOT invoke) — a documented fix', () => {
    // invoke() returns a Promise; the renderer does
    // `const SERVER = getServerUrl()` and uses it as a string immediately.
    // A regression back to invoke() silently breaks every renderer fetch
    // (relative URLs -> 404) without an obvious error at the call site.
    exposedApi.getServerUrl();
    assert.equal(sendSyncCalls.at(-1).channel, 'get-server-url-sync');
  });

  test('submitExam invokes submit-exam with the payload', async () => {
    await exposedApi.submitExam({ answers: { q1: 'A' } });
    const call = invokeCalls.at(-1);
    assert.equal(call.channel, 'submit-exam');
    assert.deepEqual(call.args, [{ answers: { q1: 'A' } }]);
  });

  test('validateStudent forwards both positional args', async () => {
    await exposedApi.validateStudent('ALICE001', 'CODE123');
    const call = invokeCalls.at(-1);
    assert.equal(call.channel, 'validate-student');
    assert.deepEqual(call.args, ['ALICE001', 'CODE123']);
  });

  test('panicUnlock wraps the reason in an object', async () => {
    await exposedApi.panicUnlock('camera blocked');
    const call = invokeCalls.at(-1);
    assert.equal(call.channel, 'panic-unlock');
    assert.deepEqual(call.args, [{ reason: 'camera blocked' }]);
  });

  test('signAttestation and signKioskState hit the v2/heartbeat channels', async () => {
    await exposedApi.signAttestation({ nonce: 'n1' });
    assert.equal(invokeCalls.at(-1).channel, 'procta:sign-attestation');
    await exposedApi.signKioskState();
    assert.equal(invokeCalls.at(-1).channel, 'procta:sign-kiosk-state');
  });
});

describe('event listener leak prevention', () => {
  test('onCalReading removes prior listeners before registering (retry/recalibration safe)', () => {
    removeAllListenersCalls.length = 0;
    onCalls.length = 0;
    exposedApi.onCalReading(() => {});
    assert.ok(removeAllListenersCalls.includes('cal-reading'));
    assert.equal(onCalls.filter(c => c.channel === 'cal-reading').length, 1);
  });

  test('onViolation removes prior listeners before registering', () => {
    removeAllListenersCalls.length = 0;
    exposedApi.onViolation(() => {});
    assert.ok(removeAllListenersCalls.includes('violation-detected'));
  });

  test('onProctorFailed removes prior listeners before registering', () => {
    removeAllListenersCalls.length = 0;
    exposedApi.onProctorFailed(() => {});
    assert.ok(removeAllListenersCalls.includes('proctor-failed'));
  });

  test('onForceSubmit uses ONCE, not ON — must fire exactly once per exam', () => {
    onceCalls.length = 0;
    exposedApi.onForceSubmit(() => {});
    assert.equal(onceCalls.filter(c => c.channel === 'force-submit').length, 1);
  });
});
