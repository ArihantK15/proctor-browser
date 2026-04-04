const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('proctor', {
  validateStudent: (roll) => ipcRenderer.invoke('validate-student', roll),
  getQuestions:    ()     => ipcRenderer.invoke('get-questions'),
  startProctor:    (data) => ipcRenderer.invoke('start-proctor', data),
  startPolling:    (data) => ipcRenderer.invoke('start-polling', data),
  stopProctor:     ()     => ipcRenderer.invoke('stop-proctor'),
  logEvent:        (data) => ipcRenderer.invoke('log-event', data),
  submitExam:      (data) => ipcRenderer.invoke('submit-exam', data),
  adminExit:       (code) => ipcRenderer.invoke('admin-exit', code),
  getEvents:       (sid)  => ipcRenderer.invoke('get-events', sid),
  onViolation:     (cb)   => {
    ipcRenderer.on('violation-detected', (_, data) => cb(data));
  },
  onForceSubmit:   (cb)   => {
    ipcRenderer.once('force-submit', () => cb());
  },
});
