-- Phase 15: Atomic invite-cap claim RPC
--
-- Replaces the read-then-write pattern in _claim_and_bump_cap that
-- could overshoot INVITE_DAILY_CAP under concurrent traffic. Two
-- workers reading count=0, both seeing remaining=500, both writing
-- count=500 → 1000 invites sent in a 500-invite cap day.
--
-- This RPC folds the check + bump into a single SQL statement with
-- row-level locking via the conditional UPDATE. The row is locked
-- by the UPDATE itself; the conflicting workers serialise behind it
-- and the second one's `count + p_batch <= p_cap` predicate fails
-- if the first one already exhausted the budget.
--
-- Returns: remaining quota after this claim succeeds, or -1 if denied.
--
-- Idempotent: drops + recreates the function. The unique constraint
-- on (teacher_id, day) is required so the INSERT … ON CONFLICT path
-- works — add it if missing.

-- Ensure the uniqueness constraint exists (no-op if already present
-- because the table was created with it). Without this, the ON
-- CONFLICT clause in the RPC errors out.
do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conname = 'invite_send_counters_teacher_day_uniq'
  ) then
    -- Defensive: only add if there are no duplicates already
    if not exists (
      select teacher_id, day, count(*)
      from invite_send_counters
      group by teacher_id, day
      having count(*) > 1
    ) then
      alter table invite_send_counters
        add constraint invite_send_counters_teacher_day_uniq
        unique (teacher_id, day);
    end if;
  end if;
end $$;

drop function if exists claim_invite_cap(text, int, int);

create or replace function claim_invite_cap(
  p_teacher_id text,
  p_batch      int,
  p_cap        int
) returns int
language plpgsql
as $$
declare
  v_new_count int;
begin
  -- Ensure today's row exists. If it already exists, ON CONFLICT DO
  -- NOTHING leaves the existing count untouched — we only want to
  -- create it on the first send of the day.
  insert into invite_send_counters (teacher_id, day, count)
    values (p_teacher_id, current_date, 0)
    on conflict (teacher_id, day) do nothing;

  -- The atomic part: increment iff the resulting count fits the cap.
  -- Postgres takes a row lock for the duration of the UPDATE, so two
  -- concurrent calls serialise: the second one re-reads `count`
  -- after the first commits, and its predicate either passes (still
  -- within cap) or fails (would overshoot).
  update invite_send_counters
    set count = count + p_batch
    where teacher_id = p_teacher_id
      and day = current_date
      and count + p_batch <= p_cap
    returning count into v_new_count;

  if v_new_count is null then
    return -1;  -- denied: would overshoot
  end if;
  return p_cap - v_new_count;  -- remaining after this claim
end $$;

-- Allow the service role / authenticated role to call this. Adjust
-- to match your role naming if Supabase is set up differently.
grant execute on function claim_invite_cap(text, int, int) to anon, authenticated, service_role;

notify pgrst, 'reload schema';
