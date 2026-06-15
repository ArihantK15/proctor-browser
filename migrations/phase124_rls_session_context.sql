-- =====================================================================
-- phase124: Session-context RLS — real DB-level tenant isolation
-- =====================================================================
-- Re-bases Procta's RLS off the dormant Supabase `auth.uid()` model onto
-- per-transaction session context (SET LOCAL app.*), so the asyncpg backend
-- gets ENFORCED tenant isolation instead of app-layer-only scoping.
--
-- SAFE TO APPLY NOW: the app still connects as the OWNER (`procta`), which
-- bypasses RLS, so these policies change NOTHING until the cutover
-- (docs/TENANCY_RLS_HARDENING.md step D: app connects as restricted
-- `procta_app` + RLS_SESSION_CONTEXT=1). Fully idempotent — re-runnable.
--
-- Context the app sets per query (empty string => NULL via nullif):
--   app.role        superadmin | admin | owner | teacher | student | system
--   app.teacher_id  caller's teachers.id
--   app.org_id      caller's organizations.id
--   app.account_id  caller's student_accounts.id
-- =====================================================================

BEGIN;

-- ── Context accessors (schema `app`) ─────────────────────────────────
CREATE SCHEMA IF NOT EXISTS app;

CREATE OR REPLACE FUNCTION app.role()       RETURNS text LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.role',       true), '') $$;
CREATE OR REPLACE FUNCTION app.teacher_id() RETURNS text LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.teacher_id', true), '') $$;
CREATE OR REPLACE FUNCTION app.org_id()     RETURNS text LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.org_id',     true), '') $$;
CREATE OR REPLACE FUNCTION app.account_id() RETURNS text LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.account_id', true), '') $$;

CREATE OR REPLACE FUNCTION app.is_privileged() RETURNS boolean
  LANGUAGE sql STABLE AS $$ SELECT app.role() IN ('superadmin','system') $$;

-- READ scope: teacher -> self; admin/owner -> every teacher in their org
-- (this is "an admin can see the teacher"). superadmin/system bypass via the
-- is_privileged() clause baked into each policy.
-- SECURITY DEFINER: these resolve the tenant mapping over the FULL teachers/
-- students tables and must NOT be re-filtered by those tables' own RLS (and must
-- not recurse). Owned by `procta` (the migration runner / table owner), so they
-- bypass RLS to compute the mapping — same pattern as the legacy get_my_*.
CREATE OR REPLACE FUNCTION app.visible_teacher_ids() RETURNS SETOF text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT app.teacher_id()
   WHERE app.role() = 'teacher' AND app.teacher_id() IS NOT NULL
  UNION
  SELECT t.id::text FROM teachers t
   WHERE app.role() IN ('admin','owner')
     AND app.org_id() IS NOT NULL
     AND t.org_id::text = app.org_id()
$$;

-- Student -> the roll numbers tied to their account.
CREATE OR REPLACE FUNCTION app.my_roll_numbers() RETURNS SETOF text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT s.roll_number::text FROM students s
   WHERE app.account_id() IS NOT NULL
     AND s.account_id::text = app.account_id()
$$;

-- Helper: drop EVERY existing policy on a table (clears the dormant
-- auth.uid() policies) so we end in a known canonical state.
CREATE OR REPLACE FUNCTION app._drop_all_policies(tbl regclass) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE p record;
BEGIN
  FOR p IN SELECT polname FROM pg_policy WHERE polrelid = tbl LOOP
    EXECUTE format('DROP POLICY %I ON %s', p.polname, tbl::text);
  END LOOP;
END $$;

-- ── Teacher-owned tables: SELECT = org-visible, WRITE = own teacher ───
-- (mirrors the app: reads org-scoped for admins, mutations self-scoped)
DO $$
DECLARE t text;
  tabs text[] := ARRAY[
    'students','exam_config','questions','question_bank','exam_sessions',
    'violations','answers','student_groups','student_group_members',
    'exam_group_assignments','exam_templates','api_keys','appeals',
    'grading_audit','exam_time_extensions','question_versions',
    'exam_batch_assignments','student_invites'
  ];
BEGIN
  FOREACH t IN ARRAY tabs LOOP
    BEGIN
      PERFORM app._drop_all_policies(t::regclass);
      EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
      EXECUTE format($q$CREATE POLICY %1$s_t_sel ON %1$I FOR SELECT
        USING (app.is_privileged() OR teacher_id::text IN (SELECT app.visible_teacher_ids()))$q$, t);
      EXECUTE format($q$CREATE POLICY %1$s_t_ins ON %1$I FOR INSERT
        WITH CHECK (app.is_privileged() OR teacher_id::text = app.teacher_id())$q$, t);
      EXECUTE format($q$CREATE POLICY %1$s_t_upd ON %1$I FOR UPDATE
        USING (app.is_privileged() OR teacher_id::text = app.teacher_id())$q$, t);
      EXECUTE format($q$CREATE POLICY %1$s_t_del ON %1$I FOR DELETE
        USING (app.is_privileged() OR teacher_id::text = app.teacher_id())$q$, t);
    EXCEPTION WHEN undefined_table OR undefined_column THEN
      RAISE NOTICE 'phase124 skip teacher-table %: %', t, SQLERRM;
    END;
  END LOOP;
END $$;

-- ── Org-owned tables: SELECT/WRITE scoped to the caller's org ─────────
DO $$
DECLARE t text;
  tabs text[] := ARRAY[
    'subscriptions','usage_records','org_invites','overage_charges',
    'billing_events','org_auth_settings','org_support_overrides'
  ];
BEGIN
  FOREACH t IN ARRAY tabs LOOP
    BEGIN
      PERFORM app._drop_all_policies(t::regclass);
      EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
      EXECUTE format($q$CREATE POLICY %1$s_o_sel ON %1$I FOR SELECT
        USING (app.is_privileged() OR org_id::text = app.org_id())$q$, t);
      EXECUTE format($q$CREATE POLICY %1$s_o_ins ON %1$I FOR INSERT
        WITH CHECK (app.is_privileged() OR (app.role() IN ('admin','owner') AND org_id::text = app.org_id()))$q$, t);
      EXECUTE format($q$CREATE POLICY %1$s_o_upd ON %1$I FOR UPDATE
        USING (app.is_privileged() OR (app.role() IN ('admin','owner') AND org_id::text = app.org_id()))$q$, t);
      EXECUTE format($q$CREATE POLICY %1$s_o_del ON %1$I FOR DELETE
        USING (app.is_privileged() OR (app.role() IN ('admin','owner') AND org_id::text = app.org_id()))$q$, t);
    EXCEPTION WHEN undefined_table OR undefined_column THEN
      RAISE NOTICE 'phase124 skip org-table %: %', t, SQLERRM;
    END;
  END LOOP;
END $$;

-- ── teachers: own row; admins/owners read their whole org ────────────
SELECT app._drop_all_policies('teachers'::regclass);
ALTER TABLE teachers ENABLE ROW LEVEL SECURITY;
CREATE POLICY teachers_sel ON teachers FOR SELECT
  USING (app.is_privileged() OR id::text = app.teacher_id()
         OR (app.role() IN ('admin','owner') AND org_id::text = app.org_id()));
CREATE POLICY teachers_ins ON teachers FOR INSERT
  WITH CHECK (app.is_privileged() OR id::text = app.teacher_id());
CREATE POLICY teachers_upd ON teachers FOR UPDATE
  USING (app.is_privileged() OR id::text = app.teacher_id());

-- ── organizations: caller's own org; owner may update ────────────────
SELECT app._drop_all_policies('organizations'::regclass);
ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;
CREATE POLICY orgs_sel ON organizations FOR SELECT
  USING (app.is_privileged() OR id::text = app.org_id());
CREATE POLICY orgs_upd ON organizations FOR UPDATE
  USING (app.is_privileged() OR (app.role() = 'owner' AND id::text = app.org_id()));

-- ── student_accounts: own account only ───────────────────────────────
SELECT app._drop_all_policies('student_accounts'::regclass);
ALTER TABLE student_accounts ENABLE ROW LEVEL SECURITY;
CREATE POLICY sa_sel ON student_accounts FOR SELECT
  USING (app.is_privileged() OR id::text = app.account_id());
CREATE POLICY sa_upd ON student_accounts FOR UPDATE
  USING (app.is_privileged() OR id::text = app.account_id());

-- ── Student-facing ADDITIVE read/write (layered on the teacher policies
--    above; multiple permissive policies are OR'd) ─────────────────────
-- students: a student sees their own roster rows
CREATE POLICY students_s_sel ON students FOR SELECT
  USING (app.role() = 'student' AND account_id::text = app.account_id());
-- exam_sessions: a student reads/owns sessions for their roll numbers
CREATE POLICY exam_sessions_s_sel ON exam_sessions FOR SELECT
  USING (app.role() = 'student' AND roll_number::text IN (SELECT app.my_roll_numbers()));
CREATE POLICY exam_sessions_s_ins ON exam_sessions FOR INSERT
  WITH CHECK (app.role() = 'student' AND roll_number::text IN (SELECT app.my_roll_numbers()));
CREATE POLICY exam_sessions_s_upd ON exam_sessions FOR UPDATE
  USING (app.role() = 'student' AND roll_number::text IN (SELECT app.my_roll_numbers()));
-- answers: student reads/writes answers for their own sessions
CREATE POLICY answers_s_all ON answers FOR ALL
  USING (app.role() = 'student' AND session_key::text IN (
           SELECT es.session_key::text FROM exam_sessions es
            WHERE es.roll_number::text IN (SELECT app.my_roll_numbers())))
  WITH CHECK (app.role() = 'student' AND session_key::text IN (
           SELECT es.session_key::text FROM exam_sessions es
            WHERE es.roll_number::text IN (SELECT app.my_roll_numbers())));
-- appeals: student reads/files their own
CREATE POLICY appeals_s_sel ON appeals FOR SELECT
  USING (app.role() = 'student' AND student_id::text = app.account_id());
CREATE POLICY appeals_s_ins ON appeals FOR INSERT
  WITH CHECK (app.role() = 'student' AND student_id::text = app.account_id());
-- violations: student reads their own session evidence; client inserts flags
CREATE POLICY violations_s_sel ON violations FOR SELECT
  USING (app.role() = 'student' AND session_key::text IN (
           SELECT es.session_key::text FROM exam_sessions es
            WHERE es.roll_number::text IN (SELECT app.my_roll_numbers())));
CREATE POLICY violations_s_ins ON violations FOR INSERT
  WITH CHECK (app.role() = 'student' AND session_key::text IN (
           SELECT es.session_key::text FROM exam_sessions es
            WHERE es.roll_number::text IN (SELECT app.my_roll_numbers())));

-- ── Privileged-only tables (no clean tenant column; superadmin/system
--    + the relevant authed flows reach them via the app's service paths) ─
DO $$
DECLARE t text;
  tabs text[] := ARRAY['coupons','breach_incidents','objection_records'];
BEGIN
  FOREACH t IN ARRAY tabs LOOP
    BEGIN
      PERFORM app._drop_all_policies(t::regclass);
      EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
      EXECUTE format($q$CREATE POLICY %1$s_priv ON %1$I FOR ALL
        USING (app.is_privileged()) WITH CHECK (app.is_privileged())$q$, t);
    EXCEPTION WHEN undefined_table THEN
      RAISE NOTICE 'phase124 skip priv-table %: %', t, SQLERRM;
    END;
  END LOOP;
END $$;

-- Bookkeeping so a later CI deploy's runner SKIPs this manually-applied file.
INSERT INTO schema_migrations (filename) VALUES ('phase124_rls_session_context.sql')
  ON CONFLICT DO NOTHING;

COMMIT;

-- =====================================================================
-- POST-APPLY DISCOVERY (run manually; NOT part of the transaction):
-- Find any public table that is NOT yet RLS-enabled — each is a
-- potential leak once the app runs as the restricted role. Triage every
-- result into teacher/org/student/privileged before the cutover (step D).
--
--   SELECT c.relname
--     FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
--    WHERE n.nspname='public' AND c.relkind='r' AND NOT c.relrowsecurity
--    ORDER BY 1;
-- =====================================================================
