-- Reverse phase146: narrow questions.question_id back to integer.
--
-- Only safe while every question_id is still digit-only (no coding-label
-- rows). `USING question_id::integer` will ERROR if any non-numeric label
-- (coding-<uuid>) is present — that is intentional: you cannot revert once
-- coding questions have been authored without first deleting them. The
-- (teacher_id, exam_id, question_id) UNIQUE index rebuilds automatically.
ALTER TABLE public.questions
    ALTER COLUMN question_id TYPE integer USING question_id::integer;
