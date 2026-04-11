-- Phase 1: student web dashboard foundation.
--
-- Creates a cross-teacher student identity that is separate from the
-- per-teacher `students` enrollment rows. A single student_account can
-- be linked to many `students` rows (one per teacher they sit an exam
-- with) via `students.account_id`.
--
-- Run this against Supabase before deploying the Phase 1 backend.

create table if not exists student_accounts (
  id           uuid primary key default gen_random_uuid(),
  supabase_uid uuid unique not null,
  email        text unique not null,
  full_name    text not null,
  created_at   timestamptz default now()
);

-- Backfill-friendly link from the existing per-teacher enrollment table
-- to the new cross-teacher account. Nullable so legacy rows keep working
-- until a student signs up and we auto-link by email on first login.
alter table students
  add column if not exists account_id uuid references student_accounts(id);

create index if not exists idx_students_account_id on students(account_id);
create index if not exists idx_student_accounts_email on student_accounts(email);
