-- =====================================================================
-- phase127: RLS policies for auth_sessions + auth_events (own-row access)
-- =====================================================================
-- phase124 enabled RLS on these auth-infra tables but gave them no policy,
-- so under RLS_SESSION_CONTEXT=1 they were deny-all to the restricted
-- procta_app role except in the system context. That broke the session
-- writes/reads that happen under an AUTHENTICATED context:
--   • auth_sessions — login insert, per-request "touch" (last_seen),
--     logout/revoke, and the revocation-check read on every request.
--   • auth_events   — the /api/v1/auth/events account-security read and the
--     authed-context audit inserts.
-- Failure was non-fatal (callers swallow it) but degraded session tracking
-- (idle-timeout revocation, "log out everywhere", auth audit log).
--
-- Fix: a principal may manage/read their OWN rows; system/superadmin keep
-- full access (pre-auth flows + workers run context-less → system default).
-- Pre-auth/system-only tables (e.g. email_otps) are intentionally left with
-- RLS on + no policy — deny-all-except-system is the correct secure default
-- there (OTP rows must never be tenant-readable).
--
-- Idempotent (DROP POLICY IF EXISTS → CREATE) so it re-applies cleanly over
-- the policies hot-applied live during the 2026-06-18 cutover.
-- =====================================================================

BEGIN;

DO $$
DECLARE t text;
  tabs text[] := ARRAY['auth_sessions','auth_events'];
BEGIN
  FOREACH t IN ARRAY tabs LOOP
    BEGIN
      EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
      EXECUTE format('DROP POLICY IF EXISTS %1$s_own ON %1$I', t);
      EXECUTE format($q$CREATE POLICY %1$s_own ON %1$I FOR ALL
        USING (app.is_privileged()
               OR (user_kind = 'teacher'         AND user_id::text = app.teacher_id())
               OR (user_kind = 'student_account' AND user_id::text = app.account_id()))
        WITH CHECK (app.is_privileged()
               OR (user_kind = 'teacher'         AND user_id::text = app.teacher_id())
               OR (user_kind = 'student_account' AND user_id::text = app.account_id()))$q$, t);
    EXCEPTION WHEN undefined_table OR undefined_column THEN
      RAISE NOTICE 'phase127 skip %: %', t, SQLERRM;
    END;
  END LOOP;
END $$;

COMMIT;
