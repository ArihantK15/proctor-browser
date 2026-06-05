// scripts/system-check.test.mjs — unit tests for the pre-exam System
// Check mapping (Phase 1.4).
//
// Exercises lib/python-manager.js _buildSystemCheckResult(): the pure,
// side-effect-free function that maps raw probe inputs (python found?,
// packages ready?, --selftest report, camera/mic status, speech models
// present?) into the green/red-per-component result the lobby renders.
//
// The I/O shell runSystemCheck() (which spawns Python + calls Electron
// systemPreferences) is intentionally NOT exercised here — that needs a
// full AI env and a real machine. This locks the CONTRACT instead:
//   - ok is gated ONLY on Python + packages + camera (graceful
//     degradation: model tier / mic / speech-models never block start).
//   - the server telemetry `summary` is METADATA ONLY (no media/identity).
//
//   node --test scripts/system-check.test.mjs
//
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { _buildSystemCheckResult } = require('../lib/python-manager');

const fullReport = {
  python_version: '3.11.9', platform: 'Darwin', arch: 'arm64',
  ort_version: '1.18.0',
  models: { retina: true, onnxruntime: true, yolo: true, gaze: true, ear: true, insightface: true, eyes: true },
  proctoring: { tier: 'full', missing: [] },
  audio_models: { vosk_en: true, vosk_hi: true, silero_vad: true },
  model_errors: {},
};

// All-green inputs — the happy path a ready machine produces.
function greenInputs(overrides = {}) {
  return {
    python: '/usr/bin/python3',
    pkgOk: true,
    selftest: { ok: true, report: fullReport },
    camera: { ok: true, status: 'granted' },
    mic: { ok: true, status: 'granted' },
    audioOk: true,
    at: '2026-06-05T00:00:00.000Z',
    ...overrides,
  };
}

describe('System Check — overall ok gating', () => {
  test('all components green → ok true, full tier', () => {
    const r = _buildSystemCheckResult(greenInputs());
    assert.equal(r.ok, true);
    assert.equal(r.tier, 'full');
    assert.equal(r.components.python.status, 'green');
    assert.equal(r.components.packages.status, 'green');
    assert.equal(r.components.models.status, 'green');
    assert.equal(r.components.camera.status, 'green');
    assert.equal(r.components.mic.status, 'green');
    assert.equal(r.components.audio.status, 'green');
  });

  test('camera denied → ok false (camera is a hard gate)', () => {
    const r = _buildSystemCheckResult(greenInputs({ camera: { ok: false, status: 'denied' } }));
    assert.equal(r.components.camera.status, 'red');
    assert.equal(r.ok, false);
  });

  test('packages missing → ok false, models skipped', () => {
    const r = _buildSystemCheckResult(greenInputs({ pkgOk: false, selftest: null }));
    assert.equal(r.components.packages.status, 'red');
    assert.equal(r.components.models.status, 'skip');
    assert.equal(r.ok, false);
  });

  test('python missing → ok false, packages red with no-python detail', () => {
    const r = _buildSystemCheckResult(greenInputs({ python: null, pkgOk: false, selftest: null }));
    assert.equal(r.components.python.status, 'red');
    assert.equal(r.components.packages.status, 'red');
    assert.match(r.components.packages.detail, /No Python/);
    assert.equal(r.components.models.status, 'skip');
    assert.equal(r.ok, false);
  });
});

describe('System Check — graceful degradation (warnings never block)', () => {
  test('reduced tier → models warn but ok stays true', () => {
    const report = { ...fullReport, proctoring: { tier: 'reduced', missing: ['gaze', 'yolo'] } };
    const r = _buildSystemCheckResult(greenInputs({ selftest: { ok: true, report } }));
    assert.equal(r.components.models.status, 'warn');
    assert.equal(r.tier, 'reduced');
    assert.deepEqual(r.components.models.missing, ['gaze', 'yolo']);
    assert.equal(r.ok, true);
  });

  test('minimal tier → models warn but ok stays true', () => {
    const report = { ...fullReport, proctoring: { tier: 'minimal', missing: ['retina'] } };
    const r = _buildSystemCheckResult(greenInputs({ selftest: { ok: true, report } }));
    assert.equal(r.components.models.status, 'warn');
    assert.equal(r.tier, 'minimal');
    assert.equal(r.ok, true);
  });

  test('selftest could not run → models warn (with error) but ok stays true', () => {
    const r = _buildSystemCheckResult(greenInputs({ selftest: { ok: false, error: 'no-report' } }));
    assert.equal(r.components.models.status, 'warn');
    assert.equal(r.components.models.error, 'no-report');
    assert.equal(r.tier, null);
    assert.equal(r.ok, true);
  });

  test('mic denied + speech models absent → both warn, ok stays true', () => {
    const r = _buildSystemCheckResult(greenInputs({ mic: { ok: false, status: 'denied' }, audioOk: false }));
    assert.equal(r.components.mic.status, 'warn');
    assert.equal(r.components.audio.status, 'warn');
    assert.equal(r.ok, true);
  });

  test('camera status unknown (non-macOS) still counts as green', () => {
    const r = _buildSystemCheckResult(greenInputs({ camera: { ok: true, status: 'unknown' } }));
    assert.equal(r.components.camera.status, 'green');
    assert.equal(r.ok, true);
  });
});

describe('System Check — telemetry summary is metadata only', () => {
  test('summary carries only OS/arch/version/statuses/tier — no media or identity', () => {
    const r = _buildSystemCheckResult(greenInputs());
    const keys = Object.keys(r.summary).sort();
    assert.deepEqual(keys, ['arch', 'components', 'electron', 'ok', 'os', 'tier']);
    // components must be plain status strings, never the detail/missing/paths.
    for (const v of Object.values(r.summary.components)) {
      assert.equal(typeof v, 'string');
      assert.ok(['green', 'red', 'warn', 'skip'].includes(v));
    }
    // No model file paths, roll numbers, frames, or audio anywhere in the
    // serialized summary — the privacy boundary the feature sells on.
    const blob = JSON.stringify(r.summary);
    assert.doesNotMatch(blob, /\/(Users|home|weights)\//);
    assert.doesNotMatch(blob, /roll|jwt|token|frame|\.onnx/i);
  });
});
