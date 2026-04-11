// Lobby preload — loaded only into the unlocked pre-exam window that
// renders the student dashboard (app/static/student.html, loaded via
// file:// from inside the Electron bundle). Exposes a tiny bridge the
// dashboard can use to launch a proctored exam window. Nothing here runs
// inside the kiosk-locked exam window.
const { contextBridge, ipcRenderer } = require('electron');

const SERVER_URL = process.env.PROCTOR_SERVER_URL || 'https://app.procta.net';

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
});
