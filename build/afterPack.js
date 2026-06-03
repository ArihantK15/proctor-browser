// electron-builder afterPack hook — sign the bundled Python runtime only.
//
// WHY ONLY PYTHON HERE (and NOT the whole .app):
// electron-builder's pack pipeline runs, in order:
//     afterPack (this hook)  →  flip Electron fuses  →  sign the .app
// The fuse flip MUTATES the Electron Framework binary. So the .app MUST be
// sealed AFTER the fuse flip, never before. v2.3.9 sealed the whole bundle
// here in afterPack with `codesign --deep`; the later fuse flip then changed
// the framework bytes, leaving the outer seal pointing at a stale framework
// hash → the kernel SIGKILL'd every student Mac with "Code Signature Invalid
// / Invalid Page" at the first fuse read, before any JS ran.
//
// THE FIX: let electron-builder do the .app signing in its own (post-fuse)
// sign step. We trigger that on the no-cert path with `-c.mac.identity=-`
// (ad-hoc) in build.yml — electron-builder then ad-hoc-signs the entire
// bundle inside-out, AFTER the fuses, sealing the mutated framework
// correctly. An ad-hoc signature satisfies the Apple-Silicon "must be
// signed" rule (the OS shows the bypassable "unidentified developer" prompt
// instead of "damaged"). NOT a substitute for Developer ID + notarization.
//
// This hook's ONLY remaining job is the one thing electron-builder's signer
// does NOT reach: the relocatable Python interpreters under
// Contents/Resources/python-runtime/ (see signBundledPython below).

const { execFileSync } = require('child_process');
const path = require('path');
const fs = require('fs');

// Ad-hoc sign the bundled relocatable Python interpreters under
// Contents/Resources/python-runtime/. On Apple Silicon an *executed*
// Mach-O must carry a valid signature, and `codesign --deep` on the .app
// does NOT reach binaries sitting in Resources. python-build-standalone
// ships its own ad-hoc signatures, but we re-sign the interpreter
// executables + libpython defensively (--force is a no-op-safe replace)
// so the venv symlink target always launches. Must run BEFORE the app is
// signed (inside-out). Best-effort: a failure here must not abort the
// build — the no-cert path is a convenience tier, not notarized.
function signBundledPython(appPath) {
  const runtimeRoot = path.join(appPath, 'Contents', 'Resources', 'python-runtime');
  if (!fs.existsSync(runtimeRoot)) {
    console.log('[afterPack] no python-runtime/ to sign (dev/empty bundle).');
    return;
  }
  // Recursively collect every Mach-O that needs an ad-hoc signature under a
  // directory: native extension modules (*.so) and shared libs (*.dylib).
  // The baked wheels (numpy, opencv, onnxruntime, insightface) ship dozens
  // of these in site-packages, and on Apple Silicon an *executed* Mach-O
  // must carry a valid signature — `codesign --deep` on the .app does NOT
  // reach Resources, so we sign them here, BEFORE the .app is sealed.
  function collectMachO(dir, out) {
    let entries;
    try { entries = fs.readdirSync(dir, { withFileTypes: true }); }
    catch { return; }
    for (const e of entries) {
      const p = path.join(dir, e.name);
      try {
        if (e.isSymbolicLink()) continue;          // sign real files, not links
        if (e.isDirectory()) { collectMachO(p, out); continue; }
        if (/\.(so|dylib)$/.test(e.name)) out.push(p);
      } catch { /* unreadable entry — skip */ }
    }
  }

  const targets = [];
  for (const arch of fs.readdirSync(runtimeRoot)) {
    const binDir = path.join(runtimeRoot, arch, 'bin');
    const libDir = path.join(runtimeRoot, arch, 'lib');
    try {
      for (const f of fs.readdirSync(binDir)) {
        const p = path.join(binDir, f);
        // Sign the real binaries, not the symlinks that point at them.
        if (!fs.lstatSync(p).isSymbolicLink()) targets.push(p);
      }
    } catch { /* no bin dir — skip */ }
    try {
      for (const f of fs.readdirSync(libDir)) {
        if (/^libpython.*\.dylib$/.test(f)) targets.push(path.join(libDir, f));
      }
    } catch { /* no lib dir — skip */ }
    // Baked wheels live in lib/python3.x/site-packages — sign all their
    // native .so/.dylib (recursively). This is what makes the no-venv
    // baked-bundle path load on Apple Silicon.
    try {
      for (const lp of fs.readdirSync(libDir)) {
        if (/^python3\.\d+$/.test(lp)) {
          collectMachO(path.join(libDir, lp, 'site-packages'), targets);
        }
      }
    } catch { /* no site-packages — empty/dev bundle, skip */ }
  }
  for (const t of targets) {
    try {
      execFileSync('codesign', ['--force', '--sign', '-', '--timestamp=none', t],
        { stdio: 'ignore' });
    } catch (e) {
      console.warn(`[afterPack] could not sign ${path.basename(t)}: ${e.message}`);
    }
  }
  console.log(`[afterPack] ad-hoc signed ${targets.length} bundled-python binary(ies).`);
}

exports.default = async function afterPack(context) {
  // macOS only — codesign doesn't exist on the win/linux runners.
  if (context.electronPlatformName !== 'darwin') return;

  // If a real Developer ID cert is configured, electron-builder performs
  // proper signing itself — don't stomp on it with an ad-hoc signature.
  // In the no-cert CI branch CSC_LINK is unset and CSC_LINK_SECRET is the
  // empty string, so both are falsy here and we proceed.
  if (process.env.CSC_LINK || process.env.CSC_LINK_SECRET) {
    console.log('[afterPack] Developer ID cert present — skipping ad-hoc signing.');
    return;
  }

  const appName = context.packager.appInfo.productFilename; // "Procta"
  // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
  // Not user input: both `context.appOutDir` and `appName`
  // (appInfo.productFilename) are build-time values supplied by
  // electron-builder, never from a request or untrusted source. There is no
  // traversal surface here — this runs only on the CI build host.
  const appPath = path.join(context.appOutDir, `${appName}.app`);

  console.log(`[afterPack] No cert — signing bundled Python under ${appPath}`);
  // Sign ONLY the bundled Python interpreters. The .app itself is sealed by
  // electron-builder AFTER the fuse flip (no-cert path uses identity=-).
  // Do NOT `codesign` the outer .app here — doing so pre-fuse is exactly the
  // v2.3.9 bug that produced "Code Signature Invalid / Invalid Page".
  signBundledPython(appPath);
  console.log('[afterPack] bundled-Python signing complete; .app sealed later by electron-builder.');
};
