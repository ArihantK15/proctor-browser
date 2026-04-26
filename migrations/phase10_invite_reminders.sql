-- Phase 10: Invite reminder timestamps
--
-- Two new columns on student_invites record when we last sent a
-- "heads up, your exam starts soon" reminder for each invite. The
-- reminder loop (see _reminder_loop in app/main.py) updates these
-- atomically WITH a `is null` guard so the UPDATE itself is the
-- race-free claim — whichever worker wins the update does the send,
-- losers get data=[] back and skip.
--
-- Kept denormalised on student_invites (rather than a separate
-- reminder_sends log) because invites are already the join table
-- between students + exams; one extra column is cheaper than another
-- table + FK. The tradeoff is that revoking/recreating an invite
-- resets the reminder state, which is the behaviour we want anyway.

alter table student_invites
  add column if not exists reminder_24h_at timestamptz,
  add column if not exists reminder_1h_at  timestamptz;

-- Helps the reminder loop filter by "not yet sent" without a full
-- table scan once the invite table grows past a few thousand rows.
create index if not exists idx_si_reminder_24h_null
  on student_invites(exam_id) where reminder_24h_at is null;
create index if not exists idx_si_reminder_1h_null
  on student_invites(exam_id) where reminder_1h_at  is null;
