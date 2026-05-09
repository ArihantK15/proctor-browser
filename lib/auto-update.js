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

  autoUpdater.on('update-available', (info) => {
    console.log(`[AutoUpdate] Update available: v${info.version}`);
    if (lobbyWindow && !lobbyWindow.isDestroyed()) {
      lobbyWindow.webContents.executeJavaScript(
        `if(document.getElementById('update-banner')){document.getElementById('update-banner').style.display='flex'}` +
        `else{var b=document.createElement('div');b.id='update-banner';` +
        `b.style.cssText='position:fixed;top:0;left:0;right:0;padding:10px 20px;background:#1a73e8;color:#fff;font-size:14px;font-family:system-ui;display:flex;align-items:center;justify-content:center;gap:8px;z-index:99999;';` +
        `b.innerHTML='<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg> Downloading update v${info.version}...';` +
        `document.body.prepend(b)}`
      ).catch(() => {});
    }
  });

  autoUpdater.on('update-not-available', () => {
    console.log('[AutoUpdate] App is up to date');
  });

  autoUpdater.on('download-progress', (progress) => {
    const pct = Math.round(progress.percent);
    console.log(`[AutoUpdate] Download: ${pct}%`);
    if (lobbyWindow && !lobbyWindow.isDestroyed()) {
      lobbyWindow.webContents.executeJavaScript(
        `var b=document.getElementById('update-banner');if(b)b.innerHTML='<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg> Downloading update... ${pct}%'`
      ).catch(() => {});
    }
  });

  autoUpdater.on('update-downloaded', (info) => {
    console.log(`[AutoUpdate] Update downloaded: v${info.version}`);
    const mw = getMainWindow ? getMainWindow() : null;
    if (mw && !mw.isDestroyed()) {
      console.log('[AutoUpdate] Exam in progress — update will install on quit');
      if (lobbyWindow && !lobbyWindow.isDestroyed()) {
        lobbyWindow.webContents.executeJavaScript(
          `var b=document.getElementById('update-banner');if(b){b.style.background='#34a853';b.innerHTML='<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M9 12l2 2 4-4"/></svg> Update ready — will install when you close the app'}`
        ).catch(() => {});
      }
    } else {
      dialog.showMessageBox(lobbyWindow || null, {
        type: 'info', title: 'Update Ready',
        message: `Procta Browser v${info.version} has been downloaded.`,
        detail: 'The app will restart to apply the update.',
        buttons: ['Restart Now', 'Later'], defaultId: 0
      }).then(({ response }) => {
        if (response === 0) autoUpdater.quitAndInstall(false, true);
      });
    }
  });

  autoUpdater.on('error', (err) => {
    console.error('[AutoUpdate] Error:', err.message);
  });

  autoUpdater.checkForUpdates().catch(err => {
    console.error('[AutoUpdate] Check failed:', err.message);
  });
}

module.exports = { initAutoUpdater };
