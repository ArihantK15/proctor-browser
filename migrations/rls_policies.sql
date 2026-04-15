-- =====================================================================
-- Row Level Security (RLS) Policies for Procta
-- =====================================================================
-- IMPORTANT: The FastAPI backend uses the service_role key which
-- bypasses RLS entirely. These policies protect against:
--   1. Direct database access with the anon key
--   2. Client-side Supabase JS SDK access
--   3. Any future frontend-direct-to-DB patterns
--
-- Supabase auth.uid() returns the authenticated user's UUID from
-- the JWT. Teachers have supabase_uid in the teachers table,
-- students have supabase_uid in the student_accounts table.
-- =====================================================================

-- ── Helper function: get teacher_id from auth.uid() ─────────────
CREATE OR REPLACE FUNCTION public.get_my_teacher_id()
RETURNS TEXT
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT id FROM teachers WHERE supabase_uid = auth.uid() LIMIT 1;
$$;

-- ── Helper function: get student account_id from auth.uid() ─────
CREATE OR REPLACE FUNCTION public.get_my_student_account_id()
RETURNS TEXT
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT id FROM student_accounts WHERE supabase_uid = auth.uid() LIMIT 1;
$$;

-- ── Helper function: get student roll numbers for current user ──
CREATE OR REPLACE FUNCTION public.get_my_roll_numbers()
RETURNS SETOF TEXT
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT roll_number FROM students
  WHERE account_id = (SELECT id FROM student_accounts WHERE supabase_uid = auth.uid() LIMIT 1);
$$;


-- =====================================================================
-- 1. TEACHERS
-- =====================================================================
ALTER TABLE teachers ENABLE ROW LEVEL SECURITY;

-- Teachers can read their own record
CREATE POLICY teachers_select_own ON teachers
  FOR SELECT USING (supabase_uid = auth.uid());

-- Teachers can insert their own record (signup)
CREATE POLICY teachers_insert_own ON teachers
  FOR INSERT WITH CHECK (supabase_uid = auth.uid());

-- Teachers can update their own record
CREATE POLICY teachers_update_own ON teachers
  FOR UPDATE USING (supabase_uid = auth.uid());


-- =====================================================================
-- 2. STUDENT_ACCOUNTS
-- =====================================================================
ALTER TABLE student_accounts ENABLE ROW LEVEL SECURITY;

-- Students can read their own account
CREATE POLICY student_accounts_select_own ON student_accounts
  FOR SELECT USING (supabase_uid = auth.uid());

-- Students can insert their own account (signup)
CREATE POLICY student_accounts_insert_own ON student_accounts
  FOR INSERT WITH CHECK (supabase_uid = auth.uid());

-- Students can update their own account
CREATE POLICY student_accounts_update_own ON student_accounts
  FOR UPDATE USING (supabase_uid = auth.uid());


-- =====================================================================
-- 3. STUDENTS (enrollment records, scoped by teacher_id)
-- =====================================================================
ALTER TABLE students ENABLE ROW LEVEL SECURITY;

-- Teachers can see their own students
CREATE POLICY students_teacher_select ON students
  FOR SELECT USING (teacher_id = public.get_my_teacher_id());

-- Teachers can register students
CREATE POLICY students_teacher_insert ON students
  FOR INSERT WITH CHECK (teacher_id = public.get_my_teacher_id());

-- Teachers can update their students
CREATE POLICY students_teacher_update ON students
  FOR UPDATE USING (teacher_id = public.get_my_teacher_id());

-- Teachers can delete their students
CREATE POLICY students_teacher_delete ON students
  FOR DELETE USING (teacher_id = public.get_my_teacher_id());

-- Students can see their own enrollment records
CREATE POLICY students_student_select ON students
  FOR SELECT USING (account_id = public.get_my_student_account_id());


-- =====================================================================
-- 4. EXAM_CONFIG
-- =====================================================================
ALTER TABLE exam_config ENABLE ROW LEVEL SECURITY;

-- Teachers can CRUD their own exam configs
CREATE POLICY exam_config_teacher_select ON exam_config
  FOR SELECT USING (teacher_id = public.get_my_teacher_id());

CREATE POLICY exam_config_teacher_insert ON exam_config
  FOR INSERT WITH CHECK (teacher_id = public.get_my_teacher_id());

CREATE POLICY exam_config_teacher_update ON exam_config
  FOR UPDATE USING (teacher_id = public.get_my_teacher_id());

CREATE POLICY exam_config_teacher_delete ON exam_config
  FOR DELETE USING (teacher_id = public.get_my_teacher_id());

-- Students can read exam config for exams they're enrolled in
-- (needed for schedule display, exam validation)
CREATE POLICY exam_config_student_select ON exam_config
  FOR SELECT USING (
    teacher_id IN (
      SELECT teacher_id FROM students
      WHERE account_id = public.get_my_student_account_id()
    )
  );

-- Anonymous read for public schedule endpoint
CREATE POLICY exam_config_anon_select ON exam_config
  FOR SELECT USING (auth.role() = 'anon');


-- =====================================================================
-- 5. QUESTIONS
-- =====================================================================
ALTER TABLE questions ENABLE ROW LEVEL SECURITY;

-- Teachers can CRUD their own questions
CREATE POLICY questions_teacher_select ON questions
  FOR SELECT USING (teacher_id = public.get_my_teacher_id());

CREATE POLICY questions_teacher_insert ON questions
  FOR INSERT WITH CHECK (teacher_id = public.get_my_teacher_id());

CREATE POLICY questions_teacher_update ON questions
  FOR UPDATE USING (teacher_id = public.get_my_teacher_id());

CREATE POLICY questions_teacher_delete ON questions
  FOR DELETE USING (teacher_id = public.get_my_teacher_id());

-- Students can read questions for their teacher's exams (during exam)
CREATE POLICY questions_student_select ON questions
  FOR SELECT USING (
    teacher_id IN (
      SELECT teacher_id FROM students
      WHERE account_id = public.get_my_student_account_id()
    )
  );


-- =====================================================================
-- 6. EXAM_SESSIONS
-- =====================================================================
ALTER TABLE exam_sessions ENABLE ROW LEVEL SECURITY;

-- Teachers can read/update sessions for their exams
CREATE POLICY exam_sessions_teacher_select ON exam_sessions
  FOR SELECT USING (teacher_id = public.get_my_teacher_id());

CREATE POLICY exam_sessions_teacher_update ON exam_sessions
  FOR UPDATE USING (teacher_id = public.get_my_teacher_id());

CREATE POLICY exam_sessions_teacher_delete ON exam_sessions
  FOR DELETE USING (teacher_id = public.get_my_teacher_id());

-- Students can read their own sessions
CREATE POLICY exam_sessions_student_select ON exam_sessions
  FOR SELECT USING (roll_number IN (SELECT public.get_my_roll_numbers()));

-- Students can insert/update their own sessions (heartbeat, exam start)
CREATE POLICY exam_sessions_student_insert ON exam_sessions
  FOR INSERT WITH CHECK (roll_number IN (SELECT public.get_my_roll_numbers()));

CREATE POLICY exam_sessions_student_update ON exam_sessions
  FOR UPDATE USING (roll_number IN (SELECT public.get_my_roll_numbers()));


-- =====================================================================
-- 7. VIOLATIONS
-- =====================================================================
ALTER TABLE violations ENABLE ROW LEVEL SECURITY;

-- Teachers can read violations for their sessions
CREATE POLICY violations_teacher_select ON violations
  FOR SELECT USING (teacher_id = public.get_my_teacher_id());

-- Students can insert violations (event logging)
CREATE POLICY violations_student_insert ON violations
  FOR INSERT WITH CHECK (
    session_key LIKE (
      (SELECT roll_number FROM students
       WHERE account_id = public.get_my_student_account_id()
       LIMIT 1) || '_%'
    )
  );

-- Teachers can delete violations (cleanup)
CREATE POLICY violations_teacher_delete ON violations
  FOR DELETE USING (teacher_id = public.get_my_teacher_id());


-- =====================================================================
-- 8. ANSWERS
-- =====================================================================
ALTER TABLE answers ENABLE ROW LEVEL SECURITY;

-- Teachers can read answers for their sessions
CREATE POLICY answers_teacher_select ON answers
  FOR SELECT USING (teacher_id = public.get_my_teacher_id());

-- Students can insert/update their own answers
CREATE POLICY answers_student_insert ON answers
  FOR INSERT WITH CHECK (
    session_key LIKE (
      (SELECT roll_number FROM students
       WHERE account_id = public.get_my_student_account_id()
       LIMIT 1) || '_%'
    )
  );

CREATE POLICY answers_student_update ON answers
  FOR UPDATE USING (
    session_key LIKE (
      (SELECT roll_number FROM students
       WHERE account_id = public.get_my_student_account_id()
       LIMIT 1) || '_%'
    )
  );

-- Students can read their own answers (for resume)
CREATE POLICY answers_student_select ON answers
  FOR SELECT USING (
    session_key LIKE (
      (SELECT roll_number FROM students
       WHERE account_id = public.get_my_student_account_id()
       LIMIT 1) || '_%'
    )
  );


-- =====================================================================
-- 9. STUDENT_GROUPS
-- =====================================================================
ALTER TABLE student_groups ENABLE ROW LEVEL SECURITY;

CREATE POLICY student_groups_teacher_select ON student_groups
  FOR SELECT USING (teacher_id = public.get_my_teacher_id());

CREATE POLICY student_groups_teacher_insert ON student_groups
  FOR INSERT WITH CHECK (teacher_id = public.get_my_teacher_id());

CREATE POLICY student_groups_teacher_update ON student_groups
  FOR UPDATE USING (teacher_id = public.get_my_teacher_id());

CREATE POLICY student_groups_teacher_delete ON student_groups
  FOR DELETE USING (teacher_id = public.get_my_teacher_id());


-- =====================================================================
-- 10. STUDENT_GROUP_MEMBERS
-- =====================================================================
ALTER TABLE student_group_members ENABLE ROW LEVEL SECURITY;

CREATE POLICY sgm_teacher_select ON student_group_members
  FOR SELECT USING (teacher_id = public.get_my_teacher_id());

CREATE POLICY sgm_teacher_insert ON student_group_members
  FOR INSERT WITH CHECK (teacher_id = public.get_my_teacher_id());

CREATE POLICY sgm_teacher_delete ON student_group_members
  FOR DELETE USING (teacher_id = public.get_my_teacher_id());


-- =====================================================================
-- 11. EXAM_GROUP_ASSIGNMENTS
-- =====================================================================
ALTER TABLE exam_group_assignments ENABLE ROW LEVEL SECURITY;

CREATE POLICY ega_teacher_select ON exam_group_assignments
  FOR SELECT USING (teacher_id = public.get_my_teacher_id());

CREATE POLICY ega_teacher_insert ON exam_group_assignments
  FOR INSERT WITH CHECK (teacher_id = public.get_my_teacher_id());

CREATE POLICY ega_teacher_delete ON exam_group_assignments
  FOR DELETE USING (teacher_id = public.get_my_teacher_id());


-- =====================================================================
-- 12. QUESTION_BANK
-- =====================================================================
ALTER TABLE question_bank ENABLE ROW LEVEL SECURITY;

CREATE POLICY question_bank_teacher_select ON question_bank
  FOR SELECT USING (teacher_id = public.get_my_teacher_id());

CREATE POLICY question_bank_teacher_insert ON question_bank
  FOR INSERT WITH CHECK (teacher_id = public.get_my_teacher_id());

CREATE POLICY question_bank_teacher_update ON question_bank
  FOR UPDATE USING (teacher_id = public.get_my_teacher_id());

CREATE POLICY question_bank_teacher_delete ON question_bank
  FOR DELETE USING (teacher_id = public.get_my_teacher_id());


-- =====================================================================
-- 13. DEMO_REQUESTS
-- =====================================================================
ALTER TABLE demo_requests ENABLE ROW LEVEL SECURITY;

-- Anyone can submit a demo request
CREATE POLICY demo_requests_anon_insert ON demo_requests
  FOR INSERT WITH CHECK (true);

-- Only teachers can read demo requests
CREATE POLICY demo_requests_teacher_select ON demo_requests
  FOR SELECT USING (public.get_my_teacher_id() IS NOT NULL);
