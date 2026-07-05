# Procta — Deploy & Operations Runbook

Single source of truth for everything you do **on the server** between
git pulls. If a step takes more than 30 seconds, it's documented here
so the next deploy doesn't surprise anyone.

The companion file is `TODO.md` — that tracks pending feature work.
This file tracks *operations*: what to run, when, and what to watch.

---

## 1. Standard deploy

### 1.0 Deploy day checklist

```markdown
□ CI is green (tests, build, audit, security scan)
□ Local release gate passed: MODE=full scripts/quality_check.sh
□ Migrations reviewed for destructive operations
□ Rollback commit identified (git log --oneline -5)
□ Database backup verified (`docker logs proctor-ofelia | grep daily-backup` for the local dump, `tail /var/log/procta-s3-backup.log` for the S3 Mumbai off-site copy — see docs/OBSERVABILITY.md §Backups)

── Deploy ──

□ ssh root@<your-vps>
□ cd ~/proctor-browser && git pull
□ Run new migrations: for f in migrations/*.sql; do psql "$DB_URL" -f "$f" 2>&1 | tail -3; done
□ docker compose build api
□ docker compose up -d --force-recreate api caddy
□ Verify health: curl -sf https://app.procta.net/health
□ Verify smoke: E2E_API_KEY=<key> cd tests/browser && pytest test_e2e_happy_path.py -q

── Post-deploy ──

□ Verify backfill: SELECT COUNT(*) FROM exam_sessions WHERE student_id IS NULL AND roll_number NOT LIKE 'LTI_%';
□ Verify page loads: curl -sf https://app.procta.net/dashboard
□ Deploy version shown on /api/v1/admin/status (release.version / release.commit)
□ docker compose logs api 2>&1 | grep -E "ERROR|WARN"
```

### 1.1 Pre-deploy checklist

Before touching the running containers, confirm:

- [ ] Latest CI run is green for tests, Docker smoke, dependency audits,
      and security scans.
- [ ] Local release gate passed: `MODE=full scripts/quality_check.sh`.
- [ ] Local LLM review was read, or explicitly skipped for an emergency
      hotfix: `RUN_LLM=1 MODE=full scripts/quality_check.sh`.
- [ ] You have a rollback commit or tag identified.
- [ ] Database backup/export is available for the current production state.
- [ ] Pending migrations were reviewed for destructive operations.
- [ ] `migrations/phase52_backfill_student_id.sql` has been applied after
      the student-account privacy linkage fix.
- [ ] `migrations/phase55_dashboard_reporting_indexes.sql` has been applied
      during a quiet window; verify with `DB_INDEX_REVIEW.md`.
- [ ] `migrations/phase56_proctoring_sensitivity.sql` has been applied before
      using the Detection Sensitivity control in Tools.
- [ ] If the release touches exam startup/submission, run one practice exam
      locally before deploying.

For privacy/session linkage specifically, verify the backfill state:

```sql
SELECT COUNT(*) AS sessions_without_student_id
FROM exam_sessions
WHERE student_id IS NULL
  AND roll_number NOT LIKE 'LTI_%';
```

Expected value after backfill: `0` for linked Procta student-account
sessions. LTI learner records are LMS-managed by design.

```bash
ssh root@<your-vps>
cd ~/proctor-browser
git pull
docker compose build api          # rebuild backend image
docker compose up -d --force-recreate api caddy
docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile
```

Verify with:
```bash
# Backend healthcheck
curl -sf https://app.procta.net/health || echo "API DOWN"
# Marketing redirect (should 302 to procta.net)
curl -sI https://app.procta.net/ | grep -i location
# Cache headers (should be no-cache, must-revalidate)
curl -sI https://app.procta.net/static/theme.css | grep -i cache
```

If anything is wrong, `git revert <bad-commit>` and rerun the deploy.
Each commit on `main` is independently revertable.

---

## 2. One-time setup steps (do once, then forget)

### 2.1 Database migrations — run all pending

These are idempotent (safe to re-run); apply them in this order:

```bash
# Set the connection string for the self-hosted Postgres container.
# Use MIGRATIONS_DATABASE_URL (the owner role, needed for DDL) — see
# scripts/run_postgres_migrations.py for why the restricted procta_app
# runtime role can't run migrations post-RLS-cutover.
export DB_URL="postgresql://procta:<password>@localhost:5432/procta"

# Migrations normally run automatically as part of the deploy preflight
# (scripts/run_postgres_migrations.py, see .github/workflows/deploy.yml).
# All migrations live in migrations/. To apply manually:
for f in migrations/*.sql; do
  echo "── $f ──"
  psql "$DB_URL" -f "$f" 2>&1 | tail -5
done
```

Specifically these were never run on prod yet:
- `migrations/phase10_invite_clicks.sql` — adds `clicked_at` /
  `click_count` columns + index for the clicked-engagement signal.
- `migrations/phase11_scorecard_insight.sql` — adds
  `scorecard_insight` text column to `exam_sessions` so AI-generated
  scorecard notes are cached (otherwise the LLM regenerates on every
  bulk download → 2× the cost).
- `migrations/phase11_questions_full_schema.sql` — adds the union
  of all columns the application writes to `questions`:
  `question_type`, `image_url`, `tags`, `created_at`, `updated_at`.
  Older deployments may be missing several of these (the legacy
  schema only had `id / teacher_id / question / options / correct /
  question_id`). Without this, "Add Selected to Exam" fails with
  PGRST204 ("Could not find the 'X' column"). The endpoint has a
  runtime fallback that detects missing columns from the error
  message and drops them on retry, but feature data is lost on
  copy until the migration runs. **Supersedes the earlier
  `phase11_questions_image_url.sql` (which is a strict subset).**

### 2.2 LLM provider — pick one (free tier)

Add to `~/proctor-browser/.env` on the VPS:

```bash
# Recommended: Groq (free, 14,400 req/day, no credit card)
LLM_API_KEY=gsk_xxxxxxxxxxxxxxxx
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_MODEL=llama-3.3-70b-versatile

# Sign up: https://console.groq.com/keys
```

Alternatives if you'd rather not use Groq — see `.env.example` for
OpenRouter (Gemini Flash 2.0 is :free), Cerebras (also free), or
local Ollama (no key, runs on the VPS but slow without a GPU).

After adding the key, restart the api container:
```bash
docker compose up -d --force-recreate api
# Test:
docker compose logs api 2>&1 | grep -E "groq|llm" | tail -3
```

If LLM is unconfigured, AI features cleanly 503 — every other feature
keeps working. The platform was designed to make LLM optional.

### 2.3 Resend dashboard (one-time, in browser)

`mail.procta.net` → settings → make sure **Track clicks** is on.
**Track opens** is optional (Outlook strips opens, Apple Mail
pre-fetches them, so it produces noise). The Clicked column on
the dashboard is the reliable signal.

### 2.4 Screenshots cleanup cron — prevents disk fill

Without this, the `./screenshots/` bind mount grows ~50 MB / 100 active
students / day. A few weeks of usage will fill the VPS.

**Install** (one-time, on the VPS):

```bash
# Crontab — runs Sunday 03:00 IST, deletes screenshots older than 90 days.
# Adjust 90 to your retention requirement.
sudo bash -c 'cat > /etc/cron.d/procta-screenshots-cleanup <<EOF
# Procta screenshots retention — remove forensic frames older than 90 days.
# Runs as root because the bind mount is owned by container UID/GID.
SHELL=/bin/bash
PATH=/usr/sbin:/usr/bin:/sbin:/bin
0 3 * * 0 root /usr/local/bin/procta-screenshots-cleanup.sh >>/var/log/procta-screenshots-cleanup.log 2>&1
EOF'

sudo cp scripts/procta-screenshots-cleanup.sh /usr/local/bin/
sudo chmod +x /usr/local/bin/procta-screenshots-cleanup.sh
sudo chmod 644 /etc/cron.d/procta-screenshots-cleanup

# Manually verify
sudo /usr/local/bin/procta-screenshots-cleanup.sh
tail /var/log/procta-screenshots-cleanup.log
```

The script lives at `scripts/procta-screenshots-cleanup.sh` in the
repo — see comments inside for retention tuning.

### 2.5 Docker self-heal cron — recovers from a dockerd crash without a human

Real incident (2026-07-04 12:22 UTC): `dockerd` crashed (a buildkit
concurrent-map-write fatal panic from overlapping build sessions —
confirmed via `journalctl -u docker` around that timestamp). systemd's
`Restart=always` brought the daemon itself back in ~3 seconds, but the
crash orphaned every running container's underlying `containerd` task
(`"Removing stale sandbox"` / `"sandbox ... not found"` on daemon
restart) — so `restart: unless-stopped` had nothing left to act on.
Nothing was running again until a human noticed and manually ran
`docker compose up -d`, roughly 10 minutes later.

This cron runs that same recovery command every 2 minutes, so recovery
happens automatically instead of waiting on a human to notice (whether
via Better Uptime alerting or otherwise). It's deliberately narrow: it
only ever runs `docker compose up -d` (idempotent — a no-op when
everything's already up) and logs a loud `ERROR` line if postgres still
isn't healthy afterward. It never rebuilds images or restarts anything
already healthy — that stays the deploy workflow's job.

**Install** (one-time, on the VPS):

```bash
sudo cp scripts/procta-docker-self-heal.sh /usr/local/bin/
sudo chmod +x /usr/local/bin/procta-docker-self-heal.sh

sudo bash -c 'cat > /etc/cron.d/procta-docker-self-heal <<EOF
# Procta docker self-heal — see DEPLOY.md §2.5 for the incident this fixes.
SHELL=/bin/bash
PATH=/usr/sbin:/usr/bin:/sbin:/bin
*/2 * * * * root PROCTA_PROJECT_DIR=/root/proctor-browser /usr/local/bin/procta-docker-self-heal.sh >>/var/log/procta-docker-self-heal.log 2>&1
EOF'

sudo chmod 644 /etc/cron.d/procta-docker-self-heal

# Manually verify
sudo PROCTA_PROJECT_DIR=/root/proctor-browser /usr/local/bin/procta-docker-self-heal.sh
tail /var/log/procta-docker-self-heal.log
```

The script lives at `scripts/procta-docker-self-heal.sh` in the repo.
Add log rotation for `/var/log/procta-docker-self-heal.log` if it grows
large (every 2-minute no-op run adds one short line).

### 2.6 Off-site backup of `screenshots/` (optional, supplementary)

**Superseded as the primary mechanism** by `scripts/backup_to_s3.sh`
(installed via `scripts/install_s3_backup.sh`, cron at `/etc/cron.d/
procta-s3-backup`), which already ships nightly DB dump + `screenshots/`
+ `question_images/` to AWS S3 (ap-south-1, Mumbai) — see
`docs/OBSERVABILITY.md` §Backups for the current setup. Postgres is
self-hosted (not Supabase-managed) and runs on a Hostinger VPS (not a
DigitalOcean droplet). The restic path below is kept only for teams that
want longer-tail forensic-frame retention beyond the S3 backup's 30-day
lifecycle — it was never actually wired up in production (no
`/etc/cron.d/procta-backup` exists on the current host); confirm that's
still true before assuming it's running.

```bash
# Install restic (Debian/Ubuntu)
sudo apt update && sudo apt install -y restic

# Provision a B2 / S3 / Backblaze bucket. Set credentials:
sudo bash -c 'cat > /etc/procta-backup.env <<EOF
RESTIC_REPOSITORY=b2:procta-backups:/screenshots
B2_ACCOUNT_ID=<your-b2-account-id>
B2_ACCOUNT_KEY=<your-b2-application-key>
RESTIC_PASSWORD=<a-strong-passphrase-for-encrypting-the-backup>
EOF'
sudo chmod 600 /etc/procta-backup.env

# Initialize the repo (one-time)
sudo bash -c 'set -a && source /etc/procta-backup.env && set +a && restic init'

# Install the backup cron
sudo cp scripts/procta-backup.sh /usr/local/bin/
sudo chmod +x /usr/local/bin/procta-backup.sh
sudo bash -c 'cat > /etc/cron.d/procta-backup <<EOF
# Nightly forensic-screenshots backup. 5 min after midnight IST so it
# overlaps as little as possible with the cleanup cron at 03:00.
SHELL=/bin/bash
PATH=/usr/sbin:/usr/bin:/sbin:/bin
5 0 * * * root /usr/local/bin/procta-backup.sh >>/var/log/procta-backup.log 2>&1
EOF'
```

Test it ran:
```bash
sudo /usr/local/bin/procta-backup.sh && tail /var/log/procta-backup.log
sudo bash -c 'set -a && source /etc/procta-backup.env && set +a && restic snapshots'
```

Restore drill (do this once a year so you know it works):
```bash
sudo bash -c 'set -a && source /etc/procta-backup.env && set +a && \
  restic restore latest --target /tmp/restore-test'
ls /tmp/restore-test/screenshots/ | head
```

---

## 3. Smoke tests

### 3.1 After every deploy (~1 minute)

```bash
# Backend up?
curl -sf https://app.procta.net/health > /dev/null && echo OK || echo FAIL

# Marketing site loading the new design?
curl -s https://procta.net | grep -o "Cheating reduced" | head -1
# Expected: "Cheating reduced"

# Dashboard shell loads?
curl -sf https://app.procta.net/dashboard > /dev/null && echo OK || echo FAIL

# Static assets serve with no-cache (deploys light up immediately)?
curl -sI https://app.procta.net/static/theme.css | grep -i 'cache-control'
# Expected: cache-control: no-cache, must-revalidate
```

### 3.2 Renderer (Electron exam window) — full end-to-end

Cosmetic changes to the renderer don't fail in CI; only a real exam
catches problems. Run this once after any commit that touches
`renderer/index.html`, `app/proctor.py`, or the Electron main process.

1. Pull the latest Electron build from your tags:
   - Mac: download .dmg from GitHub Releases
   - Win: download .exe
2. Install + open Procta.
3. From the lobby, click **Practice run → Start practice exam**.
4. Verify:
   - [ ] Camera preview appears at top of exam window (small thumbnail)
   - [ ] Calibration screen runs through 5 dots without errors
   - [ ] After calibration, exam screen shows: question text,
         option cards, navigation grid in sidebar, mini camera
         bottom-right
   - [ ] Click an option — it highlights with periwinkle accent
   - [ ] Question grid (sidebar) shows current question highlighted
   - [ ] Submit button shows green, Next blue
   - [ ] On submit, success screen appears with "Setup Verified ✓"
5. Open the **teacher dashboard** in another browser. Practice
   sessions should NOT appear on any tab — they're sandboxed.

If any step fails, that's the blocker. Roll back the renderer commit
and re-tag.

### 3.3 LLM features

```bash
# Question generation — needs auth, so do this from teacher dashboard:
# Open /dashboard → Questions tab → ✨ Generate button → topic
# "Photosynthesis" → 3 questions → should return in <3 sec.

# Or test the endpoint directly:
TOKEN="<paste a teacher JWT from /dashboard localStorage>"
curl -sf -X POST https://app.procta.net/api/admin/question-bank/generate \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"topic":"photosynthesis","count":3}' | head -50
```

If you see a 503 with "AI features unavailable", `LLM_API_KEY` isn't
set. If you see 502, the provider is down — try a different
LLM_BASE_URL from `.env.example`.

---

## 4. Rotation policy (do this when a teammate leaves or a key leaks)

Rotate these in order:

```bash
# 1. Postgres owner-role password (procta user, used for DDL/migrations
#    and by DATABASE_URL/MIGRATIONS_DATABASE_URL) — rotate via
#    `ALTER USER procta WITH PASSWORD '...'` and update both env vars,
#    then `docker compose up -d --force-recreate api worker autosave-worker`.

# 2. SUPABASE_JWT_SECRET (name predates the Postgres migration; see
#    .env.example for what it actually does today) — generate a fresh
#    random string, update SUPABASE_JWT_SECRET in .env. See
#    docs/SECRETS.md's rotation playbook for what this does and does not
#    invalidate given the per-purpose JWT_*_SIGNING_KEY derivation.

# 3. Admin password — update ADMIN_PASSWORD in .env.

# 4. LLM key — get a fresh one from the provider, update LLM_API_KEY.

# 5. Resend webhook secret — Resend dashboard → Webhooks →
#    Rotate. Update RESEND_WEBHOOK_SECRET in .env.

# After all rotations, confirm everything works:
docker compose up -d --force-recreate api
docker compose logs api 2>&1 | tail -20
```

---

## 5. Useful one-liners

```bash
# What version of the api container is running right now?
docker compose exec api cat /app/main.py | grep -m1 -A1 'app = FastAPI'

# Inspect live logs without filling the terminal
docker compose logs -f api 2>&1 | grep -E "ERROR|WARN|\[invites\]|\[webhook\]"

# How many invites sent today?
docker compose exec api python -c "
import asyncio
from app.database import async_table
async def main():
    r = await async_table('invite_send_counters').select('*').execute()
    for row in r.data: print(row)
asyncio.run(main())
"

# Disk pressure on screenshots/?
df -h /app/screenshots 2>/dev/null || df -h .

# Find sessions still 'in_progress' that should have completed (>4h ago)
docker compose exec api python -c "
import asyncio
from app.database import async_table
from datetime import datetime, timezone, timedelta
async def main():
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
    r = await async_table('exam_sessions').select('session_key,roll_number,started_at')\
        .eq('status','in_progress').lt('started_at',cutoff).execute()
    print(f'{len(r.data)} stale in-progress sessions')
    for s in r.data[:10]: print(s)
asyncio.run(main())
"
```

---

## Scaling the live-frame cache (3500+ concurrent sessions)

The teacher live-view caches one JPEG per active student in Redis
(`liveframe:<session_id>`, 10s TTL, with a sorted-set LRU index).
Two env vars govern the cap:

```
LIVEFRAME_MAX_SESSIONS=6500        # default, override per box
LIVEFRAME_MAX_FRAME_BYTES=1048576  # 1 MB per-frame upper bound
```

At 6500 sessions × 60 KB/frame ≈ **390 MB** Redis memory — ~85 %
headroom over the 3500-student target. Comfortable inside a 1 GB
Redis instance.

Observability: `GET /api/v1/admin/live-stats` (admin-only) returns
`cached_sessions`, `cap`, `utilisation_pct`, `redis_used_bytes`,
`redis_max_bytes`. Hook into Sentry / Grafana — alert when
utilisation > 80 % or memory > 800 MB.

### When to move to Redis Cluster

Single-node Redis handles ~100k ops/sec — well above 3500 sessions ×
1 Hz writes = 3500 ops/sec. Migrate to cluster only when one of:

- `redis_used_bytes` approaches box RAM, OR
- Concurrent sessions exceed ~10k, OR
- You need HA failover (single Redis = SPOF).

Setup (when needed): spin up 3+ Redis nodes, run
`redis-cli --cluster create`, set `REDIS_URL` to a comma-separated
seed list. `redis-py` auto-detects cluster mode and key
`liveframe:<sid>` auto-shards by hash slot — no app code change.
The only hot key is the sorted-set LRU index (`liveframe:_index`);
if THAT becomes the bottleneck, switch to per-shard indices with
`{<hash_tag>}` curly-brace forcing.

### When to move to WebRTC

The current pipeline is snapshot-based (~1 Hz JPEG, no audio).
WebRTC unlocks real-time video + audio but requires:

- Self-hosted SFU (mediasoup / Janus / LiveKit-server) or managed
  (LiveKit Cloud, Daily, Agora — ₹15-50k/month at our scale)
- Rewrites of student-side capture + teacher-side player to use
  `RTCPeerConnection`
- STUN/TURN ops
- Egress: 3500 × 200-500 kbps = ~1 Gbps sustained

2-4 week project, not a one-evening task. Justified when customers
explicitly request live audio verification or you need to beat Mettl
on the "watch the actual exam" demo bar. For now, snapshot + the
tab-hidden Notifications API alert covers 99 % of real proctoring
needs at 3500-student scale.
