/**
 * SSE dashboard stream load test.
 *
 * Opens many teacher dashboard EventSource connections against
 * /api/v1/sse/sessions and measures connection success, first-event
 * latency, stream lifetime, and unexpected disconnects.
 *
 * Requirements:
 *   - TARGET: backend URL
 *   - ADMIN_TOKEN: teacher/admin JWT. Prefer staging.
 *
 * Defaults are intentionally conservative because every VU keeps a
 * streaming HTTP connection open.
 */
import http from 'k6/http'
import { check, sleep } from 'k6'
import { Trend, Counter } from 'k6/metrics'

const TARGET = __ENV.TARGET || 'https://app.procta.net'
const ADMIN_TOKEN = __ENV.ADMIN_TOKEN || ''
const VUS = parseInt(__ENV.SSE_VUS || '100', 10)
const HOLD_SECONDS = parseInt(__ENV.SSE_HOLD_SECONDS || '60', 10)

const firstEventMs = new Trend('sse_first_event_ms')
const streamLifetimeMs = new Trend('sse_stream_lifetime_ms')
const streamDisconnects = new Counter('sse_disconnects')

export const options = {
  stages: [
    { duration: '30s', target: VUS },
    { duration: `${HOLD_SECONDS}s`, target: VUS },
    { duration: '20s', target: 0 },
  ],
  thresholds: {
    sse_first_event_ms: ['p(95)<3000'],
    sse_stream_lifetime_ms: [`p(50)>${Math.min(HOLD_SECONDS, 30) * 1000}`],
    sse_disconnects: ['count<10'],
    checks: ['rate>0.95'],
  },
}

export function setup() {
  if (!ADMIN_TOKEN) {
    throw new Error('ADMIN_TOKEN is required for SSE load testing')
  }
  const res = http.post(
    `${TARGET}/api/v1/sse/connect-token`,
    null,
    { headers: { Authorization: `Bearer ${ADMIN_TOKEN}` }, tags: { name: 'sse_connect_token_setup' } },
  )
  check(res, {
    'connect-token setup ok': (r) => r.status === 200 && !!r.json('connect_token'),
  })
}

export default function () {
  const tokenRes = http.post(
    `${TARGET}/api/v1/sse/connect-token`,
    null,
    { headers: { Authorization: `Bearer ${ADMIN_TOKEN}` }, tags: { name: 'sse_connect_token' } },
  )
  if (!check(tokenRes, { 'connect-token ok': (r) => r.status === 200 && !!r.json('connect_token') })) {
    streamDisconnects.add(1)
    sleep(1)
    return
  }

  const connectToken = tokenRes.json('connect_token')
  const started = Date.now()
  let firstEventAt = 0
  const res = http.get(
    `${TARGET}/api/v1/sse/sessions?token=${encodeURIComponent(connectToken)}&max_seconds=${HOLD_SECONDS}`,
    {
      tags: { name: 'sse_sessions' },
      timeout: `${HOLD_SECONDS + 10}s`,
      responseType: 'text',
    },
  )
  const ended = Date.now()
  const body = res.body || ''
  const initIdx = body.indexOf('event: init')
  const refreshIdx = body.indexOf('event: refresh')
  const firstIdx = initIdx >= 0 ? initIdx : refreshIdx
  if (firstIdx >= 0) {
    firstEventAt = ended
    firstEventMs.add(firstEventAt - started)
  }
  streamLifetimeMs.add(ended - started)

  const ok = check(res, {
    'sse status ok': (r) => r.status === 200,
    'sse emitted event': () => firstIdx >= 0,
    'sse content-type': (r) => String(r.headers['Content-Type'] || '').includes('text/event-stream'),
  })
  if (!ok || res.status !== 200) {
    streamDisconnects.add(1)
  }
  sleep(1)
}

export function handleSummary(data) {
  const ts = Date.now()
  return {
    [`summary-sse-${ts}.json`]: JSON.stringify(data, null, 2),
    stdout: textSummary(data),
  }
}

function textSummary(data) {
  const m = data.metrics
  const first = m.sse_first_event_ms?.values || {}
  const life = m.sse_stream_lifetime_ms?.values || {}
  return `
─────────────────────────────────────────────────
  Procta SSE Load Test
─────────────────────────────────────────────────
  Target:       ${TARGET}
  VUs:          ${VUS}
  Hold:         ${HOLD_SECONDS}s
  First event:
    p(95):      ${(first['p(95)'] || 0).toFixed(0)}ms
  Stream life:
    p(50):      ${(life['p(50)'] || 0).toFixed(0)}ms
    p(95):      ${(life['p(95)'] || 0).toFixed(0)}ms
  Disconnects:  ${m.sse_disconnects?.values?.count || 0}
  Checks:       ${(((m.checks?.values?.rate || 0) * 100)).toFixed(2)}%
─────────────────────────────────────────────────
`
}
