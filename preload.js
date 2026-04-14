const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('proctor', {
  getIntegrityFlags: ()   => ipcRenderer.invoke('get-integrity-flags'),
  // Phase 2: the lobby pre-fills context (roll, access code) into
  // examContext before spawning the exam window. The renderer fetches it
  // on load so the student isn't forced to retype what they just entered
  // on the web dashboard. Returns null if the exam window was opened
  // directly (legacy / debug).
  getExamContext:  ()     => ipcRenderer.invoke('get-exam-context'),
  validateStudent: (roll, accessCode) => ipcRenderer.invoke('validate-student', roll, accessCode),
  getQuestions:    (sid)  => ipcRenderer.invoke('get-questions', sid),
  startCalibration:(data) => ipcRenderer.invoke('start-calibration', data),
  stopCalibration: (data) => ipcRenderer.invoke('stop-calibration', data),
  onCalReading:    (cb)   => {
    // Remove any prior listener to prevent leaks on retry/re-calibration
    ipcRenderer.removeAllListeners('cal-reading');
    ipcRenderer.on('cal-reading', (_, data) => cb(data));
  },
  startProctor:    (data) => ipcRenderer.invoke('start-proctor', data),
  startPolling:    (data) => ipcRenderer.invoke('start-polling', data),
  stopProctor:     ()     => ipcRenderer.invoke('stop-proctor'),
  logEvent:        (data) => ipcRenderer.invoke('log-event', data),
  submitExam:      (data) => ipcRenderer.invoke('submit-exam', data),
  adminExit:       (code) => ipcRenderer.invoke('admin-exit', code),
  // Phase 2: release kiosk and return to the student web dashboard.
  // No-op if called outside an exam.
  exitToLobby:     ()     => ipcRenderer.invoke('exit-exam-to-lobby'),
  // Phase 2: panic unlock — student-triggered escape hatch. Flags the
  // session for teacher review and releases lockdown. Does NOT auto-submit.
  panicUnlock:     (reason) => ipcRenderer.invoke('panic-unlock', { reason }),
  getEvents:       (sid)  => ipcRenderer.invoke('get-events', sid),
  onViolation:     (cb)   => {
    ipcRenderer.removeAllListeners('violation-detected');
    ipcRenderer.on('violation-detected', (_, data) => cb(data));
  },
  onForceSubmit:   (cb)   => {
    ipcRenderer.removeAllListeners('force-submit');
    ipcRenderer.once('force-submit', () => cb());
  },
});
