-- Google Classroom integration — OAuth tokens, course-exam links, ephemeral OAuth states.

CREATE TABLE IF NOT EXISTS google_auth_tokens (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    teacher_id      UUID NOT NULL UNIQUE REFERENCES teachers(id) ON DELETE CASCADE,
    email           TEXT NOT NULL DEFAULT '',
    display_name    TEXT NOT NULL DEFAULT '',
    token_json      TEXT NOT NULL,              -- encrypted OAuth token payload
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS google_classroom_links (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    teacher_id      UUID NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
    google_course_id TEXT NOT NULL,
    exam_id         TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT      uq_teacher_course UNIQUE (teacher_id, google_course_id)
);

CREATE TABLE IF NOT EXISTS google_oauth_states (
    state           TEXT PRIMARY KEY,
    teacher_id      TEXT NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- OAuth states are ephemeral — cleaned up on callback. No FK needed.
