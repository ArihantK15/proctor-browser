-- phase137: RLS straggler policies — invite_send_counters + issues
--
-- These two teacher-scoped tables are RLS-ENABLED but were missed by phase124's
-- migration loop, so they had NO app.* policy. Under the procta_app cutover
-- (RLS_SESSION_CONTEXT=1, app connects as the restricted non-owner role) a
-- policy-less RLS table denies ALL rows — most visibly breaking invite sending,
-- since invite_send_counters drives the daily send cap. (`issues` would lose the
-- bug/issue reporting + admin triage views.)
--
-- This mirrors phase124's teacher-scoped pattern EXACTLY (own row for writes,
-- own-or-org for reads, superadmin/system bypass via app.is_privileged()). It is
-- INERT until the cutover flag flips — today the app connects as the table owner
-- so policies don't gate anything — which makes it safe to ship now and have the
-- tables ready before RLS goes live.
--
-- Deliberately NOT handled here (they need a cutover-architecture decision, not a
-- tenant policy — see task_c182396d):
--   * email_otps, consent_records — pre-auth / system-context tables. email_otps
--     is read/written PRE-AUTH (app/services/email_otp.py) with NO system context
--     set, so an app.is_privileged() policy would not grant it at cutover. Needs
--     either its service paths to run under system_context, or a deliberate
--     pre-auth policy.
--   * demo_requests — public lead form; anonymous INSERT is already covered by a
--     USING(true) policy. Only the superadmin SELECT would need an
--     app.is_privileged() read policy.
--
-- Requires the app.* helpers from phase124 (app._drop_all_policies,
-- app.is_privileged, app.visible_teacher_ids, app.teacher_id), which runs first.

DO $$
DECLARE
  t    text;
  tabs text[] := ARRAY['invite_send_counters','issues'];
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
      RAISE NOTICE 'phase137 skip teacher-table %: %', t, SQLERRM;
    END;
  END LOOP;
END $$;
