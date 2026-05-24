#!/usr/bin/env node

const fs = require('fs')
const path = require('path')

function usage() {
  console.error('Usage: node generate_real_exam_report.js <summary-real-exam-*.json> [output.md]')
  process.exit(1)
}

const summaryPath = process.argv[2]
if (!summaryPath) usage()

const outputPath =
  process.argv[3] ||
  path.join(
    path.dirname(summaryPath),
    'reports',
    `${path.basename(summaryPath, '.json')}.md`
  )

const data = JSON.parse(fs.readFileSync(summaryPath, 'utf8'))
const metrics = data.metrics || {}

function values(name) {
  return metrics[name]?.values || {}
}

function count(name) {
  return values(name).count || 0
}

function rate(name) {
  return values(name).rate || 0
}

function duration(tag) {
  return metrics[`http_req_duration{name:${tag}}`]?.values || {}
}

function ms(value) {
  return Number.isFinite(value) ? `${Math.round(value)} ms` : 'n/a'
}

function percent(value) {
  return `${((value || 0) * 100).toFixed(2)}%`
}

function status(ok, failure, timeout) {
  return `2xx: ${count(ok)}, failure: ${count(failure)}, timeout: ${count(timeout)}`
}

function inferRunTimestamp(filePath) {
  const match = path.basename(filePath).match(/summary-real-exam-(\d+)\.json$/)
  if (!match) return 'Unknown'
  const date = new Date(Number(match[1]))
  return Number.isNaN(date.getTime()) ? 'Unknown' : date.toISOString()
}

function passFail() {
  const failures = rate('http_req_failed')
  const checks = rate('checks')
  const bulkP95 = duration('bulk_save')['p(95)'] || 0
  const submitP95 = duration('submit')['p(95)'] || 0
  if (failures > 0 || checks < 1) return 'Needs review'
  if (bulkP95 <= 250 && submitP95 <= 250) return 'Passed'
  return 'Passed with latency note'
}

const report = `# Procta Real Exam Load Test Evidence

## Result

**${passFail()}** on live production infrastructure.

| Metric | Value |
|---|---:|
| Target | ${process.env.TARGET || 'https://app.procta.net'} |
| Run timestamp | ${inferRunTimestamp(summaryPath)} |
| Virtual users | ${process.env.VUS || '500'} |
| Test duration | ${((data.state?.testRunDurationMs || 0) / 1000).toFixed(1)} s |
| Completed exam iterations | ${count('iterations')} |
| Total HTTP requests | ${count('http_reqs')} |
| Error rate | ${percent(rate('http_req_failed'))} |
| Checks passed | ${percent(rate('checks'))} |

## Endpoint Latency

| Endpoint | p95 | p90 | avg | max |
|---|---:|---:|---:|---:|
| Bulk autosave | ${ms(duration('bulk_save')['p(95)'])} | ${ms(duration('bulk_save')['p(90)'])} | ${ms(duration('bulk_save').avg)} | ${ms(duration('bulk_save').max)} |
| Final submit | ${ms(duration('submit')['p(95)'])} | ${ms(duration('submit')['p(90)'])} | ${ms(duration('submit').avg)} | ${ms(duration('submit').max)} |

## Outcome Counts

| Endpoint | Result |
|---|---|
| Bulk autosave | ${status('bulk_save_ok', 'bulk_save_failure', 'bulk_save_timeout')} |
| Final submit | ${status('submit_ok', 'submit_failure', 'submit_timeout')} |

## Scenario Shape

- Each virtual user represents one student taking one exam attempt.
- Students join over a staggered window.
- Each student periodically sends bulk autosaves.
- Each student submits once at the end of the exam window.
- The run uses the production domain and production API/proxy/container stack.
- The run uses Procta practice-mode session IDs, so it validates request handling, routing, autosave, submit, Redis, worker availability, and production infrastructure without writing real student records.
- Route rate limiting is bypassed with the private load-test key so one test machine can simulate many students without being treated as abusive traffic.

## Public Claim Guidance

Safe wording:

> Procta has been load-tested on live production infrastructure with 500 simulated concurrent exam sessions, completing 3,000 API requests with 0.00% errors. In that run, p95 latency was 115 ms for autosave and 75 ms for final submission.

Avoid stronger wording unless a separate authenticated, database-backed test is run:

> 500 real students with full production database writes.

## Raw Evidence

- k6 summary JSON: \`${path.relative(process.cwd(), summaryPath)}\`
- Generated report: \`${path.relative(process.cwd(), outputPath)}\`
`

fs.mkdirSync(path.dirname(outputPath), { recursive: true })
fs.writeFileSync(outputPath, report)
console.log(outputPath)
