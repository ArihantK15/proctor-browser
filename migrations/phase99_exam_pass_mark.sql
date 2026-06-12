-- Add per-exam pass_mark field (default 40%) so teachers can override
-- the hard-coded 40% threshold in scorecard.py per subject/difficulty.
-- Zero / NULL means "use the system default of 40" for backwards compat.
ALTER TABLE exam_config
  ADD COLUMN pass_mark smallint NOT NULL DEFAULT 40;
