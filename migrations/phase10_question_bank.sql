-- Phase 10: Question Bank
-- Reusable question pool teachers can build, tag, import/export, and copy into exams.

create table if not exists question_bank (
  id uuid primary key default gen_random_uuid(),
  teacher_id text not null,
  question text not null,
  question_type text not null default 'mcq_single',
  options jsonb not null default '{}',
  correct text not null,
  image_url text default '',
  tags text[] default '{}',
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create index if not exists idx_qbank_teacher on question_bank(teacher_id);
