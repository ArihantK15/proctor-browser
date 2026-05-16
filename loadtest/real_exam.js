/**
 * Real exam shape.
 *
 * Each VU represents one student for one exam attempt:
 *   join spread -> periodic bulk autosave -> one final submit
 *
 * This is the scenario to use for capacity planning. It avoids the
 * harsher stress-test loop where every VU repeatedly submits exams.
 */
import http from 'k6/http'
import { check, sleep } from 'k6'
import { Counter } from 'k6/metrics'

const TARGET = __ENV.TARGET || 'https://app.procta.net'
const VUS = parseInt(__ENV.VUS || '500', 10)
const EXAM_SECONDS = parseInt(__ENV.EXAM_SECONDS || '300', 10)
const JOIN_SPREAD_SECONDS = parseInt(__ENV.JOIN_SPREAD_SECONDS || '120', 10)
const AUTOSAVE_INTERVAL_SECONDS = parseInt(__ENV.AUTOSAVE_INTERVAL_SECONDS || '60', 10)
const SUBMIT_SPREAD_SECONDS = parseInt(__ENV.SUBMIT_SPREAD_SECONDS || '60', 10)
const LOADTEST_SECRET = __ENV.LOADTEST_SECRET || ''
const REQUEST_HEADERS = LOADTEST_SECRET
  ? { 'Content-Type': 'application/json', 'X-Loadtest-Key': LOADTEST_SECRET }
  : { 'Content-Type': 'application/json' }

const bulkSaveOk = new Counter('bulk_save_ok')
const bulkSaveFailure = new Counter('bulk_save_failure')
const bulkSaveTimeout = new Counter('bulk_save_timeout')
const submitOk = new Counter('submit_ok')
const submitFailure = new Counter('submit_failure')
const submitTimeout = new Counter('submit_timeout')

export const options = {
  scenarios: {
    real_exam: {
      executor: 'per-vu-iterations',
      vus: VUS,
      iterations: 1,
      maxDuration: `${JOIN_SPREAD_SECONDS + EXAM_SECONDS + SUBMIT_SPREAD_SECONDS + 90}s`,
    },
  },
  thresholds: {
    'http_req_duration{name:bulk_save}': ['p(95)<3000'],
    'http_req_duration{name:submit}': ['p(95)<3000'],
    'http_req_failed': ['rate<0.02'],
    'checks': ['rate>0.98'],
  },
}

const QUESTION_IDS = ['q1', 'q2', 'q3', 'q4', 'q5', 'q6', 'q7', 'q8']
const ANSWER_CHOICES = ['A', 'B', 'C', 'D']

export default function () {
  const sessionId = `PRACTICE_REALEXAM_${__VU}`
  const rollNumber = `PRACTICE_REALEXAM_${__VU}`
  const answers = Object.fromEntries(
    QUESTION_IDS.map((q, i) => [q, ANSWER_CHOICES[(__VU + i) % 4]])
  )

  sleep(spreadOffset(__VU, JOIN_SPREAD_SECONDS, VUS))

  let elapsed = 0
  while (elapsed < EXAM_SECONDS) {
    doBulkSave(sessionId, answers)
    const step = Math.min(AUTOSAVE_INTERVAL_SECONDS, EXAM_SECONDS - elapsed)
    sleep(step)
    elapsed += step
  }

  sleep(spreadOffset(__VU, SUBMIT_SPREAD_SECONDS, VUS))
  doSubmit(sessionId, rollNumber, answers)
}

function doBulkSave(sessionId, answers) {
  const res = http.post(
    `${TARGET}/api/v1/save-answers-bulk`,
    JSON.stringify({ session_id: sessionId, answers }),
    {
      headers: REQUEST_HEADERS,
      tags: { name: 'bulk_save' },
      timeout: '20s',
    }
  )
  recordOutcome(res, bulkSaveOk, bulkSaveFailure, bulkSaveTimeout)
  check(res, {
    'bulk save 200': (r) => r.status === 200,
    'bulk save practice': (r) => r.status === 200 && jsonField(r, 'practice') === true,
  })
}

function doSubmit(sessionId, rollNumber, answers) {
  const res = http.post(
    `${TARGET}/api/v1/submit-exam`,
    JSON.stringify({
      session_id: sessionId,
      roll_number: rollNumber,
      full_name: `Real Exam Student ${__VU}`,
      email: `realexam${__VU}@example.com`,
      time_taken_secs: EXAM_SECONDS,
      answers,
    }),
    {
      headers: REQUEST_HEADERS,
      tags: { name: 'submit' },
      timeout: '20s',
    }
  )
  recordOutcome(res, submitOk, submitFailure, submitTimeout)
  check(res, {
    'submit 200': (r) => r.status === 200,
    'has score': (r) => r.status === 200 && jsonField(r, 'score') !== undefined,
  })
}

function spreadOffset(vu, spreadSeconds, totalVus) {
  if (spreadSeconds <= 0 || totalVus <= 1) return 0
  return ((vu - 1) / Math.max(totalVus - 1, 1)) * spreadSeconds
}

function recordOutcome(res, okCounter, failureCounter, timeoutCounter) {
  if (!res || res.error || !res.status || res.status === 0) {
    timeoutCounter.add(1)
  } else if (res.status >= 200 && res.status < 300) {
    okCounter.add(1)
  } else {
    failureCounter.add(1)
  }
}

function jsonField(res, field) {
  if (!res || res.error || !res.body || res.status === 0) return undefined
  try {
    return res.json(field)
  } catch (_) {
    return undefined
  }
}

function metricCount(data, name) {
  return data.metrics?.[name]?.values?.count || 0
}

function textSummary(data) {
  const m = data.metrics
  const get = (name) => m[name]?.values || {}
  const dur = (tag) => m[`http_req_duration{name:${tag}}`]?.values || get('http_req_duration')
  return `
─────────────────────────────────────────────────
  Procta Real Exam Load Test
─────────────────────────────────────────────────
  Target:       ${TARGET}
  VUs:          ${VUS}
  Exam seconds: ${EXAM_SECONDS}
  Join spread:  ${JOIN_SPREAD_SECONDS}s
  Submit spread:${SUBMIT_SPREAD_SECONDS}s
  Duration:     ${(data.state?.testRunDurationMs / 1000).toFixed(0)}s
  Iters:        ${get('iterations').count || 0}
  Requests:     ${get('http_reqs').count || 0}

  Per-endpoint p(95) latency:
    bulk_save:  ${(dur('bulk_save')['p(95)'] || 0).toFixed(0)}ms
    submit:     ${(dur('submit')['p(95)'] || 0).toFixed(0)}ms

  Outcome counts:
    bulk_save:  2xx:${metricCount(data, 'bulk_save_ok')}  failure:${metricCount(data, 'bulk_save_failure')}  timeout:${metricCount(data, 'bulk_save_timeout')}
    submit:     2xx:${metricCount(data, 'submit_ok')}  failure:${metricCount(data, 'submit_failure')}  timeout:${metricCount(data, 'submit_timeout')}

  Errors:       ${((get('http_req_failed').rate || 0) * 100).toFixed(2)}%
  Checks:       ${((get('checks').rate || 0) * 100).toFixed(2)}%
─────────────────────────────────────────────────
`
}

export function handleSummary(data) {
  return {
    [`summary-real-exam-${Date.now()}.json`]: JSON.stringify(data, null, 2),
    stdout: textSummary(data),
  }
}
