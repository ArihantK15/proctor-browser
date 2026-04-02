const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('proctor', {
  validateStudent: (roll) => ipcRenderer.invoke('validate-student', roll),
  getQuestions:    ()     => ipcRenderer.invoke('get-questions'),
  startProctor:    (data) => ipcRenderer.invoke('start-proctor', data),
  stopProctor:     ()     => ipcRenderer.invoke('stop-proctor'),
  logEvent:        (data) => ipcRenderer.invoke('log-event', data),
  submitExam:      (data) => ipcRenderer.invoke('submit-exam', data),
  adminExit:       (code) => ipcRenderer.invoke('admin-exit', code),
});
