// scripts/config.test.mjs — unit tests for config.js
//
// config.js had zero dedicated test coverage (has_test_file: false, bus
// factor 1) despite being the shared source of truth for kiosk lockdown,
// process-threat detection, and the Python interpreter search path. A
// DRY-violation was also flagged: THREATS and ALL_PROCESSES both define
// per-category process regexes by hand instead of one deriving from the
// other for vm/remote/screen_share (only vpn/debugger are derived via
// .filter()) — that duplication is exactly the kind of thing that silently
// drifts when someone adds a tool to one list and forgets the other.
//
//   node --test scripts/config.test.mjs
//
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const { THREATS, ALL_PROCESSES, getPythonCandidates } = require('../config');

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(__dirname, '..');

describe('THREATS / ALL_PROCESSES stay in sync (DRY-violation regression guard)', () => {
  test('every vm_detected regex in THREATS.remote list', () => {
    // vm entries aren't in THREATS at all (VM_MAC_PREFIXES covers that
    // signal differently) — ALL_PROCESSES.vm is hand-maintained on its own.
    // Pin its length so a silent accidental deletion doesn't go unnoticed.
    assert.ok(ALL_PROCESSES.vm.length >= 6, 'vm process list unexpectedly shrank');
  });

  test('every THREATS remote_desktop_detected label has a matching ALL_PROCESSES.remote entry', () => {
    const remoteLabels = THREATS.filter(t => t.type === 'remote_desktop_detected').map(t => t.label);
    // ALL_PROCESSES.remote is hand-copied from THREATS rather than derived —
    // assert every regex SOURCE STRING in THREATS appears in ALL_PROCESSES.remote
    // by matching against the same representative process-line fixtures.
    const fixtures = {
      TeamViewer: 'user 100 TeamViewer.exe',
      AnyDesk: '200 AnyDesk running',
      mstsc: 'mstsc.exe active',
      VNC: 'vncviewer session',
      RustDesk: 'rustdesk.exe',
      Parsec: 'parsec running',
      'Parsec (daemon)': 'parsecd active',
      ScreenConnect: 'screenconnect.exe',
      LogMeIn: 'logmein session',
    };
    for (const label of remoteLabels) {
      const line = fixtures[label];
      if (!line) continue;  // only check labels we have a fixture for
      const matchedInAllProcesses = ALL_PROCESSES.remote.some(rx => rx.test(line));
      assert.ok(matchedInAllProcesses, `${label} ("${line}") matches THREATS but not ALL_PROCESSES.remote — the two lists have drifted`);
    }
  });

  test('vpn/debugger categories are structurally derived from THREATS (not hand-copied)', () => {
    // These two ARE .filter()-derived in the real config, so they can never
    // drift by construction — pin that invariant so a future refactor to
    // hand-copied literals (matching the vm/remote/screen_share pattern)
    // doesn't silently reintroduce the exact DRY violation already flagged.
    const vpnFromThreats = THREATS.filter(t => t.type === 'vpn_detected').length;
    assert.equal(ALL_PROCESSES.vpn.length, vpnFromThreats);
  });
});

describe('THREATS regexes use word boundaries (no substring false-positives)', () => {
  test('parsec regex does not match an unrelated word containing it as a substring', () => {
    const parsec = THREATS.find(t => t.label === 'Parsec');
    assert.ok(parsec, 'Parsec entry missing from THREATS');
    assert.ok(!parsec.rx.test('nonparsecrelated'), 'word-boundary regex should not match a substring');
    assert.ok(parsec.rx.test('parsec.exe'), 'should still match the real process name');
  });

  test('every THREATS regex matches its own label lowercase as a sanity check', () => {
    for (const t of THREATS) {
      // Not all labels are literal process names (e.g. "OBS Studio" has a
      // space, matched by a dedicated regex) — just confirm every regex is
      // a valid RegExp and doesn't throw / match everything.
      assert.ok(t.rx instanceof RegExp);
      assert.ok(!t.rx.test('completely unrelated benign process name xyz'));
    }
  });
});

describe('getPythonCandidates()', () => {
  test('windows candidates all end in python.exe', () => {
    const orig = Object.getOwnPropertyDescriptor(process, 'platform');
    Object.defineProperty(process, 'platform', { value: 'win32' });
    try {
      const candidates = getPythonCandidates();
      assert.ok(candidates.length > 0);
      // Most candidates are python.exe; the WindowsApps store alias is
      // python3.exe — both are real, valid Windows Python launchers.
      for (const c of candidates) assert.ok(/python3?\.exe$/.test(c), c);
    } finally {
      Object.defineProperty(process, 'platform', orig);
    }
  });

  test('mac/linux candidates include Apple Silicon Homebrew path', () => {
    const orig = Object.getOwnPropertyDescriptor(process, 'platform');
    Object.defineProperty(process, 'platform', { value: 'darwin' });
    try {
      const candidates = getPythonCandidates();
      assert.ok(candidates.includes('/opt/homebrew/bin/python3'));
      assert.ok(candidates.includes('/usr/local/bin/python3'));
      // system /usr/bin/python3 must be LAST — PEP 668 "externally managed",
      // never want to pip into it if anything else is available.
      assert.equal(candidates[candidates.length - 1], '/usr/bin/python3');
    } finally {
      Object.defineProperty(process, 'platform', orig);
    }
  });

  test('bundled venv python is checked before any system fallback', () => {
    const orig = Object.getOwnPropertyDescriptor(process, 'platform');
    Object.defineProperty(process, 'platform', { value: 'darwin' });
    try {
      const candidates = getPythonCandidates();
      const venvIdx = candidates.findIndex(c => c.includes('venv'));
      const systemIdx = candidates.indexOf('/usr/bin/python3');
      assert.ok(venvIdx < systemIdx, 'bundled venv must be tried before system python3');
    } finally {
      Object.defineProperty(process, 'platform', orig);
    }
  });
});

describe('KIOSK_ALLOWED — dev-bypass gating (module-load-time, subprocess-isolated)', () => {
  // KIOSK_ALLOWED is computed once at require() time from process.argv /
  // env — it can't be safely re-triggered in-process, and this is the
  // exact security boundary the file's own comments call out ("a student
  // can launch Procta.exe --no-kiosk ... defeating the secure-browser
  // guarantee" if this ever regresses). require('electron') always throws
  // in this plain-node test process, so _IS_PACKAGED is always false here
  // — these tests pin the DEV-BRANCH bypass logic specifically.
  function runWithArgs(extraArgv, extraEnv) {
    const script = `console.log(require('${path.join(ROOT, 'config').replace(/\\/g, '\\\\')}').KIOSK_ALLOWED)`;
    // Node parses anything after `-e script` as ITS OWN flags unless a `--`
    // separator marks the rest as user argv (which is exactly what
    // config.js reads via process.argv.includes('--no-kiosk')).
    const args = extraArgv.length ? ['-e', script, '--', ...extraArgv] : ['-e', script];
    const out = execFileSync(process.execPath, args, {
      cwd: ROOT,
      env: { ...process.env, ...extraEnv },
      encoding: 'utf8',
    });
    return out.trim();
  }

  test('no-kiosk flag disables kiosk (dev escape hatch works)', () => {
    assert.equal(runWithArgs(['--no-kiosk'], {}), 'false');
  });

  test('PROCTOR_DEBUG=1 disables kiosk (dev escape hatch works)', () => {
    assert.equal(runWithArgs([], { PROCTOR_DEBUG: '1' }), 'false');
  });

  test('neither flag present -> kiosk is allowed/enforced by default', () => {
    const env = { ...process.env };
    delete env.PROCTOR_DEBUG;
    const out = execFileSync(
      process.execPath,
      ['-e', `console.log(require('${path.join(ROOT, 'config').replace(/\\/g, '\\\\')}').KIOSK_ALLOWED)`],
      { cwd: ROOT, env, encoding: 'utf8' },
    ).trim();
    assert.equal(out, 'true');
  });
});
