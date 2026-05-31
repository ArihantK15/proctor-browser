const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('setupApi', {
  onSetupStatus: (cb) => {
    const handler = (_e, msg) => cb(msg);
    ipcRenderer.on('setup-status', handler);
    return () => ipcRenderer.removeListener('setup-status', handler);
  },
  // Progress bar IPC. v2.3.5 added the setup-progress channel but the
  // inline HTML script tried to require('electron') directly — which
  // doesn't work with contextIsolation:true + nodeIntegration:false,
  // so ipcRenderer was undefined and the bar never moved. Expose the
  // listener through contextBridge instead.
  onSetupProgress: (cb) => {
    const handler = (_e, payload) => cb(payload);
    ipcRenderer.on('setup-progress', handler);
    return () => ipcRenderer.removeListener('setup-progress', handler);
  },
  // Setup-mode IPC. Two modes: 'fresh' (first install — show "Setting
  // up Procta") or 'recheck' (already-installed — show "Checking for
  // updates" since the flow short-circuits in ~1s after a package
  // verify). Drives the heading text and the bar's expected pace.
  onSetupMode: (cb) => {
    const handler = (_e, mode) => cb(mode);
    ipcRenderer.on('setup-mode', handler);
    return () => ipcRenderer.removeListener('setup-mode', handler);
  },
});
