-- Drop the truly-redundant UNIQUE constraints added by phase66.
--
-- phase66 added UNIQUE constraints on the conflict columns used by
-- async_table().upsert(...) calls, but didn't check whether the same
-- column sets were already constrained on the deployed schema. On
-- procta-prod (2026-05-22 audit), the situation per table was:
--
--   exam_sessions  PK(session_key)
--                  + phase66's UNIQUE(session_key)            ← REDUNDANT, drop
--   answers        PK(answers_pkey, on some surrogate column)
--                  + answers_session_key_question_id_key UNIQUE(session_key, question_id)
--                  + phase66's UNIQUE(session_key, question_id)  ← REDUNDANT, drop
--   exam_config    PK(exam_config_pkey, on some surrogate)
--                  + uq_exam_config_exam_id UNIQUE(exam_id)
--                  + phase66's UNIQUE(teacher_id, exam_id)    ← NOT redundant,
--                       matches the (teacher_id, exam_id) ON CONFLICT used
--                       by postgres_table.upsert(); the existing UNIQUE
--                       is only on `exam_id`, which would prevent a teacher
--                       from owning two exams with the same exam_id but
--                       does NOT satisfy the composite ON CONFLICT clause
--   questions      PK(questions_pkey, on the surrogate `id` column)
--                  + phase66's UNIQUE(teacher_id, exam_id, question_id)  ← NOT redundant,
--                       it's the ONLY constraint matching the upsert path
--
-- So we only drop the two that are genuinely redundant. The other two
-- phase66 constraints stay — without them postgres_table.upsert()
-- against exam_config and questions would 500 with
--   InvalidColumnReferenceError: there is no unique or exclusion
--   constraint matching the ON CONFLICT specification
--
-- IF EXISTS makes this idempotent. Safe to re-run.

ALTER TABLE exam_sessions DROP CONSTRAINT IF EXISTS exam_sessions_session_key_unique;
ALTER TABLE answers       DROP CONSTRAINT IF EXISTS answers_session_question_unique;

-- DELIBERATELY NOT DROPPED (they are still required for ON CONFLICT to work):
--   exam_config.exam_config_teacher_exam_unique
--   questions.questions_teacher_exam_question_unique
