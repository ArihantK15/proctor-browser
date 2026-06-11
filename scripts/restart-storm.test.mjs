// scripts/restart-storm.test.mjs — unit tests for the proctor restart-storm guard
//
// A proctor that can never initialise (e.g. the camera is held by an orphan
// from a prior crash) would respawn forever via python-manager's 'close'
// handler. The guard caps restarts to _RESTART_MAX (5) inside a rolling 60s
// window, then gives up and fires proctor-failed. That decision is the
// critical safety valve — if it never trips, the machine thrashes; if it trips
// too early, a student loses a recoverable proctor.
//
// _restartStormDecision(restartTimes, now) is the pure core of the close
// handler. Window/threshold come from the real module constants.
//
//   node --test scripts/restart-storm.test.mjs
//
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { _restartStormDecision } = require('../lib/python-manager');

const NOW = 1_000_000_000_000;
const WINDOW = 60_000;   // _RESTART_WINDOW_MS
const MAX = 5;           // _RESTART_MAX

// `n` restart timestamps, all just inside the rolling window.
const recent = (n) => Array.from({ length: n }, (_, i) => NOW - (i + 1) * 1000);

describe('_restartStormDecision', () => {
  test('first restart (no history) is permitted and recorded', () => {
    const r = _restartStormDecision([], NOW);
    assert.equal(r.giveUp, false);
    assert.deepEqual(r.times, [NOW]);
  });

  test('the 5th restart within the window is still permitted', () => {
    // 4 prior restarts in-window → this one makes 5; under the cap of 5.
    const r = _restartStormDecision(recent(4), NOW);
    assert.equal(r.giveUp, false);
    assert.equal(r.times.length, 5);
    assert.equal(r.times.includes(NOW), true);
  });

  test('the 6th restart within the window trips the storm guard', () => {
    // 5 prior restarts already in-window → give up, do not restart again.
    const r = _restartStormDecision(recent(5), NOW);
    assert.equal(r.giveUp, true);
    assert.deepEqual(r.times, []);   // history cleared on give-up
  });

  test('restarts older than the window are pruned and do not count', () => {
    // 5 restarts but all >60s ago → window is effectively empty → permitted.
    const stale = Array.from({ length: 5 }, (_, i) => NOW - WINDOW - (i + 1) * 1000);
    const r = _restartStormDecision(stale, NOW);
    assert.equal(r.giveUp, false);
    assert.deepEqual(r.times, [NOW]);   // stale entries dropped, only `now` kept
  });

  test('mixed history: only in-window restarts count toward the cap', () => {
    // 3 recent + 2 stale → only 3 count → 3 < 5 → permitted, recorded as 4.
    const mixed = [...recent(3), NOW - WINDOW - 5000, NOW - WINDOW - 9000];
    const r = _restartStormDecision(mixed, NOW);
    assert.equal(r.giveUp, false);
    assert.equal(r.times.length, 4);
    assert.equal(r.times.every(t => NOW - t < WINDOW), true);   // all kept are in-window
  });

  test('a restart exactly at the window boundary is treated as expired', () => {
    // now - t === WINDOW is NOT "< WINDOW", so it is pruned (matches the
    // filter `now - t < _RESTART_WINDOW_MS`).
    const boundary = Array.from({ length: 5 }, () => NOW - WINDOW);
    const r = _restartStormDecision(boundary, NOW);
    assert.equal(r.giveUp, false);   // all 5 pruned → not a storm
  });
});

// Guard: keep the test's assumptions about the cap honest. If someone changes
// _RESTART_MAX, this surfaces it rather than letting the threshold tests rot.
describe('storm guard cap sanity', () => {
  test('cap is exactly 5 restarts before give-up', () => {
    assert.equal(_restartStormDecision(recent(MAX - 1), NOW).giveUp, false);
    assert.equal(_restartStormDecision(recent(MAX), NOW).giveUp, true);
  });
});
