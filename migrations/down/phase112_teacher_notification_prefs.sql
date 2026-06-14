-- Reverse phase112: remove the notification_prefs column.
-- Safe because the column is nullable-scoped: DEFAULT '{}'::jsonb means the
-- data has never been anything other than JSONB, and DROP COLUMN IF EXISTS
-- is idempotent.
ALTER TABLE teachers
  DROP COLUMN IF EXISTS notification_prefs;
