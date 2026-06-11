// scripts/polling.test.mjs — unit tests for lib/polling.js
//
// The student lobby polls /api/v1/events/{sid} to receive live violations and
// the teacher force-submit signal. This loop is exam-critical and had zero
// tests; it also recently regressed (only the FIRST new violation in a batch
// was dispatched). These lock the contract:
//   • force-submit fires exactly once on exam_submitted
//   • EVERY new high/medium violation is forwarded (not just the first)
//   • already-seen events are not re-dispatched (lastEventId advances)
//   • ignored event types and low-severity events are filtered out
//   • a failing server triggers exponential backoff (ticks are skipped)
//
//   node --test scripts/polling.test.mjs
//
import { test, describe, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { startPolling, stopPolling } = require('../lib/polling');
const { POLL_INTERVAL_MS } = require('../config');

const _realFetch = global.fetch;
const flush = () => new Promise(r => setImmediate(r));

// Replace global.fetch with a queue-driven mock. Each entry describes one
// response; the last entry repeats once the queue is drained.
function mockFetch(responses) {
  let i = 0;
  const calls = { count: 0 };
  global.fetch = async () => {
    calls.count++;
    const spec = responses[Math.min(i++, responses.length - 1)] || {};
    return {
      ok: spec.ok !== false,
      status: spec.status || 200,
      json: async () => ({ events: spec.events || [] }),
    };
  };
  return calls;
}

afterEach(() => {
  stopPolling();
  global.fetch = _realFetch;
});

// Drive the poll loop `n` times, letting each async tick settle.
async function pump(t, n) {
  for (let k = 0; k < n; k++) {
    t.mock.timers.tick(POLL_INTERVAL_MS);
    await flush();
    await flush();
  }
}

describe('startPolling — force submit', () => {
  test('fires forceSubmit exactly once on exam_submitted', async (t) => {
    t.mock.timers.enable({ apis: ['setInterval'] });
    mockFetch([{ events: [{ id: 1, type: 'exam_submitted' }] }]);
    let forced = 0;
    startPolling('S1', null, 'tok', () => { forced++; }, () => {});
    await pump(t, 3);   // same event delivered 3 times
    assert.equal(forced, 1);
  });
});

describe('startPolling — violation forwarding', () => {
  test('dispatches EVERY new high/medium violation in a batch', async (t) => {
    t.mock.timers.enable({ apis: ['setInterval'] });
    mockFetch([{ events: [
      { id: 1, severity: 'high',   type: 'phone_consulting' },
      { id: 2, severity: 'medium', type: 'noise_detected' },
      { id: 3, severity: 'high',   type: 'multi_face' },
    ] }]);
    const got = [];
    startPolling('S1', null, 'tok', () => {}, (v) => got.push(v.id));
    await pump(t, 1);
    assert.deepEqual(got.sort(), [1, 2, 3]);   // all three, not just the first
  });

  test('does not re-dispatch already-seen events on the next tick', async (t) => {
    t.mock.timers.enable({ apis: ['setInterval'] });
    mockFetch([{ events: [{ id: 1, severity: 'high', type: 'phone_consulting' }] }]);
    let count = 0;
    startPolling('S1', null, 'tok', () => {}, () => { count++; });
    await pump(t, 3);   // same event id=1 returned every tick
    assert.equal(count, 1);
  });

  test('filters out ignored event types and low severity', async (t) => {
    t.mock.timers.enable({ apis: ['setInterval'] });
    mockFetch([{ events: [
      { id: 10, severity: 'high',   type: 'screenshot' },      // ignored type
      { id: 11, severity: 'low',    type: 'phone_consulting' },// low severity
      { id: 12, severity: 'medium', type: 'answer_selected' }, // ignored type
      { id: 13, severity: 'high',   type: 'gaze_away' },       // the only real one
    ] }]);
    const got = [];
    startPolling('S1', null, 'tok', () => {}, (v) => got.push(v.id));
    await pump(t, 1);
    assert.deepEqual(got, [13]);
  });
});

describe('startPolling — failure backoff', () => {
  test('a failing server skips subsequent ticks (exponential backoff)', async (t) => {
    t.mock.timers.enable({ apis: ['setInterval'] });
    const calls = mockFetch([{ ok: false, status: 500 }]);
    startPolling('S1', null, 'tok', () => {}, () => {});
    // tick1 fetches+fails → skipTicks=min(2^1,30)=2; tick2,tick3 skip; tick4 fetches.
    await pump(t, 4);
    assert.equal(calls.count, 2);   // only ticks 1 and 4 actually hit the server
  });
});
