// scripts/permission-gate.test.mjs — macOS exam-start TCC gate (fail closed)
//
// The exam must NOT start if a student denies the camera or microphone
// (incl. a hurried "Don't Allow"). lobby-launch-exam runs this pure decision
// over the two preflight results and blocks before createExamWindow().
//
//   node --test scripts/permission-gate.test.mjs
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { permissionBlock } = require('../lib/permission-gate.js');

describe('permissionBlock', () => {
  test('both granted → proceed (null)', () => {
    assert.equal(permissionBlock({ ok: true }, { ok: true }), null);
  });

  test('camera denied → blocks on Camera', () => {
    assert.deepEqual(
      permissionBlock({ ok: false, status: 'denied' }, { ok: true }),
      { kind: 'Camera', error: 'camera-denied' });
  });

  test('microphone denied (camera ok) → blocks on Microphone', () => {
    assert.deepEqual(
      permissionBlock({ ok: true }, { ok: false, status: 'denied' }),
      { kind: 'Microphone', error: 'mic-denied' });
  });

  test('camera takes precedence when both are denied', () => {
    assert.deepEqual(
      permissionBlock({ ok: false }, { ok: false }),
      { kind: 'Camera', error: 'camera-denied' });
  });

  test('non-macOS results (ok:true) → proceed', () => {
    // ensure*() return ok:true on Windows/Linux → never blocks there.
    assert.equal(
      permissionBlock({ ok: true, status: 'granted' }, { ok: true, status: 'unknown' }),
      null);
  });

  test('absent results (preflight threw upstream) → fail-open, not block', () => {
    assert.equal(permissionBlock(undefined, undefined), null);
    assert.equal(permissionBlock(null, null), null);
  });

  test("a 'restricted' (parental/MDM) camera also blocks", () => {
    assert.deepEqual(
      permissionBlock({ ok: false, status: 'restricted' }, { ok: true }),
      { kind: 'Camera', error: 'camera-denied' });
  });
});
