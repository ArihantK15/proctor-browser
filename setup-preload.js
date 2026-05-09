const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('setupApi', {
  onSetupStatus: (cb) => {
    const handler = (_e, msg) => cb(msg);
    ipcRenderer.on('setup-status', handler);
    return () => ipcRenderer.removeListener('setup-status', handler);
  },
});
