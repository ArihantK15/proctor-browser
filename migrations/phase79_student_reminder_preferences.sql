-- Student-controlled exam reminder preference.
--
-- Default is ON so existing invite/reminder behavior is preserved. Students
-- can turn this off from the student dashboard; the reminder loop respects it
-- for linked student_accounts while still reminding invitees who have not yet
-- created an account.

ALTER TABLE student_accounts
  ADD COLUMN IF NOT EXISTS email_reminders_enabled BOOLEAN NOT NULL DEFAULT TRUE;
