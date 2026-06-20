'use strict';
// Pure decision for the macOS exam-start permission gate (fail CLOSED).
//
// Given the two TCC preflight results (from python-manager's
// ensureCameraAccess / ensureMicAccess), return the first BLOCKING outcome or
// null when the exam may proceed. Camera takes precedence over microphone.
//
// ensure*() return { ok: true } on non-macOS, so this naturally no-ops off
// macOS (Windows/Linux fall through to proctor.py's own camera hard-stop).
// A missing/undefined result (an unexpected preflight throw upstream) is
// treated as non-blocking — the caller fail-opens on a preflight *bug*, never
// on an actual user denial. Kept dependency-free so it unit-tests without
// Electron, same pattern as frame_buffer.py / behavioral_analysis.py.
function permissionBlock(cam, mic) {
  if (cam && cam.ok === false) return { kind: 'Camera', error: 'camera-denied' };
  if (mic && mic.ok === false) return { kind: 'Microphone', error: 'mic-denied' };
  return null;
}

module.exports = { permissionBlock };
