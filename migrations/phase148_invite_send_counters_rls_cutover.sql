-- =====================================================================
-- phase148 — bring invite_send_counters into the procta_app session-
-- context RLS model (phase124), same as every other teacher-owned table.
-- =====================================================================
-- invite_send_counters was never added to phase124's teacher-owned `tabs`
-- array, so it was left on its original phase82 policies, which gate on
-- teacher_id::text = get_my_teacher_id() -> ... auth.uid() ... — a
-- Supabase-JWT function that only resolves under Supabase's own auth
-- context. Once the app connects as the restricted procta_app role
-- (RLS_SESSION_CONTEXT=1), auth.uid() is NULL on that connection, so
-- get_my_teacher_id() returns NULL and these three policies deny-all:
-- every invite-cap increment/lookup silently fails at the cutover.
--
-- Fix: same pattern as phase124's teacher-owned loop — drop the stale
-- auth.uid() policies and recreate SELECT/INSERT/UPDATE against the
-- app.teacher_id()/app.visible_teacher_ids() session-context functions.
-- No DELETE policy: nothing in the app deletes invite_send_counters rows.
-- =====================================================================

DO $$
BEGIN
  PERFORM app._drop_all_policies('invite_send_counters'::regclass);
  EXECUTE 'ALTER TABLE invite_send_counters ENABLE ROW LEVEL SECURITY';
  EXECUTE $q$CREATE POLICY invite_send_counters_t_sel ON invite_send_counters FOR SELECT
    USING (app.is_privileged() OR teacher_id::text IN (SELECT app.visible_teacher_ids()))$q$;
  EXECUTE $q$CREATE POLICY invite_send_counters_t_ins ON invite_send_counters FOR INSERT
    WITH CHECK (app.is_privileged() OR teacher_id::text = app.teacher_id())$q$;
  EXECUTE $q$CREATE POLICY invite_send_counters_t_upd ON invite_send_counters FOR UPDATE
    USING (app.is_privileged() OR teacher_id::text = app.teacher_id())$q$;
EXCEPTION WHEN undefined_table OR undefined_column THEN
  RAISE NOTICE 'phase148 skip invite_send_counters: %', SQLERRM;
END $$;
