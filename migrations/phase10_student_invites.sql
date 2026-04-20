-- Phase 10: Student Invites (email-based onboarding)
--
-- When a teacher uploads a roster (or clicks "Invite group") Procta
-- mints one row per student here, emails them a link back to
-- /invite/<token>, and tracks delivery + acceptance.
--
-- Status lifecycle:
--   queued   — row inserted, email not yet sent (batched)
--   sent     — provider accepted the message
--   opened   — landing page GET'd by student (best-effort, no tracking pixel)
--   accepted — student successfully validated into an exam session
--   bounced  — provider webhook reported hard bounce
--   failed   — provider rejected (spam block, etc.)
--   revoked  — teacher clicked "Revoke" before student used it
--
-- Tokens are URL-safe 32-byte random strings. They're stateless in the
-- sense that the server looks them up here to resolve student_id +
-- exam_id; no JWT / HMAC secret needed beyond ordinary DB auth. If this
-- table is ever compromised the invites should be revoked in bulk.

create table if not exists student_invites (
  id            uuid primary key default gen_random_uuid(),
  token         text unique not null,
  teacher_id    text not null,
  student_id    uuid,                            -- NULL until student row created
  roll_number   text not null,                   -- denormalised for cheap lookup
  email         text not null,
  full_name     text not null,
  exam_id       text,                            -- NULL = any-exam invite (rare)
  group_id      uuid references student_groups(id) on delete set null,
  access_code   text,                            -- per-invite one-time code (optional)
  custom_message text,                           -- teacher's message appended to email
  status        text not null default 'queued',
  sent_at       timestamptz,
  opened_at     timestamptz,
  accepted_at   timestamptz,
  bounced_at    timestamptz,
  bounce_reason text,
  provider_msg_id text,                          -- Resend message id for dedup
  expires_at    timestamptz,                     -- usually exam.ends_at
  created_at    timestamptz default now(),
  created_by    text                             -- teacher_id, for audit
);

create index if not exists idx_si_teacher       on student_invites(teacher_id);
create index if not exists idx_si_exam          on student_invites(exam_id, teacher_id);
create index if not exists idx_si_status        on student_invites(status);
create index if not exists idx_si_email_teacher on student_invites(email, teacher_id);
create index if not exists idx_si_token         on student_invites(token);

-- Per-teacher daily send cap. A simple counter table instead of a cron
-- job; each send increments today's row and invites are rejected above
-- the cap. Resets automatically via the date key.
create table if not exists invite_send_counters (
  teacher_id text not null,
  day        date not null,
  count      int  not null default 0,
  primary key (teacher_id, day)
);
create index if not exists idx_isc_day on invite_send_counters(day);
