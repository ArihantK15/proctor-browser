-- Phase 10: Invite click tracking
--
-- Records when (and how many times) a recipient clicked the link inside
-- their invite email. Resend reports `email.clicked` via webhook with
-- the same provider message id we already store on the invite row.
--
-- Why a separate signal from `opened_at`:
--   * Opens are pixel-based and unreliable — Outlook desktop blocks
--     remote images by default, Apple Mail's Privacy Protection
--     pre-fetches every pixel (firing fake opens), and Resend's
--     domain-level open tracking has to be explicitly enabled.
--   * Clicks go through Resend's redirect domain — they're a real
--     server-side hit, immune to pixel blocking, and a far more honest
--     "did the recipient engage?" signal.
--
-- We keep `clicked_at` as a first-click timestamp (same convention as
-- `opened_at`). `click_count` lets the dashboard show "clicked 3×"
-- without us having to keep an event log table — schools mostly want
-- the headline number, and Resend retains full event history anyway.
--
-- Status flow change: a `sent` invite becomes `clicked` on the first
-- click webhook. We don't downgrade `accepted` (student already in) or
-- `bounced` (hard fail) — same non-overwriting policy as `opened`.

alter table student_invites
  add column if not exists clicked_at  timestamptz,
  add column if not exists click_count int not null default 0;

-- Partial index speeds up the dashboard's "engaged but not started"
-- query — most invite rows will never be clicked, so a full index
-- would mostly hold nulls.
create index if not exists idx_si_clicked
  on student_invites(exam_id, teacher_id)
  where clicked_at is not null;
