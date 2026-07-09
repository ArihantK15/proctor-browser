// scripts/weights-env.test.mjs — regression test for a real bug found
// testing a genuine clean Windows install: proctor.py's model loaders
// (RetinaFace/SCRFD/YOLO/gaze) read the weights location via env vars
// (PROCTA_WEIGHTS_DIR used directly; ELECTRON_RESOURCES_PATH treated as
// weights/'s PARENT, with proctor.py appending "weights" itself) — but no
// JS spawn site (startPython/startCalibration/runSelfTest in
// lib/python-manager.js) ever set either one. It worked before only
// because weights/ used to ship right next to proctor.py under the OLD
// bundled-in-installer scheme; the runtime-assets decoupling moved weights/
// to a per-user cache dir with nothing telling Python where that is —
// System Check on a real machine reported "Minimal proctoring — face
// detection unavailable" despite Python + packages being fully ready.
//
// Locks the CONTRACT _weightsEnvVars() must uphold regardless of what
// bundledWeightsDir() actually resolves to (dev machine, cache hit, or
// fallback): ELECTRON_RESOURCES_PATH + "/weights" must equal
// PROCTA_WEIGHTS_DIR exactly, since proctor.py derives the former from the
// latter internally.
//
//   node --test scripts/weights-env.test.mjs
//
import { test } from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { bundledWeightsDir, _weightsEnvVars } = require('../lib/python-manager');

test('_weightsEnvVars sets PROCTA_WEIGHTS_DIR to the resolved weights dir', () => {
  const env = _weightsEnvVars();
  assert.equal(env.PROCTA_WEIGHTS_DIR, bundledWeightsDir());
});

test('_weightsEnvVars sets ELECTRON_RESOURCES_PATH such that <it>/weights === PROCTA_WEIGHTS_DIR', () => {
  // proctor.py does os.path.join(ELECTRON_RESOURCES_PATH, "weights", fname)
  // for the loaders that check that var — this is the exact join it relies on.
  const env = _weightsEnvVars();
  assert.equal(path.join(env.ELECTRON_RESOURCES_PATH, 'weights'), env.PROCTA_WEIGHTS_DIR);
});

test('_weightsEnvVars returns non-empty strings for both vars', () => {
  const env = _weightsEnvVars();
  assert.ok(env.PROCTA_WEIGHTS_DIR && env.PROCTA_WEIGHTS_DIR.length > 0);
  assert.ok(env.ELECTRON_RESOURCES_PATH && env.ELECTRON_RESOURCES_PATH.length > 0);
});
