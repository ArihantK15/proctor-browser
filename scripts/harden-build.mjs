/**
 * build hardening — Tier 3
 *
 * Run BEFORE electron-builder packs the asar, or as an `afterPack` hook.
 * Steps:
 *   3.1  Strip .map files from the unpacked app so source maps never
 *        leak into the shipped asar.
 *   3.2  Minify (not obfuscate) shipped JS with a lightweight pass.
 *        javascript-obfuscator is available as an OPT-IN via env var
 *        PROCTA_OBFUSCATE=1 — it is NOT enabled by default because it
 *        is the highest-risk-to-break item (can silently mangle runtime
 *        behaviour or crash the renderer).
 *   3.3  bytenode compile (.jsc) of sensitive modules — also opt-in via
 *        PROCTA_BYTENODE=1.  bytenode-compiled files are platform/arch
 *        specific and break if the Electron version changes.  Flagged
 *        highest-risk-to-break.
 *
 * Usage:
 *   node scripts/harden-build.mjs <path-to-unpacked-app>
 *
 * Example (macOS):
 *   node scripts/harden-build.mjs dist/mac/Procta.app/Contents/Resources/app
 *
 * The afterPack hook (build/afterPack.js) calls this automatically with
 * the unpacked app path.
 */
import { readFileSync, writeFileSync, existsSync } from 'fs';
import { readdir, stat, unlink, writeFile, readFile } from 'fs/promises';
import { join, relative, sep } from 'path';

const ROOT = process.argv[2];
if (!ROOT || !existsSync(ROOT)) {
  console.error('Usage: node scripts/harden-build.mjs <unpacked-app-resources/app>');
  process.exit(1);
}

let mutated = 0;

// ── 3.1  Strip .map files ────────────────────────────────────────
async function stripSourceMaps(dir) {
  let entries;
  try { entries = await readdir(dir, { withFileTypes: true }); } catch { return; }
  for (const e of entries) {
    const p = join(dir, e.name);
    if (e.isDirectory()) { await stripSourceMaps(p); continue; }
    if (e.name.endsWith('.map')) {
      await unlink(p);
      mutated++;
      console.log(`  [harden] removed ${relative(ROOT, p)}`);
    }
  }
}

// ── 3.2  Minify JS (opt-in) ──────────────────────────────────────
// Uses a minimal minifier (strip comments + whitespace) to avoid
// introducing a full toolchain dependency.  javascript-obfuscator is
// NOT used unless PROCTA_OBFUSCATE=1.
function minifyJS(code) {
  return code
    .replace(/\/\/.*$/gm, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*$(?:\r\n?|\n)/gm, '')
    .replace(/[ \t]{2,}/g, ' ')
    .replace(/\n{2,}/g, '\n')
    .replace(/^\s+/gm, '')
    .replace(/\s+$/gm, '')
    .trim();
}

async function minifyDir(dir) {
  let entries;
  try { entries = await readdir(dir, { withFileTypes: true }); } catch { return; }
  for (const e of entries) {
    const p = join(dir, e.name);
    if (e.isDirectory()) { await minifyDir(p); continue; }
    if (!e.name.endsWith('.js')) continue;
    if (e.name.endsWith('.min.js')) continue;
    const orig = await readFile(p, 'utf8');
    const min = minifyJS(orig);
    if (min.length < orig.length) {
      await writeFile(p, min, 'utf8');
      mutated++;
      console.log(`  [harden] minified ${relative(ROOT, p)} (${orig.length}→${min.length} bytes)`);
    }
  }
}

// ── 3.2  javascript-obfuscator (opt-in, HIGH RISK) ───────────────
async function obfuscateDir(dir) {
  let obfuscator;
  try {
    obfuscator = (await import('javascript-obfuscator')).default;
  } catch {
    console.warn('  [harden] javascript-obfuscator not installed — skipping obfuscation.');
    console.warn('  Install: npm install --save-dev javascript-obfuscator');
    return;
  }
  const OBFUSCATE_OPTIONS = {
    compact: true,
    controlFlowFlattening: false,
    deadCodeInjection: false,
    debugProtection: false,
    disableConsoleOutput: false,
    identifierNamesGenerator: 'hexadecimal',
    rotateStringArray: true,
    selfDefending: false,
    stringArray: true,
    stringArrayEncoding: ['base64'],
    stringArrayThreshold: 0.5,
    target: 'node',
    transformObjectKeys: false,
    unicodeEscapeSequence: false,
  };
  let entries;
  try { entries = await readdir(dir, { withFileTypes: true }); } catch { return; }
  for (const e of entries) {
    const p = join(dir, e.name);
    if (e.isDirectory()) { await obfuscateDir(p); continue; }
    if (!e.name.endsWith('.js')) continue;
    if (e.name.endsWith('.min.js')) continue;
    const orig = await readFile(p, 'utf8');
    try {
      const obf = obfuscator.obfuscate(orig, OBFUSCATE_OPTIONS);
      await writeFile(p, obf.getObfuscatedCode(), 'utf8');
      mutated++;
      console.log(`  [harden] obfuscated ${relative(ROOT, p)}`);
    } catch (err) {
      console.warn(`  [harden] obfuscation FAILED for ${relative(ROOT, p)}: ${err.message} — SKIPPED`);
    }
  }
}

(async () => {
  console.log(`[harden] hardening ${ROOT}`);

  // 3.1 always runs
  await stripSourceMaps(ROOT);

  // 3.2 safe minification (always on)
  await minifyDir(ROOT);

  // 3.2 obfuscation (opt-in, high risk)
  if (process.env.PROCTA_OBFUSCATE === '1') {
    console.log('[harden] PROCTA_OBFUSCATE=1 — running javascript-obfuscator (HIGH RISK)');
    await obfuscateDir(ROOT);
  } else {
    console.log('[harden] obfuscation SKIPPED (set PROCTA_OBFUSCATE=1 to enable — HIGH RISK)');
  }

  // 3.3 bytenode (opt-in, high risk)
  if (process.env.PROCTA_BYTENODE === '1') {
    console.log('[harden] PROCTA_BYTENODE=1 — bytenode compile (HIGH RISK, ARCH-SPECIFIC)');
    console.warn('  bytenode not implemented in this script — use @tybys/bytenode CLI:');
    console.warn('    npm install -g @tybys/bytenode');
    console.warn('    bytenode --compile lib/attestation.js');
    console.warn('    bytenode --compile lib/kiosk-manager.js');
    console.warn('  Then update the require() paths to load .jsc files.');
    console.warn('  WARNING: .jsc files are platform/arch-specific and break');
    console.warn('  on Electron version bumps. Test every build.');
  } else {
    console.log('[harden] bytenode SKIPPED (set PROCTA_BYTENODE=1 to enable — HIGH RISK)');
  }

  if (mutated === 0) {
    console.log('[harden] no files needed hardening — clean build.');
  } else {
    console.log(`[harden] ${mutated} file(s) hardened.`);
  }
})().catch(err => {
  console.error('[harden] FATAL:', err);
  process.exit(1);
});
