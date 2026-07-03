# Procta Observability Runbook

How to verify, activate, and triage the observability + backup stack.
Single-file successor to scattered notes in DEPLOY.md / TODO.md /
migration footers — start here when something breaks.

## Stack at a glance

| Layer | Where | Activated |
|---|---|---|
| Sentry (Python) | `app/main.py` | `SENTRY_DSN` env var |
| Sentry (dashboard React) | `app/dashboard-ui/src/main.jsx` | `VITE_SENTRY_DSN` at build time |
| Sentry (student React) | `app/student-ui/src/main.jsx` | `VITE_SENTRY_DSN` at build time |
| Sentry (Electron) | `main.js` | `SENTRY_DSN` env var on the host |
| BetterStack uptime | external | https://app.procta.net/health, 30s polling |
| `/health` endpoint | `app/routers/public.py:143` | always-on |
| Local Postgres backups | Ofelia in `docker-compose.yml` | daily 00:00 UTC |
| S3 (Mumbai) off-site backups | `scripts/backup_to_s3.sh` | cron at 01:30 UTC |
| Restic screenshot backups | `scripts/procta-backup.sh` | optional; see DEPLOY.md §2.5 |
| TTL sweeper | `public.sweep_transient_rows()` + `app/services/ttl_sweeper.py` | every 6h under leader worker |
| CI deploy | self-hosted GH runner | `actions.runner.ArihantK15-proctor-browser.<host>` systemd service |

## Activating Sentry

The code is wired but no-op until DSNs are set.

1. Create a project at https://sentry.io (free tier is plenty — 5k events/month).
2. Copy the DSN (`https://<key>@<id>.ingest.sentry.io/<project>`).
3. **Backend (FastAPI):** add to the prod `.env`:
   ```
   SENTRY_DSN=https://...
   SENTRY_ENVIRONMENT=production
   SENTRY_TRACES_SAMPLE_RATE=0.1
   ```
   Then `docker compose restart api worker autosave-worker`.
4. **React UIs (dashboard + student):** set `VITE_SENTRY_DSN` at build time
   and rebuild. Drop it in `app/dashboard-ui/.env` and `app/student-ui/.env`
   (or pass via docker build args). Vite reads `VITE_*` at compile time.
5. **Electron exam app:** set `SENTRY_DSN` in the host OS environment of
   wherever the Electron app runs (student's machine). For installer
   builds, bake into the build env so end users don't need to set it.
6. Verify in container logs: `docker logs proctor-api | grep "\[sentry\]"`.
   You should see `[sentry] initialized`. Trigger a test exception
   from a dev tool to confirm it lands in Sentry.

### Privacy posture

Session replays and screenshot capture are **deliberately off** on all
frontends. Proctoring UIs surface student PII, exam answers, and live
camera state — none of which should leave the platform. Stack traces +
breadcrumbs are sufficient for triage.

## BetterStack uptime

**Monitor:** HTTP GET `https://app.procta.net/health`, 30-second
interval, expected status 200, timeout 10s, SSL verify ON.

`/health` returns 503 if any of these fail:

| Check | Where | Notes |
|---|---|---|
| Postgres reachable | `_atable("exam_config").select(...)` | hard fail |
| Disk > 500 MB free | `shutil.disk_usage(SCREENSHOTS_DIR)` | hard fail < 500 MB; warning < 2 GB |
| Storage write test | temp file create+delete | hard fail |
| Worker heartbeat < 60s old | Redis key `worker:last_heartbeat` | hard fail |
| Email provider configured | `EMAIL_PROVIDER` / `EMAIL_FROM` env | hard fail unless `noop` |
| Redis reachable | `redis.ping()` | soft (returns unavailable but still 200) |

Manually probe to see which check failed:
```sh
curl -s https://app.procta.net/health | jq .
```

**Escalation:** configured at BetterStack signup (email + SMS). Add a
Slack/Discord webhook under **Notifications → Integrations** when you
have a team channel.

## Backups

Three tiers. Run all three.

### 1. Local Postgres dumps (already on)

Ofelia in `docker-compose.yml` runs a daily `pg_dump` at 00:00 UTC
(05:30 IST) writing to `/root/proctor-browser/backups/postgres/`.
14-day retention.

```sh
# verify it's still rotating
ls -lh /root/proctor-browser/backups/postgres/ | tail
docker logs --tail 30 proctor-ofelia | grep daily-backup
```

### 2. S3 (Mumbai) off-site (install via the installer)

```sh
sudo /root/proctor-browser/scripts/install_s3_backup.sh
sudo vi /etc/procta/secrets.env       # fill/confirm AWS_ACCESS_KEY_ID / SECRET
sudo /root/proctor-browser/scripts/backup_to_s3.sh    # test now
tail -f /var/log/procta-s3-backup.log
```

Scheduled at 01:30 UTC daily (90 min after the local dump). What ships:
Postgres dump + `screenshots/` tar + `question_images/` tar, all to an
`ap-south-1` bucket. Bucket lifecycle keeps 30 days.

**Restore:** `scripts/restore_from_s3.sh` (read the script before running
— it's destructive).

**Why S3 and not B2:** `scripts/backup_to_b2.sh` (Backblaze) has no India
region — DB backups were leaving India while the evidence they reference
(screenshots, question images) already lives in S3 Mumbai, an inconsistent
data-residency posture against `docs/DPIA.md`. `install_s3_backup.sh`
retires the B2 cron entry when it installs the S3 one. `backup_to_b2.sh`
and `install_b2_backup.sh` stay in the repo as a manual off-site fallback
only — nothing schedules them anymore once the S3 installer has run.

### 3. Restic screenshots (optional, for compliance retention)

Use this only if you need long-tail forensic frame retention beyond
the B2 tar snapshots. Setup is documented in DEPLOY.md §2.5.

## TTL sweeper

`public.sweep_transient_rows()` deletes aged rows from
`google_oauth_states`, `email_otps`, `refresh_tokens`, `auth_sessions`,
`auth_events`. Runs every 6 hours under the leader worker via
`app/services/ttl_sweeper.py`. Retention windows (see
`migrations/phase86_ttl_sweeper.sql`):

| Table | Retention |
|---|---|
| `google_oauth_states` | 1 hour past expiry |
| `email_otps` | 7 days past use or expiry |
| `refresh_tokens` | 90 days past revoke or expiry |
| `auth_sessions` | 30 days past revoke |
| `auth_events` | 180 days past creation (compliance window) |

Force a manual sweep (safe — idempotent):
```sh
psql "$DATABASE_URL" -c "SELECT public.sweep_transient_rows();"
```

Disable in an emergency: `TTL_SWEEPER_DISABLED=1` env var, then restart
api containers.

## Fleet proctor health

On-device failures — `proctor_camera_failed` (no camera) and
`model_load_failed` (a model didn't load, e.g. the RetinaFace download
failing offline, or a build with a dead onnxruntime) — are POSTed by
proctor.py as **violations that succeed (200)**, so Sentry's exception
capture never sees them. Without a rate check a fleet-wide regression stays
invisible until a student reports it.

`app/services/fleet_health.py` computes the failure **rate** over a rolling
window (denominator = `proctor_boot`, i.e. how many proctors started):

- **Visibility:** `GET /api/v1/admin/status` → `metrics.proctor_health`
  (`boots`, `camera_failed_pct`, `model_load_failed_pct`, `degraded`). A breach
  flips the top-level `status` to `degraded`, so the BetterStack monitor on
  `/status` (above) catches it.
- **Proactive alert:** the leader worker runs `proctor_health_alert_loop` every
  `PROCTOR_HEALTH_ALERT_INTERVAL_SEC` (default 600s) and, on a breach, logs
  `[ALERT] Fleet proctor health DEGRADED …` (WARNING) and — when `SENTRY_DSN`
  is set — `capture_message(level=error)`, so it shows up as a Sentry issue you
  can alert-rule on.

Thresholds (env-tunable; defaults shown):

| Knob | Default | Meaning |
|---|---|---|
| `PROCTOR_HEALTH_WINDOW_MINS` | 60 | rolling window |
| `PROCTOR_HEALTH_MIN_BOOTS` | 5 | min boots before flagging (avoids 1/1 = 100% noise) |
| `PROCTOR_CAMERA_FAIL_PCT` | 20 | camera-failure rate that flags degraded |
| `PROCTOR_MODEL_FAIL_PCT` | 30 | model-load-failure rate that flags degraded |
| `PROCTOR_HEALTH_ALERT_DISABLED` | — | `=1` to disable the alert loop |

Triage a degraded reading: a spike in `model_load_failed` right after a
desktop release usually means a bad build (e.g. an un-bundled model or a
broken runtime) — check the latest tag and consider `scripts/rollback-release.sh`.
A `camera_failed` spike is more often environmental (drivers / permissions).

## CI / deploy

GitHub Actions workflow `deploy.yml` has two jobs:

- **tests** — runs on `ubuntu-latest` cloud runner (isolated from prod).
- **deploy** — runs on the self-hosted runner installed in
  `/root/actions-runner` on the prod VM. The runner connects outbound
  to GitHub; no inbound SSH dependency.

```sh
# verify the runner is up
systemctl status actions.runner.ArihantK15-proctor-browser.srv1675832
# or: systemctl status 'actions.runner.*'
```

Deploy script is inline in the workflow. It `git fetch + reset --hard`s
`/root/proctor-browser`, rebuilds containers, runs the migration runner
preflight, restarts api + workers + caddy, then polls the healthcheck
for up to 180s.

## Triage cheatsheet

### `/health` returns 503

Hit the URL manually to see which check failed (above). Common causes:

- **`database: error`** → Postgres container down or full disk.
  `docker compose ps postgres` and `df -h /`.
- **`disk: critical`** → screenshots dir filled. `du -sh
  /root/proctor-browser/screenshots/*` and either prune old ones or
  run the restic backup + delete.
- **`worker: stale` / `no_heartbeat`** → RQ worker died.
  `docker compose logs worker --tail=100`.
- **`email: misconfigured`** → `RESEND_API_KEY` missing/expired.

### BetterStack pages but `/health` is fine

The monitor's view of the network differs from yours. Try from a
different network — if it resolves, BetterStack saw transient DNS or
TLS flakiness. Pause the monitor briefly; investigate; resume.

### Sentry receiving nothing after activation

1. `docker logs proctor-api | grep "\[sentry\]"` — should say
   `initialized`. If `init failed`, fix the DSN format.
2. From an admin endpoint or the dashboard ops panel, check
   `release.sentry_configured` (set in `app/routers/admin_status.py`).
3. Trigger a manual exception (e.g. visit a broken admin URL) and
   confirm it appears in the Sentry feed within ~30s.

### Backup gap

- **Local dumps stop rotating:** `docker logs proctor-ofelia` for
  errors, `docker compose restart ofelia`.
- **S3 hasn't received fresh files:** `tail -50
  /var/log/procta-s3-backup.log`. Usually expired/rotated AWS credentials in
  `/etc/procta/secrets.env`.
- **No local OR S3 backup last 24h:** assume snapshot lag and
  manually trigger both: the Ofelia cmd from `docker-compose.yml`
  then `sudo /root/proctor-browser/scripts/backup_to_s3.sh`.

### Self-hosted runner offline

CI deploys queue forever. Check:
```sh
systemctl status 'actions.runner.*'
sudo journalctl -u 'actions.runner.*' --since '1 hour ago'
```

Restart: `systemctl restart actions.runner.<full-name>`.

### Quota trigger blocking a legitimate enroll

If `RAISE EXCEPTION 'Student quota exceeded'` is firing on a real
upgrade-in-progress org, update `organizations.max_students` to match
their new plan BEFORE the next insert:

```sql
UPDATE organizations SET max_students = <new_cap> WHERE id = '<org-uuid>';
```

This matches what `billing.py` would do on a successful plan upgrade.

## Reading the schema

Database invariants enforced by the schema (phases 80-91) are
documented in their migration headers. For a one-shot overview of
which constraints are live:

```sql
-- FKs
SELECT conname, conrelid::regclass, convalidated FROM pg_constraint
 WHERE contype='f' ORDER BY conrelid::regclass::text;

-- RLS coverage
SELECT tablename, COUNT(*) FROM pg_policies GROUP BY tablename ORDER BY 1;

-- CHECK constraints
SELECT conname, conrelid::regclass FROM pg_constraint
 WHERE contype='c' AND conname ~ '_check$' ORDER BY conname;

-- Composite UNIQUEs
SELECT conrelid::regclass, conname FROM pg_constraint
 WHERE contype='u' AND array_length(conkey,1) > 1 ORDER BY 1;
```
