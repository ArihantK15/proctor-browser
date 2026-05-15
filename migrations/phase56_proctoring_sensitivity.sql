-- Phase 56: Per-exam proctoring sensitivity
-- Used by false-positive explainers and review defaults. Detector behavior can
-- be tightened later without changing the public config shape.

ALTER TABLE exam_config
  ADD COLUMN IF NOT EXISTS proctoring_sensitivity TEXT NOT NULL DEFAULT 'balanced';

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'exam_config_proctoring_sensitivity_check'
  ) THEN
    ALTER TABLE exam_config
      ADD CONSTRAINT exam_config_proctoring_sensitivity_check
      CHECK (proctoring_sensitivity IN ('strict', 'balanced', 'lenient'));
  END IF;
END $$;

NOTIFY pgrst, 'reload schema';
