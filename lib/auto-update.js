const { dialog } = require('electron');
const { autoUpdater } = require('electron-updater');

function initAutoUpdater(lobbyWindow, getMainWindow) {
  if (!require('electron').app.isPackaged) {
    console.log('[AutoUpdate] Skipping — app not packaged (dev mode)');
    return;
  }

  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true;

  autoUpdater.on('checking-for-update', () => {
    console.log('[AutoUpdate] Checking for updates...');
  });

  // Renders / updates / persists the update banner. Single helper so
  // every event (available/progress/downloaded) hits the same DOM
  // path — fixes the "banner only visible for 2 seconds, easy to
  // miss" bug where the banner sometimes vanished between phases.
  // The banner now also has explicit min-height + transition so it
  // doesn't flicker between text changes.
  const _updateBanner = (state, info) => {
    if (!lobbyWindow || lobbyWindow.isDestroyed()) return;
    // state: 'downloading' | 'progress' | 'ready'
    const bgFor = {
      downloading: '#1a73e8',
      progress:    '#1a73e8',
      ready:       '#34a853',
    };
    const bg = bgFor[state] || '#1a73e8';
    const restartBtn = state === 'ready'
      ? `<button id="update-restart-btn" style="margin-left:14px;background:#fff;color:#1a3a1f;border:0;border-radius:6px;padding:6px 14px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit">Restart now</button>`
      : '';
    const text = state === 'downloading'
      ? `Downloading update v${info.version || ''}…`
      : state === 'progress'
        ? `Downloading update… ${info.percent}%`
        : `Update ready — restart to apply (v${info.version || ''}). Will also install when you close the app.`;
    const html = `<style id="update-banner-style">
        #update-banner{position:fixed;top:0;left:0;right:0;min-height:44px;
          padding:10px 20px;color:#fff;font-size:14px;font-family:system-ui,-apple-system,'Segoe UI',sans-serif;
          display:flex;align-items:center;justify-content:center;gap:10px;
          z-index:99999;transition:background 0.3s ease;
          box-shadow:0 2px 8px rgba(0,0,0,0.25)}
        #update-banner svg{flex-shrink:0}
      </style>` +
      `<div id="update-banner" style="background:${bg}">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          ${state === 'ready'
            ? '<circle cx="12" cy="12" r="10"/><path d="M9 12l2 2 4-4"/>'
            : '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>'}
        </svg>
        <span>${text}</span>${restartBtn}
      </div>`;
    // Use innerHTML on a wrapper so we replace the banner atomically
    // each event — prevents the "Banner went blank for a frame
    // between events" pattern that made it look like it disappeared.
    const js = `(function(){
      var old=document.getElementById('update-banner');
      if(old)old.remove();
      var style=document.getElementById('update-banner-style');
      if(style)style.remove();
      var wrap=document.createElement('div');
      wrap.innerHTML=${JSON.stringify(html)};
      while(wrap.firstChild) document.body.prepend(wrap.lastChild);
      ${state === 'ready' ? `
      var btn=document.getElementById('update-restart-btn');
      if(btn){btn.addEventListener('click',function(){
        if(window.procta_native&&window.procta_native.quitAndInstall)window.procta_native.quitAndInstall();
      });}` : ''}
    })()`;
    lobbyWindow.webContents.executeJavaScript(js).catch(e => {
      console.debug('[AutoUpdate] banner render failed:', e.message);
    });
  };

  autoUpdater.on('update-available', (info) => {
    console.log(`[AutoUpdate] Update available: v${info.version}`);
    _updateBanner('downloading', info);
  });

  autoUpdater.on('update-not-available', () => {
    console.log('[AutoUpdate] App is up to date');
  });

  autoUpdater.on('download-progress', (progress) => {
    const pct = Math.round(progress.percent);
    console.log(`[AutoUpdate] Download: ${pct}%`);
    _updateBanner('progress', { percent: pct, version: '' });
  });

  let _readyInfo = null;
  autoUpdater.on('update-downloaded', (info) => {
    console.log(`[AutoUpdate] Update downloaded: v${info.version}`);
    _readyInfo = info;
    _updateBanner('ready', info);
    // Surface a quieter native dialog (only when no exam in progress)
    // so the user can also restart from a system-style prompt — but
    // the on-screen banner is the primary affordance now.
    const mw = getMainWindow ? getMainWindow() : null;
    if (!(mw && !mw.isDestroyed())) {
      dialog.showMessageBox(lobbyWindow || null, {
        type: 'info', title: 'Update Ready',
        message: `Procta v${info.version} is ready to install.`,
        detail: 'Restart now to apply, or close the app and it will install automatically.',
        buttons: ['Restart Now', 'Later'], defaultId: 0
      }).then(({ response }) => {
        if (response === 0) {
          // Tell main.js to paint the "Installing update" overlay
          // before tearing the app down. Without this hook the
          // 30-60s NSIS install + relaunch looks like the app hung.
          try {
            const m = require('../main');
            if (m && typeof m._setQuittingForUpdate === 'function') m._setQuittingForUpdate();
          } catch(e) { /* main.js export missing — non-fatal */ }
          autoUpdater.quitAndInstall(false, true);
        }
      });
    }
  });

  // Wire the in-banner "Restart now" button from the renderer. The
  // lobby_preload exposes procta_native.quitAndInstall() which sends a
  // direct 'procta-quit-and-install' IPC — replaces the old fragile
  // postMessage → console.log → console-message chain.
  const { ipcMain } = require('electron');
  if (!ipcMain._proctaUpdateHandlerInstalled) {
    ipcMain._proctaUpdateHandlerInstalled = true;
    ipcMain.on('procta-quit-and-install', () => {
      if (!_readyInfo) return;
      try {
        const m = require('../main');
        if (m && typeof m._setQuittingForUpdate === 'function') m._setQuittingForUpdate();
      } catch(e) { /* non-fatal */ }
      autoUpdater.quitAndInstall(false, true);
    });
  }

  autoUpdater.on('error', (err) => {
    console.error('[AutoUpdate] Error:', err.message);
  });

  // Bound every check so a hung GitHub socket can't stall the updater
  // forever (electron-updater has no built-in check timeout). The race
  // just abandons the check; the next interval tick retries.
  const _check = () => {
    const timed = Promise.race([
      autoUpdater.checkForUpdates(),
      new Promise((_, reject) =>
        setTimeout(() => reject(new Error('check timed out')), 20_000)),
    ]);
    return timed.catch(err => {
      console.error('[AutoUpdate] Check failed:', err.message);
    });
  };
  // Check at startup AND every 30 min. Previously we only checked once at
  // launch, so an app that was already running when a new release shipped
  // never saw it until the user manually relaunched — which is exactly why
  // an update "didn't work" right after a release. The periodic re-check
  // (plus autoInstallOnAppQuit) means a long-lived lobby picks up the new
  // version on its own.
  _check();
  const _timer = setInterval(() => {
    // Never check (and thus never start a background download) while an
    // exam is in progress — the kiosk exam window is the signal. A
    // mid-exam download spike competes with the proctor for CPU/network,
    // and the student can't act on an update anyway (quitAndInstall is
    // already blocked by the un-closable exam window). Resume on the next
    // tick once the exam ends.
    try {
      const mw = typeof getMainWindow === 'function' ? getMainWindow() : null;
      if (mw && !mw.isDestroyed()) return;
    } catch (_) {}
    _check();
  }, 30 * 60 * 1000);
  _timer.unref && _timer.unref();
}

module.exports = { initAutoUpdater };
