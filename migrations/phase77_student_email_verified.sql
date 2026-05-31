-- Phase 77: Student account lifecycle columns
--
-- See /Users/arihantkaul/.claude/plans/the-load-is-running-sparkling-canyon.md
-- (Student Account Deletion + OTP-Everywhere — foundation commit).
--
-- This migration unblocks two parallel tracks landing after it:
--   Track A: student signup verification + self-delete account
--   Track B: OTP password reset + email change + roster IN_PROGRESS warning
--
-- Columns added:
--
--   student_accounts.email_verified_at  TIMESTAMPTZ
--     NULL means "signup OTP not yet entered — login is rejected
--     with 'verify your email' message." Set to now() once
--     /api/v1/student/auth/verify-signup-otp accepts a valid code.
--     Teachers already have this column on `teachers` and the auth
--     flow gates login on it (see auth.py:646); we're mirroring the
--     same pattern for students to close the fake-account hole.
--
--   student_accounts.deleted_at  TIMESTAMPTZ
--     Tombstone for self-deletion paths. The row itself is hard-
--     deleted by the new /api/v1/student/account/delete-confirm
--     handler, but this column lets us record a brief grace-window
--     for incident recovery if we ever switch to soft-delete in the
--     future. Currently unused; NULL by default. Future-proofing.
--
--   students.removed_at  TIMESTAMPTZ
--     For the teacher-side "removed from roster" audit trail. The
--     existing DELETE /api/v1/admin/students/roster (commit
--     f146ae4) hard-deletes the row today; once Track B lands the
--     UI warning for IN_PROGRESS sessions, the handler will
--     OPTIONALLY soft-delete (set removed_at + leave the row) so
--     post-mortems can see who was on the roster at exam-start
--     time. Hard-delete remains the default for the current demo
--     so behavior is unchanged unless a future opts in.
--
-- Backfill posture:
--
--   Existing `student_accounts` rows get `email_verified_at = NOW()`
--   so grandfathered accounts can keep logging in without an OTP
--   round-trip. Only newly-created accounts (after this migration
--   + Track A's signup handler change) face the OTP gate.
--
--   Existing `students` rows keep `removed_at = NULL` — they are
--   live enrollments.
--
-- Idempotent: safe to re-run via ADD COLUMN IF NOT EXISTS.

ALTER TABLE student_accounts
  ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMPTZ;

ALTER TABLE student_accounts
  ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

ALTER TABLE students
  ADD COLUMN IF NOT EXISTS removed_at TIMESTAMPTZ;

-- Grandfather: any account that exists today is treated as verified
-- so the login path doesn't lock out our entire current user base on
-- deploy. New accounts insert with email_verified_at = NULL (the
-- Track A signup handler will explicitly leave it NULL).
UPDATE student_accounts
  SET email_verified_at = COALESCE(email_verified_at, NOW())
  WHERE email_verified_at IS NULL;

-- Useful indexes for the new lookup patterns:
--   - Login path checks email_verified_at every login → already
--     indexed by email (the primary identity column); the additional
--     IS NULL filter is fast enough without a partial index.
--   - removed_at lookups are bounded by teacher_id which is already
--     indexed.
-- No new indexes needed; keeping migration small.
