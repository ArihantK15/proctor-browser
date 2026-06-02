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
  // --deep: sign nested frameworks/helpers too. --force: replace any
  // partial signature. --sign -: the ad-hoc identity.
  execFileSync('codesign', ['--deep', '--force', '--sign', '-', appPath], {
    stdio: 'inherit',
  });
  console.log('[afterPack] ad-hoc signing complete.');
};
