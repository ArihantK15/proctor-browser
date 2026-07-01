-- =====================================================================
-- Reverse of phase147 — appeals.violation_id  BIGINT → UUID (phase94 shape)
-- =====================================================================
-- Restores the original UUID type. USING NULL again: bigint values cannot
-- be encoded as uuids, and the column is empty in practice, so nothing is
-- lost. Idempotent: no-op once the column is back to uuid.
-- =====================================================================

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'appeals'
      AND column_name = 'violation_id'
      AND data_type = 'bigint'
  ) THEN
    DROP INDEX IF EXISTS idx_appeals_violation;

    ALTER TABLE appeals
      ALTER COLUMN violation_id TYPE UUID USING NULL;

    CREATE INDEX IF NOT EXISTS idx_appeals_violation
      ON appeals (violation_id)
      WHERE violation_id IS NOT NULL;
  END IF;
END $$;
