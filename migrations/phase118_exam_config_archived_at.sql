-- Gap #24: reversible exam archiving (soft-hide, non-destructive).
ALTER TABLE exam_config
  ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ NULL;
