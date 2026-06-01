const { SERVER_URL, POLL_INTERVAL_MS, IGNORED_EVENT_TYPES } = require('../config');
const { authHeaders, fetchWithTimeout } = require('./utils');

let pollInterval = null;

function startPolling(sessionId, mainWindow, studentToken, forceSubmitCallback, violationCallback) {
  if (pollInterval) return;
  // Backend currently exposes SSE only on the teacher side
  // (/api/v1/sse/sessions). There is no per-session student stream, so
  // the lobby polls /api/v1/events/{sid} on a fixed interval. When a
  // student-side SSE ships, swap this for a streaming reader.
  console.log('[Poll] using legacy polling for', sessionId);
  let lastEventId = 0;
  let forceSubmitSent = false;
  let _pollInFlight = false;

  pollInterval = setInterval(async () => {
    if (_pollInFlight) return;
    _pollInFlight = true;
    try {
      const r = await fetchWithTimeout(`${SERVER_URL}/api/v1/events/${sessionId}`,
                            { headers: authHeaders(studentToken) }, 15000);
      if (!r.ok) return;
      const data = await r.json();
      const events = data.events || [];

      if (!forceSubmitSent && events.some(e => e.type === 'exam_submitted')) {
        forceSubmitSent = true;
        if (forceSubmitCallback) forceSubmitCallback();
      }

      const newV = events.filter(e =>
        e.id > lastEventId &&
        (e.severity === 'high' || e.severity === 'medium') &&
        ![...IGNORED_EVENT_TYPES].some(x => (e.type || '').includes(x))
      );
      if (newV.length > 0) {
        lastEventId = Math.max(...newV.map(e => e.id));
        // Fire callback for every new high/medium violation. The earlier
        // implementation advanced lastEventId past all of them but only
        // dispatched newV[0] — subsequent violations were silently lost.
        if (violationCallback) {
          for (const v of newV) violationCallback(v);
        }
      }
    } catch(e) { console.error('[Poll]', e.message); }
    finally { _pollInFlight = false; }
  }, POLL_INTERVAL_MS);
}

function stopPolling() {
  if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
}

module.exports = { startPolling, stopPolling };
