const {
  SERVER_URL, POLL_INTERVAL_MS, SSE_TIMEOUT_MS,
  SSE_INITIAL_BACKOFF_MS, SSE_MAX_BACKOFF_MS, IGNORED_EVENT_TYPES,
} = require('../config');
const { authHeaders, fetchWithTimeout } = require('./utils');

let pollInterval = null;
let _sseAbort = null;
let _sseReconnectDelay = 0;

function startPolling(sessionId, mainWindow, studentToken, forceSubmitCallback, violationCallback) {
  if (pollInterval || _sseAbort) return;
  _startSSE(sessionId, mainWindow, studentToken, forceSubmitCallback, violationCallback)
    .catch(() => _startLegacyPolling(sessionId, mainWindow, studentToken, forceSubmitCallback, violationCallback));
}

async function _startSSE(sessionId, mainWindow, studentToken, forceSubmitCallback, violationCallback) {
  const token = studentToken || '';
  const url = `${SERVER_URL}/api/sse/events/${encodeURIComponent(sessionId)}?token=${encodeURIComponent(token)}`;
  _sseAbort = new AbortController();
  let forceSubmitSent = false;

  const timeout = setTimeout(() => _sseAbort.abort(), SSE_TIMEOUT_MS);
  const r = await fetch(url, { signal: _sseAbort.signal });
  clearTimeout(timeout);
  if (!r.ok || !r.body) throw new Error('SSE not available');

  console.log('[SSE] connected for', sessionId);
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split('\n');
      buffer = lines.pop();
      let eventData = '';
      for (const line of lines) {
        if (line === '') {
          if (eventData) {
            try {
              const evt = JSON.parse(eventData);
              if (!forceSubmitSent && evt.type === 'exam_submitted') {
                forceSubmitSent = true;
                if (forceSubmitCallback) forceSubmitCallback();
              }
              if ((evt.severity === 'high' || evt.severity === 'medium') &&
                  ![...IGNORED_EVENT_TYPES].some(x => (evt.type || '').includes(x))) {
                if (violationCallback) violationCallback(evt);
              }
            } catch(_) {}
          }
          eventData = '';
        } else if (line.startsWith('data: ')) {
          eventData += (eventData ? '\n' : '') + line.slice(6);
        }
      }
    }
  } catch(e) {
    if (e.name !== 'AbortError') console.error('[SSE] stream error:', e.message);
  }

  // Reconnect with exponential backoff
  if (_sseAbort && !_sseAbort.signal.aborted) {
    _sseReconnectDelay = _sseReconnectDelay === 0 ? SSE_INITIAL_BACKOFF_MS
      : Math.min(_sseReconnectDelay * 2, SSE_MAX_BACKOFF_MS);
    console.log(`[SSE] stream ended, reconnecting in ${_sseReconnectDelay / 1000}s...`);
    setTimeout(() => {
      if (_sseAbort && !_sseAbort.signal.aborted) {
        _sseAbort = null;
        _startSSE(sessionId, mainWindow, studentToken, forceSubmitCallback, violationCallback)
          .then(() => { _sseReconnectDelay = 0; })
          .catch(() => _startLegacyPolling(sessionId, mainWindow, studentToken, forceSubmitCallback, violationCallback));
      }
    }, _sseReconnectDelay);
  }
}

function _startLegacyPolling(sessionId, mainWindow, studentToken, forceSubmitCallback, violationCallback) {
  if (pollInterval) return;
  console.log('[Poll] using legacy polling for', sessionId);
  let lastEventId = 0;
  let forceSubmitSent = false;
  let _pollInFlight = false;

  pollInterval = setInterval(async () => {
    if (_pollInFlight) return;
    _pollInFlight = true;
    try {
      const r = await fetchWithTimeout(`${SERVER_URL}/events/${sessionId}`,
                            { headers: authHeaders(studentToken) }, 15000);
      if (!r.ok) { _pollInFlight = false; return; }
      const data = await r.json();
      const events = data.events || [];

      if (!forceSubmitSent && events.some(e => e.type === 'exam_submitted')) {
        forceSubmitSent = true;
        if (forceSubmitCallback) forceSubmitCallback();
      }

      const newV = events.filter(e =>
        e.id > lastEventId &&
        (e.severity === 'high' || e.severity === 'medium') &&
        ![...IGNORED_EVENT_TYPES].some(x => e.type.includes(x))
      );
      if (newV.length > 0) {
        lastEventId = Math.max(...newV.map(e => e.id));
        if (violationCallback) violationCallback(newV[0]);
      }
    } catch(e) { console.error('[Poll]', e.message); }
    _pollInFlight = false;
  }, POLL_INTERVAL_MS);
}

function stopPolling() {
  if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
  if (_sseAbort) { try { _sseAbort.abort(); } catch(_) {} _sseAbort = null; }
}

module.exports = { startPolling, stopPolling };
