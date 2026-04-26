-- Phase 10: Scorecard email idempotency
--
-- Records when (and to whom) a scorecard PDF was emailed for a
-- completed session. Written by the "Email all scorecards" endpoint
-- (see /api/admin/exams/{exam_id}/email-scorecards in app/main.py).
--
-- Idempotency strategy mirrors the reminder loop: we claim the row
-- with an UPDATE-that-filters-on-NULL before sending, so a second
-- invocation of the endpoint (teacher clicks twice, two dashboard
-- tabs, curl from a script) can't double-email. If the send fails
-- the claim timestamp is rolled back so a later retry can re-claim.
--
-- Denormalised on exam_sessions rather than a separate send log
-- because we only care about "was this session emailed?" — not the
-- full history. If we later need delivery tracking (bounced, opened)
-- we'll add a scorecard_sends table with FK to the session.

alter table exam_sessions
  add column if not exists scorecard_emailed_at timestamptz,
  add column if not exists scorecard_email_msg_id text;

-- Skips already-emailed sessions when the bulk endpoint scans a
-- whole exam. Partial index because most sessions will be null for
-- the first few weeks, and we only filter by "not yet emailed".
create index if not exists idx_sess_scorecard_unemailed
  on exam_sessions(exam_id, teacher_id)
  where scorecard_emailed_at is null and status = 'completed';
