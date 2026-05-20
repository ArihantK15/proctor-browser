/**
 * Mixed proctoring load test.
 *
 * This exercises the paths not covered by real_exam.js:
 *   - periodic autosave + final submit
 *   - heartbeat updates
 *   - proctoring event ingestion
 *   - screenshot/frame upload via /api/v1/analyze-frame
 *   - live-frame upload cache via /api/v1/proctor/live-frame
 *   - optional teacher dashboard SSE streams
 *
 * Modes:
 *   AUTH_MODE=practice (default)
 *     Uses PRACTICE_* session IDs. Fast and safe, but event/heartbeat/analyze
 *     handlers intentionally bypass DB/screenshot persistence.
 *
 *   AUTH_MODE=jwt TOKEN_FILE=mixed_tokens.json
 *     Uses pre-minted student JWTs from scripts/mint_loadtest_tokens.py.
 *     This hits real heartbeat/event/analyze-frame persistence while keeping
 *     sessions synthetic and isolated from real students.
 *
 *   SUBMIT_MODE=jwt (with AUTH_MODE=jwt)
 *     Exercises the real submit-exam path including the async-scoring fast
 *     path (Fix #2). After submit, polls /api/v1/session-status until the
 *     RQ worker writes the final score, capped at POLL_MAX_SECONDS. The
 *     tokens MUST embed valid tid+eid claims that point at a real exam
 *     with questions (use mint_loadtest_tokens.py --teacher-id --exam-id).
 *     Session IDs use the real `${roll}_${timestamp}` format so the server
 *     accepts them on first heartbeat (which upserts the session row).
 */
import http from 'k6/http'
import { check, sleep } from 'k6'
import { Counter, Trend } from 'k6/metrics'

const TARGET = __ENV.TARGET || 'https://app.procta.net'
const VUS = parseInt(__ENV.VUS || '500', 10)
const EXAM_SECONDS = parseInt(__ENV.EXAM_SECONDS || '300', 10)
const JOIN_SPREAD_SECONDS = parseInt(__ENV.JOIN_SPREAD_SECONDS || '120', 10)
const AUTOSAVE_INTERVAL_SECONDS = parseInt(__ENV.AUTOSAVE_INTERVAL_SECONDS || '60', 10)
const EVENT_INTERVAL_SECONDS = parseInt(__ENV.EVENT_INTERVAL_SECONDS || '30', 10)
const FRAME_INTERVAL_SECONDS = parseInt(__ENV.FRAME_INTERVAL_SECONDS || '60', 10)
const SUBMIT_SPREAD_SECONDS = parseInt(__ENV.SUBMIT_SPREAD_SECONDS || '60', 10)
const AUTH_MODE = (__ENV.AUTH_MODE || 'practice').toLowerCase()
const SUBMIT_MODE = (__ENV.SUBMIT_MODE || (AUTH_MODE === 'practice' ? 'practice' : 'off')).toLowerCase()
const TOKEN_FILE = __ENV.TOKEN_FILE || ''
const ADMIN_TOKEN = __ENV.ADMIN_TOKEN || ''
const DASHBOARD_VUS = parseInt(__ENV.DASHBOARD_VUS || '0', 10)
const SSE_HOLD_SECONDS = parseInt(__ENV.SSE_HOLD_SECONDS || String(EXAM_SECONDS), 10)
const LOADTEST_SECRET = __ENV.LOADTEST_SECRET || ''
// session-status polling for SUBMIT_MODE=jwt (verifies Fix #2 async path)
const POLL_INTERVAL_SECONDS = parseFloat(__ENV.POLL_INTERVAL_SECONDS || '1.5')
const POLL_MAX_SECONDS = parseInt(__ENV.POLL_MAX_SECONDS || '30', 10)

if (!['practice', 'jwt'].includes(AUTH_MODE)) {
  throw new Error('AUTH_MODE must be practice or jwt')
}

if (!['practice', 'jwt', 'off'].includes(SUBMIT_MODE)) {
  throw new Error('SUBMIT_MODE must be practice, jwt, or off')
}

const tokenRows = AUTH_MODE === 'jwt' && TOKEN_FILE
  ? JSON.parse(open(TOKEN_FILE))
  : []

const scenarios = {
  students: {
    executor: 'per-vu-iterations',
    vus: VUS,
    iterations: 1,
    maxDuration: `${JOIN_SPREAD_SECONDS + EXAM_SECONDS + SUBMIT_SPREAD_SECONDS + 120}s`,
  },
}

if (ADMIN_TOKEN && DASHBOARD_VUS > 0) {
  scenarios.dashboard_sse = {
    executor: 'ramping-vus',
    startVUs: 0,
    stages: [
      { duration: '30s', target: DASHBOARD_VUS },
      { duration: `${SSE_HOLD_SECONDS}s`, target: DASHBOARD_VUS },
      { duration: '20s', target: 0 },
    ],
    exec: 'dashboardSse',
  }
}

export const options = {
  scenarios,
  thresholds: {
    http_req_failed: ['rate<0.02'],
    checks: ['rate>0.98'],
    'http_req_duration{name:bulk_save}': ['p(95)<1000'],
    ...(SUBMIT_MODE === 'off' ? {} : { 'http_req_duration{name:submit}': ['p(95)<1000'] }),
    'http_req_duration{name:heartbeat}': ['p(95)<1000'],
    'http_req_duration{name:proctor_event}': ['p(95)<1500'],
    'http_req_duration{name:analyze_frame}': ['p(95)<3000'],
    'http_req_duration{name:live_frame}': ['p(95)<1000'],
    sse_disconnects: ['count<10'],
    ...(SUBMIT_MODE === 'jwt' ? { 'http_req_duration{name:session_status}': ['p(95)<1000'] } : {}),
  },
}

const counts = {
  bulkOk: new Counter('bulk_save_ok'),
  bulkFail: new Counter('bulk_save_failure'),
  heartbeatOk: new Counter('heartbeat_ok'),
  heartbeatFail: new Counter('heartbeat_failure'),
  eventOk: new Counter('proctor_event_ok'),
  eventFail: new Counter('proctor_event_failure'),
  frameOk: new Counter('analyze_frame_ok'),
  frameFail: new Counter('analyze_frame_failure'),
  liveFrameOk: new Counter('live_frame_ok'),
  liveFrameFail: new Counter('live_frame_failure'),
  submitOk: new Counter('submit_ok'),
  submitFail: new Counter('submit_failure'),
  // Fix #2 verification (only populated when SUBMIT_MODE=jwt)
  scoringPending: new Counter('scoring_pending'),
  scoringDone: new Counter('scoring_done'),
  scoringTimeout: new Counter('scoring_timeout'),
  inlineScored: new Counter('inline_scored'),
}
const scoringLatency = new Trend('scoring_latency_ms', true)

const sseFirstEventMs = new Trend('sse_first_event_ms')
const sseLifetimeMs = new Trend('sse_lifetime_ms')
const sseDisconnects = new Counter('sse_disconnects')

const QUESTION_IDS = ['q1', 'q2', 'q3', 'q4', 'q5', 'q6', 'q7', 'q8']
const ANSWER_CHOICES = ['A', 'B', 'C', 'D']

// Small valid JPEG, repeated only as payload data. analyze-frame stores it;
// proctor/live-frame decodes and recompresses it into Redis.
const JPEG_B64 =
  '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAH/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAEFAqf/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAEDAQE/ASP/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAECAQE/ASP/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAY/Al//xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAE/IV//2gAMAwEAAgADAAAAEP/EABQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQMBAT8QH//EABQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQIBAT8QH//EABQQAQAAAAAAAAAAAAAAAAAAABD/2gAIAQEAAT8QH//Z'

export default function () {
  const identity = getIdentity()
  const headers = requestHeaders(identity.token)
  const answers = Object.fromEntries(
    QUESTION_IDS.map((q, i) => [q, ANSWER_CHOICES[(__VU + i) % 4]])
  )

  sleep(spreadOffset(__VU, JOIN_SPREAD_SECONDS, VUS))

  let elapsed = 0
  while (elapsed < EXAM_SECONDS) {
    doBulkSave(identity.sessionId, answers, headers)
    doHeartbeat(identity.sessionId, headers)

    if (elapsed % EVENT_INTERVAL_SECONDS === 0) {
      doProctorEvent(identity.sessionId, headers, elapsed)
    }
    if (elapsed % FRAME_INTERVAL_SECONDS === 0) {
      doAnalyzeFrame(identity.sessionId, headers, elapsed)
      doLiveFrame(identity.sessionId)
    }

    const step = Math.min(
      AUTOSAVE_INTERVAL_SECONDS,
      EVENT_INTERVAL_SECONDS,
      FRAME_INTERVAL_SECONDS,
      EXAM_SECONDS - elapsed,
    )
    sleep(Math.max(step, 1))
    elapsed += step
  }

  sleep(spreadOffset(__VU, SUBMIT_SPREAD_SECONDS, VUS))
  if (SUBMIT_MODE !== 'off') {
    doSubmit(submitIdentity(identity), answers, submitHeaders(headers))
  }
}

export function dashboardSse() {
  const tokenRes = http.post(
    `${TARGET}/api/v1/sse/connect-token`,
    null,
    { headers: { Authorization: `Bearer ${ADMIN_TOKEN}` }, tags: { name: 'sse_connect_token' } },
  )
  if (!check(tokenRes, { 'sse connect-token ok': (r) => r.status === 200 && !!r.json('connect_token') })) {
    sseDisconnects.add(1)
    sleep(1)
    return
  }
  const token = tokenRes.json('connect_token')
  const started = Date.now()
  const res = http.get(
    `${TARGET}/api/v1/sse/sessions?token=${encodeURIComponent(token)}&max_seconds=${SSE_HOLD_SECONDS}`,
    {
      tags: { name: 'sse_sessions' },
      timeout: `${SSE_HOLD_SECONDS + 15}s`,
      responseType: 'text',
    },
  )
  const ended = Date.now()
  const body = res.body || ''
  const firstIdx = Math.min(
    ...['event: init', 'event: refresh', 'event: ping']
      .map((needle) => body.indexOf(needle))
      .filter((idx) => idx >= 0),
  )
  if (Number.isFinite(firstIdx)) {
    sseFirstEventMs.add(ended - started)
  }
  sseLifetimeMs.add(ended - started)
  const ok = check(res, {
    'sse status 200': (r) => r.status === 200,
    'sse content type': (r) => String(r.headers['Content-Type'] || '').includes('text/event-stream'),
    'sse emitted event': () => Number.isFinite(firstIdx),
  })
  if (!ok || res.status !== 200) sseDisconnects.add(1)
  sleep(1)
}

function getIdentity() {
  if (AUTH_MODE === 'jwt') {
    const row = tokenRows[(__VU - 1) % tokenRows.length]
    if (!row || !row.token) {
      throw new Error(`AUTH_MODE=jwt requires TOKEN_FILE with at least ${VUS} usable token rows`)
    }
    const roll = row.roll_number || rollFromSession(row.session_id) || `MIXED_${__VU}`
    // For SUBMIT_MODE=jwt we need a session_id format the real submit
    // handler will accept: `${roll}_${suffix}` where suffix has NO
    // underscores (server uses rsplit('_', 1)). Date.now() guarantees
    // uniqueness across test runs (the resubmit-guard won't catch us
    // from a previous run), and the trailing `v${__VU}` disambiguates
    // VUs that hit the same ms — `v` is not '_' so the rsplit keeps
    // the whole thing as one suffix.
    const sessionId = SUBMIT_MODE === 'jwt'
      ? `${roll}_${Date.now()}v${__VU}`
      : (row.session_id || `${roll}_RUN`)
    return {
      sessionId,
      rollNumber: roll,
      token: row.token,
      realPersistence: true,
    }
  }
  const roll = `PRACTICE_MIXED_${__VU}`
  return {
    sessionId: `${roll}_${__VU}`,
    rollNumber: roll,
    token: '',
    realPersistence: false,
  }
}

function submitIdentity(identity) {
  if (SUBMIT_MODE === 'jwt') return identity
  const roll = `PRACTICE_MIXED_SUBMIT_${__VU}`
  return {
    sessionId: `${roll}_${__VU}`,
    rollNumber: roll,
    token: '',
    realPersistence: false,
  }
}

function submitHeaders(headers) {
  if (SUBMIT_MODE === 'jwt') return headers
  return requestHeaders()
}

function rollFromSession(sessionId) {
  const value = String(sessionId || '')
  const idx = value.lastIndexOf('_')
  if (idx <= 0) return value
  return value.slice(0, idx)
}

function requestHeaders(token = '') {
  const headers = { 'Content-Type': 'application/json' }
  if (LOADTEST_SECRET) headers['X-Loadtest-Key'] = LOADTEST_SECRET
  if (token) headers.Authorization = `Bearer ${token}`
  return headers
}

function doBulkSave(sessionId, answers, headers) {
  const res = http.post(
    `${TARGET}/api/v1/save-answers-bulk`,
    JSON.stringify({ session_id: sessionId, answers }),
    { headers, tags: { name: 'bulk_save' }, timeout: '20s' },
  )
  record(res, counts.bulkOk, counts.bulkFail)
  check(res, { 'bulk save ok': (r) => r.status === 200 })
}

function doHeartbeat(sessionId, headers) {
  const res = http.post(
    `${TARGET}/api/v1/heartbeat`,
    JSON.stringify({
      session_id: sessionId,
      event_type: 'heartbeat',
      severity: 'low',
      details: 'mixed proctoring loadtest heartbeat',
    }),
    { headers, tags: { name: 'heartbeat' }, timeout: '20s' },
  )
  record(res, counts.heartbeatOk, counts.heartbeatFail)
  check(res, { 'heartbeat ok': (r) => r.status === 200 })
}

function doProctorEvent(sessionId, headers, elapsed) {
  const res = http.post(
    `${TARGET}/api/v1/event`,
    JSON.stringify({
      session_id: sessionId,
      event_type: elapsed === 0 ? 'exam_started' : 'gaze_away',
      severity: elapsed === 0 ? 'low' : 'medium',
      details: `mixed proctoring synthetic event at ${elapsed}s`,
      detection_confidence: 0.73,
    }),
    { headers, tags: { name: 'proctor_event' }, timeout: '20s' },
  )
  record(res, counts.eventOk, counts.eventFail)
  check(res, { 'event ok': (r) => r.status === 200 })
}

function doAnalyzeFrame(sessionId, headers, elapsed) {
  const res = http.post(
    `${TARGET}/api/v1/analyze-frame`,
    JSON.stringify({
      session_id: sessionId,
      frame: JPEG_B64,
      timestamp: new Date().toISOString(),
      event_type: elapsed === 0 ? 'calibration_frame' : 'periodic_frame',
    }),
    { headers, tags: { name: 'analyze_frame' }, timeout: '30s' },
  )
  record(res, counts.frameOk, counts.frameFail)
  check(res, { 'analyze frame ok': (r) => r.status === 200 })
}

function doLiveFrame(sessionId) {
  const res = http.post(
    `${TARGET}/api/v1/proctor/live-frame`,
    JSON.stringify({ session_id: sessionId, jpeg_b64: JPEG_B64 }),
    { headers: requestHeaders(), tags: { name: 'live_frame' }, timeout: '20s' },
  )
  if (res.status === 204) counts.liveFrameOk.add(1)
  else counts.liveFrameFail.add(1)
  check(res, { 'live frame accepted': (r) => r.status === 204 })
}

function doSubmit(identity, answers, headers) {
  const res = http.post(
    `${TARGET}/api/v1/submit-exam`,
    JSON.stringify({
      session_id: identity.sessionId,
      roll_number: identity.rollNumber,
      full_name: `Mixed Proctor Student ${__VU}`,
      email: `mixed${__VU}@example.com`,
      time_taken_secs: EXAM_SECONDS,
      answers,
    }),
    { headers, tags: { name: 'submit' }, timeout: '30s' },
  )
  record(res, counts.submitOk, counts.submitFail)
  check(res, { 'submit ok': (r) => r.status === 200 })

  // When the JWT submit path returns `scoring: pending`, poll the
  // session-status endpoint to verify the RQ worker drains the job.
  // This is how we actually exercise Fix #2 end-to-end.
  if (SUBMIT_MODE !== 'jwt') return
  let body
  try { body = res.json() } catch (_) { body = null }
  if (!body) return
  if (body.scoring === 'pending') {
    counts.scoringPending.add(1)
    pollSessionStatus(identity.sessionId, headers)
  } else if (typeof body.score !== 'undefined') {
    counts.inlineScored.add(1)
  }
}

function pollSessionStatus(sessionId, headers) {
  const start = Date.now()
  const maxMs = POLL_MAX_SECONDS * 1000
  while (Date.now() - start < maxMs) {
    sleep(POLL_INTERVAL_SECONDS)
    const res = http.get(
      `${TARGET}/api/v1/session-status?session_id=${encodeURIComponent(sessionId)}`,
      { headers, tags: { name: 'session_status' }, timeout: '10s' },
    )
    if (res.status === 200) {
      let body
      try { body = res.json() } catch (_) { continue }
      if (body && (body.scoring === 'done' || body.status === 'completed')) {
        scoringLatency.add(Date.now() - start)
        counts.scoringDone.add(1)
        return
      }
    }
  }
  counts.scoringTimeout.add(1)
}

function record(res, okCounter, failCounter) {
  if (res && res.status >= 200 && res.status < 300) okCounter.add(1)
  else failCounter.add(1)
}

function spreadOffset(vu, spreadSeconds, totalVus) {
  if (spreadSeconds <= 0 || totalVus <= 1) return 0
  return ((vu - 1) / Math.max(totalVus - 1, 1)) * spreadSeconds
}

function metricCount(data, name) {
  return data.metrics?.[name]?.values?.count || 0
}

function metricRate(data, name) {
  return data.metrics?.[name]?.values?.rate || 0
}

function dur(data, tag) {
  return data.metrics?.[`http_req_duration{name:${tag}}`]?.values || {}
}

export function handleSummary(data) {
  const ts = Date.now()
  return {
    [`summary-mixed-proctoring-${ts}.json`]: JSON.stringify(data, null, 2),
    stdout: textSummary(data),
  }
}

function textSummary(data) {
  return `
─────────────────────────────────────────────────
  Procta Mixed Proctoring Load Test
─────────────────────────────────────────────────
  Target:       ${TARGET}
  Auth mode:    ${AUTH_MODE}
  Submit mode:  ${SUBMIT_MODE}
  Student VUs:  ${VUS}
  Dashboard VUs:${ADMIN_TOKEN ? DASHBOARD_VUS : 0}
  Duration:     ${(data.state?.testRunDurationMs / 1000).toFixed(0)}s
  Requests:     ${data.metrics?.http_reqs?.values?.count || 0}

  Endpoint p(95):
    bulk_save:     ${(dur(data, 'bulk_save')['p(95)'] || 0).toFixed(0)}ms
    heartbeat:     ${(dur(data, 'heartbeat')['p(95)'] || 0).toFixed(0)}ms
    event:         ${(dur(data, 'proctor_event')['p(95)'] || 0).toFixed(0)}ms
    analyze_frame: ${(dur(data, 'analyze_frame')['p(95)'] || 0).toFixed(0)}ms
    live_frame:    ${(dur(data, 'live_frame')['p(95)'] || 0).toFixed(0)}ms
    submit:        ${(dur(data, 'submit')['p(95)'] || 0).toFixed(0)}ms

  Outcome counts:
    bulk_save:     ok:${metricCount(data, 'bulk_save_ok')} fail:${metricCount(data, 'bulk_save_failure')}
    heartbeat:     ok:${metricCount(data, 'heartbeat_ok')} fail:${metricCount(data, 'heartbeat_failure')}
    event:         ok:${metricCount(data, 'proctor_event_ok')} fail:${metricCount(data, 'proctor_event_failure')}
    analyze_frame: ok:${metricCount(data, 'analyze_frame_ok')} fail:${metricCount(data, 'analyze_frame_failure')}
    live_frame:    ok:${metricCount(data, 'live_frame_ok')} fail:${metricCount(data, 'live_frame_failure')}
    submit:        ok:${metricCount(data, 'submit_ok')} fail:${metricCount(data, 'submit_failure')}
    sse disconnect:${metricCount(data, 'sse_disconnects')}
${SUBMIT_MODE === 'jwt' ? `
  Scoring path (Fix #2 verification):
    async pending:   ${metricCount(data, 'scoring_pending')}  (drained: ${metricCount(data, 'scoring_done')}, timed out: ${metricCount(data, 'scoring_timeout')})
    inline returned: ${metricCount(data, 'inline_scored')}
    avg scoring latency: ${(data.metrics?.scoring_latency_ms?.values?.avg || 0).toFixed(0)}ms   p95: ${(data.metrics?.scoring_latency_ms?.values?.['p(95)'] || 0).toFixed(0)}ms
    session_status p95: ${(dur(data, 'session_status')['p(95)'] || 0).toFixed(0)}ms
` : ''}
  Errors:       ${(metricRate(data, 'http_req_failed') * 100).toFixed(2)}%
  Checks:       ${(metricRate(data, 'checks') * 100).toFixed(2)}%
─────────────────────────────────────────────────
`
}
