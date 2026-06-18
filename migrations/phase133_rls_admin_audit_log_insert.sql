-- =====================================================================
-- phase133: RLS INSERT policy for admin_audit_log
-- =====================================================================
-- Live audit (pg_policies) found admin_audit_log is the only RLS-enabled table
-- still missing an INSERT policy. Its writes (services/admin_audit.py) are
-- best-effort (wrapped in try/except), so this failed silently under RLS —
-- the admin audit trail has been dropping rows since the cutover instead of
-- erroring. Not user-facing, but a compliance/audit gap.
--
-- Admin actions run under an authenticated teacher/superadmin context, and
-- some housekeeping writes run system/context-less. Allow the privileged
-- (system/superadmin) context or a row whose teacher_id matches the caller.
-- Mirrors the established pattern (app.is_privileged() OR own-id). Idempotent.
-- =====================================================================

BEGIN;

DROP POLICY IF EXISTS admin_audit_log_ins ON admin_audit_log;
CREATE POLICY admin_audit_log_ins ON admin_audit_log FOR INSERT
  WITH CHECK (app.is_privileged() OR teacher_id::text = app.teacher_id());

COMMIT;
