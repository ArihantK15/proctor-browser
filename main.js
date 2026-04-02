const {
  app, BrowserWindow, ipcMain,
  globalShortcut, powerSaveBlocker
} = require('electron');
const path = require('path');

const SERVER_URL = 'https://trance-expertise-dec-egg.trycloudflare.com';
const ADMIN_CODE = 'EXIT2026';

let mainWindow   = null;
let powerBlockId = null;
let isKiosk      = false;

function createWindow() {
  mainWindow = new BrowserWindow({
    width:  1200,
    height: 800,
    webPreferences: {
      preload:          path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration:  false,
      devTools:         true,
    }
  });
  mainWindow.loadFile(
    path.join(__dirname, 'renderer', 'index.html'));
  mainWindow.webContents.openDevTools();
  mainWindow.webContents.setWindowOpenHandler(
    () => ({action:'deny'}));
}

app.whenReady().then(createWindow);
app.on('window-all-closed', () => app.quit());

ipcMain.handle('validate-student', async (_, roll) => {
  const r = await fetch(`${SERVER_URL}/api/validate-student`, {
    method:  'POST',
    headers: {'Content-Type': 'application/json'},
    body:    JSON.stringify({roll_number: roll})
  });
  if (!r.ok) { const e = await r.json(); throw new Error(e.detail); }
  return r.json();
});

ipcMain.handle('get-questions', async () => {
  const r = await fetch(`${SERVER_URL}/api/questions`);
  if (!r.ok) throw new Error('Could not load questions');
  return r.json();
});

ipcMain.handle('log-event', async (_, data) => {
  try {
    await fetch(`${SERVER_URL}/event`, {
      method:  'POST',
      headers: {'Content-Type': 'application/json'},
      body:    JSON.stringify(data)
    });
  } catch(e) { console.error('[log-event]', e.message); }
});

ipcMain.handle('submit-exam', async (_, data) => {
  const r = await fetch(`${SERVER_URL}/api/submit-exam`, {
    method:  'POST',
    headers: {'Content-Type': 'application/json'},
    body:    JSON.stringify(data)
  });
  if (!r.ok) throw new Error('Submission failed');
  return r.json();
});

ipcMain.handle('start-proctor', (_, data) => {
  console.log('[Proctor] Started:', data.sessionId);
  return {started: true};
});

ipcMain.handle('stop-proctor', () => {
  console.log('[Proctor] Stopped');
  return {stopped: true};
});

ipcMain.handle('admin-exit', (_, code) => {
  if (code === ADMIN_CODE || code === 'AUTO_CLOSE') {
    app.quit();
    return {success: true};
  }
  return {success: false};
});
