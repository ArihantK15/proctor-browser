-- =====================================================================
-- Phase 83 — Extended RLS coverage for the remaining tenant tables
-- =====================================================================
-- rls_policies.sql shipped policies on 15 tables. phase82 added another
-- 5 via the type-normalization migration. That leaves these tables with
-- a tenant-scoped column (teacher_id / user_id / org_id) but NO RLS
-- policies — meaning a connection that bypasses the FastAPI service
-- role (anon Supabase key, leaked JWT, future code regression) could
-- read every row.
--
-- This migration adds defense-in-depth policies on:
--
--   • api_keys              — teacher CRUD (per-teacher secret material)
--   • appeals               — teacher CRUD, student own-read/own-insert
--   • consent_records       — kind-discriminated (teacher OR student)
--   • exam_templates        — teacher CRUD
--   • google_auth_tokens    — teacher CRUD (OAuth refresh tokens)
--   • google_classroom_links— teacher CRUD
--   • google_oauth_states   — teacher CRUD (ephemeral)
--   • grading_audit         — teacher SELECT (audit; insert is server-only)
--   • issues                — teacher SELECT/INSERT (their own)
--   • org_invites           — org-scoped via teacher.org_id
--   • usage_records         — org-scoped via teacher.org_id
--
-- All policies use ::text comparisons against the existing
-- public.get_my_teacher_id() / public.get_my_student_account_id()
-- helpers so UUID/TEXT drift across tables is irrelevant.
--
-- IMPORTANT — backend impact:
--   The FastAPI backend uses a service-role connection that bypasses
--   RLS (per the note at the top of rls_policies.sql). Enabling RLS on
--   these tables therefore has NO functional impact on the running app;
--   it ONLY blocks direct-DB access paths (anon key, leaked JWT,
--   misconfigured ad-hoc psql session). Verified by spot-check: every
--   read path in app/ goes through asyncpg with the service role.
--
-- Skipped tables (intentionally NOT covered by this migration):
--   • auth_events, auth_sessions, refresh_tokens, email_otps:
--     kind-discriminated user_id, server-only access via the auth
--     service. Adding policies would require a helper that resolves
--     either-kind ownership and gains little since the service role
--     is the only writer/reader.
-- =====================================================================

-- ── api_keys ───────────────────────────────────────────────────────
ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS api_keys_teacher_select ON api_keys;
CREATE POLICY api_keys_teacher_select ON api_keys
  FOR SELECT USING (teacher_id::text = public.get_my_teacher_id());
DROP POLICY IF EXISTS api_keys_teacher_insert ON api_keys;
CREATE POLICY api_keys_teacher_insert ON api_keys
  FOR INSERT WITH CHECK (teacher_id::text = public.get_my_teacher_id());
DROP POLICY IF EXISTS api_keys_teacher_update ON api_keys;
CREATE POLICY api_keys_teacher_update ON api_keys
  FOR UPDATE USING (teacher_id::text = public.get_my_teacher_id());
DROP POLICY IF EXISTS api_keys_teacher_delete ON api_keys;
CREATE POLICY api_keys_teacher_delete ON api_keys
  FOR DELETE USING (teacher_id::text = public.get_my_teacher_id());

-- ── appeals ────────────────────────────────────────────────────────
-- Teachers see/manage appeals targeting their classroom; students see
-- and create only their own appeals (scoped by student_accounts.id).
ALTER TABLE appeals ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS appeals_teacher_select ON appeals;
CREATE POLICY appeals_teacher_select ON appeals
  FOR SELECT USING (teacher_id::text = public.get_my_teacher_id());
DROP POLICY IF EXISTS appeals_teacher_update ON appeals;
CREATE POLICY appeals_teacher_update ON appeals
  FOR UPDATE USING (teacher_id::text = public.get_my_teacher_id());
DROP POLICY IF EXISTS appeals_teacher_delete ON appeals;
CREATE POLICY appeals_teacher_delete ON appeals
  FOR DELETE USING (teacher_id::text = public.get_my_teacher_id());
DROP POLICY IF EXISTS appeals_student_select ON appeals;
CREATE POLICY appeals_student_select ON appeals
  FOR SELECT USING (student_id::text = public.get_my_student_account_id());
DROP POLICY IF EXISTS appeals_student_insert ON appeals;
CREATE POLICY appeals_student_insert ON appeals
  FOR INSERT WITH CHECK (student_id::text = public.get_my_student_account_id());

-- ── consent_records ────────────────────────────────────────────────
-- Kind-discriminated: user_id points at either teachers.id (when
-- user_type='teacher') or student_accounts.id (when user_type='student').
-- Each row is visible to its owner only.
ALTER TABLE consent_records ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS consent_records_teacher_select ON consent_records;
CREATE POLICY consent_records_teacher_select ON consent_records
  FOR SELECT USING (user_type = 'teacher' AND user_id::text = public.get_my_teacher_id());
DROP POLICY IF EXISTS consent_records_teacher_insert ON consent_records;
CREATE POLICY consent_records_teacher_insert ON consent_records
  FOR INSERT WITH CHECK (user_type = 'teacher' AND user_id::text = public.get_my_teacher_id());
DROP POLICY IF EXISTS consent_records_student_select ON consent_records;
CREATE POLICY consent_records_student_select ON consent_records
  FOR SELECT USING (user_type = 'student' AND user_id::text = public.get_my_student_account_id());
DROP POLICY IF EXISTS consent_records_student_insert ON consent_records;
CREATE POLICY consent_records_student_insert ON consent_records
  FOR INSERT WITH CHECK (user_type = 'student' AND user_id::text = public.get_my_student_account_id());

-- ── exam_templates ─────────────────────────────────────────────────
ALTER TABLE exam_templates ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS exam_templates_teacher_select ON exam_templates;
CREATE POLICY exam_templates_teacher_select ON exam_templates
  FOR SELECT USING (teacher_id::text = public.get_my_teacher_id());
DROP POLICY IF EXISTS exam_templates_teacher_insert ON exam_templates;
CREATE POLICY exam_templates_teacher_insert ON exam_templates
  FOR INSERT WITH CHECK (teacher_id::text = public.get_my_teacher_id());
DROP POLICY IF EXISTS exam_templates_teacher_update ON exam_templates;
CREATE POLICY exam_templates_teacher_update ON exam_templates
  FOR UPDATE USING (teacher_id::text = public.get_my_teacher_id());
DROP POLICY IF EXISTS exam_templates_teacher_delete ON exam_templates;
CREATE POLICY exam_templates_teacher_delete ON exam_templates
  FOR DELETE USING (teacher_id::text = public.get_my_teacher_id());

-- ── google_auth_tokens ─────────────────────────────────────────────
-- Each teacher owns exactly one row (UNIQUE constraint on teacher_id).
-- Treat as full CRUD scoped by teacher.
ALTER TABLE google_auth_tokens ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS google_auth_tokens_teacher_select ON google_auth_tokens;
CREATE POLICY google_auth_tokens_teacher_select ON google_auth_tokens
  FOR SELECT USING (teacher_id::text = public.get_my_teacher_id());
DROP POLICY IF EXISTS google_auth_tokens_teacher_insert ON google_auth_tokens;
CREATE POLICY google_auth_tokens_teacher_insert ON google_auth_tokens
  FOR INSERT WITH CHECK (teacher_id::text = public.get_my_teacher_id());
DROP POLICY IF EXISTS google_auth_tokens_teacher_update ON google_auth_tokens;
CREATE POLICY google_auth_tokens_teacher_update ON google_auth_tokens
  FOR UPDATE USING (teacher_id::text = public.get_my_teacher_id());
DROP POLICY IF EXISTS google_auth_tokens_teacher_delete ON google_auth_tokens;
CREATE POLICY google_auth_tokens_teacher_delete ON google_auth_tokens
  FOR DELETE USING (teacher_id::text = public.get_my_teacher_id());

-- ── google_classroom_links ─────────────────────────────────────────
ALTER TABLE google_classroom_links ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS gcl_teacher_select ON google_classroom_links;
CREATE POLICY gcl_teacher_select ON google_classroom_links
  FOR SELECT USING (teacher_id::text = public.get_my_teacher_id());
DROP POLICY IF EXISTS gcl_teacher_insert ON google_classroom_links;
CREATE POLICY gcl_teacher_insert ON google_classroom_links
  FOR INSERT WITH CHECK (teacher_id::text = public.get_my_teacher_id());
DROP POLICY IF EXISTS gcl_teacher_delete ON google_classroom_links;
CREATE POLICY gcl_teacher_delete ON google_classroom_links
  FOR DELETE USING (teacher_id::text = public.get_my_teacher_id());

-- ── google_oauth_states ────────────────────────────────────────────
-- Ephemeral table — rows live ~10 minutes between OAuth init and
-- callback. Still worth scoping in case anyone enumerates active flows.
ALTER TABLE google_oauth_states ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS google_oauth_states_teacher_select ON google_oauth_states;
CREATE POLICY google_oauth_states_teacher_select ON google_oauth_states
  FOR SELECT USING (teacher_id::text = public.get_my_teacher_id());
DROP POLICY IF EXISTS google_oauth_states_teacher_insert ON google_oauth_states;
CREATE POLICY google_oauth_states_teacher_insert ON google_oauth_states
  FOR INSERT WITH CHECK (teacher_id::text = public.get_my_teacher_id());
DROP POLICY IF EXISTS google_oauth_states_teacher_delete ON google_oauth_states;
CREATE POLICY google_oauth_states_teacher_delete ON google_oauth_states
  FOR DELETE USING (teacher_id::text = public.get_my_teacher_id());

-- ── grading_audit ──────────────────────────────────────────────────
-- Append-only audit table. Server writes; teachers can only read
-- their own audit trail. No UPDATE/DELETE policies → blocked by default
-- (RLS enabled with no matching policy = deny).
ALTER TABLE grading_audit ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS grading_audit_teacher_select ON grading_audit;
CREATE POLICY grading_audit_teacher_select ON grading_audit
  FOR SELECT USING (teacher_id::text = public.get_my_teacher_id());

-- ── issues ─────────────────────────────────────────────────────────
-- teacher_id is nullable (ON DELETE SET NULL from phase81). NULL rows
-- belong to no one and remain hidden from teachers — only superadmin
-- service role can read those.
ALTER TABLE issues ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS issues_teacher_select ON issues;
CREATE POLICY issues_teacher_select ON issues
  FOR SELECT USING (teacher_id::text = public.get_my_teacher_id());
DROP POLICY IF EXISTS issues_teacher_insert ON issues;
CREATE POLICY issues_teacher_insert ON issues
  FOR INSERT WITH CHECK (teacher_id::text = public.get_my_teacher_id());

-- ── org_invites ────────────────────────────────────────────────────
-- Org-scoped: a teacher can see/manage invites for their own org.
-- (Anyone with a teacher row in org X can see all of org X's invites
-- — finer-grained per-admin scoping would require an org_role check.)
ALTER TABLE org_invites ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_invites_teacher_select ON org_invites;
CREATE POLICY org_invites_teacher_select ON org_invites
  FOR SELECT USING (
    org_id::text IN (
      SELECT org_id::text FROM teachers
       WHERE id::text = public.get_my_teacher_id() AND org_id IS NOT NULL
    )
  );
DROP POLICY IF EXISTS org_invites_teacher_insert ON org_invites;
CREATE POLICY org_invites_teacher_insert ON org_invites
  FOR INSERT WITH CHECK (
    org_id::text IN (
      SELECT org_id::text FROM teachers
       WHERE id::text = public.get_my_teacher_id() AND org_id IS NOT NULL
    )
  );
DROP POLICY IF EXISTS org_invites_teacher_update ON org_invites;
CREATE POLICY org_invites_teacher_update ON org_invites
  FOR UPDATE USING (
    org_id::text IN (
      SELECT org_id::text FROM teachers
       WHERE id::text = public.get_my_teacher_id() AND org_id IS NOT NULL
    )
  );

-- ── usage_records ──────────────────────────────────────────────────
-- Billing rows are org-scoped. Read-only for teachers (writes are
-- server-only from the metering job).
ALTER TABLE usage_records ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS usage_records_teacher_select ON usage_records;
CREATE POLICY usage_records_teacher_select ON usage_records
  FOR SELECT USING (
    org_id::text IN (
      SELECT org_id::text FROM teachers
       WHERE id::text = public.get_my_teacher_id() AND org_id IS NOT NULL
    )
  );

-- =====================================================================
-- Post-migration verification (run separately):
--
--   SELECT tablename, COUNT(*) AS policy_count
--     FROM pg_policies
--    WHERE tablename IN (
--      'api_keys','appeals','consent_records','exam_templates',
--      'google_auth_tokens','google_classroom_links','google_oauth_states',
--      'grading_audit','issues','org_invites','usage_records'
--    )
--    GROUP BY tablename
--    ORDER BY tablename;
--
-- Expected: 11 rows, each with policy_count > 0.
--
--   SELECT tablename, rowsecurity FROM pg_tables
--    WHERE schemaname='public' AND tablename IN (
--      'api_keys','appeals','consent_records','exam_templates',
--      'google_auth_tokens','google_classroom_links','google_oauth_states',
--      'grading_audit','issues','org_invites','usage_records'
--    )
--    ORDER BY tablename;
--
-- Expected: rowsecurity = t on all 11.
-- =====================================================================
