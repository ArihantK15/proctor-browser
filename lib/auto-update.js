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
        window.postMessage({type:'__procta_update_restart'},'*');
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

  // Wire the in-banner "Restart now" button from the renderer.
  const { ipcMain } = require('electron');
  if (!ipcMain._proctaUpdateHandlerInstalled) {
    ipcMain._proctaUpdateHandlerInstalled = true;
    // Renderer posts a window message to itself; we register a
    // before-unload-style channel via the postMessage bridge in the
    // lobby_preload (if any), but the simplest robust path is to
    // listen for the postMessage via console-message → no IPC change
    // needed. For now we re-listen on the lobby for a custom event.
    if (lobbyWindow && !lobbyWindow.isDestroyed()) {
      lobbyWindow.webContents.on('console-message', (_e, _l, msg) => {
        if (msg === '__procta_update_restart_click' && _readyInfo) {
          try {
            const m = require('../main');
            if (m && typeof m._setQuittingForUpdate === 'function') m._setQuittingForUpdate();
          } catch(e) { /* non-fatal */ }
          autoUpdater.quitAndInstall(false, true);
        }
      });
      // Forward the postMessage to a console.log so the main can
      // observe it. Injected once after dom-ready.
      lobbyWindow.webContents.on('dom-ready', () => {
        lobbyWindow.webContents.executeJavaScript(`
          window.addEventListener('message', function(e){
            if (e.data && e.data.type === '__procta_update_restart') {
              console.log('__procta_update_restart_click');
            }
          });
        `).catch(() => {});
      });
    }
  }

  autoUpdater.on('error', (err) => {
    console.error('[AutoUpdate] Error:', err.message);
  });

  autoUpdater.checkForUpdates().catch(err => {
    console.error('[AutoUpdate] Check failed:', err.message);
  });
}

module.exports = { initAutoUpdater };
