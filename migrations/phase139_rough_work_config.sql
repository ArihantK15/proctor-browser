-- phase139: rough-sheet proctoring per-exam config
-- See docs/ROUGH_SHEET_PROCTORING_SPEC.md §5f.
--   rough_work_allowed  — master flag: enables check-in/reconcile bookends +
--                         down-gaze leniency. REQUIRES phone_camera_enabled
--                         (enforced in the API, §0.1); default OFF.
--   rough_sheets_max     — declared-sheet ceiling.
--   require_sheet_nonce  — opt-in corner-mark (server nonce) for high-stakes.
DO $$
BEGIN
  ALTER TABLE exam_config ADD COLUMN IF NOT EXISTS rough_work_allowed  BOOLEAN NOT NULL DEFAULT FALSE;
  ALTER TABLE exam_config ADD COLUMN IF NOT EXISTS rough_sheets_max    INTEGER NOT NULL DEFAULT 1;
  ALTER TABLE exam_config ADD COLUMN IF NOT EXISTS require_sheet_nonce BOOLEAN NOT NULL DEFAULT FALSE;
EXCEPTION WHEN undefined_table THEN
  RAISE NOTICE 'phase139 skip: exam_config missing';
END $$;
