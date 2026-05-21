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
