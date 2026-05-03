-- Phase 12: Short-answer questions with AI-suggested grading
--
-- Adds columns to `questions` for the new "short_answer" question
-- type, plus columns to `answers` so teacher-confirmed grades are
-- distinguished from raw student responses.
--
-- Why these specific columns:
--   reference_answer  — the model answer the teacher writes ("ACID")
--   rubric            — optional grading criteria ("Accept any of:
--                       atomicity / consistency / isolation / durability")
--                       Free text; the LLM uses it as additional context.
--   max_score         — usually 1 / 2 / 3 / 5 marks. Affects how
--                       partial credit is calculated.
--
-- Why columns on `answers`:
--   ai_score          — the LLM-suggested score (decimal so we can
--                       do half-marks: 1.5/2). NULL = not graded yet.
--   ai_feedback       — 1-2 sentence rationale shown to teacher.
--   ai_confidence     — high|medium|low; teacher can sort by this
--                       to triage which answers most need review.
--   teacher_score     — the FINAL score after teacher review. NULL
--                       means "still pending review". When set,
--                       overrides ai_score for the gradebook.
--   graded_at         — timestamp of teacher confirmation, for
--                       audit trail.
--
-- Idempotent. Two-phase: questions first, then answers. Notify
-- pgrst at the end so the new columns are queryable without an
-- API restart.

alter table questions
  add column if not exists reference_answer text default '',
  add column if not exists rubric           text default '',
  add column if not exists max_score        numeric(5,2) default 1.0;

alter table answers
  add column if not exists ai_score        numeric(5,2),
  add column if not exists ai_feedback     text,
  add column if not exists ai_confidence   text,
  add column if not exists teacher_score   numeric(5,2),
  add column if not exists graded_at       timestamptz;

-- Index for the "pending grades" query — find answers that have
-- raw text but no teacher confirmation yet. Partial index because
-- the vast majority of answers (MCQ) won't need this column at all.
create index if not exists idx_answers_pending_grade
  on answers (teacher_id, exam_id)
  where teacher_score is null and ai_score is not null;

notify pgrst, 'reload schema';
