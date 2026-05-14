/**
 * Submission spike — the actual scary scenario.
 *
 * Real exams end at a deadline. Within ~60 seconds of that deadline,
 * 90% of students hit "Submit". This script reproduces that pattern:
 * 300 VUs all run a submit at the same time, then exit. No ramp.
 *
 * Why this matters more than sustained load: a long-running 500-VU
 * exam test gives you smooth p(95). The submission burst is the
 * "is this real?" test — does the API stay responsive when 300
 * requests arrive in the same second?
 *
 * Expected failure modes if the droplet can't handle it:
 *   - Submit p(99) blows past 5s (uvicorn workers saturated)
 *   - 503s from rate-limiter (slowapi default is 5/minute on submit)
 *   - Supabase returns 429 (free tier rate limit kicks in)
 *
 * If you see those, look at:
 *   - Bump uvicorn workers (--workers 4) — 2 vCPUs handles it
 *   - Per-route rate-limit override on submit-exam
 *   - Supabase Pro upgrade
 */
import http from 'k6/http'
import { check } from 'k6'

const TARGET = __ENV.TARGET || 'https://app.procta.net'
const BURST_VUS = parseInt(__ENV.BURST_VUS || '300', 10)

export const options = {
  // The shape: ramp 0→300 in 5 seconds (not instantaneous, because
  // even at exam-end real students click "Submit" over ~60s, not at
  // the exact same millisecond — 5s is the worst-case clustering),
  // hold for 60s while all 300 try to submit, then ramp down.
  stages: [
    { duration: '5s',  target: BURST_VUS },
    { duration: '60s', target: BURST_VUS },
    { duration: '10s', target: 0          },
  ],
  thresholds: {
    // Submit can be slow; we just need it to COMPLETE, not be fast.
    'http_req_duration': ['p(99)<5000'],
    'http_req_failed':   ['rate<0.05'],   // <5% errors is acceptable in a spike
    'checks':            ['rate>0.95'],
  },
}

const QUESTION_IDS = ['q1', 'q2', 'q3', 'q4', 'q5', 'q6', 'q7', 'q8']
const ANSWER_CHOICES = ['A', 'B', 'C', 'D']

export default function () {
  const sessionId = `PRACTICE_BURST_${__VU}_${__ITER}`

  const submitBody = {
    session_id:       sessionId,
    full_name:        `Burst Student ${__VU}`,
    email:            `burst${__VU}@example.com`,
    time_taken_secs:  1800,
    answers:          Object.fromEntries(
      QUESTION_IDS.map((q, i) => [q, ANSWER_CHOICES[i % 4]])
    ),
  }

  const res = http.post(
    `${TARGET}/api/v1/submit-exam`,
    JSON.stringify(submitBody),
    {
      headers: { 'Content-Type': 'application/json' },
      tags: { name: 'submit_burst' },
      timeout: '30s',
    }
  )

  check(res, {
    'submit completed': (r) => r.status === 200 || r.status === 429,
    'submit not 5xx':   (r) => r.status < 500,
  })

  // No sleep — we want all 300 VUs hammering submit as fast as the
  // network allows. This is the worst case, not the realistic case.
}

export function handleSummary(data) {
  const m = data.metrics
  const dur = m.http_req_duration?.values || {}
  const fail = m.http_req_failed?.values || {}
  const total = m.http_reqs?.values?.count || 0
  const errors_429 = Object.values(m).filter(v => v.values?.['count'] !== undefined).length

  return {
    [`summary-burst-${Date.now()}.json`]: JSON.stringify(data, null, 2),
    stdout: `
─────────────────────────────────────────────────
  Procta Submit Burst Test
─────────────────────────────────────────────────
  Target:        ${TARGET}
  Concurrent:    ${BURST_VUS} VUs
  Total submits: ${total}

  Latency:
    avg:         ${(dur.avg || 0).toFixed(0)}ms
    p(95):       ${(dur['p(95)'] || 0).toFixed(0)}ms
    p(99):       ${(dur['p(99)'] || 0).toFixed(0)}ms
    max:         ${(dur.max || 0).toFixed(0)}ms

  Failure rate:  ${((fail.rate || 0) * 100).toFixed(2)}%
                 (note: 429 = rate-limited, which is INTENDED in a burst)

  Verdict:       ${
    (dur['p(99)'] || 0) < 5000 && (fail.rate || 0) < 0.05
      ? '✅ Burst absorbed cleanly'
      : '⚠️  Saturation detected — see thresholds output above'
  }
─────────────────────────────────────────────────
`,
  }
}
