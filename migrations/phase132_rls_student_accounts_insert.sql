-- =====================================================================
-- phase132: RLS INSERT policy for student_accounts (unbreak student signup)
-- =====================================================================
-- phase124 enabled RLS on student_accounts and created SELECT (sa_sel) +
-- UPDATE (sa_upd) policies, but NO INSERT policy. With RLS on and no INSERT
-- policy, every insert is denied — so student signup
-- (/api/v1/student/auth/signup) failed with "Failed to create student record"
-- for the entire time RLS has been live: the signup is pre-auth (context-less
-- → role=system) and there was nothing for the row to satisfy on INSERT.
--
-- Same class of gap as organizations (phase128 orgs_ins) and auth_sessions/
-- auth_events (phase127). A teacher/student never self-creates an account row
-- in an authenticated context — account creation is always the system signup
-- path — so gate INSERT on app.is_privileged() (true for the system/superadmin
-- context). Idempotent.
-- =====================================================================

BEGIN;

DROP POLICY IF EXISTS sa_ins ON student_accounts;
CREATE POLICY sa_ins ON student_accounts FOR INSERT WITH CHECK (app.is_privileged());

COMMIT;
