-- =====================================================================
-- phase128: organizations INSERT policy (system-provisioned org creation)
-- =====================================================================
-- phase124 gave `organizations` SELECT + UPDATE policies but NO INSERT policy.
-- Under RLS that means procta_app cannot create an org at all — a table with
-- RLS enabled and no INSERT policy denies all inserts to a non-bypass role,
-- and the system-context "bypass" only works where a policy references
-- app.is_privileged(); with no INSERT policy there's nothing to satisfy.
--
-- This broke EVERY org creation path: teacher signup AND LTI Model-3
-- auto-provisioning. Surfaced live during the 2026-06-18 LTI cutover:
-- "new row violates row-level security policy for table organizations".
--
-- Org creation only ever happens via system provisioning (signup + LTI launch
-- both run context-less => role=system) or a superadmin; a regular
-- teacher/student never creates an org. So gate INSERT on app.is_privileged().
-- Idempotent; matches the policy hot-applied live during the cutover.
-- =====================================================================

BEGIN;
DROP POLICY IF EXISTS orgs_ins ON organizations;
CREATE POLICY orgs_ins ON organizations FOR INSERT WITH CHECK (app.is_privileged());
COMMIT;
