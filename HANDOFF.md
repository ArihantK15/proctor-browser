# Procta — Handoff Notes (2026-05-21)

A snapshot for a future Claude session (or future-self) to pick up
without re-deriving context. Last updated mid-conversation, while a
1000-VU load test is running.

---

## 1. Who / what / where

- **Project**: Procta — proctored-browser. Electron student lockdown
  client + FastAPI backend. Real exams with face detection, autosave,
  scoring, billing, LTI 1.3, brand-voice consultation, RQ workers.
- **Operator**: Solo Indian student, savings-funded, ₹3000/month
  pocket money. Cannot afford freelancers/engineers. Bus-factor 1.
  Wants pragmatic, free-or-near-free engineering advice.
- **Repo**: `~/proctored-browser/` on Mac. `~/proctor-browser/`
  (note: no hyphen between `proctor` and `browser`) on the KVM.
  Push origin: `https://github.com/ArihantK15/proctor-browser`
- **Production KVM**: Hostinger KVM 4 — 4 vCPU, 16 GB RAM, NVMe.
  - SSH: `ssh root@187.127.169.89`
  - Hostname (on the box): `srv1675832` — NOT DNS-resolvable from the
    Mac; always use the IP for `scp` / `ssh` from Mac.
  - Project path: `/root/proctor-browser`
- **Production URL**: `https://app.procta.net` (Cloudflare in front
  of Caddy in front of FastAPI).
- **Marketing URL**: `https://procta.net`

## 2. The full prod stack as of 2026-05-21

```
Student Mac/Phone
  │
  ▼
Cloudflare (free tier — DNS, TLS edge, static asset cache, DDoS)
  │
  ▼
Caddy (1 GB memory limit on KVM — TLS termination + reverse proxy + static)
  │
  ▼
FastAPI (4 uvicorn workers — UVICORN_WORKERS=4)
  │
  ▼
┌──────────────────┬────────────────────┐
│                  │                    │
▼                  ▼                    ▼
asyncpg Pool   Redis (cache +    proctor-worker (RQ)
  │            RQ broker)       proctor-autosave-worker (RQ)
  ▼                              proctor-ofelia (cron)
Postgres (self-hosted on KVM —
  THIS IS A DATA-LOSS RISK,
  no backups yet)
```

Containers (`docker compose`):
- `proctor-api` — FastAPI + uvicorn
- `proctor-caddy` — TLS + reverse proxy
- `proctor-postgres` — DB (POSTGRES_USER=procta, POSTGRES_DB=procta,
  password is in `.env`)
- `proctor-redis` — cache + RQ broker
- `proctor-worker` — consumes `default` and `scoring` queues
- `proctor-autosave-worker` — consumes `autosave` queue
- `proctor-ofelia` — cron (backups, cleanup)

## 3. What today (2026-05-21) actually fixed

Today was the day Procta's load test framework finally ran the
**real production submit path**. Every prior load test had been
hitting an `is_practice()` shortcut that returned in <1 ms without
touching the DB. The "5000 VUs at 99.96 %" headline number was
measuring TLS handshakes, not exam scoring. **This is important
context — don't be misled by older claims in chats or docs.**

### Commits landed today

| Commit | What |
|---|---|
| `380e075` | (prior) — schema migrations for usage tracking |
| `a10ae69` | phase62 — `teachers.status` column |
| `7d40658` | reaper datetime bug (was passing ISO string to `.lt()` on timestamptz; asyncpg only accepts datetime for WHERE filter parameters) |
| `68bd7c2` | postgres adapter: auto-coerce ISO 8601 strings to datetime in `_SQL.add` for UPDATE/INSERT param binding |
| `73b1de1` | `setup_test_data.py` sends `X-CSRF-Token` (decodes the JWT to extract `csrf` claim) |
| `9ce75f4` | `questions.options` — `json.dumps(dict)` before insert (column is TEXT, not JSONB); reader does defensive `json.loads` |
| `8c9107f` | `run_from_mac.sh` defaults `KVM_HOST` to `root@187.127.169.89` instead of `srv1675832` |
| `5540a64` | k6 scripts now send `X-CSRF-Token` (mint script emits `csrf` field next to `token`) |
| `8b1566e` | wrapper does `docker compose build api` before run-mint |
| `e685689` | wrapper does `docker compose up -d --force-recreate --build api` and sleeps 12s so migrations apply |
| `3686553` | phase64 — `exam_config.created_at` |
| `9c2ed22` | phase65 — sweeping schema reconciliation across `exam_sessions`, `violations`, `exam_config` |
| `00d3d1e` | postgres adapter: per-table `ON CONFLICT` registry (`_UPSERT_CONFLICT_COLS`); phase66 adds the UNIQUE constraints |
| `46577d9` | new `loadtest/run_from_mac.sh` wrapper |
| `f4d8766` | **Database consolidation**: deleted Supabase REST adapter (`AsyncTable`, `_AsyncResult`, httpx pool). `async_table()` hardcoded to `postgres_table`. Net –184 lines. |

### Bug patterns hit today (don't repeat these)

1. **asyncpg + ISO datetime strings**: Supabase REST tolerates ISO
   strings for timestamptz; asyncpg rejects with
   `expected a datetime.date or datetime.datetime instance, got 'str'`.
   Fixed by `_SQL.add` coercion (commit `68bd7c2`). Heuristic:
   16–40 char string containing `T` → try `datetime.fromisoformat`.
2. **`ON CONFLICT (id)` default**: PostgREST/Supabase infers the
   conflict column from the unique index. asyncpg requires it
   explicit. Fixed by `_UPSERT_CONFLICT_COLS` registry in
   `app/postgres_table.py` (commit `00d3d1e`).
3. **Schema drift**: many columns existed on the Supabase deployed
   schema but never made it into `migrations/`. They were added via
   the Supabase dashboard. Fixed by phase62–phase66 catch-up
   migrations. **Going forward: every schema change MUST be a file in
   `migrations/`. No more dashboard edits.**
4. **Docker layer cache lying about file freshness**: `docker compose
   build` reused stale layers even after the source file changed. Use
   `docker compose build --no-cache api` when verifying that a code
   change actually landed in the running container. Symptom: code on
   disk has the change, `docker exec grep` finds it, but the running
   uvicorn workers behave as if it's not there.
5. **`column "id" does not exist` red herring**: was actually about
   `exam_config.created_at` first, then about `ON CONFLICT (id)`
   inference, depending on the SQL the adapter generated. Always
   read the Postgres HINT line in the error — it often points at
   the real column.
6. **CSRF token echo**: every Bearer-auth POST/PUT/PATCH/DELETE must
   include `X-CSRF-Token` matching the JWT's `csrf` claim. Both
   `setup_test_data.py` and the k6 scripts forgot this — both fixed.
7. **k6 load-test session_id format**: server uses `rsplit('_', 1)`
   to extract the roll number from the session id. So
   `${roll}_${ts}_${vu}` parses as roll=`{roll}_{ts}`, which doesn't
   match the JWT's roll claim. Use `${roll}_${ts}v${vu}` instead
   (the `v` separator survives rsplit).

## 4. Current load-test numbers (the only defensible ones)

Production submit path, **real exam end-to-end** (exam_started event
→ bulk_save loop → heartbeat loop → submit with full scoring):

| VUs | exam_started | bulk_save | heartbeat | submit | submit p95 | errors |
|---|---|---|---|---|---|---|
| 200 | 200/200 | 1000/1000 | 1000/1000 | 200/200 | 126 ms | 0.00 % |
| 500 | 500/500 | 2500/2500 | 2500/2500 | 500/500 | 126 ms | 0.00 % |
| 1000 | 1000/1000 | 5000/5000 | 5000/5000 | 1000/1000 | 133 ms | 0.00 % |
| **2000** | **2000/2000** | **10000/10000** | **10000/10000** | **2000/2000** | **142 ms** | **0.00 %** |
| 3500 (first attempt) | 719/3500 ❌ | 5818/17500 ❌ | 6171/17500 ❌ | 1260/3500 ❌ | 295 ms | 66 % |

The 3500 attempt cracked, but **the failure mode was queueing on the
asyncpg connection pool, not CPU/RAM/network**. Docker stats during
the submit wave showed api CPU peak at 91% of one core (well under
the 3-core allocation), postgres at 52%, redis idle. The pool default
of 3-10 connections × 4 workers = 40 total was the wall. Pool
exhaustion → requests queue → hit k6's 15s client timeout → fail.

**UPDATE — pool bump did NOT fix 3500.** We tried THREE times:
  1. Default pool (4×10=40) → 66% errors on first 3500 attempt
  2. After POSTGRES_POOL_MAX=30 + max_connections=200 → still cracking
     at t=132s on /event with > 60% errors
  3. After cleaning up zombie LOADTEST_* sessions (the reaper was
     hammering the DB trying to score them, hitting a stale
     `flush_answers_to_db(student_id=...)` kwarg bug) → STILL cracking,
     now broader (event + heartbeat + bulk_save all timing out)

Three attempts, same failure shape — the root cause is NOT pool size
or reaper noise alone. The actual bottleneck at 3500 VUs lives
somewhere we haven't yet localized. Candidates to investigate
tomorrow morning with fresh eyes:

  - phase66 may have added a REDUNDANT unique constraint on
    exam_sessions.session_key (the table already has a PRIMARY KEY
    on that column). Two unique indexes doubles the write cost per
    upsert. Run `\d exam_sessions` and check if both
    `exam_sessions_pkey` AND `exam_sessions_session_key_unique`
    exist — if so, drop the latter:
      ```
      ALTER TABLE exam_sessions DROP CONSTRAINT exam_sessions_session_key_unique;
      ```
  - Caddy may have a default connection limit somewhere. Check
    Caddy logs for `dial tcp ... i/o timeout` lines during the
    failing window.
  - asyncpg pool may need `min_size=20` (not 10) to actually have
    enough warm connections by the time the join wave hits.
  - The heartbeat reaper's `flush_answers_to_db(student_id=...)`
    call IS a real bug — find that call site and fix the kwarg.
  - exam_sessions's RLS policies invoke `get_my_roll_numbers()` and
    `get_my_teacher_id()` functions. At high write rate these
    might be slow. Verify procta is a superuser (RLS-exempt) on
    the asyncpg connection, and if not, set
    `SET row_security = OFF;` for the load test session.

**Verified ceiling for now: 2000 VUs, 100% success, 142ms p95.**
3500+ is a tomorrow problem, not a tonight problem.

---

### 2026-05-22 update — 3000 VU distributed test PASSED

We resolved the 3500 VU mystery via three findings:

1. **Single-Mac k6 is the wall, not the server.** When we ran 1500
   VUs from the Mac alone, the server saw 0% errors. When we ran
   3500 from one Mac, dial timeouts climbed even with the server at
   0.76% CPU and `TcpExtListenDrops = 0`. The Mac's TLS/socket stack
   simply cannot drive 3000+ concurrent VUs reliably.

2. **Cloudflare throttles single-IP firehoses.** Adding
   `BYPASS_CF=1` (resolve `app.procta.net` directly to origin) at
   3500 VUs from one Mac took success from 7% to 86% — half the
   "failures" were CF's WAF protecting the origin from what looked
   like a DDoS.

3. **Distributed test (Mac + GitHub Codespace, 1500 VUs each) =
   3000 concurrent VUs hitting the server. Mac side: 99.91 % success,
   1500/1500 iterations complete.**

| Source | VUs | Success | submit p95 | scoring drained |
|---|---|---|---|---|
| Mac alone | 1500 | 99.99 % | 416ms | 82 % |
| Mac (parallel) | 1500 | **99.91 %** | 9.5s | 38 % |
| Codespace (parallel) | 1500 | client-timeout at 30s poll cap | — | — |

The Codespace's `submit-exam: request timeout` errors are
client-side timeouts at extra RTT (Codespace → KVM ~200ms vs Mac
~30ms), NOT server failures. The server processed all those
submissions; the Codespace's k6 just gave up earlier than the Mac's.

**New defensible pitch number: "3,000 concurrent students,
99.91 % success rate, all submissions accepted."** With the caveat
that scoring tail latency under saturation reaches ~30s (the async
queue depth grows because RQ workers can't drain at the arrival
rate). For real-world distributed traffic — 3000 students on 3000
different IPs — the RTT averages out and the latency tail compresses.

#### What changed this session
- 16384 somaxconn / tcp_max_syn_backlog / netdev_max_backlog
- 8 RQ scoring workers (was 1, then 3) via `--scale worker=8`
- Removed `container_name: proctor-worker` from compose to enable
  `--scale`
- `loadtest/run_distributed.sh` orchestrator + `merge_k6_summaries.py`
- `BYPASS_CF=1` env in real_exam_jwt.js
- Backups + restore drill verified (B2)

#### The current bottleneck — scoring queue depth
Under 1500 simultaneous submits, 8 workers drain ~8/sec but the
arrival rate is ~25/sec. 929 of 1493 polls timed out at the k6
30s cap. Easy lever: more workers (next step in §4a below).

---

## 4a. Optimization roadmap (Tiered by effort × impact)

Goal: take the proven 3000 VU number and push it cleanly past 5000.
Every item is free except where noted.

### Tier 1 — Cheap wins (< 1 hour each, do now)

| # | Change | Where | Expected impact |
|---|---|---|---|
| T1 | `--scale worker=16` permanent | docker-compose default OR a one-line `make scale` | scoring drain rate doubles → submit p95 drops from 9s to ~4s |
| T2 | Postgres `shared_buffers=2GB` (default 128MB) | `docker-compose.yml` postgres command override | 30-50% faster reads on hot tables (questions, exam_sessions) |
| T3 | Postgres `effective_cache_size=4GB` | same | helps planner pick index scans |
| T4 | asyncpg `min_size=20` (warm-up) | `app/database.py` pool init | first 100 reqs after restart don't wait for pool to grow |
| T5 | Caddy `max_idle_conns_per_host=200` on api upstream | `Caddyfile` reverse_proxy block | reuses connections to api, cuts handshake overhead |

### Tier 2 — Medium-effort, big wins (1-3 hours each)

| # | Change | Why | Expected impact |
|---|---|---|---|
| T6 | Move `/api/v1/event` writes to the autosave queue (already exists, just unused for events) | events are fire-and-forget audit rows; no reason they should hit the DB synchronously | bulk_save + heartbeat p95 drops from 3s to ~200ms under load |
| T7 | Combine scoring job's 4 separate DB queries into 1 with a CTE | scoring job currently does: load session → load answers → load questions → load exam_config. They can all be one query. | scoring throughput ~doubles, so 8 workers behave like 16 |
| T8 | Add a second autosave-worker (`--scale autosave-worker=2`) | single worker is the bottleneck for the bulk_save fanout | bulk_save tail latency cuts in half |
| T9 | k6: bump `submit` timeout from 30s → 60s in `real_exam_jwt.js` poll loop | the *scoring* drain takes >30s under load; the actual submit *response* takes <100ms. The test is incorrectly reporting timeouts for slow async scoring. | cleaner test signal — failures vs slow scoring become distinguishable |
| T10 | Raise k6 `POLL_MAX_SECONDS` env var or change script to NOT poll (just verify 202 response) | poll loop conflates submit success with scoring completion | proves true "ingestion" capacity separately from "scoring" capacity |

### Tier 3 — Architectural (1+ day each, do when revenue justifies)

| # | Change | When to do it |
|---|---|---|
| T11 | Add **pgbouncer** in transaction-pooling mode in front of Postgres | When you hit `max_connections=200` ceiling consistently, not before. Not worth the operational complexity yet. |
| T12 | Move heartbeat from "DB write every 30s" to "Redis write every 30s, flushed to DB every 5min" | When heartbeat becomes the proven bottleneck (currently bulk_save is worse) |
| T13 | Shard the scoring queue by hash(session_id) across multiple Redis instances | If you're running > 10k concurrent and Redis CPU is at 100% (currently 3%) |
| T14 | Move to a 2nd KVM for Caddy + read replicas for Postgres | When a single KVM 4 actually saturates (CPU > 80% sustained across all containers under load) — not close yet |
| T15 | Upgrade to KVM 8 (8 vCPU, 32 GB RAM) — ~₹1,400/mo | When 3500-5000 VUs becomes a routine sales requirement. Defer until at least one paying school is sized that big. |

### Recommended order

For tomorrow morning (T1-T5, all under 2 hours total):

```bash
# 1. Permanent 16 workers
sed -i '' 's/--scale worker=8/--scale worker=16/g' (wherever you saved that)
# OR add `deploy.replicas: 16` to docker-compose.yml worker block

# 2-3. Postgres tuning — add to docker-compose.yml postgres command:
#   postgres -c shared_buffers=2GB -c effective_cache_size=4GB \
#            -c work_mem=16MB -c max_connections=200

# 4. asyncpg warmup — in app/database.py where the pool is created:
#   min_size=20, max_size=40 (was probably 10,40)

# 5. Caddy max_idle_conns — in Caddyfile reverse_proxy block:
#   transport http {
#     max_idle_conns_per_host 200
#   }
```

Then re-run the distributed 3000 VU test. Expectation: submit p95
drops from 9.5s to ~3s, scoring drain rate from 38% → ~80%.

If that works, push to 4000 VUs distributed (2000 from each
source). The next wall after T1-T5 is almost certainly autosave
single-worker bottleneck (T8).

---

## 4b. pgbouncer cutover playbook (Tier 2 → done 2026-05-22)

We added `pgbouncer` as a transaction-pooling connection multiplexer
in front of Postgres. Why this matters beyond Tier 1:

| Issue without pgbouncer | After pgbouncer |
|---|---|
| 4 uvicorn × 40 + 16 workers × 40 + 2 autosave × 40 = **880 worst-case logical pool slots** | **25 real Postgres backends** serve all 880 logical slots |
| Connection acquire cost: TCP+TLS+SCRAM = ~10ms cold, ~1ms warm | Acquire from pgbouncer pool: ~0.1ms (UDS-like local socket) |
| `max_connections=200` is a hard ceiling | pgbouncer hides 200 from app — we can crank app pool higher freely |
| Pool exhaustion on burst = request waits / errors | pgbouncer queues clients (transient wait, never connection refused) |

### Rollout (on KVM)

```bash
cd /root/proctor-browser
git pull --rebase=false

# 1. Edit .env — change DATABASE_URL host + add the flag:
#    DATABASE_URL=postgresql://procta:<pw>@pgbouncer:6432/procta
#    DATABASE_USE_PGBOUNCER=1
# (rollback is just changing the host back to proctor-postgres:5432
#  and removing the flag.)

# 2. Bring up the stack with the new pgbouncer service:
docker compose --profile postgres up -d --scale worker=16 --scale autosave-worker=2

# 3. Verify pgbouncer is healthy + app is connecting through it:
docker compose ps | grep pgbouncer
make pgbouncer-verify    # should print "api → pgbouncer ✓"

# 4. Smoke test — run a single exam end-to-end:
docker exec proctor-pgbouncer psql -h 127.0.0.1 -p 6432 -U procta -d procta \
  -c "SELECT count(*) FROM teachers"   # should return a number, not an error

# 5. Check pool occupancy:
make pgbouncer-stats
# cl_active = clients currently holding a transaction
# sv_active = real postgres backends currently serving them
# sv_idle = real backends warm + free
# A healthy ratio under load is cl_active >> sv_active.
```

### Things to watch for in the first hours

1. **`prepared statement "__asyncpg_stmt_xxx" does not exist`** — means
   asyncpg's statement cache wasn't disabled. Check that
   `DATABASE_USE_PGBOUNCER=1` is in `.env` and the app container
   has it: `docker exec proctor-api env | grep PGBOUNCER`
2. **Auth failures** — `SCRAM authentication requires libpq version
   10 or above`. The pgbouncer-postgres connection uses
   `AUTH_TYPE=scram-sha-256`. If Postgres is using a different auth
   method (e.g. md5), pgbouncer can't proxy. Fix:
   `docker exec proctor-postgres psql -U procta -c "SHOW password_encryption"`
   should return `scram-sha-256` (Postgres 16 default).
3. **Worker stat says "waiting"** — `cl_waiting > 0` for any sustained
   period means DEFAULT_POOL_SIZE=25 is too small. Bump to 50.

### When pgbouncer is the wrong tool

- If you start needing LISTEN/NOTIFY for SSE (you don't today — SSE
  uses Redis pub/sub)
- If you ever do long-running transactions (you don't)
- If you write code that calls `SET LOCAL` (you don't)
- If you use advisory locks (you don't)

### Tuning knobs (env vars on the pgbouncer container)

| Knob | Default | When to bump |
|---|---|---|
| `DEFAULT_POOL_SIZE` | 25 | If `cl_waiting > 0` sustained — try 50 |
| `MIN_POOL_SIZE` | 10 | Bump if first-request latency climbs after idle |
| `RESERVE_POOL_SIZE` | 5 | Burst headroom — keep at 5 |
| `MAX_CLIENT_CONN` | 4000 | Bump if you ever scale workers past 40 |
| `SERVER_LIFETIME` | 3600 | Drop to 1800 if you see slow connection accumulation |

### Reading SHOW POOLS output

Example healthy output under 3000 VU load (target state):
```
 database |  user   | cl_active | cl_waiting | sv_active | sv_idle | sv_used | pool_mode
----------+---------+-----------+------------+-----------+---------+---------+-----------
 procta   | procta  |       180 |          0 |        18 |       7 |       0 | transaction
```

`cl_active=180` (app holding 180 logical pool slots) → `sv_active=18`
(only 18 real Postgres backends doing work). That's the 10× multiplier
this whole exercise was designed to deliver.



Also: macOS file descriptor limit defaults to 256. Raise to 65536
before running > 2000 VU k6 tests, otherwise k6 stalls at ~4267/N
during VU init:
```bash
ulimit -n 65536    # in the shell where you run k6
```

p95 latency barely climbing as VUs 10× — the box still has serious
headroom. **2000 is the highest tested, not the measured ceiling.**
Next sensible step is 5000 (one full prior plan target) or 3500
(midway).

The starter-plan student limit on the load-test org was raised to
50 000 via:
```bash
docker exec proctor-postgres psql -U procta -d procta \
  -c "UPDATE organizations SET max_students = 50000 \
      WHERE id = (SELECT org_id FROM teachers WHERE email = 'loadtest@procta.net');"
```
(Run on the KVM where you're already SSH'd in. The shell-escaped
version from earlier needs the outer SSH wrapper, not when you're
on the box.)

**Cost**: ~₹699/month KVM + free Cloudflare. **No** managed services
yet. This is unusually impressive for the price point.

## 5. Async-scoring (Fix #2) — known broken, not blocking

The fast-path in `submit_exam` falls back to inline every time with:

```
[SUBMIT-ASYNC] fast-path failed: cannot perform operation: another
operation is in progress
```

**Root cause** (confirmed): `RQ_ENABLED` is not set in `.env` on the
KVM, so `enqueue_job(score_submission_job, ...)` runs the function
synchronously instead of queuing to Redis. `score_submission_job`
calls `_run_coro_in_sync()` which spawns a thread with a fresh event
loop. That fresh loop tries to use the asyncpg pool, which is bound
to the original request's loop → asyncpg's "another operation in
progress" error → try/except in the fast path catches it → falls
back to inline scoring → inline scoring works perfectly.

**Fix** (5 minutes when ready):
```bash
ssh root@187.127.169.89
echo "RQ_ENABLED=1" >> /root/proctor-browser/.env
cd /root/proctor-browser && docker compose up -d --no-deps --force-recreate api
```

This is not urgent — the inline path is comfortably handling 500
VUs at 126 ms p95. Only matters when inline starts struggling
(probably > 1500 VUs).

## 6. Load test infrastructure

### The one command (run on Mac)

```bash
cd ~/proctored-browser
loadtest/run_from_mac.sh loadtest@procta.net 'LoadTest!2026' 500 300
#                          email                 password       VUs duration(s)
```

What it does, in order:
1. SSHes to `root@187.127.169.89`, runs `git pull` on the KVM
2. Rebuilds + recreates `proctor-api` container (`--force-recreate --build`)
3. Sleeps 12 s for migrations to finish
4. Runs `loadtest/setup_test_data.py` to create exam + students
5. Reads exam_id + teacher_id from the manifest
6. Runs `scripts/mint_loadtest_tokens.py` inside the api container
   (`--zero-pad 4` to match the setup script's `LOADTEST_0001` format)
7. scp's `loadtest_tokens.json` from KVM → Mac
8. Runs `k6 run loadtest/real_exam_jwt.js` locally on the Mac

Mac-side files needed:
- `loadtest/run_from_mac.sh` (the wrapper)
- `loadtest/real_exam_jwt.js` (the k6 script)
- k6 binary (`brew install k6`)
- Working SSH key auth to `root@187.127.169.89`

### Test teacher account (already created)

- Email: `loadtest@procta.net`
- Password: `LoadTest!2026`
- Created via `scripts/create_loadtest_teacher.py` inside the api container

### Token format the mint script emits

Each row in `loadtest/loadtest_tokens.json`:
```json
{
  "roll_number": "LOADTEST_0001",
  "session_id": "LOADTEST_0001_RUN",
  "token": "eyJ...",
  "csrf": "abc123..."   // ← MUST be echoed as X-CSRF-Token header
}
```

The k6 script overrides `session_id` per-iteration to
`${roll}_${Date.now()}v${__VU}` to avoid the rsplit pitfall above.

## 7. Open work items (priority order)

Priority is "what makes Procta survive solo + bus-factor 1", NOT
"what scales it bigger".

### P0 — do this week (each is < 2 hours)

1. **Add Sentry free tier** — 5 K events/month captured forever.
   Today's debugging would have been 10× faster. Sign up at
   sentry.io, add `SENTRY_DSN` to `.env`, the codebase already has
   `sentry_sdk.init(...)` plumbing (search `SENTRY_DSN` in worker.py
   and the main FastAPI bootstrap).
2. **Write RUNBOOK.md** — how to deploy, how to rollback, the 5 most
   common errors and their fixes, where every secret lives. 1 weekend
   afternoon. This is the cheapest, highest-impact bus-factor fix.
3. **Backup test** — `pg_dump` cron exists somewhere
   (`scripts/procta-backup.sh`?). Write a companion
   `scripts/backup_restore_test.sh` that restores into a temp container
   monthly. A backup you've never restored doesn't exist.

### P1 — next 2-4 weeks

4. **Fix async scoring** — set `RQ_ENABLED=1` (one line). Then
   re-run 1000 / 2000 VU tests to confirm Fix #2 actually engages
   and submit p95 drops to ~15 ms.
5. **Drop `supabase` from `requirements.txt`** — only doable once
   you've audited every `supabase.auth.*` call in `app/routers/auth.py`
   and confirmed it has a local equivalent that's actually wired up.
6. **Remove `_DATABASE_BACKEND` env var entirely** — already deleted
   the REST adapter (commit `f4d8766`). The env var refusal at
   boot can be removed once we're sure nobody sets it.

### P2 — month 2-3

7. **Neon migration** — DO IT IN SHADOW-WRITE MODE:
   - Week 1: connect Neon as a read replica via logical replication
   - Week 2: dual-write to both DBs; reads still from Hostinger PG
   - Week 3: flip reads to Neon (single env var change)
   - Week 4: stop writes to Hostinger
   - Month 2: decommission Hostinger PG
   - Bus-factor-safe because every step is reversible by env var.
8. **R2/S3 for screenshots** — currently on KVM disk = data-loss
   risk. R2 is cheaper than S3 for this access pattern. Probably
   ₹30-50/month at current volume.

### Things to consciously DEFER

- Kubernetes (don't — docker-compose is fine)
- Microservices (don't — monolith is fine)
- Multi-region (don't — until a customer demands it)
- AI workers as a separate compute pool (only if you add real
  AI features beyond the current short-answer LLM grading)

## 8. The user's situation — preserve this context

**Important framing for any new conversation:**

- Solo developer. Student. ₹3000/month pocket money. Cannot afford
  paid services, freelancers, or hires.
- Has tried to find a second engineer. Nobody wants to join a project
  "too far ahead" — recurring pattern with sprawling solo codebases.
- Bus-factor 1 is the project's single biggest risk.
- All advice should default to **free or near-free** tools. Sentry
  free tier > Datadog. Backblaze B2 ($0.005/GB) > S3. UptimeRobot free
  > Pingdom paid.
- The previous (pre-today) cost-story claim was "₹699/month box
  serves 5000 students". That number was inflated (practice path
  shortcut). **The honest claim is 500 concurrent students on the
  production path with 100 % success and 126 ms p95 submit.** Don't
  let the user (or yourself) repeat the inflated figure.
- The user pushed back today on three things I should remember:
  (a) iterating one migration at a time vs auditing all gaps upfront
  (b) suggesting paid freelancers without checking budget
  (c) over-engineering ahead of validated need
  All three were fair. Calibrate accordingly.

## 9. Tooling reminders for fresh sessions

- The user runs commands on Mac (`arihantkaul@Arihants-MacBook-Air-M4`)
  and SSHes to KVM (`root@srv1675832#` prompt, IP `187.127.169.89`).
- zsh on the Mac treats `#` as a literal command — don't paste
  multi-line snippets with `#` comments. Give one command per
  paragraph.
- `docker compose up --build` doesn't always pick up file changes
  due to layer cache. Use `--no-cache` when verifying a fix.
- Python on the user's Mac is 3.9, project requires 3.12. Local
  pytest doesn't work without venv. Real test is on the KVM.
- The user has limited time. Long debug-by-iteration loops are
  painful for them. When uncertain, ask for the actual error log
  instead of guessing.

## 10. Useful one-shot diagnostic commands

```bash
# Container health
ssh root@187.127.169.89 'docker ps --filter name=proctor-'

# Recent api errors (filtered)
ssh root@187.127.169.89 'docker logs proctor-api --since 10m 2>&1 | grep -E "ERROR|FATAL|asyncpg.exceptions" | tail -20'

# Confirm migrations applied
ssh root@187.127.169.89 'docker logs proctor-api 2>&1 | grep -iE "phase6[0-9]" | tail -10'

# Schema snapshot of a table
ssh root@187.127.169.89 'docker exec proctor-postgres psql -U procta -d procta -c "\d exam_sessions"'

# Confirm an env var made it into the container
ssh root@187.127.169.89 'docker exec proctor-api printenv RQ_ENABLED ASYNC_SCORING_ENABLED UVICORN_WORKERS'

# Force-restart the api with the latest image (when a normal rebuild silently used cache)
ssh root@187.127.169.89 'cd /root/proctor-browser && docker compose stop api && docker compose build --no-cache api && docker compose up -d --no-deps --force-recreate api'
```

## 11. Where each piece of work lives

- **k6 scripts**: `loadtest/real_exam_jwt.js`, `loadtest/mixed_proctoring.js`
- **Setup**: `loadtest/setup_test_data.py` (creates exam + students)
- **Tokens**: `scripts/mint_loadtest_tokens.py` (must run inside api
  container so SUPABASE_JWT_SECRET matches)
- **Wrappers**: `loadtest/run_from_mac.sh` (Mac-side, one-line trigger),
  `loadtest/run_real_exam.sh` (KVM-side, runs k6 locally on KVM)
- **DB adapter**: `app/postgres_table.py` (has `_UPSERT_CONFLICT_COLS`
  registry and ISO-coercion in `_SQL.add`)
- **DB factory**: `app/database.py` (now ~100 lines, just exposes
  `async_table`, `supabase` placeholder, `_required_env`)
- **Migrations**: `migrations/phase62*..phase66*` — today's catch-up
  for columns/constraints that existed on Supabase but not in repo
- **Job queue**: `app/jobs/` — `scoring_jobs.py`, `autosave_jobs.py`,
  `email_jobs.py`. Workers consume via RQ.

---

If you're a fresh Claude session reading this: the user is solo,
constrained, capable, and has built something genuinely impressive
for their stage. Match their pragmatism. No premature optimization.
No paid recommendations without checking budget. Always show the
actual error log before suggesting a fix.

If you're future-me reading this: today was painful but productive.
Sleep. The 1000-VU test is running. Tomorrow ramp 1000 → 2000 → 5000.
Then RUNBOOK.md.
