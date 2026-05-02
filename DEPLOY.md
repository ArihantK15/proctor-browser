# Procta — Deploy & Operations Runbook

Single source of truth for everything you do **on the server** between
git pulls. If a step takes more than 30 seconds, it's documented here
so the next deploy doesn't surprise anyone.

The companion file is `TODO.md` — that tracks pending feature work.
This file tracks *operations*: what to run, when, and what to watch.

---

## 1. Standard deploy

After pushing a code change to `main`:

```bash
ssh root@<your-droplet>
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
# Set your Supabase connection string. Get it from
# Supabase dashboard → Project Settings → Database → Connection string
# (use the URI form for psql).
export DB_URL="postgresql://postgres.<project>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres"

# All migrations live in migrations/. Run each:
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

### 2.2 LLM provider — pick one (free tier)

Add to `~/proctor-browser/.env` on the droplet:

```bash
# Recommended: Groq (free, 14,400 req/day, no credit card)
LLM_API_KEY=gsk_xxxxxxxxxxxxxxxx
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_MODEL=llama-3.3-70b-versatile

# Sign up: https://console.groq.com/keys
```

Alternatives if you'd rather not use Groq — see `.env.example` for
OpenRouter (Gemini Flash 2.0 is :free), Cerebras (also free), or
local Ollama (no key, runs on the droplet but slow without a GPU).

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
students / day. A few weeks of usage will fill the droplet.

**Install** (one-time, on the droplet):

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

### 2.5 Off-site backup of `screenshots/`

The Postgres database is backed up by Supabase automatically. The
forensic screenshots are NOT — they live on droplet ephemeral disk.
If the droplet dies, evidence is gone. Set up nightly off-site sync:

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
# 1. Supabase service-role key — Supabase dashboard → Settings →
#    API → "Reset service_role key". Update SUPABASE_SERVICE_ROLE_KEY
#    in .env, then `docker compose up -d --force-recreate api`.

# 2. JWT secret — generate a fresh random string, update
#    SUPABASE_JWT_SECRET in .env. Note: this invalidates every
#    teacher session. They'll have to log in again.

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
from database import supabase
r = supabase.table('invite_send_counters').select('*').execute()
for row in r.data: print(row)
"

# Disk pressure on screenshots/?
df -h /app/screenshots 2>/dev/null || df -h .

# Find sessions still 'in_progress' that should have completed (>4h ago)
docker compose exec api python -c "
from database import supabase
from datetime import datetime, timezone, timedelta
cutoff = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
r = supabase.table('exam_sessions').select('session_key,roll_number,started_at')\
  .eq('status','in_progress').lt('started_at',cutoff).execute()
print(f'{len(r.data)} stale in-progress sessions')
for s in r.data[:10]: print(s)
"
```
