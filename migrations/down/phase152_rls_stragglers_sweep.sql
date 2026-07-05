-- =====================================================================
-- Reverse of phase152 — restore admin_audit_log, consent_records, and
-- demo_requests to their original auth.uid()-based policies (rollback
-- to pre-sweep shape).
-- =====================================================================

DO $$
BEGIN
  PERFORM app._drop_all_policies('admin_audit_log'::regclass);
  CREATE POLICY admin_audit_log_ins ON admin_audit_log
    FOR INSERT WITH CHECK (app.is_privileged() OR (teacher_id::text = app.teacher_id()));
  CREATE POLICY admin_audit_log_teacher_select ON admin_audit_log
    FOR SELECT USING (teacher_id::text = get_my_teacher_id());
EXCEPTION WHEN undefined_table OR undefined_function THEN
  RAISE NOTICE 'phase152 down skip admin_audit_log: %', SQLERRM;
END $$;

DO $$
BEGIN
  PERFORM app._drop_all_policies('consent_records'::regclass);
  CREATE POLICY consent_records_student_insert ON consent_records
    FOR INSERT WITH CHECK (user_type = 'student' AND user_id::text = get_my_student_account_id());
  CREATE POLICY consent_records_student_select ON consent_records
    FOR SELECT USING (user_type = 'student' AND user_id::text = get_my_student_account_id());
  CREATE POLICY consent_records_teacher_insert ON consent_records
    FOR INSERT WITH CHECK (user_type = 'teacher' AND user_id::text = get_my_teacher_id());
  CREATE POLICY consent_records_teacher_select ON consent_records
    FOR SELECT USING (user_type = 'teacher' AND user_id::text = get_my_teacher_id());
EXCEPTION WHEN undefined_table OR undefined_function THEN
  RAISE NOTICE 'phase152 down skip consent_records: %', SQLERRM;
END $$;

DO $$
BEGIN
  PERFORM app._drop_all_policies('demo_requests'::regclass);
  CREATE POLICY demo_requests_anon_insert ON demo_requests
    FOR INSERT WITH CHECK (true);
  CREATE POLICY demo_requests_teacher_select ON demo_requests
    FOR SELECT USING (get_my_teacher_id() IS NOT NULL);
EXCEPTION WHEN undefined_table OR undefined_function THEN
  RAISE NOTICE 'phase152 down skip demo_requests: %', SQLERRM;
END $$;
