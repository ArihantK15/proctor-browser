#!/usr/bin/env node
// verify-windows-build.mjs — post-build reliability gate for the Windows app.
//
// WHY THIS EXISTS
// ----------------
// The macOS build has verify-macos-build.mjs (born from the v2.3.9 SIGKILL
// disaster). Windows had only a file-existence check — a packaged Procta.exe
// that immediately crashes (corrupted asar, missing MSVC/Electron DLL, a fuse
// regression that re-enables runAsNode) would pass CI with zero red flags.
// This is the missing Windows counterpart. It inspects the REAL packaged app
// under dist/win-unpacked and FAILS the build (exit 1) if it would not launch
// or has a security-relevant defect.
//
// Checks (deterministic ones are hard gates; the launch smoke is guarded):
//   1. Procta.exe is a real PE executable (MZ header) — catches truncation.
//   2. resources/app.asar present and non-trivial — the corrupted/empty-asar
//      guard (an empty asar means the renderer/main JS never loads).
//   3. Electron fuses: runAsNode MUST be disabled (same sentinel byte-read the
//      macOS gate uses — the fuse wire is embedded in the main binary on
//      Windows too). A security regression guard.
//   4. latest.yml (auto-update manifest) has the required version/path/sha512
//      keys — a malformed manifest silently breaks auto-update.
//   5. Launch smoke: spawn Procta.exe --no-sandbox; if it crashes (non-zero
//      exit) within the first 4s the bundle is broken. Survival = launchable.
//      Skipped off-Windows and when VERIFY_NO_LAUNCH=1.
//
// USAGE
//   node scripts/verify-windows-build.mjs [path/to/win-unpacked ...]
//   With no args it auto-discovers dist/win-unpacked.
//
import { spawn } from 'node:child_process';
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join, basename } from 'node:path';

const failures = [];
const warnings = [];
const fail = (msg) => failures.push(`✗ ${msg}`);
const warn = (msg) => warnings.push(`! ${msg}`);
const ok   = (msg) => console.log(`  ✓ ${msg}`);

function discoverDirs() {
  if (process.argv.length > 2) return process.argv.slice(2);
  // electron-builder --win emits dist/win-unpacked (x64) and may emit
  // dist/win-ia32-unpacked / win-arm64-unpacked for other arches.
  const dist = 'dist';
  if (!existsSync(dist)) return [];
  return readdirSync(dist)
    .filter((d) => /^win.*unpacked$/.test(d))
    .map((d) => join(dist, d));
}

function appExe(dir) {
  // productName "Procta" → Procta.exe. Fall back to the first top-level .exe.
  const direct = join(dir, 'Procta.exe');
  if (existsSync(direct)) return direct;
  try {
    const exe = readdirSync(dir).find((f) => f.toLowerCase().endsWith('.exe'));
    return exe ? join(dir, exe) : null;
  } catch { return null; }
}

// ── Check 1: real PE executable ───────────────────────────────────────────
function verifyPE(exe) {
  const fd = readFileSync(exe);
  if (fd.length < 2 || fd[0] !== 0x4d || fd[1] !== 0x5a) {   // 'MZ'
    fail(`${basename(exe)} is not a valid PE executable (no MZ header) — truncated/corrupt build`);
    return false;
  }
  ok(`${basename(exe)} is a valid PE executable`);
  return true;
}

// ── Check 2: asar present + sane size ─────────────────────────────────────
function verifyAsar(dir) {
  const asar = join(dir, 'resources', 'app.asar');
  if (!existsSync(asar)) { fail('resources/app.asar missing — the app has no JS to run'); return; }
  const sz = statSync(asar).size;
  if (sz < 50 * 1024) { fail(`app.asar suspiciously small (${sz} bytes) — likely empty/corrupt`); return; }
  ok(`app.asar present (${(sz / 1024 / 1024).toFixed(1)} MB)`);
}

// ── Check 3: Electron fuses — runAsNode disabled ──────────────────────────
// Same approach as verify-macos-build.mjs: the @electron/fuses sentinel,
// followed by [version:1][length:1][fuse bytes...]; runAsNode is index 0,
// '0' off / '1' on / 'r' removed.
function verifyFuses(exe) {
  const SENTINEL = 'dL7pKGdnNz796PbbjQWNKmHXBZaB9tsX';
  const buf = readFileSync(exe);
  const idx = buf.indexOf(Buffer.from(SENTINEL, 'latin1'));
  if (idx === -1) { warn('fuse sentinel not found in Procta.exe — cannot confirm runAsNode disabled'); return; }
  const runAsNode = String.fromCharCode(buf[idx + SENTINEL.length + 2]);
  if (runAsNode === '1') {
    fail('SECURITY: runAsNode fuse is ENABLED — the packaged app can be driven as a raw Node process');
  } else {
    ok(`fuses flipped (runAsNode=${runAsNode === '0' ? 'disabled' : runAsNode})`);
  }
}

// ── Check 4: auto-update manifest is well-formed ──────────────────────────
function verifyManifest() {
  const yml = join('dist', 'latest.yml');
  if (!existsSync(yml)) { fail('dist/latest.yml (auto-update manifest) missing'); return; }
  const txt = readFileSync(yml, 'utf8');
  for (const key of ['version', 'path', 'sha512']) {
    if (!new RegExp(`(^|\\n)${key}\\s*:`).test(txt)) {
      fail(`latest.yml missing required '${key}:' key — auto-update would break`);
      return;
    }
  }
  ok('latest.yml has version/path/sha512');
}

// ── Check 5: launch smoke ─────────────────────────────────────────────────
function launchSmoke(exe) {
  return new Promise((resolve) => {
    if (process.platform !== 'win32') { warn('launch smoke skipped (not running on Windows)'); return resolve(); }
    if (process.env.VERIFY_NO_LAUNCH === '1') { warn('launch smoke skipped (VERIFY_NO_LAUNCH=1)'); return resolve(); }
    const child = spawn(exe, ['--no-sandbox'], { stdio: 'ignore' });
    let settled = false;
    const done = () => { if (!settled) { settled = true; resolve(); } };
    const timer = setTimeout(() => {
      // Survived the early-crash window → the bundle loads. Stop it.
      try { child.kill(); } catch { /* already gone */ }
      ok('launch smoke: survived the early-crash window');
      done();
    }, 4000);
    child.on('error', (e) => { clearTimeout(timer); warn(`launch smoke inconclusive (spawn error: ${e.message})`); done(); });
    child.on('exit', (code) => {
      if (settled) return;   // our own kill after the survival timer
      clearTimeout(timer);
      // A non-zero exit inside the window is a real crash (e.g. 0xC0000135
      // missing-DLL, 0xC0000005 access-violation, corrupt asar).
      if (code && code !== 0) fail(`launch smoke: Procta.exe crashed at startup (exit code ${code}) — broken bundle`);
      else warn(`launch smoke: exited cleanly within 4s (code ${code}) — unusual but not a crash`);
      done();
    });
  });
}

const dirs = discoverDirs();
console.log('\n=== Windows build verification gate ===');
if (dirs.length === 0) {
  console.error('✗ No dist/win*-unpacked found (and none passed as args). Build first.');
  process.exit(1);
}

for (const dir of dirs) {
  console.log(`\n• ${dir}`);
  const exe = appExe(dir);
  if (!exe) { fail(`no .exe found in ${dir}`); continue; }
  if (verifyPE(exe)) verifyFuses(exe);
  verifyAsar(dir);
  await launchSmoke(exe);
}
verifyManifest();

console.log('\n────────────────────────────────────────');
if (warnings.length) { console.log('Warnings:'); warnings.forEach((w) => console.log('  ' + w)); }
if (failures.length) {
  console.error('\nFAILED — this build could ship broken to students:');
  failures.forEach((f) => console.error('  ' + f));
  console.error('\n✗ Windows build verification FAILED. Do not publish this artifact.');
  process.exit(1);
}
console.log('\n✓ Windows build verification PASSED.');
process.exit(0);
