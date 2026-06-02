// electron-builder afterPack hook — ad-hoc code-signing fallback.
//
// WHY: when no Apple Developer ID certificate is configured (no CSC_LINK
// secret), the build is otherwise completely unsigned. On Apple Silicon
// macOS REFUSES to launch a downloaded unsigned binary and shows the
// blunt, dead-end error: "Procta is damaged and can't be opened."
//
// An *ad-hoc* signature (`codesign --sign -`) satisfies the arm64
// "must be signed" requirement without needing a paid cert. The OS then
// shows the softer, BYPASSABLE "unidentified developer" prompt instead
// (right-click → Open, or `xattr -cr`). This is NOT a substitute for
// notarization — it only improves the unsigned distribution path until
// the Developer ID + notarization secrets are added (see build.yml).
//
// This hook runs after the .app is packed but before the DMG is built,
// so the DMG ships the ad-hoc-signed app.

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

  console.log(`[afterPack] No cert — ad-hoc signing ${appPath}`);
  // Inside-out: sign the bundled Python binaries FIRST, then the app, so
  // the .app's signature seals an already-signed payload.
  signBundledPython(appPath);
  // --deep: sign nested frameworks/helpers too. --force: replace any
  // partial signature. --sign -: the ad-hoc identity.
  execFileSync('codesign', ['--deep', '--force', '--sign', '-', appPath], {
    stdio: 'inherit',
  });
  console.log('[afterPack] ad-hoc signing complete.');
};
