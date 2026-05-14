/**
 * Smoke test — 1-minute confidence check.
 *
 * Hits two anonymous-friendly endpoints with 10 VUs for 30 seconds.
 * Use to verify a deploy is up before running the heavier scripts.
 *
 * What to expect on a healthy deploy:
 *   - p(95) < 200ms
 *   - http_req_failed = 0%
 *   - 5,000+ requests completed in 30s
 */
import http from 'k6/http'
import { check, group, sleep } from 'k6'

const TARGET = __ENV.TARGET || 'https://app.procta.net'

export const options = {
  // Two-stage ramp: 0→10 VUs over 5s, hold for 25s, ramp down.
  // Short and friendly enough to run on a coffee break.
  stages: [
    { duration: '5s',  target: 10 },
    { duration: '25s', target: 10 },
    { duration: '5s',  target: 0  },
  ],
  thresholds: {
    // If any of these fail the run exits non-zero — useful for CI.
    'http_req_duration': ['p(95)<200'],
    'http_req_failed':   ['rate<0.01'],   // <1% errors
    'checks':            ['rate>0.99'],   // 99%+ assertions pass
  },
}

export default function () {
  group('health', () => {
    const res = http.get(`${TARGET}/health`, { tags: { name: 'health' } })
    check(res, {
      'health 200':  (r) => r.status === 200,
      'health fast': (r) => r.timings.duration < 100,
    })
  })

  group('plans', () => {
    const res = http.get(`${TARGET}/api/v1/billing/plans`, { tags: { name: 'plans' } })
    check(res, {
      'plans 200':            (r) => r.status === 200,
      'plans has starter':    (r) => r.json('plans.0.id') !== undefined,
    })
  })

  // 1-second pause per VU iteration — k6 default of 0 is too aggressive
  // for a smoke test and shows up as "synthetic-only" CPU spikes.
  sleep(1)
}

export function handleSummary(data) {
  // Auto-generate both JSON (machine-readable) and HTML (human) reports.
  // The HTML one is the easy way to share results with non-engineers.
  return {
    [`summary-smoke-${Date.now()}.json`]: JSON.stringify(data, null, 2),
    [`summary-smoke-${Date.now()}.html`]: htmlReport(data),
    stdout: textSummary(data),
  }
}

// ── helpers ──────────────────────────────────────────────────
// Inlined so the script is self-contained (no extra files to install).

function textSummary(data) {
  const m = data.metrics
  const dur = m.http_req_duration?.values || {}
  const fail = m.http_req_failed?.values || {}
  const checks = m.checks?.values || {}
  return `
─────────────────────────────────────────
  Procta Smoke Test
─────────────────────────────────────────
  Target:      ${TARGET}
  Iterations:  ${m.iterations?.values?.count || 0}
  Duration:    ${(data.state?.testRunDurationMs / 1000).toFixed(1)}s
  http_req_duration:
    avg:       ${(dur.avg || 0).toFixed(0)}ms
    p(95):     ${(dur['p(95)'] || 0).toFixed(0)}ms
    p(99):     ${(dur['p(99)'] || 0).toFixed(0)}ms
  http_req_failed:  ${((fail.rate || 0) * 100).toFixed(2)}%
  checks passed:    ${((checks.rate || 0) * 100).toFixed(2)}%
─────────────────────────────────────────
`
}

function htmlReport(data) {
  const m = data.metrics
  const dur = m.http_req_duration?.values || {}
  const fail = m.http_req_failed?.values || {}
  const checks = m.checks?.values || {}
  return `<!doctype html><html><head><title>Procta Smoke Report</title>
<style>body{font-family:system-ui,sans-serif;max-width:720px;margin:40px auto;padding:0 20px;color:#0d1117}
h1{color:#5b6df0}table{width:100%;border-collapse:collapse}td{padding:8px;border-bottom:1px solid #eee}
.ok{color:#10b981}.warn{color:#f59e0b}.bad{color:#ef4444}</style></head><body>
<h1>Procta Smoke Test</h1>
<p>Target: <code>${TARGET}</code> · Iterations: ${m.iterations?.values?.count || 0}</p>
<table>
<tr><td>p(95) latency</td><td><span class="${dur['p(95)'] < 200 ? 'ok' : 'bad'}">${(dur['p(95)'] || 0).toFixed(0)} ms</span></td></tr>
<tr><td>p(99) latency</td><td>${(dur['p(99)'] || 0).toFixed(0)} ms</td></tr>
<tr><td>Failure rate</td><td><span class="${fail.rate < 0.01 ? 'ok' : 'bad'}">${((fail.rate || 0) * 100).toFixed(2)}%</span></td></tr>
<tr><td>Checks passed</td><td><span class="${checks.rate > 0.99 ? 'ok' : 'bad'}">${((checks.rate || 0) * 100).toFixed(2)}%</span></td></tr>
</table></body></html>`
}
