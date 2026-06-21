-- phase138: RLS policy for email_otps — the last cutover-straggler.
--
-- email_otps was the ONLY RLS-enabled table on prod with NO policy of any kind,
-- so under the live procta_app cutover it denied ALL rows — breaking OTP
-- issue()/verify() (2FA fallback / step-up / account recovery). It is a
-- SYSTEM-managed, PRE-AUTH table: issue()/verify() run before the user has any
-- session identity, so there is nothing to tenant-scope to at that point. Those
-- paths are now wrapped in system_context() (app/services/email_otp.py) so the
-- app.is_privileged() clause grants them under procta_app.
--
-- The self-scoped clauses additionally let an AUTHENTICATED teacher/student act
-- on their OWN rows (e.g. the best-effort privacy/SAR self-cleanup deletes)
-- without elevation. user_id holds teachers.id for user_kind='teacher' and the
-- student account_id for user_kind='student' — matching app.teacher_id() /
-- app.account_id(). Closed table (no USING(true)); requires the phase124 helpers.

DO $$
BEGIN
  PERFORM app._drop_all_policies('email_otps'::regclass);
  ALTER TABLE email_otps ENABLE ROW LEVEL SECURITY;

  CREATE POLICY email_otps_sel ON email_otps FOR SELECT
    USING (app.is_privileged()
           OR (user_kind = 'teacher' AND user_id::text = app.teacher_id())
           OR (user_kind = 'student' AND user_id::text = app.account_id()));
  CREATE POLICY email_otps_ins ON email_otps FOR INSERT
    WITH CHECK (app.is_privileged()
           OR (user_kind = 'teacher' AND user_id::text = app.teacher_id())
           OR (user_kind = 'student' AND user_id::text = app.account_id()));
  CREATE POLICY email_otps_upd ON email_otps FOR UPDATE
    USING (app.is_privileged()
           OR (user_kind = 'teacher' AND user_id::text = app.teacher_id())
           OR (user_kind = 'student' AND user_id::text = app.account_id()));
  CREATE POLICY email_otps_del ON email_otps FOR DELETE
    USING (app.is_privileged()
           OR (user_kind = 'teacher' AND user_id::text = app.teacher_id())
           OR (user_kind = 'student' AND user_id::text = app.account_id()));
EXCEPTION WHEN undefined_table OR undefined_column THEN
  RAISE NOTICE 'phase138 skip email_otps: %', SQLERRM;
END $$;
