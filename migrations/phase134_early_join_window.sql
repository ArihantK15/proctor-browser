-- Phase 134: Early-join pre-exam verification window.
--
-- Lets students enter a SCHEDULED exam early to finish ID + phone-camera
-- verification, then wait in the lobby until starts_at to begin. The column
-- holds the lead time in minutes; 0 (or no starts_at) = today's behaviour
-- (entry == start). The schedule (starts_at/ends_at) lives on exam_config, so
-- the lead time does too. See docs/superpowers/specs/2026-06-20-early-join-
-- verification-window-design.md and exam.py _check_lobby_window /
-- _check_exam_started.
DO $$
BEGIN
    ALTER TABLE exam_config
        ADD COLUMN IF NOT EXISTS early_join_minutes INTEGER NOT NULL DEFAULT 15;
EXCEPTION
    WHEN undefined_table THEN
        RAISE NOTICE 'exam_config table absent; skipping early_join_minutes';
    WHEN duplicate_column THEN
        RAISE NOTICE 'early_join_minutes already present; skipping';
END $$;

-- Defensive clamp: keep the lead time in a sane 0..240 min range.
DO $$
BEGIN
    ALTER TABLE exam_config
        ADD CONSTRAINT chk_exam_config_early_join_minutes
        CHECK (early_join_minutes >= 0 AND early_join_minutes <= 240) NOT VALID;
EXCEPTION
    WHEN duplicate_object THEN RAISE NOTICE 'chk_exam_config_early_join_minutes exists; skipping';
    WHEN undefined_table THEN RAISE NOTICE 'exam_config table absent; skipping check';
END $$;

DO $$
BEGIN
    ALTER TABLE exam_config VALIDATE CONSTRAINT chk_exam_config_early_join_minutes;
EXCEPTION
    WHEN undefined_object THEN RAISE NOTICE 'constraint absent; skipping validate';
    WHEN undefined_table THEN RAISE NOTICE 'exam_config table absent; skipping validate';
END $$;
