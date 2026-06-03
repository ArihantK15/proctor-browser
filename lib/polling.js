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
  // Failure backoff. `_pollInFlight` already prevents a slow 15s fetch
  // from stacking behind the 2s interval; this adds graceful backoff so a
  // server that is 5xx-ing (or a network that is down) isn't hammered at
  // 0.5 req/s per student — which, multiplied across thousands of
  // students, becomes a thundering herd against an already-struggling
  // backend. On each failure we skip an exponentially growing number of
  // ticks (capped ~60s); a single success resets it.
  let _failStreak = 0;
  let _skipTicks = 0;
  let _authExpiryLogged = false;

  pollInterval = setInterval(async () => {
    if (_pollInFlight) return;
    if (_skipTicks > 0) { _skipTicks--; return; }
    _pollInFlight = true;
    try {
      const r = await fetchWithTimeout(`${SERVER_URL}/api/v1/events/${encodeURIComponent(sessionId)}`,
                            { headers: authHeaders(studentToken) }, 15000);
      if (!r.ok) {
        // 401/403 mid-exam means the student token expired. Polling can't
        // refresh it, so violation + force-submit delivery would silently
        // stop for the rest of the exam. Log it ONCE, loudly, so it's
        // diagnosable (the server-side reaper still protects the session).
        if ((r.status === 401 || r.status === 403) && !_authExpiryLogged) {
          _authExpiryLogged = true;
          console.error(`[Poll] auth failed (${r.status}) — student token likely ` +
            `expired mid-exam; live violation/force-submit delivery is now degraded`);
        } else {
          console.warn(`[Poll] non-OK response: ${r.status}`);
        }
        _failStreak++;
        _skipTicks = Math.min(2 ** _failStreak, 30); // cap ~60s at a 2s interval
        return;
      }
      _failStreak = 0; // healthy response — clear backoff
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
    } catch(e) {
      // Network error / timeout / abort — back off the same way so a dead
      // network doesn't keep firing a doomed fetch every 2s.
      console.error('[Poll]', e.message);
      _failStreak++;
      _skipTicks = Math.min(2 ** _failStreak, 30);
    }
    finally { _pollInFlight = false; }
  }, POLL_INTERVAL_MS);
}

function stopPolling() {
  if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
}

module.exports = { startPolling, stopPolling };
