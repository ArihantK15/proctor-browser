/**
 * Realistic exam-load test — the headline scenario.
 *
 * Simulates N students writing a full exam: validate → save-answer
 * × 8 → submit. Uses PRACTICE_<vu> session IDs which the Procta
 * backend short-circuits before auth + DB writes, so the test
 * exercises the entire FastAPI pipeline (middleware → routers →
 * Pydantic → rate-limit → response) without polluting the DB or
 * needing JWTs.
 *
 * Default ramp: 0 → 500 VUs over 1 min, hold 3 min, ramp down 30s.
 * Override VUS or DURATION_MIN env vars to tune.
 *
 * What to expect on a 4 GiB / 2 vCPU droplet:
 *   - p(95) save-answer:  <300ms
 *   - p(95) submit:       <1s
 *   - http_req_failed:    <1%
 *   - ~150,000 total requests over 4-5 min
 */
import http from 'k6/http'
import { check, group, sleep } from 'k6'

const TARGET = __ENV.TARGET || 'https://app.procta.net'
const VUS = parseInt(__ENV.VUS || '500', 10)
const DURATION_MIN = parseInt(__ENV.DURATION_MIN || '3', 10)

export const options = {
  stages: [
    { duration: '1m',                  target: VUS },
    { duration: `${DURATION_MIN}m`,    target: VUS },
    { duration: '30s',                 target: 0   },
  ],
  thresholds: {
    'http_req_duration{name:save_answer}': ['p(95)<300'],
    'http_req_duration{name:submit}':      ['p(95)<1500'],
    // Practice-mode endpoints aren't rate-limited (slowapi only
    // gates after auth, and practice mode short-circuits before
    // auth) so a real failure here means the API actually choked.
    'http_req_failed':                     ['rate<0.02'],
    'checks':                              ['rate>0.98'],
  },
}

// Pre-generated answer payloads. Cheaper than randomising per
// request because k6 is single-threaded per VU and string allocation
// shows up as noise at this scale.
const QUESTION_IDS = ['q1', 'q2', 'q3', 'q4', 'q5', 'q6', 'q7', 'q8']
const ANSWER_CHOICES = ['A', 'B', 'C', 'D']

export default function () {
  // Each VU gets a unique practice session_id. PRACTICE_ prefix
  // short-circuits auth + DB writes in the Procta backend
  // (services/practice.py:is_practice). NOTE: practice-mode bypass
  // is ONLY active on save-answer / save-answers-bulk / submit-exam.
  // /api/v1/validate-student always hits Supabase — so we skip it
  // here and jump straight to the high-volume calls. validate-student
  // runs once per student per exam in real life; the spike scenario
  // is save-answer + submit happening in bulk.
  const sessionId = `PRACTICE_LOADTEST_${__VU}_${__ITER}`

  group('exam_lifecycle', () => {
    // 2. Save answers — 8 in a row, simulating a student working
    // through the exam. Real students click "next" every 30-60s but
    // for load purposes we hit the endpoint as fast as the network
    // allows; that's the more punishing case.
    for (let i = 0; i < QUESTION_IDS.length; i++) {
      const saveRes = http.post(
        `${TARGET}/api/v1/save-answer`,
        JSON.stringify({
          session_id:  sessionId,
          question_id: QUESTION_IDS[i],
          answer:      ANSWER_CHOICES[(__ITER + i) % 4],
        }),
        {
          headers: { 'Content-Type': 'application/json' },
          tags: { name: 'save_answer' },
          // 10s hard cap. save-answer is a single Supabase upsert
          // (or a no-op in practice mode); anything over 10s means
          // the server is starved — fail fast rather than queue.
          timeout: '10s',
        }
      )
      check(saveRes, {
        'save 200':       (r) => r.status === 200,
        'save practice':  (r) => r.json('practice') === true,
      })
      // 100ms between answers — realistic student typing cadence is
      // 5-30s but at sub-second pace we generate maximum sustained
      // load on the endpoint. Adjust upward if you want to model
      // "what a normal exam window looks like" rather than "what's
      // the ceiling".
      sleep(0.1)
    }

    // 3. Submit exam — the spike-relevant call. Includes the full
    // answers map so the server-side recalculate logic fires (even
    // though in practice mode it short-circuits to a canned response).
    const submitBody = {
      session_id:       sessionId,
      full_name:        `Loadtest Student ${__VU}`,
      email:            `load${__VU}@example.com`,
      time_taken_secs:  1800,
      answers:          Object.fromEntries(
        QUESTION_IDS.map((q, i) => [q, ANSWER_CHOICES[(__ITER + i) % 4]])
      ),
    }
    const submitRes = http.post(
      `${TARGET}/api/v1/submit-exam`,
      JSON.stringify(submitBody),
      {
        headers: { 'Content-Type': 'application/json' },
        tags: { name: 'submit' },
        // 15s cap. Submit recalculates score + writes session row +
        // logs violation + queues scorecard email. p(95) under 1s is
        // the target; 15s is the "something is very wrong" line.
        timeout: '15s',
      }
    )
    check(submitRes, {
      'submit 200':  (r) => r.status === 200,
      'has score':   (r) => r.json('score') !== undefined,
    })
  })

  // 2-second pause between iterations. Without this, k6 cycles each
  // VU back to start instantly and you measure the API's burst
  // capacity, not its sustained capacity. Tune down for stress test.
  sleep(2)
}

export function handleSummary(data) {
  return {
    [`summary-exam-${Date.now()}.json`]: JSON.stringify(data, null, 2),
    [`summary-exam-${Date.now()}.html`]: htmlReport(data),
    stdout: textSummary(data),
  }
}

// ── Optional: WebSocket live-frame sketch ──────────────────────
// Uncomment to ALSO push synthetic JPEG frames during the exam.
// Significantly more bandwidth-heavy; only use against staging.
//
// import ws from 'k6/ws'
// const JPEG = open('./fixture.jpg', 'b')  // ~50 KB fixture file
// ws.connect(`${TARGET.replace('http', 'ws')}/ws/v1/live-frame/${sessionId}`,
//   {}, (socket) => {
//     socket.setInterval(() => socket.sendBinary(JPEG), 1000)
//     socket.setTimeout(() => socket.close(), 60_000)
//   })

// ── helpers (same as smoke.js for consistency) ────────────────

function textSummary(data) {
  const m = data.metrics
  const get = (name) => m[name]?.values || {}
  const dur = (tag) => {
    const k = `http_req_duration{name:${tag}}`
    return m[k]?.values || get('http_req_duration')
  }
  return `
─────────────────────────────────────────────────
  Procta Exam Load Test
─────────────────────────────────────────────────
  Target:    ${TARGET}
  VUs:       ${VUS}
  Duration:  ${(data.state?.testRunDurationMs / 1000).toFixed(0)}s
  Iters:     ${get('iterations').count || 0}
  Requests:  ${get('http_reqs').count || 0}

  Per-endpoint p(95) latency:
    save_answer:  ${(dur('save_answer')['p(95)'] || 0).toFixed(0)}ms
    submit:       ${(dur('submit')['p(95)'] || 0).toFixed(0)}ms

  (validate-student skipped — it bypasses practice mode and would
   hit Supabase free-tier rate limits at this VU count. Test it
   separately with a smaller VU count if needed.)

  Errors:    ${((get('http_req_failed').rate || 0) * 100).toFixed(2)}%
  Checks:    ${((get('checks').rate || 0) * 100).toFixed(2)}%
─────────────────────────────────────────────────
`
}

function htmlReport(data) {
  const m = data.metrics
  const dur = (tag) => {
    const k = `http_req_duration{name:${tag}}`
    return (m[k]?.values || m.http_req_duration?.values || {})['p(95)'] || 0
  }
  const fail = m.http_req_failed?.values?.rate || 0
  const checks = m.checks?.values?.rate || 0
  return `<!doctype html><html><head><title>Procta Exam Load Report</title>
<style>body{font-family:system-ui,sans-serif;max-width:720px;margin:40px auto;padding:0 20px;color:#0d1117}
h1{color:#5b6df0}table{width:100%;border-collapse:collapse}td{padding:8px;border-bottom:1px solid #eee}
.ok{color:#10b981}.bad{color:#ef4444}</style></head><body>
<h1>Procta Exam Load Test</h1>
<p>Target: <code>${TARGET}</code> · VUs: ${VUS} · Iterations: ${m.iterations?.values?.count || 0}</p>
<h2>Per-endpoint p(95) latency</h2>
<table>
<tr><td>save_answer</td><td><span class="${dur('save_answer') < 300 ? 'ok' : 'bad'}">${dur('save_answer').toFixed(0)} ms</span></td></tr>
<tr><td>submit</td><td><span class="${dur('submit') < 1000 ? 'ok' : 'bad'}">${dur('submit').toFixed(0)} ms</span></td></tr>
</table>
<h2>Overall</h2>
<table>
<tr><td>Failure rate</td><td><span class="${fail < 0.01 ? 'ok' : 'bad'}">${(fail * 100).toFixed(2)}%</span></td></tr>
<tr><td>Checks passed</td><td><span class="${checks > 0.99 ? 'ok' : 'bad'}">${(checks * 100).toFixed(2)}%</span></td></tr>
</table></body></html>`
}
