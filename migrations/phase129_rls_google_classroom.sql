-- =====================================================================
-- phase129: migrate google_* RLS policies to the session-context model
-- =====================================================================
-- The Google Classroom tables (google_auth_tokens, google_classroom_links,
-- google_oauth_states) were given RLS policies in phase83 keyed on the LEGACY
-- public.get_my_teacher_id() → auth.uid() helper. Under the session-context
-- model (asyncpg + procta_app, phase124) auth.uid() is NULL, so those policies
-- evaluate false → DENY-ALL. phase124 never migrated these tables (they weren't
-- in its lists), so under RLS the whole Google Classroom flow breaks for an
-- authenticated teacher (can't store OAuth state, read tokens, link exams, or
-- sync rosters).
--
-- Re-base them on app.* session context, matching every other teacher-owned
-- table: a teacher manages their own rows (teacher_id = app.teacher_id());
-- system/superadmin keep full access — needed because the OAuth /callback is
-- unauthenticated (context-less → system) when it stores the exchanged tokens.
-- Idempotent (_drop_all_policies clears the legacy set, then recreate).
-- =====================================================================

BEGIN;

DO $$
DECLARE t text;
  tabs text[] := ARRAY['google_auth_tokens','google_classroom_links','google_oauth_states'];
BEGIN
  FOREACH t IN ARRAY tabs LOOP
    BEGIN
      PERFORM app._drop_all_policies(t::regclass);   -- clears phase83's get_my_teacher_id() policies
      EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
      EXECUTE format($q$CREATE POLICY %1$s_t_all ON %1$I FOR ALL
        USING (app.is_privileged() OR teacher_id::text = app.teacher_id())
        WITH CHECK (app.is_privileged() OR teacher_id::text = app.teacher_id())$q$, t);
    EXCEPTION WHEN undefined_table OR undefined_column THEN
      RAISE NOTICE 'phase129 skip %: %', t, SQLERRM;
    END;
  END LOOP;
END $$;

COMMIT;
