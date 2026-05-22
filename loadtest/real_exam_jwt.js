/**
 * Real exam load test (Option B — production submit path).
 *
 * Unlike real_exam.js (which uses PRACTICE_* session IDs and hits an
 * in-memory shortcut), this script hits every production code path:
 *
 *   1.  exam_started event       → creates exam_sessions row (status=IN_PROGRESS)
 *   2.  bulk autosave (looped)   → real cache_autosave_snapshot + answers writes
 *   3.  heartbeat (looped)       → real last_heartbeat updates
 *   4.  submit-exam              → real score recalculation OR async enqueue
 *   5.  session-status (polled)  → verifies async scoring drains
 *
 * Prereqs:
 *
 *   # On the box that has API access:
 *   python3 loadtest/setup_test_data.py \
 *     --host https://app.procta.net \
 *     --students 500 \
 *     --teacher-email <you@example.com> \
 *     --teacher-password <…> \
 *     --questions 20
 *   # → writes loadtest/test_students.json with {exam_id, teacher_id, roll_numbers}
 *
 *   # Inside the API container (so JWT secret matches production):
 *   docker compose run --rm --entrypoint python api \
 *     scripts/mint_loadtest_tokens.py \
 *     --count 500 \
 *     --prefix LOADTEST \
 *     --teacher-id <from manifest> \
 *     --exam-id   <from manifest> \
 *     > loadtest/loadtest_tokens.json
 *
 * Run:
 *
 *   k6 run \
 *     -e TARGET=https://app.procta.net \
 *     -e TOKEN_FILE=./loadtest_tokens.json \
 *     -e VUS=500 \
 *     -e EXAM_SECONDS=300 \
 *     loadtest/real_exam_jwt.js
 *
 * The k6 binary reads TOKEN_FILE from disk at startup, so the file must
 * be present on the machine running k6 — NOT inside the API container.
 */
import http from 'k6/http'
import { check, sleep } from 'k6'
import { Counter, Trend } from 'k6/metrics'

const TARGET                    = __ENV.TARGET || 'https://app.procta.net'
const VUS                       = parseInt(__ENV.VUS || '500', 10)
const EXAM_SECONDS              = parseInt(__ENV.EXAM_SECONDS || '300', 10)
const JOIN_SPREAD_SECONDS       = parseInt(__ENV.JOIN_SPREAD_SECONDS || '120', 10)
const SUBMIT_SPREAD_SECONDS     = parseInt(__ENV.SUBMIT_SPREAD_SECONDS || '60', 10)
const AUTOSAVE_INTERVAL_SECONDS = parseInt(__ENV.AUTOSAVE_INTERVAL_SECONDS || '60', 10)
const HEARTBEAT_INTERVAL_SECONDS = parseInt(__ENV.HEARTBEAT_INTERVAL_SECONDS || '30', 10)
const POLL_INTERVAL_SECONDS     = parseFloat(__ENV.POLL_INTERVAL_SECONDS || '1.5')
// 30s default was too tight — under 3000 VU load the scoring queue
// hits ~1500 jobs and 16 workers drain ~16/s, so the tail clears in
// ~90s. Setting 60s catches the median and most of the tail; the
// remaining 10% are reported via scoringTimeout counter.
const POLL_MAX_SECONDS          = parseInt(__ENV.POLL_MAX_SECONDS || '60', 10)
const TOKEN_FILE                = __ENV.TOKEN_FILE || './loadtest_tokens.json'
// BYPASS_CF: when set, resolve the TARGET host directly to ORIGIN_IP so traffic
// skips Cloudflare. Used to distinguish a real server bottleneck from CF edge
// throttling (which kicks in when a single test machine fires thousands of
// reqs/sec at one CF-fronted hostname). A real exam has students on many IPs,
// so CF won't throttle in production — but it WILL throttle our single-Mac
// load test, producing fake timeouts.
//
//   BYPASS_CF=1 ORIGIN_IP=187.127.169.89 k6 run ...
const BYPASS_CF                 = __ENV.BYPASS_CF === '1' || __ENV.BYPASS_CF === 'true'
const ORIGIN_IP                 = __ENV.ORIGIN_IP || '187.127.169.89'

// ── Load the JWTs at startup (one-time disk read) ───────────────
// k6's `open()` runs in the init context — file is bundled into every
// VU's working set, so VUs don't re-read disk during the test.
const tokenRows = JSON.parse(open(TOKEN_FILE))
if (!Array.isArray(tokenRows) || tokenRows.length === 0) {
  throw new Error(`TOKEN_FILE ${TOKEN_FILE} did not yield a non-empty JSON array`)
}
if (tokenRows.length < VUS) {
  console.warn(
    `[real_exam_jwt] only ${tokenRows.length} tokens for ${VUS} VUs — tokens will be reused (roll collisions likely)`
  )
}

// ── Counters / Trends ───────────────────────────────────────────
const c = {
  examStart:      new Counter('exam_start_ok'),
  examStartFail:  new Counter('exam_start_fail'),
  bulkOk:         new Counter('bulk_save_ok'),
  bulkFail:       new Counter('bulk_save_fail'),
  hbOk:           new Counter('heartbeat_ok'),
  hbFail:         new Counter('heartbeat_fail'),
  submitOk:       new Counter('submit_ok'),
  submitFail:     new Counter('submit_fail'),
  submitTimeout:  new Counter('submit_timeout'),
  scoringPending: new Counter('scoring_pending'),  // Fix #2 async path taken
  scoringDone:    new Counter('scoring_done'),     // poll observed final score
  scoringTimeout: new Counter('scoring_timeout'),  // 30 s poll cap exceeded
  inlineScored:   new Counter('inline_scored'),    // legacy path (returns score immediately)
}
const scoringLatency = new Trend('scoring_latency_ms', true)

// Build the hosts map only when BYPASS_CF is on. The TARGET URL stays
// `https://app.procta.net` so the TLS SNI + Host header still claim the
// real hostname (Caddy needs them to pick the right vhost) — k6's `hosts`
// option only overrides DNS resolution, not the SNI/Host. We also need
// insecureSkipTLSVerify because the origin presents the procta.net cert
// directly which would normally be validated against CF's chain.
const hostsMap = BYPASS_CF
  ? (() => {
      const host = TARGET.replace(/^https?:\/\//, '').split('/')[0].split(':')[0]
      // Init context runs per VU — only log from VU 1 to avoid 3500x duplicate lines.
      if (typeof __VU === 'undefined' || __VU <= 1) {
        console.log(`[real_exam_jwt] BYPASS_CF=1 — resolving ${host} → ${ORIGIN_IP}`)
      }
      return { [host]: ORIGIN_IP }
    })()
  : undefined

export const options = {
  ...(hostsMap ? { hosts: hostsMap, insecureSkipTLSVerify: true } : {}),
  scenarios: {
    real_exam: {
      executor: 'per-vu-iterations',
      vus: VUS,
      iterations: 1,
      maxDuration: `${JOIN_SPREAD_SECONDS + EXAM_SECONDS + SUBMIT_SPREAD_SECONDS + POLL_MAX_SECONDS + 90}s`,
    },
  },
  // Thresholds calibrated against the 2026-05-22 3000 VU distributed
  // run (Mac+Codespace each at 1500 VUs). Below these numbers means
  // the server is healthy. Above them, something regressed.
  //
  // Submit's p95 reflects the k6 poll loop waiting for async scoring
  // to drain — not the time to return 202 (which is < 100ms). When
  // scoring queue depth grows under saturation, submit p95 climbs.
  // 12s is "Comfortable" for 3000 VUs with 16 scoring workers.
  thresholds: {
    'http_req_duration{name:bulk_save}':       ['p(95)<5000'],
    'http_req_duration{name:heartbeat}':       ['p(95)<5000'],
    'http_req_duration{name:submit}':          ['p(95)<12000'],
    'http_req_duration{name:session_status}':  ['p(95)<5000'],
    'http_req_failed':                         ['rate<0.02'],
    'checks':                                  ['rate>0.98'],
  },
}

// ── Per-VU exam lifecycle ──────────────────────────────────────
export default function () {
  const row = tokenRows[(__VU - 1) % tokenRows.length]
  const roll = row.roll_number
  const token = row.token
  // The server's CSRFMiddleware requires every state-changing request
  // (POST/PUT/PATCH/DELETE) with a Bearer token to echo the JWT's
  // `csrf` claim as X-CSRF-Token. mint_loadtest_tokens.py emits the
  // csrf alongside the token so we don't have to decode the JWT here.
  const csrf = row.csrf || ''
  // session_id format must be `${roll}_${suffix}` where suffix has NO
  // underscores — the server uses rsplit('_', 1), so an underscore in
  // the suffix would shift the inferred roll and cause a 403. Combine
  // Date.now() and __VU with a `v` separator so:
  //   - Date.now() guarantees uniqueness across test runs (no collision
  //     with the SUBMITTED-retry guard from previous runs)
  //   - the trailing __VU disambiguates VUs that hit the same ms
  const sessionId = `${roll}_${Date.now()}v${__VU}`
  const headers = {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${token}`,
  }
  if (csrf) headers['X-CSRF-Token'] = csrf
  const answers = buildAnswers(__VU)

  // 1. Join spread — fan out the exam-start events across the join window
  sleep(spreadOffset(__VU, JOIN_SPREAD_SECONDS, VUS))

  // 2. exam_started event — creates the exam_sessions row server-side
  doExamStarted(sessionId, headers)

  // 3. Autosave + heartbeat loop for the exam duration
  let elapsed = 0
  let nextHeartbeat = 0
  while (elapsed < EXAM_SECONDS) {
    doBulkSave(sessionId, answers, headers)
    if (elapsed >= nextHeartbeat) {
      doHeartbeat(sessionId, headers)
      nextHeartbeat = elapsed + HEARTBEAT_INTERVAL_SECONDS
    }
    const step = Math.min(AUTOSAVE_INTERVAL_SECONDS, EXAM_SECONDS - elapsed)
    sleep(Math.max(step, 1))
    elapsed += step
  }

  // 4. Submit spread — staggers the submit wave across all VUs
  sleep(spreadOffset(__VU, SUBMIT_SPREAD_SECONDS, VUS))
  const submitResp = doSubmit(sessionId, roll, answers, headers)

  // 5. If async scoring path was taken, poll until the worker writes
  //    the final score. This is the part of Fix #2 the test must verify.
  if (submitResp && submitResp.scoring === 'pending') {
    c.scoringPending.add(1)
    pollSessionStatus(sessionId, headers)
  } else if (submitResp && typeof submitResp.score !== 'undefined') {
    // Legacy inline path — score returned directly in the submit response.
    c.inlineScored.add(1)
  }
}

// ── Step implementations ───────────────────────────────────────

function doExamStarted(sessionId, headers) {
  const res = http.post(
    `${TARGET}/api/v1/event`,
    JSON.stringify({
      session_id: sessionId,
      event_type: 'exam_started',
      severity:   'low',
      details:    'real-exam-loadtest start',
    }),
    { headers, tags: { name: 'exam_start' }, timeout: '30s' }
  )
  if (res.status === 200) c.examStart.add(1)
  else c.examStartFail.add(1)
  check(res, { 'exam_started 200': (r) => r.status === 200 })
}
// 15s → 30s timeout (2026-05-22): exam_started writes a row to
// exam_sessions synchronously. Under 3000 VU joins, that's 25/s of
// inserts. At p99 with normal RTT this is ~5s, but from a Codespace
// (extra 200ms RTT each way + slower TLS) it can clip 15s. Bumping
// to 30s reflects what a real student's browser tolerates.

function doBulkSave(sessionId, answers, headers) {
  const res = http.post(
    `${TARGET}/api/v1/save-answers-bulk`,
    JSON.stringify({ session_id: sessionId, answers }),
    { headers, tags: { name: 'bulk_save' }, timeout: '30s' }
  )
  if (res.status === 200) c.bulkOk.add(1)
  else c.bulkFail.add(1)
  check(res, { 'bulk_save 200': (r) => r.status === 200 })
}

function doHeartbeat(sessionId, headers) {
  const res = http.post(
    `${TARGET}/api/v1/heartbeat`,
    JSON.stringify({
      session_id: sessionId,
      event_type: 'heartbeat',
      severity:   'low',
      details:    'real-exam-loadtest hb',
    }),
    { headers, tags: { name: 'heartbeat' }, timeout: '30s' }
  )
  if (res.status === 200) c.hbOk.add(1)
  else c.hbFail.add(1)
  check(res, { 'heartbeat 200': (r) => r.status === 200 })
}

function doSubmit(sessionId, roll, answers, headers) {
  const res = http.post(
    `${TARGET}/api/v1/submit-exam`,
    JSON.stringify({
      session_id:      sessionId,
      roll_number:     roll,
      full_name:       `Real Exam Student ${__VU}`,
      email:           `${roll.toLowerCase()}@loadtest.local`,
      time_taken_secs: EXAM_SECONDS,
      answers,
    }),
    { headers, tags: { name: 'submit' }, timeout: '60s' }
  )
  if (res.error || !res.status) {
    c.submitTimeout.add(1)
    return null
  }
  if (res.status >= 200 && res.status < 300) c.submitOk.add(1)
  else c.submitFail.add(1)
  check(res, { 'submit 200': (r) => r.status === 200 || r.status === 202 })
  try {
    return res.json()
  } catch (_) {
    return null
  }
}

function pollSessionStatus(sessionId, headers) {
  const start = Date.now()
  const maxMs = POLL_MAX_SECONDS * 1000
  while (Date.now() - start < maxMs) {
    sleep(POLL_INTERVAL_SECONDS)
    const res = http.get(
      `${TARGET}/api/v1/session-status?session_id=${encodeURIComponent(sessionId)}`,
      { headers, tags: { name: 'session_status' }, timeout: '10s' }
    )
    if (res.status === 200) {
      let body
      try { body = res.json() } catch (_) { continue }
      if (body && (body.scoring === 'done' || body.status === 'completed')) {
        scoringLatency.add(Date.now() - start)
        c.scoringDone.add(1)
        return
      }
    }
  }
  c.scoringTimeout.add(1)
}

// ── Helpers ────────────────────────────────────────────────────

function buildAnswers(vu) {
  // 20-question exam from setup_test_data.py. IDs are 90001..90020,
  // correct answer is always "A" so we mostly pick "A" with some noise.
  const answers = {}
  for (let i = 1; i <= 20; i++) {
    const qid = 90000 + i
    answers[String(qid)] = vu % 5 === 0 ? 'B' : 'A'
  }
  return answers
}

function spreadOffset(vu, spreadSeconds, totalVus) {
  if (spreadSeconds <= 0 || totalVus <= 1) return 0
  return ((vu - 1) / Math.max(totalVus - 1, 1)) * spreadSeconds
}

// ── Summary ────────────────────────────────────────────────────

function metricCount(data, name) {
  return data.metrics?.[name]?.values?.count || 0
}

function dur(data, tag) {
  return data.metrics?.[`http_req_duration{name:${tag}}`]?.values || {}
}

function textSummary(data) {
  const m = data.metrics
  const get = (n) => m[n]?.values || {}
  const scoringMean = (m.scoring_latency_ms?.values?.avg || 0).toFixed(0)
  const scoringP95 = (m.scoring_latency_ms?.values?.['p(95)'] || 0).toFixed(0)
  return `
─────────────────────────────────────────────────
  Procta Real Exam Load Test (Option B — JWT path)
─────────────────────────────────────────────────
  Target:        ${TARGET}
  VUs:           ${VUS}
  Tokens loaded: ${tokenRows.length}
  Exam seconds:  ${EXAM_SECONDS}
  Duration:      ${(data.state?.testRunDurationMs / 1000).toFixed(0)}s
  Requests:      ${get('http_reqs').count || 0}

  Endpoint p(95):
    exam_start:     ${(dur(data, 'exam_start')['p(95)'] || 0).toFixed(0)}ms
    bulk_save:      ${(dur(data, 'bulk_save')['p(95)'] || 0).toFixed(0)}ms
    heartbeat:      ${(dur(data, 'heartbeat')['p(95)'] || 0).toFixed(0)}ms
    submit:         ${(dur(data, 'submit')['p(95)'] || 0).toFixed(0)}ms
    session_status: ${(dur(data, 'session_status')['p(95)'] || 0).toFixed(0)}ms

  Outcome counts:
    exam_started:   ok:${metricCount(data, 'exam_start_ok')} fail:${metricCount(data, 'exam_start_fail')}
    bulk_save:      ok:${metricCount(data, 'bulk_save_ok')} fail:${metricCount(data, 'bulk_save_fail')}
    heartbeat:      ok:${metricCount(data, 'heartbeat_ok')} fail:${metricCount(data, 'heartbeat_fail')}
    submit:         ok:${metricCount(data, 'submit_ok')} fail:${metricCount(data, 'submit_fail')} timeout:${metricCount(data, 'submit_timeout')}

  Scoring path taken:
    async (Fix #2): ${metricCount(data, 'scoring_pending')}  (drained: ${metricCount(data, 'scoring_done')}  timed out: ${metricCount(data, 'scoring_timeout')})
    inline:         ${metricCount(data, 'inline_scored')}
    avg scoring latency: ${scoringMean}ms   p95: ${scoringP95}ms

  Errors:        ${((get('http_req_failed').rate || 0) * 100).toFixed(2)}%
  Checks:        ${((get('checks').rate || 0) * 100).toFixed(2)}%
─────────────────────────────────────────────────
`
}

export function handleSummary(data) {
  return {
    [`summary-real-exam-jwt-${Date.now()}.json`]: JSON.stringify(data, null, 2),
    stdout: textSummary(data),
  }
}
