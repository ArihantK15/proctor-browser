# Tenancy / RLS Hardening — DB-level enforcement

**Goal:** zero cross-organization and cross-teacher data leakage, enforced at the
**database** (not just the app layer). An org **admin** may read all of their
own org's teachers' data; no one sees across org boundaries. Defense-in-depth
behind the existing app-layer scoping (`resolve_scope` / `.eq("teacher_id")`).

Status: **planning + staged build**. Nothing in this plan changes app behavior
until the final **cutover** step (4), which is gated on staging tests.

---

## 1. Why the current RLS is dormant (the problem)

- The app connects via asyncpg as **`procta`**, the schema **owner**. Table
  owners **bypass RLS** (unless `FORCE ROW LEVEL SECURITY`). So the 27 RLS'd
  tables don't constrain the app at all.
- Every existing policy keys off `get_my_teacher_id()` → `auth.uid()`
  (Supabase/PostgREST). Under raw asyncpg `auth.uid()` is never set → the helper
  returns NULL → policies are dead (deny-all if they applied; moot because the
  owner bypasses them).
- `asyncpg.create_pool` sets **no** per-connection/per-request context, and
  **pgbouncer runs `POOL_MODE=transaction`** — so the only context mechanism
  that survives is `SET LOCAL` *inside an explicit transaction* per query.
- **Net today:** tenancy is enforced **only** in the app. A missed
  `.eq("teacher_id")` anywhere = a cross-tenant leak with no DB backstop.

## 2. Target mechanism (session-context RLS)

1. App connects as a **restricted role `procta_app`** — `LOGIN`, `NOSUPERUSER`,
   `NOBYPASSRLS`, **not** the table owner. DDL/migrations stay on `procta`.
2. Per query, the execute layer opens a transaction and emits, from the
   authenticated request:
   ```sql
   SET LOCAL app.role        = '<superadmin|admin|owner|teacher|student|system>';
   SET LOCAL app.teacher_id  = '<uuid or empty>';
   SET LOCAL app.org_id      = '<uuid or empty>';
   SET LOCAL app.account_id  = '<student account uuid or empty>';
   ```
   `SET LOCAL` is transaction-scoped → pgbouncer-transaction-pool safe.
3. Policies read that context via NULL-safe accessors (below) — never `auth.uid()`.
4. **Background workers / no request** (reaper, billing, reconciler, RQ jobs)
   run under `app.role='system'` → policies grant full cross-tenant access (they
   are cross-tenant by design).

### Context accessors (schema `app`)

```sql
CREATE SCHEMA IF NOT EXISTS app;
CREATE OR REPLACE FUNCTION app.role()        RETURNS text LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.role',       true), '') $$;
CREATE OR REPLACE FUNCTION app.teacher_id()  RETURNS text LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.teacher_id', true), '') $$;
CREATE OR REPLACE FUNCTION app.org_id()      RETURNS text LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.org_id',     true), '') $$;
CREATE OR REPLACE FUNCTION app.account_id()  RETURNS text LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.account_id', true), '') $$;

-- Cross-cutting: which teacher_ids the current principal may READ.
--   teacher  -> just self
--   admin/owner -> every teacher in their org   (this is "admin can see the teacher")
--   superadmin/system handled separately (full bypass clause in each policy)
CREATE OR REPLACE FUNCTION app.visible_teacher_ids() RETURNS SETOF text
LANGUAGE sql STABLE AS $$
  SELECT app.teacher_id()
   WHERE app.role() = 'teacher' AND app.teacher_id() IS NOT NULL
  UNION
  SELECT t.id::text FROM teachers t
   WHERE app.role() IN ('admin','owner')
     AND app.org_id() IS NOT NULL
     AND t.org_id::text = app.org_id()
$$;

CREATE OR REPLACE FUNCTION app.is_privileged() RETURNS boolean
LANGUAGE sql STABLE AS $$ SELECT app.role() IN ('superadmin','system') $$;
```

## 3. Policy matrix (the precise model)

**Read vs write split (mirrors current app behavior):**
- **SELECT** on teacher-owned tables: `app.is_privileged() OR teacher_id::text IN (SELECT app.visible_teacher_ids())` → admin reads org-wide, teacher reads own.
- **INSERT/UPDATE/DELETE** on teacher-owned tables: `app.is_privileged() OR teacher_id::text = app.teacher_id()` → writes stay **own-teacher** even for admins (matches the app: reads are org-scoped, session mutations are self-scoped). Org-wide admin writes are a deliberate later opt-in, not default.

| Table | Tenant col | SELECT | WRITE |
|---|---|---|---|
| teachers | id / org_id | privileged OR id=teacher_id OR (admin/owner AND org_id=app.org_id) | privileged OR id=teacher_id |
| organizations | id | privileged OR id=app.org_id() | privileged OR (owner AND id=app.org_id()) |
| students, exam_config, questions, question_bank, exam_sessions, violations, answers, student_groups, student_group_members, exam_group_assignments, exam_templates, api_keys, appeals, grading_audit, exam_batch_assignments, exam_time_extensions, question_versions, student_invites, google_* , issues, admin_audit_log | teacher_id | standard teacher SELECT | standard teacher WRITE |
| subscriptions, usage_records, org_invites, overage_charges, billing_events, breach_incidents, objection_records, consent_records, org_auth_settings, org_support_overrides | org_id (or user_id+type) | privileged OR org_id=app.org_id() | privileged OR (admin/owner AND org_id=app.org_id()) |
| **Student-facing** (student_accounts, students-as-student, exam_sessions, answers, appeals) | account_id / roll | + clause: `app.account_id()` / roll match when `app.role()='student'` | student INSERT/UPDATE own |
| coupons (global), demo_requests, PLANS-like config | none | privileged only (or anon-insert for demo) | privileged only |
| auth_sessions, refresh_tokens, email_otps, auth_events | account/teacher fk | own principal only | own principal only |

**Default-deny posture:** every table in `public` gets `ENABLE ROW LEVEL
SECURITY`. A table with RLS enabled and **no** policy denies all to `procta_app`
(safe). The cutover checklist (below) enumerates every table from
`pg_tables` and asserts each is either covered by a policy or intentionally
privileged-only — so nothing is silently wide-open.

## 4. Staged rollout (no prod breakage until step D)

- **A. Policies (inert).** Apply `phase124_rls_session_context.sql`: create `app`
  schema + accessors, DROP the old `auth.uid()` policies, CREATE the
  `current_setting`-based policies, ENABLE RLS on all tenant tables incl. the gap
  tables. Inert because `procta` (owner) still bypasses. Reversible.
- **B. App plumbing (flagged off).** `RLS_SESSION_CONTEXT=0` by default. Add a
  request middleware that stashes `{role, teacher_id, org_id, account_id}` in a
  `ContextVar`; the asyncpg execute layer wraps each statement in a tx and emits
  `SET LOCAL app.*` when the flag is on. Workers set `app.role='system'`. With
  the flag off, behavior is byte-identical to today.
- **C. Restricted role + grants (prod psql — run as superuser/`procta` owner).**
  Idempotent. Do **not** point the app at it yet.
  ```sql
  DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='procta_app') THEN
      CREATE ROLE procta_app LOGIN PASSWORD 'SET_A_REAL_SECRET'
        NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    END IF;
  END $$;
  GRANT USAGE ON SCHEMA public TO procta_app;
  GRANT USAGE ON SCHEMA app    TO procta_app;
  GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES   IN SCHEMA public TO procta_app;
  GRANT USAGE, SELECT                  ON ALL SEQUENCES IN SCHEMA public TO procta_app;
  GRANT EXECUTE                        ON ALL FUNCTIONS IN SCHEMA app    TO procta_app;
  GRANT EXECUTE                        ON ALL FUNCTIONS IN SCHEMA public TO procta_app;
  -- future objects created by procta inherit the grants:
  ALTER DEFAULT PRIVILEGES FOR ROLE procta GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES   TO procta_app;
  ALTER DEFAULT PRIVILEGES FOR ROLE procta GRANT USAGE,SELECT                ON SEQUENCES TO procta_app;
  ALTER DEFAULT PRIVILEGES FOR ROLE procta GRANT EXECUTE                     ON FUNCTIONS TO procta_app;
  ```
  `procta_app` is a non-owner, so RLS applies to it (no `FORCE` needed). The
  `SECURITY DEFINER` helpers run as `procta` and read the full teachers/students.
  **pgbouncer:** add `procta_app` to the pgbouncer userlist / auth_query so the
  app can authenticate through it (infra prereq, not SQL).

- **C-verify (PROVE isolation before any cutover — connect AS `procta_app`).**
  Owner bypasses RLS, so this MUST run as `procta_app`:
  ```sql
  BEGIN;  -- teacher A sees only their rows
  SELECT set_config('app.role','teacher',true), set_config('app.teacher_id','<TEACHER_A_UUID>',true);
  SELECT count(*) FROM exam_sessions WHERE teacher_id='<TEACHER_B_UUID>';  -- MUST be 0
  ROLLBACK;
  BEGIN;  -- admin sees their whole org, nothing outside it
  SELECT set_config('app.role','admin',true), set_config('app.org_id','<ORG_UUID>',true);
  SELECT count(*) FROM teachers WHERE org_id <> '<ORG_UUID>';              -- MUST be 0
  ROLLBACK;
  BEGIN;  -- system = full access (workers)
  SELECT set_config('app.role','system',true);
  SELECT count(*) FROM exam_sessions;                                      -- all rows
  ROLLBACK;
  ```
- **D. Cutover (staging first, then prod).** Prereqs: A applied, C applied +
  C-verify green, pgbouncer knows `procta_app`.
  **Migration DSN (required):** the startup migration runner does DDL
  (incl. `_ensure_bookkeeping`'s CREATE EXTENSION/SCHEMA/TABLE) and CANNOT run as
  `procta_app`. Set `MIGRATIONS_DATABASE_URL` to the owner, straight to postgres
  (not pgbouncer): `postgresql://procta:<pw>@postgres:5432/procta`.
  `run_postgres_migrations.py` prefers it over `DATABASE_URL`; without it the app
  crash-loops on `permission denied for database procta` at startup.
  1. **Staging:** `RLS_SESSION_CONTEXT=1` + `DATABASE_URL`→`procta_app` + `MIGRATIONS_DATABASE_URL`→owner, restart.
     Run the full test suite + §5 probes + app smoke (login, exam, dashboard,
     chat). Watch logs for `permission denied` / unexpected 0-row results.
  2. **Prod (off-peak):** same env change + restart; re-run §5 probes on a canary.
  3. **Rollback:** revert `DATABASE_URL`→`procta` and `RLS_SESSION_CONTEXT=0`,
     restart. Policies stay (inert under the owner).

## 5. Cutover verification (run in staging, then prod-canary)
- Cross-tenant probe: as teacher A's context, `SELECT count(*)` on every tenant
  table filtered to teacher B's id → must be 0.
- Admin probe: as org-admin context, can read all org teachers' rows, **zero**
  rows from another org.
- Worker probe: `app.role='system'` sees everything (reaper/billing keep working).
- App smoke: login, lobby, exam start, heartbeat, submit, dashboard live, chat.

## 6. Rollback
Each step is independently reversible: D = revert env (URL + flag); C = leave
role unused; B = flag off; A = re-apply prior policy file. Policies never block
`procta` (owner), so A can sit in prod indefinitely with zero effect until D.
