-- phase116: assign exams to cohorts/batches (gap #59 — standing access).
--
-- Parallel to exam_group_assignments, but the "membership" is DERIVED from the
-- students.batch label rather than explicit member rows — so assigning an exam
-- to a batch gives the whole cohort standing access with zero per-student
-- bookkeeping. Access semantics (see repositories/sessions.check_group_access):
--   * exam has NO group AND NO batch assignments  → open to everyone (unchanged)
--   * exam has assignments → a student may enter iff they are a member of an
--     assigned group OR their students.batch matches an assigned batch.
-- teacher_id/exam_id are TEXT to match exam_group_assignments exactly.

create table if not exists exam_batch_assignments (
  id uuid primary key default gen_random_uuid(),
  exam_id text not null,
  batch varchar(120) not null,
  teacher_id text not null,
  created_at timestamptz default now(),
  unique(exam_id, batch, teacher_id)
);

create index if not exists idx_eba_exam on exam_batch_assignments(exam_id, teacher_id);
