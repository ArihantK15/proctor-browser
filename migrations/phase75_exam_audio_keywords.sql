-- Phase 75: Per-exam audio keyword detection settings
--
-- See /Users/arihantkaul/.claude/plans/the-load-is-running-sparkling-canyon.md
-- (Part A — On-device audio detection).
--
-- The Procta proctor daemon now runs on-device speech-to-text (Vosk)
-- and a multi-voice check (Silero VAD + MFCC clustering). The teacher
-- can extend the built-in keyword list with exam-specific phrases —
-- e.g. for a chemistry exam, add "periodic table", "potassium nitrate".
--
-- Columns:
--   audio_keywords          JSON-encoded list of strings. NULL means
--                           "use the built-in defaults only". 50
--                           entries max, 2-80 chars each (validated
--                           at the API layer, NOT enforced as a
--                           schema constraint so a future bigger cap
--                           doesn't need a migration).
--   audio_keywords_language one of 'en', 'hi', 'en+hi'. Default 'en'
--                           so existing exams behave as if only the
--                           English keyword set was active.
--
-- No backfill needed: NULL columns let load_exam_config() fall back
-- to defaults without a code change.
--
-- Idempotent: safe to re-run.

ALTER TABLE exam_config
  ADD COLUMN IF NOT EXISTS audio_keywords TEXT NULL;

ALTER TABLE exam_config
  ADD COLUMN IF NOT EXISTS audio_keywords_language TEXT NULL DEFAULT 'en';
