-- Enforce account email uniqueness case-insensitively.
--
-- The application normalizes teacher/student-account emails to lowercase
-- before signup and email-change, but the existing UNIQUE(email)
-- constraints are case-sensitive in Postgres. These functional indexes
-- close the bypass where manual SQL or a future import path could create
-- both "Alice@School.edu" and "alice@school.edu".
--
-- If this migration fails, clean up duplicate lower(email) rows before
-- retrying; failing closed is intentional because duplicate login
-- identities are a privacy boundary risk.

CREATE UNIQUE INDEX IF NOT EXISTS uq_teachers_email_lower
  ON teachers (lower(email))
  WHERE email IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_student_accounts_email_lower
  ON student_accounts (lower(email))
  WHERE email IS NOT NULL;
