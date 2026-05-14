-- Phase 54: Detection confidence scores for violations
-- Enables the false-positive confidence explainer in the timeline.

ALTER TABLE violations ADD COLUMN IF NOT EXISTS detection_confidence NUMERIC(4,3);
CREATE INDEX IF NOT EXISTS idx_violations_confidence ON violations(detection_confidence);
