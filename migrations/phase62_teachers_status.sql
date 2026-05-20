-- Add `status` column to teachers table.
--
-- The auth code (app/routers/auth.py) selects this column when fetching a
-- teacher for login and checks `if teacher["status"] == "pending_verification"`
-- to block login for unverified accounts. The column was assumed to exist on
-- the deployed Supabase schema (where PostgREST silently tolerates missing
-- columns) but was never added by any migration. On the plain-Postgres
-- backend, asyncpg correctly rejects the SELECT with:
--
--   asyncpg.exceptions.UndefinedColumnError: column "status" does not exist
--
-- causing every teacher login to fail with a 500. This migration is
-- idempotent (IF NOT EXISTS) so it's safe to re-run.
--
-- Semantics:
--   NULL  / 'active'              -- normal account, can log in
--   'pending_verification'        -- created via a path that requires email
--                                    verification before first login
--   'suspended'                   -- admin-disabled; reserved, not yet used

ALTER TABLE teachers
  ADD COLUMN IF NOT EXISTS status TEXT;
