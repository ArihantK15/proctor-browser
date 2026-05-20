# Procta Load Tests

Two tools in this directory, pick whichever you need:

| Tool | When to use | Fidelity | Setup time |
|---|---|---|---|
| **k6** (this README) — `smoke.js`, `exam_flow.js`, `real_exam.js`, `mixed_proctoring.js`, `submit_burst.js`, `sse_sessions.js` | Fast confidence checks, CI gates, "did I just break submit or live dashboard streams?" | Practice-mode exam scripts skip DB writes; SSE uses a real admin JWT and opens streaming connections; mixed proctoring can use synthetic JWTs for real event/frame persistence | **30 seconds**: `brew install k6 && ./run.sh smoke` |
| **Locust** — `locustfile.py` + `setup_test_data.py` | Pre-launch capacity validation, the "can we actually do 500 board exam students?" question | Full auth, real DB writes, mirrors production exactly | **10–20 minutes**: pip install locust, pre-create test data, run |

If you don't know which you need, **start with k6**. It's stupid-fast
to run and surfaces 80% of issues at 10% of the setup cost. Move to
the Locust scripts when you need the high-fidelity number to put in
a sales conversation.

## k6 quick start (this guide)

One runner, several focused scenarios, no setup hell.

## What this tests

| Script | What it does | When to run |
|---|---|---|
| `smoke.js` | 10 VUs hit `/health` + `/api/v1/billing/plans` for 30 s | Every deploy — 1-minute confidence check |
| `exam_flow.js` | Stress loop: every VU repeatedly bulk-saves and submits | Finding the API ceiling |
| `real_exam.js` | Real exam shape: staggered joins, periodic autosave, one final submit | Capacity planning and sales confidence |
| `mixed_proctoring.js` | Real exam shape plus heartbeat, proctoring events, screenshot analysis upload, live-frame upload, and optional teacher dashboard SSE streams | Proof run for the full proctoring path, not just answer save/submit |
| `submit_burst.js` | 300 students all submit within 60 s — the end-of-exam spike | Before any board-exam-scale deployment |
| `sse_sessions.js` | 100 teacher dashboard SSE streams measure connect-token success, first-event latency, stream lifetime, and disconnects | Before live-monitor or dashboard stream changes |

Most exam-path scripts use **practice mode** (`PRACTICE_*` session IDs)
which is built into the Procta backend. Practice-mode requests
exercise the full FastAPI middleware + router + Pydantic validation
+ route-handler pipeline but **skip DB writes and JWT auth**. Route
decorators still run before the practice-mode handler body, so high-VU
production runs also need `LOADTEST_SECRET` configured on the server
and passed to k6. Otherwise every virtual user looks like the same
client IP to SlowAPI.

Real-world load behaves slightly differently (DB write latency on
real submits, etc.), so for the most accurate numbers run these
against a staging deployment that's a clone of prod.

## Install

```bash
# macOS
brew install k6

# Linux (Debian/Ubuntu)
sudo gpg -k && sudo gpg --no-default-keyring \
  --keyring /usr/share/keyrings/k6-archive-keyring.gpg \
  --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys C5AD17C747E3415A3642D57D77C6C491D6AC1D69
echo "deb [signed-by=/usr/share/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" | sudo tee /etc/apt/sources.list.d/k6.list
sudo apt-get update && sudo apt-get install k6

# Windows
choco install k6
```

k6 is a single Go binary, ~30 MB. No Python, no node_modules, no Docker.

## Run

```bash
# Smoke test against staging (most common)
./run.sh smoke

# Realistic exam-load — 500 VUs, ~5 min
./run.sh exam

# Real exam shape — 500 students join over 2 min, autosave every 60s,
# then submit once over a 60s deadline window.
LOADTEST_SECRET=... VUS=500 EXAM_SECONDS=300 ./run.sh real-exam

# Safer production ramp. Requires the same LOADTEST_SECRET in the API env.
LOADTEST_SECRET=... VUS=25 DURATION_MIN=1 ./run.sh exam

# Legacy worst-case save-answer stress path.
LOADTEST_SECRET=... SAVE_MODE=individual VUS=50 DURATION_MIN=1 ./run.sh exam

# Submission spike — 300 VUs all hit submit in 60s
./run.sh burst

# Teacher live dashboard SSE streams — requires a staging admin JWT
ADMIN_TOKEN=... TARGET=https://staging.example.com ./run.sh sse

# Mixed proctoring practice-mode run:
# autosave + heartbeat + event + analyze-frame + live-frame + practice submit.
LOADTEST_SECRET=... VUS=500 EXAM_SECONDS=300 ./run.sh mixed-proctoring

# Mixed proctoring real persistence run:
# first mint synthetic student JWTs on the server/API environment:
docker compose run --rm --entrypoint python api \
  scripts/mint_loadtest_tokens.py --count 1000 --prefix MIXED \
  > /tmp/mixed_tokens.json

# then copy the token file to this loadtest directory and run:
LOADTEST_SECRET=... AUTH_MODE=jwt TOKEN_FILE=mixed_tokens.json \
  VUS=1000 EXAM_SECONDS=300 SUBMIT_MODE=off ./run.sh mixed-proctoring

# Add live teacher dashboard SSE streams to the same mixed run:
LOADTEST_SECRET=... AUTH_MODE=jwt TOKEN_FILE=mixed_tokens.json \
  ADMIN_TOKEN=... DASHBOARD_VUS=50 VUS=1000 ./run.sh mixed-proctoring

# Override target — defaults to https://app.procta.net
TARGET=http://localhost:8000 ./run.sh smoke
```

Each script writes a `summary-<scenario>-<timestamp>.json` to the
current directory. Open `summary-exam-*.html` (auto-generated) in
a browser for the pretty version with p95/p99 latency graphs.

## Interpreting results

### What k6 prints at the end

```
✓ http_req_duration..............: avg=83ms    min=12ms    med=42ms   max=2.1s    p(95)=180ms   p(99)=420ms
✓ http_req_failed................: 0.02%       ✓ 4       ✗ 19996
✓ checks.........................: 100.00%     ✓ 20000   ✗ 0
✗ http_req_duration{name:submit}.: avg=850ms   min=210ms   med=620ms  max=8.2s    p(95)=2100ms  p(99)=4500ms
```

| Metric | What it means | Pass threshold |
|---|---|---|
| `http_req_duration p(95)` | 95% of requests faster than this | <300ms for reads, <1s for submits |
| `http_req_duration p(99)` | 99% of requests faster than this | <1s for reads, <3s for submits |
| `http_req_failed` | % of requests that returned 5xx or timed out | <1% |
| `checks` | scenario-specific assertions (status code, response shape) | 100% |

If `p(95)` is high but `p(99)` is much higher, you've got tail
latency — typically Supabase rate-limiting or a cold cache. Re-run
once and see if it stabilises.

If k6 shows request timeouts around exactly 10s for `save-answer` or
15s/30s for submit, the request hit the script timeout. That is a real
capacity or routing symptom, but it is not a JSON/API-shape failure.
First confirm whether the run included `LOADTEST_SECRET`; without it,
route rate limits and reverse-proxy queues can dominate the result.

### What "500 concurrent" actually means

k6's "VUs" (virtual users) is the number of parallel goroutines
hitting the API. Each VU runs the scenario start-to-finish in a
loop. With `exam_flow.js` at 500 VUs over 5 min, you'll get roughly:

- 500 students all "in an exam" at any given moment
- bulk autosave + submit traffic that matches the current client
- Submission burst happens twice (once mid-test as some VUs cycle, once at end)

This maps cleanly to "500 students writing an exam at the same time".
Use `SAVE_MODE=individual` only when you deliberately want to stress
the legacy `/save-answer` endpoint with one request per answer.

For production capacity claims, prefer `real_exam.js`: each VU is one
student, autosave happens every `AUTOSAVE_INTERVAL_SECONDS`, and submit
happens once. `exam_flow.js` is harsher because it loops through many
complete exam attempts per VU.

For full proctoring claims, run `mixed_proctoring.js` after the answer
path is already green. In default `AUTH_MODE=practice`, it safely checks
router/middleware latency and the Redis live-frame cache path. In
`AUTH_MODE=jwt`, it uses synthetic student JWTs and synthetic session IDs
to exercise real heartbeat, proctoring event, and screenshot upload
persistence. `SUBMIT_MODE` defaults to `off` in JWT mode because scored
submit requires a complete real exam fixture; the dedicated `real_exam.js`
script should remain the source of truth for submit capacity.

## Adapting for your scenario

### Higher load — 1,000+ VUs

Edit the script:
```js
export const options = {
  stages: [
    { duration: '1m', target: 1000 },   // ramp to 1000
    { duration: '3m', target: 1000 },   // hold for 3 min
    { duration: '30s', target: 0 },     // ramp down
  ],
}
```

Past ~500 VUs you may need to raise the file descriptor limit on
the machine running k6:
```bash
ulimit -n 65536
```

### Test a specific endpoint

Edit `exam_flow.js` — each `group()` block hits one endpoint. Drop
the groups you don't care about, or duplicate a group to weight it
more heavily.

### Run against localhost

```bash
docker compose up -d
TARGET=http://localhost:8000 ./run.sh smoke
```

If you're testing the WebSocket live-frame path, k6 supports it via
`import ws from 'k6/ws'` — see the comment at the bottom of
`exam_flow.js` for a sketch. The HTTP scripts here don't cover WS
because raw bandwidth (200 KB/s per student) is the binding
constraint there, not request handling — and bandwidth is best
tested by running a real Electron client at scale, not synthetic WS.

## What practice mode actually bypasses

Important nuance — the `PRACTICE_*` session-ID short-circuit only
applies to these endpoints:

| Endpoint | Practice-mode bypass? |
|---|---|
| `POST /api/v1/save-answer` | ✅ Yes (`services/practice.py:is_practice`) |
| `POST /api/v1/save-answers-bulk` | ✅ Yes |
| `POST /api/v1/submit-exam` | ✅ Yes |
| `POST /api/v1/validate-student` | ❌ **NO** — always hits Supabase |
| `GET /api/v1/questions/{exam}` | ❌ NO |
| `POST /api/v1/auth/*` | ❌ NO |

**`exam_flow.js` deliberately skips `validate-student`** for this
reason. At 500 VUs hammering validate-student in parallel, Supabase's
free-tier rate limit kicks in immediately (1500-2500 RPS spike of
student-table SELECTs) and every request waits 60s for a connection.
That's not a Procta problem — it's the Supabase free tier doing what
it says on the tin.

Practice mode does **not** skip route decorators. For `save-answer` and
`submit-exam`, SlowAPI evaluates the request before the function body
can return the practice response. The backend has a controlled bypass:
set `LOADTEST_SECRET` in the API process and pass the same value to
k6, which sends it as `X-Loadtest-Key`. Use this only for deliberate
capacity tests, not for normal traffic.

Recommended production ramp:

```bash
# 1. Smoke the target first.
./run.sh smoke

# 2. Start below the droplet's likely ceiling and watch CPU, memory,
#    worker logs, reverse-proxy logs, and health checks.
LOADTEST_SECRET=... VUS=25 EXAM_SECONDS=180 ./run.sh real-exam
LOADTEST_SECRET=... VUS=50 EXAM_SECONDS=180 ./run.sh real-exam
LOADTEST_SECRET=... VUS=100 EXAM_SECONDS=240 ./run.sh real-exam

# 3. Only continue if p95, failure rate, and server health stay stable.
LOADTEST_SECRET=... VUS=250 EXAM_SECONDS=300 ./run.sh real-exam
LOADTEST_SECRET=... VUS=500 EXAM_SECONDS=300 ./run.sh real-exam
```

On a constrained droplet, tune the API before declaring the app broken:

```bash
# More workers if CPU has headroom; keep this near 2 x vCPU for I/O-heavy API work.
UVICORN_WORKERS=4 docker compose up -d --force-recreate api

# Optional overload protection: return fast failures instead of 10s queues.
UVICORN_LIMIT_CONCURRENCY=200 docker compose up -d --force-recreate api
```

In real life, students hit `/validate-student` once per exam-join,
spread over a 2-5 min window before the exam starts. The high-volume
calls are bulk autosave during the exam and `submit-exam` at the
deadline. The default k6 exam scenario follows that current client
behavior; set `SAVE_MODE=individual` to test the older per-answer
write pattern.

If you want to test the validate-student path under realistic load,
use the Locust setup with pre-created students (its `setup_test_data.py`
provisions real DB rows so the lookups are warm). Or upgrade Supabase
to Pro (~$25/mo, removes the rate-limit ceiling) before testing.

## What this DOESN'T test

- WebSocket live-frame upload (use `iperf3` or run real Electron
  clients on N VMs for that — see `docs/LOADTEST_WEBSOCKETS.md` if
  it exists, or just sanity-check with 10 real clients first)
- Email send throughput (Resend rate-limits separately; not a
  Procta-side concern unless `EMAIL_PROVIDER=noop` is set)
- Razorpay / Stripe webhook bursts (only test with their sandbox)
- Frontend bundle performance (use Lighthouse / WebPageTest)

## When to stop

Trust these results to plan capacity, but **don't trust them
absolutely.** A real exam has weird patterns synthetic tests miss:

- Students all log in within the first 2 minutes
- They submit clustered around the deadline
- Some hit "Submit" 3× because they didn't see the loading state
- Network conditions vary wildly (4G dropouts on phone-cam stream)

Plan to run a **real pilot at 30-50 students** before you commit
to a 500-student board exam. That catches the weird stuff.
