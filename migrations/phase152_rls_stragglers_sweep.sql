-- migration:contract full sweep for tables still on auth.uid()-based RLS policies (get_my_teacher_id/get_my_student_account_id), per task_c182396d. admin_audit_log, consent_records, demo_requests would deny-all at the procta_app RLS cutover exactly like invite_send_counters did before phase148. Reverse: migrations/down/phase152_rls_stragglers_sweep.sql
-- =====================================================================
-- Phase 152 — RLS straggler sweep: admin_audit_log, consent_records,
-- demo_requests → app.* session-context model (phase124)
-- =====================================================================
-- task_c182396d (audit_rls_cutover_2026_06 memory) called for a full
-- sweep of every RLS'd table still keyed on the old auth.uid()-based
-- helpers (get_my_teacher_id / get_my_student_account_id / auth.uid()
-- directly) before the procta_app cutover — phase148 fixed the one
-- confirmed straggler at the time (invite_send_counters). This sweep,
-- run against every function whose body resolves via auth.uid()
-- (get_my_teacher_id, get_my_student_account_id, get_my_roll_numbers),
-- found three more:
--   * admin_audit_log.admin_audit_log_teacher_select (SELECT only —
--     its INSERT policy was already migrated)
--   * consent_records — all four of its policies (student + teacher,
--     insert + select)
--   * demo_requests.demo_requests_teacher_select (SELECT only — its
--     anon INSERT policy needs no auth context at all)
-- Under procta_app (auth.uid() NULL on that connection), every one of
-- these would silently deny-all at cutover: audit-log reads, consent
-- read/write, and the demo-requests admin list would all appear empty
-- with no error, exactly the invite_send_counters failure mode.
--
-- Semantics preserved exactly as they were under the old model — this
-- is a literal translation, not a scope change:
--   * admin_audit_log: teacher-owned, SELECT allows privileged roles OR
--     the row's own teacher (matches app.visible_teacher_ids(), the
--     established phase124/phase148 SELECT pattern for teacher-owned
--     tables — INSERT already used this shape).
--   * consent_records: personal (not organizational) data — kept
--     strict self-only (app.teacher_id() / app.account_id() equality),
--     no privileged broadening, matching the original self-only checks.
--   * demo_requests: no teacher_id column at all (global lead data);
--     original policy was "any authenticated teacher", translated
--     verbatim to app.teacher_id() IS NOT NULL.
--
-- Idempotent: each block is independently guarded so a re-run (or a
-- table that's already been migrated by a future phase) is a no-op.
-- =====================================================================

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'admin_audit_log'
      AND policyname = 'admin_audit_log_teacher_select'
      AND qual ILIKE '%get_my_teacher_id%'
  ) THEN
    RETURN;  -- already migrated (or table/policy doesn't exist)
  END IF;

  PERFORM app._drop_all_policies('admin_audit_log'::regclass);
  EXECUTE 'ALTER TABLE admin_audit_log ENABLE ROW LEVEL SECURITY';
  EXECUTE $q$CREATE POLICY admin_audit_log_ins ON admin_audit_log FOR INSERT
    WITH CHECK (app.is_privileged() OR (teacher_id::text = app.teacher_id()))$q$;
  EXECUTE $q$CREATE POLICY admin_audit_log_t_sel ON admin_audit_log FOR SELECT
    USING (app.is_privileged() OR teacher_id::text IN (SELECT app.visible_teacher_ids()))$q$;
EXCEPTION WHEN undefined_table OR undefined_column OR undefined_function THEN
  RAISE NOTICE 'phase152 skip admin_audit_log: %', SQLERRM;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'consent_records'
      AND policyname = 'consent_records_teacher_select'
      AND qual ILIKE '%get_my_teacher_id%'
  ) THEN
    RETURN;
  END IF;

  PERFORM app._drop_all_policies('consent_records'::regclass);
  EXECUTE 'ALTER TABLE consent_records ENABLE ROW LEVEL SECURITY';
  EXECUTE $q$CREATE POLICY consent_records_student_insert ON consent_records FOR INSERT
    WITH CHECK (user_type = 'student' AND user_id::text = app.account_id())$q$;
  EXECUTE $q$CREATE POLICY consent_records_student_select ON consent_records FOR SELECT
    USING (user_type = 'student' AND user_id::text = app.account_id())$q$;
  EXECUTE $q$CREATE POLICY consent_records_teacher_insert ON consent_records FOR INSERT
    WITH CHECK (user_type = 'teacher' AND user_id::text = app.teacher_id())$q$;
  EXECUTE $q$CREATE POLICY consent_records_teacher_select ON consent_records FOR SELECT
    USING (user_type = 'teacher' AND user_id::text = app.teacher_id())$q$;
EXCEPTION WHEN undefined_table OR undefined_column OR undefined_function THEN
  RAISE NOTICE 'phase152 skip consent_records: %', SQLERRM;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'demo_requests'
      AND policyname = 'demo_requests_teacher_select'
      AND qual ILIKE '%get_my_teacher_id%'
  ) THEN
    RETURN;
  END IF;

  PERFORM app._drop_all_policies('demo_requests'::regclass);
  EXECUTE 'ALTER TABLE demo_requests ENABLE ROW LEVEL SECURITY';
  EXECUTE $q$CREATE POLICY demo_requests_anon_insert ON demo_requests FOR INSERT
    WITH CHECK (true)$q$;
  EXECUTE $q$CREATE POLICY demo_requests_t_sel ON demo_requests FOR SELECT
    USING (app.teacher_id() IS NOT NULL)$q$;
EXCEPTION WHEN undefined_table OR undefined_column OR undefined_function THEN
  RAISE NOTICE 'phase152 skip demo_requests: %', SQLERRM;
END $$;
