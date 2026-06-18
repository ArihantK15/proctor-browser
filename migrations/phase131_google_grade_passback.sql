-- =====================================================================
-- phase131: Google Classroom grade passback support
-- =====================================================================
-- Wiring exam-score passback into Google Classroom needs two facts the
-- roster sync didn't previously persist, plus a place to remember the
-- Classroom assignment we grade against:
--
--   students.google_user_id    — the student's Google directory id, needed
--                                to target their studentSubmission (the API
--                                grades by userId, not email).
--   students.google_course_id  — which linked course the student was synced
--                                from, to disambiguate when an exam is linked
--                                from more than one course.
--   google_classroom_links.course_work_id
--                              — the assignment (courseWork) Procta creates
--                                lazily in the course the first time a grade
--                                is pushed; grades are patched onto its
--                                studentSubmissions.
--
-- All additive + nullable; no backfill (re-syncing the roster populates the
-- student columns, and course_work_id is created on first passback).
-- =====================================================================

BEGIN;

ALTER TABLE students ADD COLUMN IF NOT EXISTS google_user_id TEXT;
ALTER TABLE students ADD COLUMN IF NOT EXISTS google_course_id TEXT;
ALTER TABLE google_classroom_links ADD COLUMN IF NOT EXISTS course_work_id TEXT;

COMMIT;
