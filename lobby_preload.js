// Lobby preload — loaded only into the unlocked pre-exam window that
// renders the student dashboard (app/static/student.html, served via the
// procta-lobby:// custom protocol registered in main.js since v2.3.14).
// Exposes a tiny bridge the dashboard can use to launch a proctored exam
// window. Nothing here runs inside the kiosk-locked exam window.
const { contextBridge, ipcRenderer } = require('electron');

// NOTE: this preload runs with `sandbox: true` (see kiosk-manager.js). A
// sandboxed preload only gets a PARTIAL `process` polyfill — `process.env`
// may be undefined, in which case a bare `process.env.X` THROWS at load,
// aborting the script before contextBridge runs and leaving the lobby with
// no `window.procta_native` (students can't launch exams). Read it
// defensively so it can never throw; prod uses the default URL anyway.
const SERVER_URL =
  (typeof process !== 'undefined' && process.env && process.env.PROCTOR_SERVER_URL) ||
  'https://app.procta.net';

contextBridge.exposeInMainWorld('procta_native', {
  isLobby: true,
  // Absolute API base. The lobby HTML is loaded via file://, so relative
  // `/api/...` URLs would resolve to file:// paths and fail. The
  // dashboard prepends this whenever window.procta_native is present.
  serverUrl: SERVER_URL,
  // The student dashboard calls this when "Start exam" is clicked on an
  // open/in-progress exam card. Main.js stashes the context, hides the
  // lobby, and spawns the kiosk exam window. The exam renderer fetches
  // this context via proctor.getExamContext() so the student doesn't have
  // to retype what the lobby already knows.
  //   ctx = { rollNumber, accessCode, examTitle, teacherId }
  launchExam: (ctx) => ipcRenderer.invoke('lobby-launch-exam', ctx),
  // ── Invite deep-link (procta://invite/<token>) ────────────────
  // The dashboard calls consumeInviteToken() on load. If the user
  // launched the app by clicking a procta:// link, this returns the
  // token once (subsequent calls return null). onInviteToken(cb)
  // covers the race where a SECOND link is clicked after the lobby
  // already loaded — main.js pushes via IPC in that case.
  // App version for the on-screen "Procta vX.Y.Z" badge.
  getAppVersion: () => ipcRenderer.invoke('get-app-version'),
  // Signed {payload, sig} proving this is a genuine Procta build, so the
  // login/signup form can skip Cloudflare Turnstile (which can't validate
  // against the procta-lobby:// origin's non-domain host). See main.js's
  // get-app-attestation handler for the signing details.
  getAppAttestation: () => ipcRenderer.invoke('get-app-attestation'),
  // Pre-exam System Check (Phase 1.4). Exercises the full on-device
  // pipeline (Python, AI packages, models, camera, mic, speech models)
  // and resolves a green/red summary per component. On-device only —
  // returns metadata, never media. The dashboard's "Run system check"
  // button calls this. Can take up to ~60s on a cold machine.
  runSystemCheck: () => ipcRenderer.invoke('run-system-check'),
  // Background AI-setup state, so the dashboard's "Start exam" flow can
  // show "Preparing AI environment…" when a launch lands before setup has
  // finished (the lobby now opens before Python provisioning completes).
  getSetupState: () => ipcRenderer.invoke('get-setup-state'),
  // Tells Electron to follow the app's OWN light/dark choice for
  // prefers-color-scheme instead of nativeTheme's OS-following default —
  // see the comment on this handler in main.js for why that matters (OS
  // dark mode was fighting our own theme switcher on a real Mac).
  setNativeThemeSource: (source) => ipcRenderer.invoke('set-native-theme-source', source),
  onSetupState: (cb) => {
    ipcRenderer.removeAllListeners('setup-state');
    ipcRenderer.on('setup-state', (_, st) => {
      try { cb(st); } catch(e) { console.error('[setup] state cb failed', e); }
    });
  },
  consumeInviteToken: () => ipcRenderer.invoke('consume-invite-token'),
  onInviteToken: (cb) => {
    ipcRenderer.removeAllListeners('invite-token-available');
    ipcRenderer.on('invite-token-available', (_, token) => {
      try { cb(token); } catch(e) { console.error('[invite] cb failed', e); }
    });
  },
  // Surfaced when a procta:// URL was clicked but didn't match the
  // expected `procta://invite/<token>` format. The lobby UI shows a
  // brief error chip telling the user to copy the invite link from
  // their email again, instead of silently swallowing the click.
  onInviteTokenMalformed: (cb) => {
    ipcRenderer.removeAllListeners('invite-token-malformed');
    ipcRenderer.on('invite-token-malformed', (_, info) => {
      try { cb(info); } catch(e) { console.error('[invite] malformed cb failed', e); }
    });
  },
  // In-banner "Restart now" button for the update banner. Called from
  // injected JS in the lobby page (auto-update.js) — avoids the fragile
  // postMessage → console.log → console-message chain that silently
  // broke in earlier versions when Electron changed the event signature.
  quitAndInstall: () => ipcRenderer.send('procta-quit-and-install'),
  // "Retry now" button + auto-retry timer on the lobby's own static
  // "failed to load" error page (kiosk-manager.js's did-fail-load
  // handler) — lets a transient origin outage recover without the
  // student having to relaunch the whole app. See kiosk-manager.js's
  // retryLobbyLoad() for what this actually re-attempts.
  retryLoad: () => ipcRenderer.send('lobby-retry-load'),
});
