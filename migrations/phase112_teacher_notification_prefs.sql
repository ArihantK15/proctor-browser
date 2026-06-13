-- phase112: teacher/admin notification preferences (gap #28)
--
-- JSONB column with opt-out flags for non-essential notification categories.
-- Absent key or 'true' = send; explicit 'false' = suppress.
-- Default empty object = receive everything (backward compatible).
ALTER TABLE teachers
  ADD COLUMN IF NOT EXISTS notification_prefs JSONB NOT NULL DEFAULT '{}'::jsonb;
