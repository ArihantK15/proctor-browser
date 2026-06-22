-- phase142: exam_config.coding_max_submit_attempts — the per-question submit cap
-- (Edge Compiler output-oracle defense). load_exam_config() now SELECTs this
-- column (it was added to _EXAM_CONFIG_COLUMNS), so WITHOUT this migration the
-- config read raises UndefinedColumnError and breaks EVERY exam's config load,
-- not just coding exams. Default 10 = the judge's DEFAULT_MAX_SUBMIT_ATTEMPTS.
--
-- Numbering: 139/140 are reserved by the parked rough-sheet branch, 141 by the
-- coding tables; this is 142.
DO $$
BEGIN
  ALTER TABLE exam_config
    ADD COLUMN IF NOT EXISTS coding_max_submit_attempts INTEGER NOT NULL DEFAULT 10;
EXCEPTION WHEN undefined_table THEN
  RAISE NOTICE 'phase142 skip: exam_config missing';
END $$;
