-- Phase 10: Student Groups / Sections
-- Allows teachers to create groups, assign students, and restrict exam access by group.

create table if not exists student_groups (
  id uuid primary key default gen_random_uuid(),
  teacher_id text not null,
  group_name text not null,
  created_at timestamptz default now(),
  unique(teacher_id, group_name)
);

create table if not exists student_group_members (
  id uuid primary key default gen_random_uuid(),
  group_id uuid not null references student_groups(id) on delete cascade,
  roll_number text not null,
  teacher_id text not null,
  unique(group_id, roll_number, teacher_id)
);

create table if not exists exam_group_assignments (
  id uuid primary key default gen_random_uuid(),
  exam_id text not null,
  group_id uuid not null references student_groups(id) on delete cascade,
  teacher_id text not null,
  unique(exam_id, group_id, teacher_id)
);

create index if not exists idx_sg_teacher on student_groups(teacher_id);
create index if not exists idx_sgm_group on student_group_members(group_id);
create index if not exists idx_ega_exam on exam_group_assignments(exam_id, teacher_id);
