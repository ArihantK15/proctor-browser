// scripts/utils.test.mjs — unit tests for lib/utils.js
//
// Covers the pure, exam-critical helpers that had zero coverage:
//   • authHeaders        — bearer-token request header construction
//   • extractInviteToken — procta:// deep-link parsing + validation
//   • scanProcessOutput  — cheat/remote-desktop/screen-share detection
//
// The detection maps and invite regex are imported from the REAL config so
// these double as regression guards on the actual patterns (a TeamViewer /
// OBS / VirtualBox process line must still be flagged; a too-short or
// malformed invite token must still be rejected).
//
//   node --test scripts/utils.test.mjs
//
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { authHeaders, extractInviteToken, scanProcessOutput, fetchWithTimeout } = require('../lib/utils');
const { ALL_PROCESSES, SCAN_TYPE_MAP, INVITE_REGEX } = require('../config');

const _realFetch = global.fetch;

describe('authHeaders', () => {
  test('with a token → Content-Type + Bearer Authorization', () => {
    const h = authHeaders('tok123');
    assert.equal(h['Content-Type'], 'application/json');
    assert.equal(h['Authorization'], 'Bearer tok123');
  });

  test('without a token → Content-Type only, no Authorization', () => {
    const h = authHeaders('');
    assert.equal(h['Content-Type'], 'application/json');
    assert.equal('Authorization' in h, false);
  });

  test('null token → no Authorization header', () => {
    assert.equal('Authorization' in authHeaders(null), false);
  });
});

describe('extractInviteToken — procta:// deep links', () => {
  const VALID = 'abc123XYZ_-0';   // 12 chars, in [A-Za-z0-9_-]

  test('procta://invite/<token> path form returns the token', () => {
    assert.equal(extractInviteToken(`procta://invite/${VALID}`, INVITE_REGEX), VALID);
  });

  test('procta://...?token=<token> query form returns the token', () => {
    assert.equal(extractInviteToken(`procta://launch?token=${VALID}`, INVITE_REGEX), VALID);
  });

  test('case-insensitive scheme is accepted', () => {
    assert.equal(extractInviteToken(`PROCTA://invite/${VALID}`, INVITE_REGEX), VALID);
  });

  test('token shorter than 8 chars is rejected', () => {
    assert.equal(extractInviteToken('procta://invite/short', INVITE_REGEX), null);
  });

  test('token with illegal characters is rejected', () => {
    assert.equal(extractInviteToken('procta://invite/bad token!!', INVITE_REGEX), null);
  });

  test('null / empty input returns null', () => {
    assert.equal(extractInviteToken(null, INVITE_REGEX), null);
    assert.equal(extractInviteToken('', INVITE_REGEX), null);
  });

  test('a plain (non-procta) string still matches via the legacy regex', () => {
    // INVITE_REGEX only matches the full procta:// form, so an arbitrary
    // string returns null — proving we are not over-accepting.
    assert.equal(extractInviteToken('https://evil.example/invite/' + VALID, INVITE_REGEX), null);
  });
});

describe('scanProcessOutput — cheat / remote-desktop / screen-share detection', () => {
  test('TeamViewer process line is flagged as remote_desktop_detected (high)', () => {
    const flags = scanProcessOutput('user 4012 TeamViewer.exe', ALL_PROCESSES, SCAN_TYPE_MAP);
    assert.equal(flags.length >= 1, true);
    const f = flags.find(x => x.type === 'remote_desktop_detected');
    assert.ok(f, 'expected a remote_desktop_detected flag');
    assert.equal(f.severity, 'high');
  });

  test('VirtualBox service is flagged as vm_detected', () => {
    const flags = scanProcessOutput('501 vboxservice running', ALL_PROCESSES, SCAN_TYPE_MAP);
    assert.ok(flags.some(f => f.type === 'vm_detected'));
  });

  test('OBS screen-share is flagged with medium severity', () => {
    const flags = scanProcessOutput('1234 obs64.exe', ALL_PROCESSES, SCAN_TYPE_MAP);
    const f = flags.find(x => x.type === 'screen_share_detected');
    assert.ok(f, 'expected screen_share_detected');
    assert.equal(f.severity, 'medium');   // screen_share is medium, not high
  });

  test('a clean process listing produces no flags', () => {
    const flags = scanProcessOutput(
      'root 1 launchd\nuser 200 Finder\nuser 300 Safari',
      ALL_PROCESSES, SCAN_TYPE_MAP);
    assert.deepEqual(flags, []);
  });

  test('empty / null output produces no flags (no crash)', () => {
    assert.deepEqual(scanProcessOutput('', ALL_PROCESSES, SCAN_TYPE_MAP), []);
    assert.deepEqual(scanProcessOutput(null, ALL_PROCESSES, SCAN_TYPE_MAP), []);
  });

  test('each pattern reports at most once even if it appears on many lines', () => {
    const flags = scanProcessOutput(
      'a TeamViewer.exe\nb TeamViewer.exe\nc TeamViewer.exe',
      ALL_PROCESSES, SCAN_TYPE_MAP);
    assert.equal(flags.filter(f => f.type === 'remote_desktop_detected').length, 1);
  });

  test('Apple\'s own CoreParsec system framework (SIP-protected path) is NOT flagged', () => {
    // Real-world false positive on stock macOS: /System/Library/PrivateFrameworks/
    // CoreParsec.framework/parsecd is Apple's own on-device visual-intelligence
    // process, unrelated to the third-party Parsec remote-desktop app. SIP
    // guarantees nothing third-party can live under /System/Library, so any
    // match rooted there must be excluded rather than flagged as a threat.
    const flags = scanProcessOutput(
      'arihantkaul 71982 /System/Library/PrivateFrameworks/CoreParsec.framework/parsecd',
      ALL_PROCESSES, SCAN_TYPE_MAP);
    assert.deepEqual(flags, []);
  });

  test('a real third-party Parsec install (outside /System/Library) is still flagged', () => {
    const flags = scanProcessOutput(
      'user 5555 /Applications/Parsec.app/Contents/MacOS/parsecd',
      ALL_PROCESSES, SCAN_TYPE_MAP);
    assert.ok(flags.some(f => f.type === 'remote_desktop_detected'));
  });
});

describe('fetchWithTimeout', () => {
  test('resolves with the response when fetch returns before the timeout', async (t) => {
    t.after(() => { global.fetch = _realFetch; });
    global.fetch = async () => ({ ok: true, status: 200 });
    const r = await fetchWithTimeout('http://x', {}, 1000);
    assert.equal(r.status, 200);
  });

  test('passes an abort signal through and aborts after the timeout', async (t) => {
    t.after(() => { global.fetch = _realFetch; });
    // Simulate a real fetch that never resolves until its signal aborts.
    global.fetch = (_url, opts) => new Promise((_resolve, reject) => {
      assert.ok(opts.signal, 'expected an AbortSignal to be passed');
      opts.signal.addEventListener('abort', () => reject(new Error('aborted')));
    });
    await assert.rejects(fetchWithTimeout('http://x', {}, 30), /abort/i);
  });
});
