const { dialog } = require('electron');
const { autoUpdater } = require('electron-updater');

function initAutoUpdater(getLobbyWindow, getMainWindow) {
  if (!require('electron').app.isPackaged) {
    console.log('[AutoUpdate] Skipping — app not packaged (dev mode)');
    return;
  }

  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true;

  // Human-readable size / ETA for the progress banner.
  const _fmtMB = (b) => `${((Number(b) || 0) / 1048576).toFixed(1)} MB`;
  const _fmtETA = (s) => {
    if (!isFinite(s) || s <= 0) return '';
    if (s < 60) return `${Math.round(s)}s`;
    return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
  };

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
    // Resolve the CURRENT lobby window each call — it's recreated after every
    // exam (kiosk-manager createLobbyWindow), so a reference captured once at
    // init goes stale and the banner silently renders to a destroyed window
    // (why an available update "didn't show" while the app stayed open).
    const lobbyWindow = (typeof getLobbyWindow === 'function') ? getLobbyWindow() : null;
    if (!lobbyWindow || lobbyWindow.isDestroyed()) return;
    // state: 'downloading' | 'progress' | 'finalizing' | 'ready'
    const bgFor = {
      downloading: '#1a73e8',
      progress:    '#1a73e8',
      finalizing:  '#f9ab00',   // amber — visibly distinct so a stalled bar in the
                                // post-download NSIS verify/apply phase doesn't read as a hang
      ready:       '#34a853',
    };
    const bg = bgFor[state] || '#1a73e8';
    const restartBtn = state === 'ready'
      ? `<button id="update-restart-btn" style="margin-left:14px;background:#fff;color:#1a3a1f;border:0;border-radius:6px;padding:6px 14px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit">Restart now</button>`
      : '';
    const _progLine = (info.total)
      ? `${info.percent}%  ·  ${_fmtMB(info.transferred)} / ${_fmtMB(info.total)}  ·  ${_fmtMB(info.speed)}/s${info.eta ? '  ·  ETA ' + _fmtETA(info.eta) : ''}`
      : `${info.percent}%`;
    const text = state === 'downloading'
      ? `Downloading update v${info.version || ''}…`
      : state === 'progress'
        ? `Downloading update… ${_progLine}`
        : state === 'finalizing'
          ? `Finalizing update… verifying & applying the patch. This can take a minute on Windows — the app is not frozen.`
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
            : state === 'finalizing'
              ? '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'
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

  let _readyInfo = null;
  let _stallTimer = null;
  autoUpdater.on('download-progress', (progress) => {
    const pct = Math.round(progress.percent);
    const transferred = progress.transferred || 0;
    const total = progress.total || 0;
    const speed = progress.bytesPerSecond || 0;
    const eta = speed > 0 ? (total - transferred) / speed : 0;
    console.log(`[AutoUpdate] Download: ${pct}% (${_fmtMB(transferred)}/${_fmtMB(total)} @ ${_fmtMB(speed)}/s)`);
    if (_stallTimer) { clearTimeout(_stallTimer); _stallTimer = null; }
    if (pct >= 100) {
      // Download bytes are in; the Windows NSIS verify/apply phase runs now.
      _updateBanner('finalizing', {});
    } else {
      _updateBanner('progress', { percent: pct, transferred, total, speed, eta });
    }
    // If progress events go quiet before 'update-downloaded' fires, we're in the
    // post-download verify/apply phase — show the distinct finalizing banner so a
    // frozen-looking bar doesn't read as a hang ("60% forever").
    _stallTimer = setTimeout(() => { if (!_readyInfo) _updateBanner('finalizing', {}); }, 8000);
  });
  // macOS-specific safety net, researched 2026-07-04: Squirrel.Mac's
  // quitAndInstall() has documented, currently-active reliability issues
  // on recent Electron versions (electron/electron#50200, still open,
  // reports it failing on Electron 39.6.0+ — Procta runs 42.4.1, newer)
  // — sometimes it silently does nothing: doesn't quit, doesn't relaunch,
  // no error thrown. Worse, electron/electron#8912 confirms there is no
  // reliable way to even DETECT a Squirrel.Mac install failure from JS.
  // Without this, a student who clicks "Restart Now" and hits this bug
  // is left staring at nothing — the dialog closed, nothing visibly
  // happens, no error, no explanation. Same class of "looks like a hang"
  // problem the Windows path already solved with the Installing-update
  // overlay (see main.js's before-quit comment) — this is the mac-side
  // equivalent: if the process is STILL running N seconds after calling
  // quitAndInstall, that itself is the failure signal (a truly-quit
  // process can't run a JS timer), so show a clear, actionable message
  // instead of leaving the student confused. Harmless no-op on the
  // success path — the process exits well before the timeout fires.
  function _quitAndInstallWithFallback(lobbyWinGetter) {
    autoUpdater.quitAndInstall(false, true);
    if (process.platform !== 'darwin') return;
    setTimeout(() => {
      console.error('[AutoUpdate] quitAndInstall did not quit the app within 10s — Squirrel.Mac install likely failed silently (electron/electron#50200)');
      try {
        const lw = typeof lobbyWinGetter === 'function' ? lobbyWinGetter() : null;
        dialog.showMessageBox((lw && !lw.isDestroyed()) ? lw : null, {
          type: 'warning', title: 'Restart Didn\'t Complete',
          message: 'Procta could not restart automatically to finish installing the update.',
          detail: 'Please quit Procta completely (Cmd+Q) and reopen it — the update will finish installing on the next launch.',
          buttons: ['OK'], defaultId: 0,
        }).catch(() => {});
      } catch(e) { /* best-effort */ }
    }, 10_000).unref();
  }

  autoUpdater.on('update-downloaded', (info) => {
    console.log(`[AutoUpdate] Update downloaded: v${info.version}`);
    _readyInfo = info;
    if (_stallTimer) { clearTimeout(_stallTimer); _stallTimer = null; }
    _updateBanner('ready', info);
    // Surface a quieter native dialog (only when no exam in progress)
    // so the user can also restart from a system-style prompt — but
    // the on-screen banner is the primary affordance now.
    const mw = getMainWindow ? getMainWindow() : null;
    if (!(mw && !mw.isDestroyed())) {
      dialog.showMessageBox((typeof getLobbyWindow === 'function' ? getLobbyWindow() : null) || null, {
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
          _quitAndInstallWithFallback(getLobbyWindow);
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
      _quitAndInstallWithFallback(getLobbyWindow);
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
    // 15-min cadence (was 30) so an already-open lobby picks up a release
    // sooner — important on exam day when students leave the app open.
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
  }, 15 * 60 * 1000);
  _timer.unref && _timer.unref();
}

module.exports = { initAutoUpdater };
