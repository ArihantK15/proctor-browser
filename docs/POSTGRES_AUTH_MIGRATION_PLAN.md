# Plain Postgres + Local Auth Migration Plan

This is the long-term migration away from Supabase Auth/PostgREST to a
KVM-hosted Postgres database and Procta-owned authentication.

## Guiding Rules

- Keep current production Supabase untouched until the KVM stack passes
  authenticated end-to-end tests.
- Ship changes behind environment flags first.
- Preserve existing table IDs and app JWT claims so dashboard, exam, and
  worker code keep working while the storage layer changes underneath.
- Do not publish "real DB-backed 500 students" claims until an authenticated
  seeded run writes and verifies database rows.

## Phase 1: Local Auth Bridge

Status: implemented behind `AUTH_PROVIDER=local`.

Code changes:

- Add `password_hash`, `auth_provider`, and `password_changed_at` columns to
  `teachers` and `student_accounts`.
- Add `AUTH_PROVIDER=local` support for email/password signup, login, refresh,
  reauth, password reset, and org-invite acceptance.
- Keep Supabase as the default auth provider until the migration is deliberate.

Deployment order:

1. Apply `migrations/phase60_local_auth.sql`.
2. Deploy code with `AUTH_PROVIDER` unset or `AUTH_PROVIDER=supabase`.
3. Confirm existing Supabase login still works.
4. On staging only, set `AUTH_PROVIDER=local`.
5. Create a new teacher and student account.
6. Verify login, refresh, password reset, 2FA reauth, dashboard, and exam access.

Rollback:

- Set `AUTH_PROVIDER=supabase`.
- Restart API.
- Existing Supabase users continue to authenticate through Supabase.

**⚠ Rollback gotcha — accounts created during the local-auth window cannot
roll back.** Any teacher/student that signs up while `AUTH_PROVIDER=local`
has only a bcrypt hash in our DB, with no corresponding row in
Supabase Auth. Flipping back to `AUTH_PROVIDER=supabase` will lock those
users out (their login fails because Supabase has no record of them).
Two safe options if you need to roll back after such signups exist:

- Keep `AUTH_PROVIDER=local` for the affected users and roll back only
  the other code paths.
- Bulk-create Supabase Auth users for everyone with
  `auth_provider='local'` using a `password_reset_for_email` flow, then
  flip.

This is why Phase 5 cutover should happen after a maintenance window in
which we are committed — not as a flag-flip experiment in production.

## Phase 2: KVM Postgres

Add a Postgres service with persistent storage and private Docker networking.
Keep it unavailable from the public internet.

Required before import:

- Daily `pg_dump` backup script.
- Local restore drill.
- Disk monitoring.
- Postgres user roles:
  - app runtime role
  - migration/admin role
  - read-only reporting role

### Backups

`docker-compose.yml` ships two services behind the `postgres` profile:

- `postgres` — the database itself, exposed only on the docker network.
- `ofelia` — a 3 MB cron-for-docker sidecar that reads `ofelia.*`
  labels on the postgres container and runs the daily backup at
  00:00 UTC (05:30 IST, before customer traffic). Output goes to
  `./backups/postgres/procta-<TIMESTAMP>.dump` on the host.
  14-day retention is built into the schedule.

Start the whole stack:

```bash
export POSTGRES_PASSWORD='<strong unique password>'
docker compose --profile postgres up -d
```

Container limits are now KVM-sized by default, but still overrideable from
`.env`. The "MEM USAGE / LIMIT" value in `docker stats` is the container cap,
not the host's 16 GB RAM. Relevant knobs:

```bash
API_MEMORY_LIMIT=4g
API_CPU_LIMIT=3.0
POSTGRES_MEMORY_LIMIT=6g
POSTGRES_CPU_LIMIT=3.0
```

Verify the first backup ran:

```bash
ls -la backups/postgres/
```

Manual backup (any time):

```bash
./scripts/backup_postgres.sh
```

Restore drill (do this once before going live so you know it works):

```bash
docker compose --profile postgres exec -T postgres \
  pg_restore -U procta -d procta --clean --if-exists \
  < backups/postgres/procta-<TIMESTAMP>.dump
```

## Phase 3: Database Transport Layer

Replace Supabase PostgREST calls with a Postgres-backed query adapter.

Status: implemented behind `DATABASE_BACKEND=postgres`.

Approach:

- Keep `async_table(...)` as the compatibility boundary initially.
- Implement a `DATABASE_BACKEND=postgres` path using async Postgres.
- Support the subset currently used by the app:
  - `select`
  - `insert`
  - `upsert`
  - `update`
  - `delete`
  - filters: `eq`, `neq`, `is`, `in`, ranges, ordering, limits
- Move hot paths to explicit repository functions after the compatibility
  adapter is stable.
- Startup uses `scripts/run_postgres_migrations.py` when
  `DATABASE_BACKEND=postgres`; otherwise it keeps the existing Supabase
  migration runner.

## Phase 4: Data Export and Import

From Supabase:

- Export schema (`pg_dump --schema-only --no-owner --no-privileges`).
- Export data (`pg_dump --data-only --no-owner --no-privileges`).
- Export auth users only for account-mapping, not password hashes.

Into KVM Postgres:

- Restore schema and data.
- Preserve `teachers.id`, `student_accounts.id`, `students`, `exam_config`,
  `exam_sessions`, `answers`, `violations`, org/billing/invite tables, and audit
  tables.
- Existing users must reset passwords because Supabase password hashes are not
  portable through the public API.

Recommended concrete cutover flow:

```bash
# 1. On the KVM, create a custom-format dump from the direct Supabase DB URI.
#    This uses a postgres client container, so pg_dump does not need to be
#    installed on the host.
export SUPABASE_DB_URL='postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres'
./scripts/backup_supabase.sh

# 2. Restore the newest Supabase dump into the private postgres container.
latest_dump="$(ls -t backups/supabase/procta-supabase-*.dump | head -1)"
docker compose --profile postgres up -d postgres
docker compose --profile postgres exec -T postgres \
  pg_restore -U procta -d procta --clean --if-exists \
  < "$latest_dump"

# 3. Apply local migrations directly to KVM Postgres.
docker compose --profile postgres run --rm \
  --entrypoint python \
  -e DATABASE_BACKEND=postgres \
  -e DATABASE_URL="postgresql://procta:${POSTGRES_PASSWORD}@postgres:5432/procta" \
  api scripts/run_postgres_migrations.py

# 4. Verify the restored schema shape before changing app env.
docker compose --profile postgres run --rm \
  --entrypoint python \
  -e DATABASE_URL="postgresql://procta:${POSTGRES_PASSWORD}@postgres:5432/procta" \
  api scripts/verify_postgres_cutover.py

# 5. Take a local Postgres backup after restore + migrations pass.
./scripts/backup_postgres.sh
```

**Migration application order on the fresh KVM Postgres:**

1. Restore schema dump from Supabase (creates all base tables: `teachers`,
   `student_accounts`, `exam_sessions`, `answers`, `violations`, etc.).
2. Apply any local migrations that are NOT yet reflected in the Supabase
   schema dump, in numeric order: `phase01..phase60`. The dump captures
   the live state of Supabase, so any migration that hasn't been applied
   there yet must be applied here too.
3. Restore data dump.
4. Apply `phase60_local_auth.sql` *only if not already in the schema dump*.

Apply migrations idempotently — every migration in this repo uses
`IF NOT EXISTS` guards, so running them twice is safe.

`scripts/run_postgres_migrations.py` records files in `schema_migrations` and
creates a harmless `auth.uid()` compatibility shim so old Supabase RLS policy
SQL can compile on plain Postgres.

**Required env vars on the KVM API container:**

| Var | Required | Default | Notes |
|---|---|---|---|
| `DATABASE_URL` | Yes (when `DATABASE_BACKEND=postgres`) | — | `postgresql://procta:PASS@postgres:5432/procta` |
| `DATABASE_BACKEND` | No | `supabase` | Set to `postgres` to flip storage layer |
| `AUTH_PROVIDER` | No | `supabase` | Use `hybrid` during transition; set to `local` only after legacy users have local hashes/OAuth |
| `POSTGRES_POOL_MIN` | No | `3` | Warm connections for snappier first requests |
| `POSTGRES_POOL_MAX` | No | `10` | Set to ~ `2 * uvicorn_workers + headroom` |
| `POSTGRES_COMMAND_TIMEOUT` | No | `15` | Seconds. Long-running batch jobs may need more |
| `POSTGRES_PASSWORD` | Yes (on the postgres compose profile) | — | Read by docker-compose for the postgres service |
| `POSTGRES_USER` | No | `procta` | docker-compose default |
| `POSTGRES_DB` | No | `procta` | docker-compose default |

## Phase 5: Auth Cutover

1. Put dashboard in a planned maintenance window.
2. Stop writes briefly, or at least block new signups/submits while the final
   dump is taken.
3. Restore the final dump into KVM Postgres.
4. Run `scripts/run_postgres_migrations.py`.
5. Run `scripts/verify_postgres_cutover.py`.
6. Set:

```bash
DATABASE_BACKEND=postgres
AUTH_PROVIDER=local
DATABASE_URL=postgresql://procta:<password>@postgres:5432/procta
POSTGRES_POOL_MIN=3
POSTGRES_POOL_MAX=20
```

7. Restart API and workers:

```bash
docker compose --profile postgres up -d --force-recreate api worker autosave-worker caddy
```

8. Verify:
   - `/health`
   - teacher login
   - student login
   - create exam
   - invite flow
   - validate student
   - autosave
   - submit
   - scorecard
   - password reset

Rollback:

1. Restore previous `.env` with `DATABASE_BACKEND=supabase` and
   `AUTH_PROVIDER=supabase`.
2. Restart API and workers.
3. Point DNS/app back only if needed.
4. Remember the local-auth rollback gotcha above: any users created after
   `AUTH_PROVIDER=local` will not exist in Supabase Auth.

## Phase 6: Proof Test

Run a seeded authenticated test against the KVM Postgres stack:

- 500 test students.
- Real validate/login/session creation.
- Real autosave persistence.
- Real final submit rows.
- DB verification queries after the run.

Public claim only after this passes:

> Procta completed a database-backed load test with 500 authenticated exam
> sessions on production-equivalent infrastructure.
