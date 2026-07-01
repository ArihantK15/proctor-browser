-- =====================================================================
-- Reverse of phase148 — restore invite_send_counters to its original
-- phase82 auth.uid()-based policies (rollback to pre-cutover shape).
-- =====================================================================

DO $$
BEGIN
  PERFORM app._drop_all_policies('invite_send_counters'::regclass);

  CREATE POLICY invite_send_counters_teacher_insert ON invite_send_counters
    FOR INSERT WITH CHECK (teacher_id::text = get_my_teacher_id());
  CREATE POLICY invite_send_counters_teacher_select ON invite_send_counters
    FOR SELECT USING (teacher_id::text = get_my_teacher_id());
  CREATE POLICY invite_send_counters_teacher_update ON invite_send_counters
    FOR UPDATE USING (teacher_id::text = get_my_teacher_id());
EXCEPTION WHEN undefined_table OR undefined_function THEN
  RAISE NOTICE 'phase148 down skip invite_send_counters: %', SQLERRM;
END $$;
