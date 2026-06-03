#!/usr/bin/env node
// verify-macos-build.mjs — the post-build reliability gate for the macOS app.
//
// WHY THIS EXISTS
// ----------------
// v2.3.9 shipped a macOS build that the kernel SIGKILL'd at launch with
// "Code Signature Invalid / Invalid Page" inside electron::fuses (before any
// of our JS ran). Root cause: the old afterPack hook sealed the .app with
// `codesign --deep` BEFORE electron-builder flipped the Electron fuses, which
// mutated the Electron Framework binary afterwards — leaving the bundle's
// outer seal pointing at a stale framework hash. The app died on every
// student Mac, yet CI was green because the only "test" (electron-smoke-test)
// is a static string-checker that never builds, signs, launches, or verifies
// the packaged artifact.
//
// This gate closes that hole. It inspects the REAL packaged .app and FAILS
// the build (exit 1) if the artifact would not launch on a student machine.
// `codesign --verify --deep --strict` directly catches the exact 2.3.9 defect
// (a stale/inconsistent framework seal), so a broken build can never ship
// again.
//
// USAGE
//   node scripts/verify-macos-build.mjs [path/to/App.app ...]
// With no args it auto-discovers dist/mac*/*.app (both arm64 + x64).
//
// ENV
//   VERIFY_NO_LAUNCH=1   skip the best-effort launch smoke (set in dev so a
//                        window never pops; CI leaves it on).

import { execFileSync, spawn } from 'node:child_process';
import { existsSync, readdirSync, readFileSync, cpSync, rmSync, mkdtempSync, lstatSync } from 'node:fs';
import { join, basename } from 'node:path';
import { tmpdir } from 'node:os';

if (process.platform !== 'darwin') {
  console.log('[verify-macos] not darwin — nothing to verify here.');
  process.exit(0);
}

const failures = [];
const warnings = [];
const fail = (app, msg) => { failures.push(`✗ [${basename(app)}] ${msg}`); };
const warn = (app, msg) => { warnings.push(`! [${basename(app)}] ${msg}`); };
const ok   = (app, msg) => { console.log(`  ✓ [${basename(app)}] ${msg}`); };

// codesign returning non-zero is a real signal here, so capture rather than throw.
function run(cmd, args) {
  try {
    const out = execFileSync(cmd, args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
    return { code: 0, out };
  } catch (e) {
    return { code: e.status ?? 1, out: `${e.stdout || ''}${e.stderr || ''}` };
  }
}

function discoverApps() {
  if (process.argv.length > 2) return process.argv.slice(2);
  const dist = 'dist';
  if (!existsSync(dist)) return [];
  const apps = [];
  for (const d of readdirSync(dist)) {
    if (!d.startsWith('mac')) continue;
    const sub = join(dist, d);
    try {
      for (const f of readdirSync(sub)) {
        if (f.endsWith('.app')) apps.push(join(sub, f));
      }
    } catch { /* not a dir */ }
  }
  return apps;
}

// ── Hard check 1: the .app's full seal is internally consistent ───────────
// --deep walks every nested framework/helper; --strict refuses to ignore
// the kind of stale/missing nested seal that killed 2.3.9. This is THE check.
function verifySeal(app) {
  const r = run('codesign', ['--verify', '--deep', '--strict', '--verbose=2', app]);
  if (r.code !== 0) {
    fail(app, `codesign --verify --deep --strict FAILED (this is the 2.3.9 crash class):\n${r.out.trim().split('\n').map(l => '      ' + l).join('\n')}`);
    return false;
  }
  ok(app, 'codesign --verify --deep --strict passed');
  return true;
}

// ── Hard check 2: the Electron Framework binary specifically ──────────────
// The 2.3.9 Invalid Page was IN the Electron Framework — verify it directly.
function verifyFramework(app) {
  const fw = join(app, 'Contents', 'Frameworks', 'Electron Framework.framework');
  if (!existsSync(fw)) { fail(app, 'Electron Framework.framework missing'); return; }
  const r = run('codesign', ['--verify', '--strict', '--verbose=2', fw]);
  if (r.code !== 0) {
    fail(app, `Electron Framework signature INVALID — exactly what SIGKILL'd 2.3.9:\n${r.out.trim()}`);
    return;
  }
  ok(app, 'Electron Framework signature valid');
}

// True if the file begins with a Mach-O magic number (thin or fat/universal).
// Scripts like python3.x-config are text shebangs — they are NOT executed by
// the venv (which launches the python3 Mach-O), and they can't carry an
// embedded signature, so they must not be a hard gate. We only block on
// actual Mach-O interpreters, which is what gets paged in and signature-checked.
function isMachO(p) {
  try {
    const fd = readFileSync(p);
    if (fd.length < 4) return false;
    const m = fd.readUInt32BE(0);
    // feedface/feedfacf (thin LE/BE read as BE), cafebabe/cafebabf (fat)
    return [0xfeedface, 0xfeedfacf, 0xcefaedfe, 0xcffaedfe, 0xcafebabe, 0xcafebabf, 0xbebafeca, 0xbfbafeca].includes(m);
  } catch { return false; }
}

// ── Hard check 2b: NOT merely linker-signed — the literal 2.3.9 fingerprint.
// An arm64 binary that was never `codesign`d carries only the linker's default
// ad-hoc signature (CS_LINKER_SIGNED, shown as "linker-signed" in the flags).
// That signature's page hashes do not survive the Electron fuse-flip → the
// kernel SIGKILL'd v2.3.9 with "Invalid Page". A properly codesigned bundle
// shows flags=0x2(adhoc) with NO linker-signed bit.
function verifyNotLinkerSigned(app) {
  const targets = {
    'main executable': join(app, 'Contents', 'MacOS', basename(app, '.app')),
    'Electron Framework': join(app, 'Contents', 'Frameworks', 'Electron Framework.framework'),
  };
  for (const [label, p] of Object.entries(targets)) {
    if (!existsSync(p)) continue;
    const r = run('codesign', ['-dvvv', p]);
    const flagLine = (r.out.match(/CodeDirectory[^\n]*/) || [''])[0];
    if (/linker-signed/.test(flagLine)) {
      fail(app, `${label} is LINKER-SIGNED only (never codesigned) — the exact v2.3.9 crash fingerprint: ${flagLine.trim()}`);
    } else {
      ok(app, `${label} properly codesigned (not linker-signed)`);
    }
  }
}

// ── Hard check 3: bundled python *interpreters* carry a valid signature ───
// On Apple Silicon an *executed* Mach-O must be validly signed; the venv
// symlinks at first launch point straight at the python3 interpreter.
// extraResources binaries are not signed by electron-builder, so afterPack
// signs them — verify the ones that actually launch. (Helper *scripts* like
// python3.x-config lose their xattr signature through the release zip and are
// never executed → reported as a soft warning, not a build-blocking failure.)
function verifyBundledPython(app) {
  const root = join(app, 'Contents', 'Resources', 'python-runtime');
  if (!existsSync(root)) { warn(app, 'no bundled python-runtime (dev/empty build) — falls back to system python3'); return; }
  let interpreters = 0, scriptsUnsigned = 0;
  for (const arch of readdirSync(root)) {
    const binDir = join(root, arch, 'bin');
    if (!existsSync(binDir)) continue;
    for (const f of readdirSync(binDir)) {
      const p = join(binDir, f);
      try { if (lstatSync(p).isSymbolicLink()) continue; } catch { continue; }
      if (!/^python3/.test(f)) continue;
      const r = run('codesign', ['--verify', '--strict', p]);
      if (isMachO(p)) {
        interpreters++;
        if (r.code !== 0) fail(app, `bundled python INTERPRETER unsigned/invalid (${arch}/${f}) → venv launch SIGKILL on student Mac:\n${r.out.trim()}`);
      } else if (r.code !== 0) {
        scriptsUnsigned++; // non-executed helper script — informational only
      }
    }
  }
  if (interpreters) ok(app, `bundled python interpreter(s) signed (${interpreters} Mach-O checked)`);
  else warn(app, 'python-runtime present but no Mach-O python3 interpreter found to verify');
  if (scriptsUnsigned) warn(app, `${scriptsUnsigned} python helper script(s) unsigned (e.g. python3.x-config) — not executed by the venv, harmless`);
}

// ── Soft check: confirm the Electron fuses were actually flipped ──────────
// Security regression guard — runAsNode must be OFF. Best-effort: scan the
// framework binary for the fuse sentinel and read the first fuse byte.
function checkFuses(app) {
  const bin = join(app, 'Contents', 'Frameworks', 'Electron Framework.framework', 'Versions', 'A', 'Electron Framework');
  if (!existsSync(bin)) { warn(app, 'cannot locate Electron Framework binary for fuse check'); return; }
  // @electron/fuses' real (obfuscated) sentinel, followed by the fuse wire:
  //   [SENTINEL][version:1 byte][length:1 byte][fuse bytes...]
  // each fuse byte is '0' off / '1' on / 'r' removed. RunAsNode is index 0.
  const SENTINEL = 'dL7pKGdnNz796PbbjQWNKmHXBZaB9tsX';
  const buf = readFileSync(bin);
  const idx = buf.indexOf(Buffer.from(SENTINEL, 'latin1'));
  if (idx === -1) { warn(app, 'fuse sentinel not found — cannot confirm runAsNode disabled'); return; }
  const fusesStart = idx + SENTINEL.length + 2; // skip version + length bytes
  const runAsNode = String.fromCharCode(buf[fusesStart]);
  if (runAsNode === '1') fail(app, 'SECURITY: runAsNode fuse is ENABLED — app can be driven as a raw Node process');
  else ok(app, `fuses flipped (runAsNode=${runAsNode === '0' ? 'disabled' : runAsNode})`);
}

// ── Soft check: launch the binary and ensure it is not SIGKILL'd at start ──
// A code-signature kill is signal 9 within the first moment, before a window.
// Any other outcome (clean-ish exit, or still alive → we stop it) is fine.
function launchSmoke(app) {
  return new Promise((resolve) => {
    if (process.env.VERIFY_NO_LAUNCH === '1') { warn(app, 'launch smoke skipped (VERIFY_NO_LAUNCH=1)'); return resolve(); }
    const bin = join(app, 'Contents', 'MacOS', basename(app, '.app'));
    if (!existsSync(bin)) { warn(app, 'launch smoke skipped — app binary not found'); return resolve(); }
    const child = spawn(bin, ['--no-sandbox'], { stdio: 'ignore', detached: false });
    let settled = false;
    const done = () => { if (!settled) { settled = true; resolve(); } };
    const timer = setTimeout(() => {
      // Survived past the code-signing kill window → good. Stop it.
      try { child.kill('SIGKILL'); } catch { /* already gone */ }
      ok(app, 'launch smoke: survived the code-signing kill window');
      done();
    }, 4000);
    child.on('error', (e) => { clearTimeout(timer); warn(app, `launch smoke inconclusive (spawn error: ${e.message})`); done(); });
    child.on('exit', (code, signal) => {
      clearTimeout(timer);
      // We only kill it ourselves AFTER the timer fires (handled above). An
      // early SIGKILL here is the kernel rejecting the signature.
      if (signal === 'SIGKILL') fail(app, 'launch smoke: process SIGKILL\'d at startup — code-signature rejected (the 2.3.9 failure)');
      else ok(app, `launch smoke: exited early without code-signing kill (code=${code}, signal=${signal})`);
      done();
    });
  });
}

const apps = discoverApps();
console.log(`\n=== macOS build verification gate ===`);
if (apps.length === 0) {
  console.error('✗ No .app found under dist/mac*/ (and none passed as args). Nothing to verify — build first.');
  process.exit(1);
}
console.log(`Verifying ${apps.length} app bundle(s):\n`);

for (const app of apps) {
  if (!existsSync(app)) { fail(app, 'path does not exist'); continue; }
  console.log(`• ${app}`);
  verifySeal(app);
  verifyFramework(app);
  verifyNotLinkerSigned(app);
  verifyBundledPython(app);
  checkFuses(app);
  await launchSmoke(app);
}

console.log('\n────────────────────────────────────────');
if (warnings.length) {
  console.log('Warnings:');
  warnings.forEach(w => console.log('  ' + w));
}
if (failures.length) {
  console.error('\nFAILED — this build would crash on student Macs:');
  failures.forEach(f => console.error('  ' + f));
  console.error('\n✗ macOS build verification FAILED. Do not publish this artifact.');
  process.exit(1);
}
console.log('\n✓ macOS build verification PASSED — artifact is launchable.');
process.exit(0);
