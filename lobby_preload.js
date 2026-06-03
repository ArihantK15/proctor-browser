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
});
