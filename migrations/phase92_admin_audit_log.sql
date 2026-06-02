-- =====================================================================
-- Phase 92 — Admin actions audit log
-- =====================================================================
-- auth_events captures auth-side activity (login, logout, 2fa, etc.).
-- This table covers the gap: who-changed-what on data, when, from
-- where. Filled by app/services/admin_audit.py log_admin_action()
-- which sensitive admin endpoints call before/after mutations.
--
-- Forensic shape:
--   teacher_id  — WHO (the admin who did it). Nullable + SET NULL on
--                 teacher delete so the audit row outlives the actor
--                 (you still see "deleted-teacher X did Y" historically).
--   action      — WHAT (verb_object, e.g. 'delete_exam', 'bulk_dismiss').
--   target_type — WHICH ENTITY TYPE ('exam', 'students', 'group', etc.)
--   target_id   — WHICH ROW (UUID or string key, depending on entity).
--   before_data — minimal snapshot of pre-mutation state. JSONB so we
--                 don't have to predict the shape; capture only what's
--                 forensically useful (IDs + identifying fields, not
--                 entire rows).
--   after_data  — same, post-mutation. NULL for deletes.
--   details     — free-form extra context (row counts, reason codes,
--                 IP, etc.) without polluting before/after.
--   ip / user_agent — WHERE FROM. Captured by the helper from the
--                 FastAPI Request when called.
--
-- RLS: teachers see their own audit trail only. Server-side inserts go
-- via the service-role connection which bypasses RLS, so logging is
-- always possible. Super-admin reads go directly through service-role
-- too.
-- =====================================================================

CREATE TABLE IF NOT EXISTS admin_audit_log (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  teacher_id   UUID REFERENCES teachers(id) ON DELETE SET NULL,
  action       TEXT NOT NULL,
  target_type  TEXT NOT NULL,
  target_id    TEXT,
  before_data  JSONB,
  after_data   JSONB,
  details      JSONB NOT NULL DEFAULT '{}'::JSONB,
  ip           TEXT,
  user_agent   TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Hot paths: "show me what teacher X did" + "show me what happened to
-- entity Y over time" + "show me all 'delete_exam' actions ever".
CREATE INDEX IF NOT EXISTS idx_admin_audit_log_teacher
  ON admin_audit_log (teacher_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_admin_audit_log_target
  ON admin_audit_log (target_type, target_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_admin_audit_log_action
  ON admin_audit_log (action, created_at DESC);

-- ── RLS ────────────────────────────────────────────────────────────
ALTER TABLE admin_audit_log ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS admin_audit_log_teacher_select ON admin_audit_log;
CREATE POLICY admin_audit_log_teacher_select ON admin_audit_log
  FOR SELECT USING (teacher_id::text = public.get_my_teacher_id());

-- No INSERT/UPDATE/DELETE policies → service role writes via bypass,
-- non-service roles cannot mutate the log (read-only audit trail).

-- =====================================================================
-- Verification:
--
--   SELECT tablename, rowsecurity FROM pg_tables
--    WHERE schemaname='public' AND tablename='admin_audit_log';
--   -- Expected: rowsecurity = t
--
--   SELECT policyname, cmd FROM pg_policies
--    WHERE tablename='admin_audit_log';
--   -- Expected: 1 row, admin_audit_log_teacher_select SELECT
-- =====================================================================
