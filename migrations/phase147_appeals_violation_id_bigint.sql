-- migration:contract appeals.violation_id was created UUID (phase94) but the flags it points at live in violations.id which is BIGINT — so every flag-specific appeal 500'd (asyncpg DataError, Sentry PYTHON-W) and the column never held a single valid value. Retype UUID -> BIGINT so flag-linked appeals work. Reverse: migrations/down/phase147_appeals_violation_id_bigint.sql
-- =====================================================================
-- Phase 147 — appeals.violation_id  UUID → BIGINT
-- =====================================================================
-- phase94 added appeals.violation_id as UUID on the assumption that a
-- violation id was a UUID. It is not: violations.id is BIGINT (baseline).
-- Consequences of the mismatch:
--   * submit_appeal filters violations by body.violation_id → binding a
--     value to the bigint violations.id fails ('str'/'uuid' not int8) → 500.
--   * even if that were coerced, inserting a bigint into the uuid
--     appeals.violation_id column would fail too.
-- So flag-specific appeals have NEVER worked; the column is empty in
-- practice (whole-session appeals use violation_id = NULL and are fine).
--
-- USING NULL is safe precisely because no valid bigint violation id could
-- ever have been stored in a uuid column — there is nothing to preserve.
-- Idempotent: guarded so a re-run is a no-op once the column is bigint.
-- =====================================================================

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'appeals'
      AND column_name = 'violation_id'
      AND data_type = 'uuid'
  ) THEN
    -- The partial index is defined on the column being retyped — drop it,
    -- change the type, then recreate it with the identical shape.
    DROP INDEX IF EXISTS idx_appeals_violation;

    ALTER TABLE appeals
      ALTER COLUMN violation_id TYPE BIGINT USING NULL;

    CREATE INDEX IF NOT EXISTS idx_appeals_violation
      ON appeals (violation_id)
      WHERE violation_id IS NOT NULL;
  END IF;
END $$;
