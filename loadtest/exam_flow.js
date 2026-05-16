/**
 * Realistic exam-load test — the headline scenario.
 *
 * Simulates N students writing a full exam: validate → bulk autosave
 * → submit. Uses PRACTICE_<vu> session IDs which the Procta
 * backend short-circuits before auth + DB writes, so the test
 * exercises the entire FastAPI pipeline (middleware → routers →
 * Pydantic → route handler → response) without polluting the DB or
 * needing JWTs. Route-level rate limits still run before the handler;
 * set LOADTEST_SECRET on both the server and this script for high-VU
 * production capacity tests.
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
import { Counter } from 'k6/metrics'

const TARGET = __ENV.TARGET || 'https://app.procta.net'
const VUS = parseInt(__ENV.VUS || '500', 10)
const DURATION_MIN = parseInt(__ENV.DURATION_MIN || '3', 10)
const SAVE_MODE = __ENV.SAVE_MODE || 'bulk'
const LOADTEST_SECRET = __ENV.LOADTEST_SECRET || ''
const REQUEST_HEADERS = LOADTEST_SECRET
  ? { 'Content-Type': 'application/json', 'X-Loadtest-Key': LOADTEST_SECRET }
  : { 'Content-Type': 'application/json' }
const bulkSaveOk = new Counter('bulk_save_ok')
const bulkSave400 = new Counter('bulk_save_400')
const bulkSave401 = new Counter('bulk_save_401')
const bulkSave403 = new Counter('bulk_save_403')
const bulkSaveRateLimited = new Counter('bulk_save_429')
const bulkSave422 = new Counter('bulk_save_422')
const bulkSaveClientError = new Counter('bulk_save_4xx')
const bulkSaveServerError = new Counter('bulk_save_5xx')
const bulkSaveTimeout = new Counter('bulk_save_timeout')
const saveAnswerOk = new Counter('save_answer_ok')
const saveAnswer400 = new Counter('save_answer_400')
const saveAnswer401 = new Counter('save_answer_401')
const saveAnswer403 = new Counter('save_answer_403')
const saveAnswerRateLimited = new Counter('save_answer_429')
const saveAnswer422 = new Counter('save_answer_422')
const saveAnswerClientError = new Counter('save_answer_4xx')
const saveAnswerServerError = new Counter('save_answer_5xx')
const saveAnswerTimeout = new Counter('save_answer_timeout')
const submitOk = new Counter('submit_ok')
const submit400 = new Counter('submit_400')
const submit401 = new Counter('submit_401')
const submit403 = new Counter('submit_403')
const submit409 = new Counter('submit_409')
const submitRateLimited = new Counter('submit_429')
const submit422 = new Counter('submit_422')
const submitClientError = new Counter('submit_4xx')
const submitServerError = new Counter('submit_5xx')
const submitTimeout = new Counter('submit_timeout')

export const options = {
  stages: [
    { duration: '1m',                  target: VUS },
    { duration: `${DURATION_MIN}m`,    target: VUS },
    { duration: '30s',                 target: 0   },
  ],
  thresholds: {
    // Bulk autosave is a background safety net, not a blocking submit.
    // Keep it under 2s p95 at load; submit remains the stricter path.
    'http_req_duration{name:bulk_save}':   ['p(95)<2000'],
    'http_req_duration{name:save_answer}': ['p(95)<500'],
    'http_req_duration{name:submit}':      ['p(95)<1500'],
    // High-VU production runs need LOADTEST_SECRET so SlowAPI does
    // not collapse all practice users into one client IP bucket.
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
    const answers = Object.fromEntries(
      QUESTION_IDS.map((q, i) => [q, ANSWER_CHOICES[(__ITER + i) % 4]])
    )

    if (SAVE_MODE === 'individual') {
      // Legacy stress path: 8 per-answer writes per iteration. This is
      // intentionally harsher than the current client, which uses bulk
      // autosave for MCQ answers.
      for (let i = 0; i < QUESTION_IDS.length; i++) {
        const saveRes = http.post(
          `${TARGET}/api/v1/save-answer`,
          JSON.stringify({
            session_id:  sessionId,
            question_id: QUESTION_IDS[i],
            answer:      answers[QUESTION_IDS[i]],
          }),
          {
            headers: REQUEST_HEADERS,
            tags: { name: 'save_answer' },
            timeout: '10s',
          }
        )
        recordOutcome('save_answer', saveRes)
        check(saveRes, {
          'save 200':       (r) => r.status === 200,
          'save practice':  (r) => r.status === 200 && jsonField(r, 'practice') === true,
        })
        sleep(0.1)
      }
    } else {
      // Current product path: local answer changes are coalesced and
      // synced through /save-answers-bulk, reducing write RPS by an
      // order of magnitude under large exams.
      const bulkRes = http.post(
        `${TARGET}/api/v1/save-answers-bulk`,
        JSON.stringify({ session_id: sessionId, answers }),
        {
          headers: REQUEST_HEADERS,
          tags: { name: 'bulk_save' },
          timeout: '20s',
        }
      )
      recordOutcome('bulk_save', bulkRes)
      check(bulkRes, {
        'bulk save 200':      (r) => r.status === 200,
        'bulk save practice': (r) => r.status === 200 && jsonField(r, 'practice') === true,
      })
    }

    // 3. Submit exam — the spike-relevant call. Includes the full
    // answers map so the server-side recalculate logic fires (even
    // though in practice mode it short-circuits to a canned response).
    const submitBody = {
      session_id:       sessionId,
      roll_number:      `PRACTICE_LOADTEST_${__VU}`,
      full_name:        `Loadtest Student ${__VU}`,
      email:            `load${__VU}@example.com`,
      time_taken_secs:  1800,
      answers,
    }
    const submitRes = http.post(
      `${TARGET}/api/v1/submit-exam`,
      JSON.stringify(submitBody),
      {
        headers: REQUEST_HEADERS,
        tags: { name: 'submit' },
        // 15s cap. Submit recalculates score + writes session row +
        // logs violation + queues scorecard email. p(95) under 1s is
        // the target; 15s is the "something is very wrong" line.
        timeout: '15s',
      }
    )
    recordOutcome('submit', submitRes)
    check(submitRes, {
      'submit 200':  (r) => r.status === 200,
      'has score':   (r) => r.status === 200 && jsonField(r, 'score') !== undefined,
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

function jsonField(res, field) {
  if (!res || res.error || !res.body || res.status === 0) {
    return undefined
  }
  try {
    return res.json(field)
  } catch (_) {
    return undefined
  }
}

function recordOutcome(endpoint, res) {
  const counters = {
    bulk_save: {
      ok: bulkSaveOk,
      badRequest: bulkSave400,
      unauthorized: bulkSave401,
      forbidden: bulkSave403,
      rateLimited: bulkSaveRateLimited,
      validation: bulkSave422,
      clientError: bulkSaveClientError,
      serverError: bulkSaveServerError,
      timeout: bulkSaveTimeout,
    },
    save_answer: {
      ok: saveAnswerOk,
      badRequest: saveAnswer400,
      unauthorized: saveAnswer401,
      forbidden: saveAnswer403,
      rateLimited: saveAnswerRateLimited,
      validation: saveAnswer422,
      clientError: saveAnswerClientError,
      serverError: saveAnswerServerError,
      timeout: saveAnswerTimeout,
    },
    submit: {
      ok: submitOk,
      badRequest: submit400,
      unauthorized: submit401,
      forbidden: submit403,
      conflict: submit409,
      rateLimited: submitRateLimited,
      validation: submit422,
      clientError: submitClientError,
      serverError: submitServerError,
      timeout: submitTimeout,
    },
  }[endpoint]

  if (!res || res.error || !res.status || res.status === 0) {
    counters.timeout.add(1)
  } else if (res.status >= 200 && res.status < 300) {
    counters.ok.add(1)
  } else if (res.status === 400) {
    counters.badRequest.add(1)
  } else if (res.status === 401) {
    counters.unauthorized.add(1)
  } else if (res.status === 403) {
    counters.forbidden.add(1)
  } else if (res.status === 409 && counters.conflict) {
    counters.conflict.add(1)
  } else if (res.status === 429) {
    counters.rateLimited.add(1)
  } else if (res.status === 422) {
    counters.validation.add(1)
  } else if (res.status >= 400 && res.status < 500) {
    counters.clientError.add(1)
  } else if (res.status >= 500) {
    counters.serverError.add(1)
  }
}

function metricCount(data, name) {
  return data.metrics?.[name]?.values?.count || 0
}

function outcomeBreakdown(data, prefix) {
  return [
    `2xx:${metricCount(data, `${prefix}_ok`)}`,
    `400:${metricCount(data, `${prefix}_400`)}`,
    `401:${metricCount(data, `${prefix}_401`)}`,
    `403:${metricCount(data, `${prefix}_403`)}`,
    `409:${metricCount(data, `${prefix}_409`)}`,
    `429:${metricCount(data, `${prefix}_429`)}`,
    `422:${metricCount(data, `${prefix}_422`)}`,
    `4xx:${metricCount(data, `${prefix}_4xx`)}`,
    `5xx:${metricCount(data, `${prefix}_5xx`)}`,
    `timeout:${metricCount(data, `${prefix}_timeout`)}`,
  ].join('  ')
}

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
    bulk_save:    ${(dur('bulk_save')['p(95)'] || 0).toFixed(0)}ms
    save_answer:  ${(dur('save_answer')['p(95)'] || 0).toFixed(0)}ms
    submit:       ${(dur('submit')['p(95)'] || 0).toFixed(0)}ms

  Status counts:
    bulk_save:    ${outcomeBreakdown(data, 'bulk_save')}
    save_answer:  ${outcomeBreakdown(data, 'save_answer')}
    submit:       ${outcomeBreakdown(data, 'submit')}

  (validate-student skipped — it bypasses practice mode and would
   hit Supabase free-tier rate limits at this VU count. Test it
   separately with a smaller VU count if needed.)

  Save mode: ${SAVE_MODE}
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
<tr><td>bulk_save</td><td><span class="${dur('bulk_save') < 500 ? 'ok' : 'bad'}">${dur('bulk_save').toFixed(0)} ms</span></td></tr>
<tr><td>save_answer</td><td><span class="${dur('save_answer') < 300 ? 'ok' : 'bad'}">${dur('save_answer').toFixed(0)} ms</span></td></tr>
<tr><td>submit</td><td><span class="${dur('submit') < 1000 ? 'ok' : 'bad'}">${dur('submit').toFixed(0)} ms</span></td></tr>
</table>
<h2>Overall</h2>
<table>
<tr><td>Failure rate</td><td><span class="${fail < 0.01 ? 'ok' : 'bad'}">${(fail * 100).toFixed(2)}%</span></td></tr>
<tr><td>Checks passed</td><td><span class="${checks > 0.99 ? 'ok' : 'bad'}">${(checks * 100).toFixed(2)}%</span></td></tr>
</table></body></html>`
}
