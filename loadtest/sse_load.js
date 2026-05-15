// SSE load test — simulates teacher dashboards watching live sessions.
// Run against a staging environment with real data.
//
// Usage:
//   k6 run -e TARGET=https://staging.procta.net -e TOKEN=<teacher_jwt> loadtest/sse_load.js

import { check, sleep } from 'k6'
import http from 'k6/http'

export const options = {
  scenarios: {
    sse_connections: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 50 },   // ramp up to 50 SSE connections
        { duration: '60s', target: 50 },   // hold at 50
        { duration: '30s', target: 0 },    // ramp down
      ],
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.05'],
    http_req_duration: ['p(95)<5000'],     // SSE init can be slow
  },
}

const TARGET = `${__ENV.TARGET || 'https://app.procta.net'}`
const TOKEN = __ENV.TOKEN || ''
const EXAM_ID = __ENV.EXAM_ID || ''

export default function () {
  // Step 1: Obtain SSE connect-token
  const tokenResp = http.post(`${TARGET}/api/v1/sse/connect-token`, JSON.stringify({
    exam_id: EXAM_ID,
  }), {
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${TOKEN}`,
    },
  })
  check(tokenResp, { 'connect-token obtained': (r) => r.status === 200 })
  if (tokenResp.status !== 200) {
    sleep(1)
    return
  }
  const connectToken = tokenResp.json('connect_token')

  // Step 2: Open SSE connection — k6 doesn't support true SSE streaming,
  // so we simulate by connecting and reading the initial response.
  const sseResp = http.get(`${TARGET}/api/v1/sse/sessions?token=${connectToken}`, {
    headers: {
      'Accept': 'text/event-stream',
      'Authorization': `Bearer ${TOKEN}`,
    },
  })
  check(sseResp, {
    'SSE connected': (r) => r.status === 200,
    'SSE has event-stream content-type': (r) => (r.headers['Content-Type'] || '').includes('text/event-stream'),
  })

  sleep(30)  // Keep connection alive for 30s
}
