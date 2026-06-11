--
-- PostgreSQL database dump
--

\restrict d6bIkeM5mOCueFX7vBkNe0QeCieLnNvtKkjM1Le8EGvdYRdUU7GqDdYZMz8ZT6r

-- Dumped from database version 16.14
-- Dumped by pg_dump version 16.14

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: auth; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA auth;


--
-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;


--
-- Name: EXTENSION pgcrypto; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pgcrypto IS 'cryptographic functions';


--
-- Name: role(); Type: FUNCTION; Schema: auth; Owner: -
--

CREATE FUNCTION auth.role() RETURNS text
    LANGUAGE sql STABLE
    AS $$ SELECT 'service_role'::text $$;


--
-- Name: uid(); Type: FUNCTION; Schema: auth; Owner: -
--

CREATE FUNCTION auth.uid() RETURNS uuid
    LANGUAGE sql STABLE
    AS $$ SELECT NULL::uuid $$;


--
-- Name: claim_invite_cap(text, integer, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.claim_invite_cap(p_teacher_id text, p_batch integer, p_cap integer) RETURNS integer
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: claim_scorecard_email(text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.claim_scorecard_email(p_session_key text, p_teacher_id text) RETURNS boolean
    LANGUAGE plpgsql
    AS $$
declare
  v_count int;
begin
  update exam_sessions
    set scorecard_claim_at = now()
    where session_key = p_session_key
      and teacher_id = p_teacher_id
      and (
        scorecard_claim_at is null
        or scorecard_claim_at < now() - interval '5 minutes'
      )
    returning 1 into v_count;

  return v_count is not null;
end $$;


--
-- Name: enforce_org_student_quota(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.enforce_org_student_quota() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public'
    AS $$
  DECLARE
    v_org_id       UUID;
    v_max_students INT;
    v_current      INT;
  BEGIN
    SELECT t.org_id INTO v_org_id FROM teachers t WHERE t.id = NEW.teacher_id;
    IF v_org_id IS NULL THEN RETURN NEW; END IF;

 PERFORM pg_advisory_xact_lock(hashtextextended(v_org_id::text, 0));

    SELECT o.max_students INTO v_max_students FROM organizations o WHERE o.id = v_org_id;
    IF v_max_students IS NULL THEN RETURN NEW; END IF;

    SELECT COUNT(*) INTO v_current
      FROM students s
      JOIN teachers t ON t.id = s.teacher_id
     WHERE t.org_id = v_org_id;

    IF v_current + 1 > v_max_students THEN
      RAISE EXCEPTION
        'Student quota exceeded for organization %: % current, % allowed by plan. Upgrade your plan to add more students.',
        v_org_id, v_current, v_max_students
        USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
  END;
  $$;


--
-- Name: get_my_roll_numbers(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.get_my_roll_numbers() RETURNS SETOF text
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public'
    AS $$
  SELECT roll_number::text FROM students
  WHERE account_id::text = (SELECT id::text FROM student_accounts WHERE supabase_uid::text = auth.uid()::text LIMIT 1);
$$;


--
-- Name: get_my_student_account_id(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.get_my_student_account_id() RETURNS text
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public'
    AS $$
  SELECT id::text FROM student_accounts WHERE supabase_uid::text = auth.uid()::text LIMIT 1;
$$;


--
-- Name: get_my_teacher_id(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.get_my_teacher_id() RETURNS text
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public'
    AS $$
  SELECT id::text FROM teachers WHERE supabase_uid::text = auth.uid()::text LIMIT 1;
$$;


--
-- Name: sweep_transient_rows(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sweep_transient_rows() RETURNS integer
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public'
    AS $$
  DECLARE
    v_oauth_states  INTEGER := 0;
    v_email_otps    INTEGER := 0;
    v_refresh_toks  INTEGER := 0;
    v_auth_sessions INTEGER := 0;
    v_auth_events   INTEGER := 0;
    v_total         INTEGER := 0;
  BEGIN
    DELETE FROM google_oauth_states
      WHERE expires_at < now() - INTERVAL '1 hour';
    GET DIAGNOSTICS v_oauth_states = ROW_COUNT;
    RAISE NOTICE 'sweep: google_oauth_states deleted % rows', v_oauth_states;
  
    DELETE FROM email_otps
      WHERE (used_at IS NOT NULL AND used_at < now() - INTERVAL '7 days')
         OR (used_at IS NULL AND expires_at < now() - INTERVAL '7 days');
    GET DIAGNOSTICS v_email_otps = ROW_COUNT;
    RAISE NOTICE 'sweep: email_otps deleted % rows', v_email_otps;
  
    DELETE FROM refresh_tokens
      WHERE (revoked_at IS NOT NULL AND revoked_at < now() - INTERVAL '90 days')
         OR (revoked_at IS NULL AND expires_at < now() - INTERVAL '90 days');
    GET DIAGNOSTICS v_refresh_toks = ROW_COUNT;
    RAISE NOTICE 'sweep: refresh_tokens deleted % rows', v_refresh_toks;
  
    DELETE FROM auth_sessions 
      WHERE revoked_at IS NOT NULL
        AND revoked_at < now() - INTERVAL '30 days';
    GET DIAGNOSTICS v_auth_sessions = ROW_COUNT;
    RAISE NOTICE 'sweep: auth_sessions deleted % rows', v_auth_sessions;
  
    DELETE FROM auth_events
      WHERE created_at < now() - INTERVAL '180 days';
    GET DIAGNOSTICS v_auth_events = ROW_COUNT;
    RAISE NOTICE 'sweep: auth_events deleted % rows', v_auth_events;
  
    v_total := v_oauth_states + v_email_otps + v_refresh_toks
             + v_auth_sessions + v_auth_events;
    RAISE NOTICE 'sweep: total deleted % rows', v_total;
    RETURN v_total;
  END;
  $$;


--
-- Name: upsert_usage(text, integer, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.upsert_usage(p_org_id text, p_exam_attempts integer DEFAULT 1, p_students_used integer DEFAULT NULL::integer) RETURNS uuid
    LANGUAGE plpgsql
    AS $$
DECLARE
  v_now TIMESTAMPTZ := now();
  v_period_start TIMESTAMPTZ := date_trunc('month', v_now);
  v_period_end TIMESTAMPTZ := date_trunc('month', v_now) + INTERVAL '1 month';
  v_plan_limit INT;
  v_usage_id UUID;
BEGIN
  -- Get plan limit from org's max_students column (set by billing webhooks).
  -- Avoids plan::json cast which would fail for text values like 'starter'.
  SELECT COALESCE(
    (SELECT o.max_students
     FROM organizations o
     WHERE o.id = p_org_id
     LIMIT 1
    ), 30
  ) INTO v_plan_limit;

  INSERT INTO usage_records (org_id, period_start, period_end, exam_attempts, students_used, plan_limit)
  VALUES (p_org_id, v_period_start, v_period_end, p_exam_attempts, COALESCE(p_students_used, 0), v_plan_limit)
  ON CONFLICT (org_id, period_start)
  DO UPDATE SET
    exam_attempts = usage_records.exam_attempts + p_exam_attempts,
    students_used = GREATEST(usage_records.students_used, COALESCE(p_students_used, usage_records.students_used)),
    overage = GREATEST(0, usage_records.exam_attempts + p_exam_attempts - usage_records.plan_limit * 10),
    updated_at = now()
  RETURNING id INTO v_usage_id;

  RETURN v_usage_id;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: admin_audit_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.admin_audit_log (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    teacher_id uuid,
    action text NOT NULL,
    target_type text NOT NULL,
    target_id text,
    before_data jsonb,
    after_data jsonb,
    details jsonb DEFAULT '{}'::jsonb NOT NULL,
    ip text,
    user_agent text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: answers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.answers (
    id bigint NOT NULL,
    session_key text NOT NULL,
    question_id text NOT NULL,
    answer text NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    teacher_id uuid,
    exam_id text,
    ai_score numeric(5,2),
    ai_feedback text,
    ai_confidence text,
    teacher_score numeric(5,2),
    graded_at timestamp with time zone
);


--
-- Name: answers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.answers_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: answers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.answers_id_seq OWNED BY public.answers.id;


--
-- Name: api_keys; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.api_keys (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    teacher_id uuid NOT NULL,
    name text NOT NULL,
    key_hash text NOT NULL,
    key_prefix text NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_used_at timestamp with time zone
);


--
-- Name: appeals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.appeals (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    session_key text NOT NULL,
    student_id uuid NOT NULL,
    roll_number text DEFAULT ''::text NOT NULL,
    exam_id text,
    teacher_id uuid NOT NULL,
    appeal_type text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    teacher_note text DEFAULT ''::text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    resolved_at timestamp with time zone,
    violation_id uuid,
    resolution text,
    escalated_to text,
    escalated_at timestamp with time zone,
    CONSTRAINT appeals_appeal_type_check CHECK ((appeal_type = ANY (ARRAY['violation'::text, 'grade'::text, 'other'::text]))),
    CONSTRAINT appeals_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'accepted'::text, 'rejected'::text])))
);


--
-- Name: auth_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.auth_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_kind text NOT NULL,
    user_id text,
    email text,
    event_type text NOT NULL,
    ip text,
    user_agent text,
    meta jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: auth_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.auth_sessions (
    jti uuid NOT NULL,
    user_kind text NOT NULL,
    user_id text NOT NULL,
    issued_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    ip text,
    user_agent text,
    revoked_at timestamp with time zone
);


--
-- Name: billing_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.billing_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    event_id text,
    org_id uuid,
    razorpay_subscription_id text,
    razorpay_payment_id text,
    event_type text NOT NULL,
    status text,
    amount integer,
    currency text DEFAULT 'INR'::text,
    payload jsonb,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: consent_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.consent_records (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    user_type text NOT NULL,
    consent_type text NOT NULL,
    ip_address text DEFAULT ''::text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT consent_records_consent_type_check CHECK ((consent_type = ANY (ARRAY['signup_terms'::text, 'privacy_policy'::text, 'phone_camera'::text]))),
    CONSTRAINT consent_records_user_type_check CHECK ((user_type = ANY (ARRAY['teacher'::text, 'student'::text])))
);


--
-- Name: demo_requests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.demo_requests (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    email text NOT NULL,
    institution text,
    role text,
    message text,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: email_otps; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.email_otps (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_kind text NOT NULL,
    user_id text NOT NULL,
    purpose text NOT NULL,
    code_hash text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    attempts integer DEFAULT 0,
    used_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: exam_config_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.exam_config_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: exam_config; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.exam_config (
    id integer DEFAULT nextval('public.exam_config_id_seq'::regclass) NOT NULL,
    exam_title text DEFAULT 'Exam'::text,
    duration_minutes integer DEFAULT 60,
    updated_at timestamp with time zone DEFAULT now(),
    access_code text DEFAULT ''::text,
    starts_at timestamp with time zone,
    ends_at timestamp with time zone,
    teacher_id uuid,
    shuffle_questions boolean DEFAULT true,
    shuffle_options boolean DEFAULT true,
    exam_id uuid DEFAULT gen_random_uuid(),
    phone_camera_enabled boolean DEFAULT false NOT NULL,
    proctoring_sensitivity text DEFAULT 'balanced'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    audio_keywords text,
    audio_keywords_language text DEFAULT 'en'::text,
    CONSTRAINT exam_config_proctoring_sensitivity_check CHECK ((proctoring_sensitivity = ANY (ARRAY['strict'::text, 'balanced'::text, 'lenient'::text])))
);


--
-- Name: exam_group_assignments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.exam_group_assignments (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    exam_id text NOT NULL,
    group_id uuid NOT NULL,
    teacher_id uuid NOT NULL
);


--
-- Name: exam_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.exam_sessions (
    session_key text NOT NULL,
    roll_number text NOT NULL,
    full_name text,
    email text,
    status text DEFAULT 'in_progress'::text,
    started_at timestamp with time zone,
    last_heartbeat timestamp with time zone,
    submitted_at timestamp with time zone,
    score integer,
    total integer,
    percentage double precision,
    time_taken_secs integer,
    risk_score double precision,
    created_at timestamp with time zone DEFAULT now(),
    teacher_id uuid,
    exam_id uuid,
    scorecard_emailed_at timestamp with time zone,
    scorecard_email_msg_id text,
    scorecard_insight text,
    room_cam_status text DEFAULT 'disabled'::text NOT NULL,
    room_cam_approved_at timestamp with time zone,
    room_cam_last_frame_at timestamp with time zone,
    phone_camera_consented boolean DEFAULT false NOT NULL,
    scorecard_claim_at timestamp with time zone,
    student_id text,
    updated_at timestamp with time zone DEFAULT now(),
    paused_at timestamp with time zone,
    paused_secs_total integer DEFAULT 0 NOT NULL,
    terminated_by text,
    termination_reason_code text,
    termination_reason_text text,
    CONSTRAINT exam_sessions_status_check CHECK (((status IS NULL) OR (status = ANY (ARRAY['in_progress'::text, 'paused'::text, 'completed'::text, 'submitted'::text, 'force_submitted'::text, 'abandoned'::text, 'rejected'::text]))))
);


--
-- Name: exam_templates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.exam_templates (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    teacher_id uuid NOT NULL,
    template_name text NOT NULL,
    exam_title text NOT NULL,
    duration_minutes integer DEFAULT 60,
    access_code text DEFAULT ''::text,
    shuffle_questions boolean DEFAULT false,
    shuffle_options boolean DEFAULT false,
    questions jsonb DEFAULT '[]'::jsonb,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: google_auth_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.google_auth_tokens (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    teacher_id uuid NOT NULL,
    email text DEFAULT ''::text NOT NULL,
    display_name text DEFAULT ''::text NOT NULL,
    token_json text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: google_classroom_links; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.google_classroom_links (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    teacher_id uuid NOT NULL,
    google_course_id text NOT NULL,
    exam_id text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: google_oauth_states; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.google_oauth_states (
    state text NOT NULL,
    teacher_id uuid NOT NULL,
    expires_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: grading_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.grading_audit (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    teacher_id uuid NOT NULL,
    teacher_name text DEFAULT ''::text NOT NULL,
    exam_id text,
    session_key text,
    answer_id text,
    question_id text,
    ai_score numeric,
    ai_confidence text,
    teacher_score numeric NOT NULL,
    max_score numeric DEFAULT 1.0 NOT NULL,
    action text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT grading_audit_action_check CHECK ((action = ANY (ARRAY['confirmed'::text, 'bulk_accept'::text, 'bulk_reject'::text, 'overridden'::text]))),
    CONSTRAINT grading_audit_ai_confidence_check CHECK (((ai_confidence IS NULL) OR (ai_confidence = ANY (ARRAY['low'::text, 'medium'::text, 'high'::text]))))
);


--
-- Name: invite_send_counters; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.invite_send_counters (
    teacher_id uuid NOT NULL,
    day date NOT NULL,
    count integer DEFAULT 0 NOT NULL
);


--
-- Name: issues; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.issues (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    org_id uuid NOT NULL,
    teacher_id uuid NOT NULL,
    session_id text,
    exam_id uuid,
    category text NOT NULL,
    severity text DEFAULT 'normal'::text NOT NULL,
    description text NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    superadmin_note text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    resolved_at timestamp with time zone,
    CONSTRAINT issues_category_check CHECK ((category = ANY (ARRAY['bug'::text, 'question'::text, 'feature'::text, 'session-issue'::text, 'other'::text]))),
    CONSTRAINT issues_severity_check CHECK ((severity = ANY (ARRAY['low'::text, 'normal'::text, 'high'::text]))),
    CONSTRAINT issues_status_check CHECK ((status = ANY (ARRAY['open'::text, 'triaged'::text, 'resolved'::text])))
);


--
-- Name: org_invites; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.org_invites (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    org_id uuid NOT NULL,
    email text NOT NULL,
    full_name text,
    status text DEFAULT 'pending'::text NOT NULL,
    invited_by uuid,
    expires_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    accepted_at timestamp with time zone,
    token_hash text,
    CONSTRAINT org_invites_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'accepted'::text, 'expired'::text, 'revoked'::text])))
);


--
-- Name: organizations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.organizations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    slug text NOT NULL,
    max_students integer DEFAULT 30 NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    logo_url text,
    gstin text
);


--
-- Name: COLUMN organizations.logo_url; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.organizations.logo_url IS 'Optional HTTPS URL to the org logo (PNG/JPG/SVG). Rendered top-left of every scorecard PDF and at the head of branded email templates. App-layer validates scheme=https and length<=1024.';


--
-- Name: question_bank; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.question_bank (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    teacher_id uuid NOT NULL,
    question text NOT NULL,
    question_type text DEFAULT 'mcq_single'::text NOT NULL,
    options jsonb DEFAULT '{}'::jsonb NOT NULL,
    correct text NOT NULL,
    image_url text DEFAULT ''::text,
    tags text[] DEFAULT '{}'::text[],
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: questions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.questions (
    id bigint NOT NULL,
    question_id integer NOT NULL,
    question text NOT NULL,
    options jsonb NOT NULL,
    correct text NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    teacher_id uuid,
    exam_id uuid,
    image_url text DEFAULT ''::text,
    question_type text DEFAULT 'mcq_single'::text,
    tags text[] DEFAULT '{}'::text[],
    reference_answer text DEFAULT ''::text,
    rubric text DEFAULT ''::text,
    max_score numeric(5,2) DEFAULT 1.0
);


--
-- Name: questions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.questions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: questions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.questions_id_seq OWNED BY public.questions.id;


--
-- Name: refresh_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.refresh_tokens (
    jti uuid NOT NULL,
    user_id text NOT NULL,
    kind text NOT NULL,
    issued_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    replaced_by_jti uuid,
    ip text,
    user_agent text,
    CONSTRAINT refresh_tokens_kind_check CHECK ((kind = ANY (ARRAY['teacher'::text, 'student'::text])))
);


--
-- Name: schema_migrations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schema_migrations (
    filename text NOT NULL,
    applied_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: student_accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.student_accounts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    supabase_uid uuid NOT NULL,
    email text NOT NULL,
    full_name text NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    email_verified_at timestamp with time zone,
    totp_secret text,
    totp_enabled_at timestamp with time zone,
    backup_codes_hash jsonb DEFAULT '[]'::jsonb,
    totp_grace_started_at timestamp with time zone DEFAULT now(),
    password_hash text,
    auth_provider text DEFAULT 'supabase'::text NOT NULL,
    password_changed_at timestamp with time zone,
    email_2fa_enabled_at timestamp with time zone,
    deleted_at timestamp with time zone,
    email_reminders_enabled boolean DEFAULT true NOT NULL
);


--
-- Name: student_group_members; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.student_group_members (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    group_id uuid NOT NULL,
    roll_number text NOT NULL,
    teacher_id uuid NOT NULL
);


--
-- Name: student_groups; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.student_groups (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    teacher_id uuid NOT NULL,
    group_name text NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: student_invites; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.student_invites (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    token text NOT NULL,
    teacher_id uuid NOT NULL,
    student_id uuid,
    roll_number text NOT NULL,
    email text NOT NULL,
    full_name text NOT NULL,
    exam_id text,
    group_id uuid,
    access_code text,
    custom_message text,
    status text DEFAULT 'queued'::text NOT NULL,
    sent_at timestamp with time zone,
    opened_at timestamp with time zone,
    accepted_at timestamp with time zone,
    bounced_at timestamp with time zone,
    bounce_reason text,
    provider_msg_id text,
    expires_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    created_by text,
    reminder_24h_at timestamp with time zone,
    reminder_1h_at timestamp with time zone,
    clicked_at timestamp with time zone,
    click_count integer DEFAULT 0 NOT NULL
);


--
-- Name: students; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.students (
    id bigint NOT NULL,
    roll_number text NOT NULL,
    full_name text NOT NULL,
    email text,
    phone text,
    created_at timestamp with time zone DEFAULT now(),
    teacher_id uuid,
    account_id uuid,
    org_id uuid,
    removed_at timestamp with time zone
);


--
-- Name: students_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.students_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: students_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.students_id_seq OWNED BY public.students.id;


--
-- Name: subscriptions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.subscriptions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    org_id uuid NOT NULL,
    plan text DEFAULT 'starter'::text NOT NULL,
    status text DEFAULT 'trialing'::text NOT NULL,
    trial_end timestamp with time zone,
    razorpay_subscription_id text,
    razorpay_order_id text,
    current_period_start timestamp with time zone,
    current_period_end timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    past_due_since timestamp with time zone,
    CONSTRAINT subscriptions_plan_check CHECK ((plan = ANY (ARRAY['starter'::text, 'growth'::text, 'pro'::text, 'enterprise'::text]))),
    CONSTRAINT subscriptions_status_check CHECK ((status = ANY (ARRAY['trialing'::text, 'active'::text, 'paused'::text, 'expired'::text, 'cancelled'::text])))
);


--
-- Name: teachers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.teachers (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    email text NOT NULL,
    full_name text NOT NULL,
    supabase_uid uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    org_id uuid,
    org_role text DEFAULT 'teacher'::text NOT NULL,
    email_verified_at timestamp with time zone,
    totp_secret text,
    totp_enabled_at timestamp with time zone,
    backup_codes_hash jsonb DEFAULT '[]'::jsonb,
    totp_grace_started_at timestamp with time zone DEFAULT now(),
    password_hash text,
    auth_provider text DEFAULT 'supabase'::text NOT NULL,
    password_changed_at timestamp with time zone,
    status text,
    email_2fa_enabled_at timestamp with time zone,
    CONSTRAINT teachers_org_role_check CHECK (((org_role IS NULL) OR (org_role = ANY (ARRAY['admin'::text, 'teacher'::text])))),
    CONSTRAINT teachers_status_check CHECK (((status IS NULL) OR (status = ANY (ARRAY['active'::text, 'pending_verification'::text, 'suspended'::text, 'deleted'::text]))))
);


--
-- Name: usage_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.usage_records (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    org_id uuid NOT NULL,
    period_start timestamp with time zone NOT NULL,
    period_end timestamp with time zone NOT NULL,
    exam_attempts integer DEFAULT 0 NOT NULL,
    students_used integer DEFAULT 0 NOT NULL,
    plan_limit integer DEFAULT 30 NOT NULL,
    overage integer DEFAULT 0 NOT NULL,
    overage_amount numeric(10,2) DEFAULT 0.00 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: violations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.violations (
    id bigint NOT NULL,
    session_key text NOT NULL,
    violation_type text NOT NULL,
    severity text NOT NULL,
    details text,
    created_at timestamp with time zone DEFAULT now(),
    teacher_id uuid,
    detection_confidence numeric(4,3),
    dismissed_at timestamp with time zone,
    dismissed_reason text
);


--
-- Name: violations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.violations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: violations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.violations_id_seq OWNED BY public.violations.id;


--
-- Name: answers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.answers ALTER COLUMN id SET DEFAULT nextval('public.answers_id_seq'::regclass);


--
-- Name: questions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.questions ALTER COLUMN id SET DEFAULT nextval('public.questions_id_seq'::regclass);


--
-- Name: students id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.students ALTER COLUMN id SET DEFAULT nextval('public.students_id_seq'::regclass);


--
-- Name: violations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.violations ALTER COLUMN id SET DEFAULT nextval('public.violations_id_seq'::regclass);


--
-- Name: admin_audit_log admin_audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_audit_log
    ADD CONSTRAINT admin_audit_log_pkey PRIMARY KEY (id);


--
-- Name: answers answers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.answers
    ADD CONSTRAINT answers_pkey PRIMARY KEY (id);


--
-- Name: answers answers_session_key_question_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.answers
    ADD CONSTRAINT answers_session_key_question_id_key UNIQUE (session_key, question_id);


--
-- Name: api_keys api_keys_key_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_key_hash_key UNIQUE (key_hash);


--
-- Name: api_keys api_keys_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_pkey PRIMARY KEY (id);


--
-- Name: appeals appeals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.appeals
    ADD CONSTRAINT appeals_pkey PRIMARY KEY (id);


--
-- Name: auth_events auth_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auth_events
    ADD CONSTRAINT auth_events_pkey PRIMARY KEY (id);


--
-- Name: auth_sessions auth_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auth_sessions
    ADD CONSTRAINT auth_sessions_pkey PRIMARY KEY (jti);


--
-- Name: billing_events billing_events_event_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.billing_events
    ADD CONSTRAINT billing_events_event_id_key UNIQUE (event_id);


--
-- Name: billing_events billing_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.billing_events
    ADD CONSTRAINT billing_events_pkey PRIMARY KEY (id);


--
-- Name: consent_records consent_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.consent_records
    ADD CONSTRAINT consent_records_pkey PRIMARY KEY (id);


--
-- Name: demo_requests demo_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.demo_requests
    ADD CONSTRAINT demo_requests_pkey PRIMARY KEY (id);


--
-- Name: email_otps email_otps_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.email_otps
    ADD CONSTRAINT email_otps_pkey PRIMARY KEY (id);


--
-- Name: exam_config exam_config_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exam_config
    ADD CONSTRAINT exam_config_pkey PRIMARY KEY (id);


--
-- Name: exam_config exam_config_teacher_exam_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exam_config
    ADD CONSTRAINT exam_config_teacher_exam_unique UNIQUE (teacher_id, exam_id);


--
-- Name: exam_group_assignments exam_group_assignments_exam_id_group_id_teacher_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exam_group_assignments
    ADD CONSTRAINT exam_group_assignments_exam_id_group_id_teacher_id_key UNIQUE (exam_id, group_id, teacher_id);


--
-- Name: exam_group_assignments exam_group_assignments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exam_group_assignments
    ADD CONSTRAINT exam_group_assignments_pkey PRIMARY KEY (id);


--
-- Name: exam_sessions exam_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exam_sessions
    ADD CONSTRAINT exam_sessions_pkey PRIMARY KEY (session_key);


--
-- Name: exam_sessions exam_sessions_result_has_submitted_at; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.exam_sessions
    ADD CONSTRAINT exam_sessions_result_has_submitted_at CHECK (((status <> ALL (ARRAY['completed'::text, 'force_submitted'::text])) OR (submitted_at IS NOT NULL))) NOT VALID;


--
-- Name: exam_sessions exam_sessions_status_known; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.exam_sessions
    ADD CONSTRAINT exam_sessions_status_known CHECK ((status = ANY (ARRAY['in_progress'::text, 'paused'::text, 'completed'::text, 'submitted'::text, 'force_submitted'::text, 'abandoned'::text, 'rejected'::text]))) NOT VALID;


--
-- Name: exam_templates exam_templates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exam_templates
    ADD CONSTRAINT exam_templates_pkey PRIMARY KEY (id);


--
-- Name: exam_templates exam_templates_teacher_name_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exam_templates
    ADD CONSTRAINT exam_templates_teacher_name_unique UNIQUE (teacher_id, template_name);


--
-- Name: google_auth_tokens google_auth_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.google_auth_tokens
    ADD CONSTRAINT google_auth_tokens_pkey PRIMARY KEY (id);


--
-- Name: google_auth_tokens google_auth_tokens_teacher_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.google_auth_tokens
    ADD CONSTRAINT google_auth_tokens_teacher_id_key UNIQUE (teacher_id);


--
-- Name: google_classroom_links google_classroom_links_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.google_classroom_links
    ADD CONSTRAINT google_classroom_links_pkey PRIMARY KEY (id);


--
-- Name: google_oauth_states google_oauth_states_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.google_oauth_states
    ADD CONSTRAINT google_oauth_states_pkey PRIMARY KEY (state);


--
-- Name: grading_audit grading_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.grading_audit
    ADD CONSTRAINT grading_audit_pkey PRIMARY KEY (id);


--
-- Name: invite_send_counters invite_send_counters_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invite_send_counters
    ADD CONSTRAINT invite_send_counters_pkey PRIMARY KEY (teacher_id, day);


--
-- Name: invite_send_counters invite_send_counters_teacher_day_uniq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invite_send_counters
    ADD CONSTRAINT invite_send_counters_teacher_day_uniq UNIQUE (teacher_id, day);


--
-- Name: issues issues_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.issues
    ADD CONSTRAINT issues_pkey PRIMARY KEY (id);


--
-- Name: org_invites org_invites_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.org_invites
    ADD CONSTRAINT org_invites_pkey PRIMARY KEY (id);


--
-- Name: organizations organizations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.organizations
    ADD CONSTRAINT organizations_pkey PRIMARY KEY (id);


--
-- Name: organizations organizations_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.organizations
    ADD CONSTRAINT organizations_slug_key UNIQUE (slug);


--
-- Name: question_bank question_bank_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.question_bank
    ADD CONSTRAINT question_bank_pkey PRIMARY KEY (id);


--
-- Name: questions questions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.questions
    ADD CONSTRAINT questions_pkey PRIMARY KEY (id);


--
-- Name: questions questions_teacher_exam_question_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.questions
    ADD CONSTRAINT questions_teacher_exam_question_unique UNIQUE (teacher_id, exam_id, question_id);


--
-- Name: refresh_tokens refresh_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.refresh_tokens
    ADD CONSTRAINT refresh_tokens_pkey PRIMARY KEY (jti);


--
-- Name: schema_migrations schema_migrations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (filename);


--
-- Name: student_accounts student_accounts_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.student_accounts
    ADD CONSTRAINT student_accounts_email_key UNIQUE (email);


--
-- Name: student_accounts student_accounts_email_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.student_accounts
    ADD CONSTRAINT student_accounts_email_unique UNIQUE (email);


--
-- Name: student_accounts student_accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.student_accounts
    ADD CONSTRAINT student_accounts_pkey PRIMARY KEY (id);


--
-- Name: student_accounts student_accounts_supabase_uid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.student_accounts
    ADD CONSTRAINT student_accounts_supabase_uid_key UNIQUE (supabase_uid);


--
-- Name: student_accounts student_accounts_supabase_uid_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.student_accounts
    ADD CONSTRAINT student_accounts_supabase_uid_unique UNIQUE (supabase_uid);


--
-- Name: student_group_members student_group_members_group_id_roll_number_teacher_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.student_group_members
    ADD CONSTRAINT student_group_members_group_id_roll_number_teacher_id_key UNIQUE (group_id, roll_number, teacher_id);


--
-- Name: student_group_members student_group_members_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.student_group_members
    ADD CONSTRAINT student_group_members_pkey PRIMARY KEY (id);


--
-- Name: student_groups student_groups_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.student_groups
    ADD CONSTRAINT student_groups_pkey PRIMARY KEY (id);


--
-- Name: student_groups student_groups_teacher_id_group_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.student_groups
    ADD CONSTRAINT student_groups_teacher_id_group_name_key UNIQUE (teacher_id, group_name);


--
-- Name: student_invites student_invites_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.student_invites
    ADD CONSTRAINT student_invites_pkey PRIMARY KEY (id);


--
-- Name: student_invites student_invites_token_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.student_invites
    ADD CONSTRAINT student_invites_token_key UNIQUE (token);


--
-- Name: students students_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.students
    ADD CONSTRAINT students_pkey PRIMARY KEY (id);


--
-- Name: students students_roll_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.students
    ADD CONSTRAINT students_roll_number_key UNIQUE (roll_number);


--
-- Name: students students_roll_teacher_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.students
    ADD CONSTRAINT students_roll_teacher_unique UNIQUE (roll_number, teacher_id);


--
-- Name: subscriptions subscriptions_org_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscriptions
    ADD CONSTRAINT subscriptions_org_id_key UNIQUE (org_id);


--
-- Name: subscriptions subscriptions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscriptions
    ADD CONSTRAINT subscriptions_pkey PRIMARY KEY (id);


--
-- Name: subscriptions subscriptions_status_chk; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.subscriptions
    ADD CONSTRAINT subscriptions_status_chk CHECK ((status = ANY (ARRAY['trialing'::text, 'created'::text, 'authenticated'::text, 'active'::text, 'past_due'::text, 'halted'::text, 'cancelling'::text, 'cancelled'::text, 'completed'::text, 'expired'::text, 'paused'::text]))) NOT VALID;


--
-- Name: teachers teachers_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.teachers
    ADD CONSTRAINT teachers_email_key UNIQUE (email);


--
-- Name: teachers teachers_email_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.teachers
    ADD CONSTRAINT teachers_email_unique UNIQUE (email);


--
-- Name: teachers teachers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.teachers
    ADD CONSTRAINT teachers_pkey PRIMARY KEY (id);


--
-- Name: teachers teachers_supabase_uid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.teachers
    ADD CONSTRAINT teachers_supabase_uid_key UNIQUE (supabase_uid);


--
-- Name: teachers teachers_supabase_uid_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.teachers
    ADD CONSTRAINT teachers_supabase_uid_unique UNIQUE (supabase_uid);


--
-- Name: exam_config uq_exam_config_exam_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exam_config
    ADD CONSTRAINT uq_exam_config_exam_id UNIQUE (exam_id);


--
-- Name: google_classroom_links uq_teacher_course; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.google_classroom_links
    ADD CONSTRAINT uq_teacher_course UNIQUE (teacher_id, google_course_id);


--
-- Name: api_keys uq_teacher_keyname; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT uq_teacher_keyname UNIQUE (teacher_id, name);


--
-- Name: usage_records usage_records_org_id_period_start_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.usage_records
    ADD CONSTRAINT usage_records_org_id_period_start_key UNIQUE (org_id, period_start);


--
-- Name: usage_records usage_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.usage_records
    ADD CONSTRAINT usage_records_pkey PRIMARY KEY (id);


--
-- Name: violations violations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.violations
    ADD CONSTRAINT violations_pkey PRIMARY KEY (id);


--
-- Name: idx_admin_audit_log_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_admin_audit_log_action ON public.admin_audit_log USING btree (action, created_at DESC);


--
-- Name: idx_admin_audit_log_target; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_admin_audit_log_target ON public.admin_audit_log USING btree (target_type, target_id, created_at DESC);


--
-- Name: idx_admin_audit_log_teacher; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_admin_audit_log_teacher ON public.admin_audit_log USING btree (teacher_id, created_at DESC);


--
-- Name: idx_answers_pending_grade; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_answers_pending_grade ON public.answers USING btree (teacher_id, exam_id) WHERE ((teacher_score IS NULL) AND (ai_score IS NOT NULL));


--
-- Name: idx_answers_question_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_answers_question_id ON public.answers USING btree (question_id);


--
-- Name: idx_answers_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_answers_session ON public.answers USING btree (session_key);


--
-- Name: idx_answers_session_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_answers_session_key ON public.answers USING btree (session_key);


--
-- Name: idx_answers_session_teacher_question; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_answers_session_teacher_question ON public.answers USING btree (session_key, teacher_id, question_id);


--
-- Name: idx_answers_teacher; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_answers_teacher ON public.answers USING btree (teacher_id);


--
-- Name: idx_answers_teacher_exam; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_answers_teacher_exam ON public.answers USING btree (teacher_id, exam_id) WHERE (teacher_score IS NULL);


--
-- Name: idx_answers_teacher_exam_pending_question; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_answers_teacher_exam_pending_question ON public.answers USING btree (teacher_id, exam_id, question_id) WHERE (teacher_score IS NULL);


--
-- Name: idx_api_keys_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_api_keys_hash ON public.api_keys USING btree (key_hash);


--
-- Name: idx_api_keys_teacher; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_api_keys_teacher ON public.api_keys USING btree (teacher_id);


--
-- Name: idx_appeals_session_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_appeals_session_key ON public.appeals USING btree (session_key);


--
-- Name: idx_appeals_student; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_appeals_student ON public.appeals USING btree (student_id, created_at DESC);


--
-- Name: idx_appeals_teacher; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_appeals_teacher ON public.appeals USING btree (teacher_id, status, created_at DESC);


--
-- Name: idx_appeals_violation; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_appeals_violation ON public.appeals USING btree (violation_id) WHERE (violation_id IS NOT NULL);


--
-- Name: idx_auth_events_failed; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_auth_events_failed ON public.auth_events USING btree (email, created_at DESC) WHERE (event_type = 'login_failed'::text);


--
-- Name: idx_auth_events_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_auth_events_user ON public.auth_events USING btree (user_kind, user_id, created_at DESC);


--
-- Name: idx_auth_sessions_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_auth_sessions_user ON public.auth_sessions USING btree (user_kind, user_id, revoked_at, last_seen_at DESC);


--
-- Name: idx_billing_events_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_billing_events_created ON public.billing_events USING btree (created_at DESC);


--
-- Name: idx_billing_events_org; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_billing_events_org ON public.billing_events USING btree (org_id);


--
-- Name: idx_billing_events_sub; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_billing_events_sub ON public.billing_events USING btree (razorpay_subscription_id);


--
-- Name: idx_consent_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_consent_user ON public.consent_records USING btree (user_id, created_at DESC);


--
-- Name: idx_email_otps_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_email_otps_expires ON public.email_otps USING btree (expires_at) WHERE (used_at IS NULL);


--
-- Name: idx_email_otps_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_email_otps_lookup ON public.email_otps USING btree (user_kind, user_id, purpose, created_at DESC);


--
-- Name: idx_exam_config_teacher; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_exam_config_teacher ON public.exam_config USING btree (teacher_id);


--
-- Name: idx_exam_config_teacher_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_exam_config_teacher_id ON public.exam_config USING btree (teacher_id);


--
-- Name: idx_exam_group_assignments_group_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_exam_group_assignments_group_id ON public.exam_group_assignments USING btree (group_id);


--
-- Name: idx_exam_group_assignments_teacher_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_exam_group_assignments_teacher_id ON public.exam_group_assignments USING btree (teacher_id);


--
-- Name: idx_exam_sessions_currently_paused; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_exam_sessions_currently_paused ON public.exam_sessions USING btree (teacher_id) WHERE (paused_at IS NOT NULL);


--
-- Name: idx_exam_sessions_exam_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_exam_sessions_exam_id ON public.exam_sessions USING btree (exam_id);


--
-- Name: idx_exam_sessions_roll_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_exam_sessions_roll_number ON public.exam_sessions USING btree (roll_number);


--
-- Name: idx_exam_sessions_session_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_exam_sessions_session_key ON public.exam_sessions USING btree (session_key);


--
-- Name: idx_exam_sessions_student_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_exam_sessions_student_id ON public.exam_sessions USING btree (student_id);


--
-- Name: idx_exam_sessions_teacher; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_exam_sessions_teacher ON public.exam_sessions USING btree (teacher_id);


--
-- Name: idx_exam_sessions_teacher_exam_roll_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_exam_sessions_teacher_exam_roll_status ON public.exam_sessions USING btree (teacher_id, exam_id, roll_number, status);


--
-- Name: idx_exam_sessions_teacher_exam_status_submitted; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_exam_sessions_teacher_exam_status_submitted ON public.exam_sessions USING btree (teacher_id, exam_id, status, submitted_at DESC);


--
-- Name: idx_exam_sessions_teacher_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_exam_sessions_teacher_id ON public.exam_sessions USING btree (teacher_id);


--
-- Name: idx_exam_sessions_teacher_roll_status_submitted; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_exam_sessions_teacher_roll_status_submitted ON public.exam_sessions USING btree (teacher_id, roll_number, status, submitted_at DESC);


--
-- Name: idx_exam_sessions_teacher_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_exam_sessions_teacher_status ON public.exam_sessions USING btree (teacher_id, status);


--
-- Name: idx_exam_sessions_teacher_status_submitted; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_exam_sessions_teacher_status_submitted ON public.exam_sessions USING btree (teacher_id, status, submitted_at DESC);


--
-- Name: idx_exam_templates_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_exam_templates_created_at ON public.exam_templates USING btree (created_at DESC);


--
-- Name: idx_exam_templates_teacher_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_exam_templates_teacher_id ON public.exam_templates USING btree (teacher_id);


--
-- Name: idx_google_oauth_states_teacher_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_google_oauth_states_teacher_id ON public.google_oauth_states USING btree (teacher_id);


--
-- Name: idx_grading_audit_exam; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_grading_audit_exam ON public.grading_audit USING btree (exam_id, created_at DESC);


--
-- Name: idx_grading_audit_session_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_grading_audit_session_key ON public.grading_audit USING btree (session_key);


--
-- Name: idx_grading_audit_teacher; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_grading_audit_teacher ON public.grading_audit USING btree (teacher_id, created_at DESC);


--
-- Name: idx_isc_day; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_isc_day ON public.invite_send_counters USING btree (day);


--
-- Name: idx_issues_org; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_issues_org ON public.issues USING btree (org_id, created_at DESC);


--
-- Name: idx_issues_status_partial; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_issues_status_partial ON public.issues USING btree (status) WHERE (status <> 'resolved'::text);


--
-- Name: idx_issues_teacher; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_issues_teacher ON public.issues USING btree (teacher_id, created_at DESC);


--
-- Name: idx_org_invites_email; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_org_invites_email ON public.org_invites USING btree (email);


--
-- Name: idx_org_invites_invited_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_org_invites_invited_by ON public.org_invites USING btree (invited_by);


--
-- Name: idx_org_invites_org_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_org_invites_org_id ON public.org_invites USING btree (org_id);


--
-- Name: idx_org_invites_token_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_org_invites_token_hash ON public.org_invites USING btree (token_hash) WHERE (status = 'pending'::text);


--
-- Name: idx_qbank_teacher; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_qbank_teacher ON public.question_bank USING btree (teacher_id);


--
-- Name: idx_questions_exam_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_questions_exam_id ON public.questions USING btree (exam_id);


--
-- Name: idx_questions_order; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_questions_order ON public.questions USING btree (question_id);


--
-- Name: idx_questions_teacher; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_questions_teacher ON public.questions USING btree (teacher_id);


--
-- Name: idx_refresh_tokens_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_refresh_tokens_expires ON public.refresh_tokens USING btree (expires_at) WHERE (revoked_at IS NULL);


--
-- Name: idx_refresh_tokens_user_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_refresh_tokens_user_active ON public.refresh_tokens USING btree (user_id, kind, revoked_at);


--
-- Name: idx_sess_scorecard_unemailed; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sess_scorecard_unemailed ON public.exam_sessions USING btree (exam_id, teacher_id) WHERE ((scorecard_emailed_at IS NULL) AND (status = 'completed'::text));


--
-- Name: idx_sessions_roll; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sessions_roll ON public.exam_sessions USING btree (roll_number);


--
-- Name: idx_sessions_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sessions_status ON public.exam_sessions USING btree (status);


--
-- Name: idx_si_clicked; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_si_clicked ON public.student_invites USING btree (exam_id, teacher_id) WHERE (clicked_at IS NOT NULL);


--
-- Name: idx_si_email_teacher; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_si_email_teacher ON public.student_invites USING btree (email, teacher_id);


--
-- Name: idx_si_exam; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_si_exam ON public.student_invites USING btree (exam_id, teacher_id);


--
-- Name: idx_si_reminder_1h_null; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_si_reminder_1h_null ON public.student_invites USING btree (exam_id) WHERE (reminder_1h_at IS NULL);


--
-- Name: idx_si_reminder_24h_null; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_si_reminder_24h_null ON public.student_invites USING btree (exam_id) WHERE (reminder_24h_at IS NULL);


--
-- Name: idx_si_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_si_status ON public.student_invites USING btree (status);


--
-- Name: idx_si_teacher; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_si_teacher ON public.student_invites USING btree (teacher_id);


--
-- Name: idx_si_token; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_si_token ON public.student_invites USING btree (token);


--
-- Name: idx_student_accounts_auth_provider; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_student_accounts_auth_provider ON public.student_accounts USING btree (auth_provider);


--
-- Name: idx_student_accounts_email; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_student_accounts_email ON public.student_accounts USING btree (email);


--
-- Name: idx_student_group_members_teacher_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_student_group_members_teacher_id ON public.student_group_members USING btree (teacher_id);


--
-- Name: idx_student_invites_access_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_student_invites_access_code ON public.student_invites USING btree (teacher_id, exam_id, access_code);


--
-- Name: idx_student_invites_group_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_student_invites_group_id ON public.student_invites USING btree (group_id);


--
-- Name: idx_student_invites_roll_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_student_invites_roll_number ON public.student_invites USING btree (roll_number);


--
-- Name: idx_student_invites_teacher_exam; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_student_invites_teacher_exam ON public.student_invites USING btree (teacher_id, exam_id);


--
-- Name: idx_student_invites_token; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_student_invites_token ON public.student_invites USING btree (token);


--
-- Name: idx_students_account_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_students_account_id ON public.students USING btree (account_id);


--
-- Name: idx_students_email; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_students_email ON public.students USING btree (email);


--
-- Name: idx_students_org_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_students_org_id ON public.students USING btree (org_id);


--
-- Name: idx_students_roll_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_students_roll_number ON public.students USING btree (roll_number);


--
-- Name: idx_students_teacher; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_students_teacher ON public.students USING btree (teacher_id);


--
-- Name: idx_subscriptions_org_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_subscriptions_org_id ON public.subscriptions USING btree (org_id);


--
-- Name: idx_teachers_auth_provider; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_teachers_auth_provider ON public.teachers USING btree (auth_provider);


--
-- Name: idx_teachers_email; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_teachers_email ON public.teachers USING btree (email);


--
-- Name: idx_teachers_org_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_teachers_org_id ON public.teachers USING btree (org_id);


--
-- Name: idx_teachers_supabase_uid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_teachers_supabase_uid ON public.teachers USING btree (supabase_uid);


--
-- Name: idx_usage_org_period; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_usage_org_period ON public.usage_records USING btree (org_id, period_start DESC);


--
-- Name: idx_violations_active_clusters; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_violations_active_clusters ON public.violations USING btree (teacher_id, violation_type) WHERE (dismissed_at IS NULL);


--
-- Name: idx_violations_confidence; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_violations_confidence ON public.violations USING btree (detection_confidence);


--
-- Name: idx_violations_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_violations_created ON public.violations USING btree (created_at);


--
-- Name: idx_violations_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_violations_created_at ON public.violations USING btree (created_at DESC);


--
-- Name: idx_violations_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_violations_session ON public.violations USING btree (session_key);


--
-- Name: idx_violations_session_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_violations_session_key ON public.violations USING btree (session_key);


--
-- Name: idx_violations_session_teacher_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_violations_session_teacher_created ON public.violations USING btree (session_key, teacher_id, created_at);


--
-- Name: idx_violations_teacher; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_violations_teacher ON public.violations USING btree (teacher_id);


--
-- Name: idx_violations_teacher_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_violations_teacher_id ON public.violations USING btree (teacher_id);


--
-- Name: idx_violations_teacher_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_violations_teacher_session ON public.violations USING btree (teacher_id, session_key);


--
-- Name: idx_violations_teacher_type_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_violations_teacher_type_created ON public.violations USING btree (teacher_id, violation_type, created_at DESC);


--
-- Name: uq_appeals_session_student_type_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_appeals_session_student_type_pending ON public.appeals USING btree (session_key, student_id, appeal_type) WHERE (status = 'pending'::text);


--
-- Name: uq_exam_sessions_teacher_exam_roll; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_exam_sessions_teacher_exam_roll ON public.exam_sessions USING btree (teacher_id, exam_id, roll_number) WHERE ((teacher_id IS NOT NULL) AND (exam_id IS NOT NULL) AND (roll_number IS NOT NULL) AND (roll_number <> ''::text));


--
-- Name: uq_org_invites_org_email_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_org_invites_org_email_pending ON public.org_invites USING btree (org_id, lower(email)) WHERE (status = 'pending'::text);


--
-- Name: uq_student_accounts_email_lower; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_student_accounts_email_lower ON public.student_accounts USING btree (lower(email)) WHERE (email IS NOT NULL);


--
-- Name: uq_student_invites_teacher_email_exam; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_student_invites_teacher_email_exam ON public.student_invites USING btree (teacher_id, lower(email), exam_id) WHERE ((email IS NOT NULL) AND (email <> ''::text) AND (exam_id IS NOT NULL));


--
-- Name: uq_teachers_email_lower; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_teachers_email_lower ON public.teachers USING btree (lower(email)) WHERE (email IS NOT NULL);


--
-- Name: students enforce_org_student_quota; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER enforce_org_student_quota BEFORE INSERT ON public.students FOR EACH ROW EXECUTE FUNCTION public.enforce_org_student_quota();


--
-- Name: admin_audit_log admin_audit_log_teacher_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_audit_log
    ADD CONSTRAINT admin_audit_log_teacher_id_fkey FOREIGN KEY (teacher_id) REFERENCES public.teachers(id) ON DELETE SET NULL;


--
-- Name: answers answers_session_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.answers
    ADD CONSTRAINT answers_session_fk FOREIGN KEY (session_key) REFERENCES public.exam_sessions(session_key) ON DELETE CASCADE;


--
-- Name: answers answers_teacher_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.answers
    ADD CONSTRAINT answers_teacher_fk FOREIGN KEY (teacher_id) REFERENCES public.teachers(id) ON DELETE RESTRICT;


--
-- Name: answers answers_teacher_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.answers
    ADD CONSTRAINT answers_teacher_id_fkey FOREIGN KEY (teacher_id) REFERENCES public.teachers(id);


--
-- Name: api_keys api_keys_teacher_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_teacher_id_fkey FOREIGN KEY (teacher_id) REFERENCES public.teachers(id) ON DELETE CASCADE;


--
-- Name: appeals appeals_session_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.appeals
    ADD CONSTRAINT appeals_session_fk FOREIGN KEY (session_key) REFERENCES public.exam_sessions(session_key) ON DELETE CASCADE;


--
-- Name: appeals appeals_student_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.appeals
    ADD CONSTRAINT appeals_student_fk FOREIGN KEY (student_id) REFERENCES public.student_accounts(id) ON DELETE SET NULL;


--
-- Name: appeals appeals_teacher_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.appeals
    ADD CONSTRAINT appeals_teacher_fk FOREIGN KEY (teacher_id) REFERENCES public.teachers(id) ON DELETE RESTRICT;


--
-- Name: billing_events billing_events_org_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.billing_events
    ADD CONSTRAINT billing_events_org_id_fkey FOREIGN KEY (org_id) REFERENCES public.organizations(id);


--
-- Name: consent_records consent_records_user_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.consent_records
    ADD CONSTRAINT consent_records_user_fk FOREIGN KEY (user_id) REFERENCES public.student_accounts(id) ON DELETE CASCADE;


--
-- Name: exam_config exam_config_teacher_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exam_config
    ADD CONSTRAINT exam_config_teacher_fk FOREIGN KEY (teacher_id) REFERENCES public.teachers(id) ON DELETE RESTRICT;


--
-- Name: exam_config exam_config_teacher_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exam_config
    ADD CONSTRAINT exam_config_teacher_id_fkey FOREIGN KEY (teacher_id) REFERENCES public.teachers(id);


--
-- Name: exam_group_assignments exam_group_assignments_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exam_group_assignments
    ADD CONSTRAINT exam_group_assignments_group_id_fkey FOREIGN KEY (group_id) REFERENCES public.student_groups(id) ON DELETE CASCADE;


--
-- Name: exam_group_assignments exam_group_assignments_teacher_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exam_group_assignments
    ADD CONSTRAINT exam_group_assignments_teacher_fk FOREIGN KEY (teacher_id) REFERENCES public.teachers(id) ON DELETE CASCADE;


--
-- Name: exam_sessions exam_sessions_teacher_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exam_sessions
    ADD CONSTRAINT exam_sessions_teacher_fk FOREIGN KEY (teacher_id) REFERENCES public.teachers(id) ON DELETE RESTRICT;


--
-- Name: exam_sessions exam_sessions_teacher_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exam_sessions
    ADD CONSTRAINT exam_sessions_teacher_id_fkey FOREIGN KEY (teacher_id) REFERENCES public.teachers(id);


--
-- Name: exam_templates exam_templates_teacher_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exam_templates
    ADD CONSTRAINT exam_templates_teacher_fk FOREIGN KEY (teacher_id) REFERENCES public.teachers(id) ON DELETE CASCADE;


--
-- Name: google_auth_tokens google_auth_tokens_teacher_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.google_auth_tokens
    ADD CONSTRAINT google_auth_tokens_teacher_id_fkey FOREIGN KEY (teacher_id) REFERENCES public.teachers(id) ON DELETE CASCADE;


--
-- Name: google_classroom_links google_classroom_links_teacher_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.google_classroom_links
    ADD CONSTRAINT google_classroom_links_teacher_fk FOREIGN KEY (teacher_id) REFERENCES public.teachers(id) ON DELETE CASCADE;


--
-- Name: google_classroom_links google_classroom_links_teacher_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.google_classroom_links
    ADD CONSTRAINT google_classroom_links_teacher_id_fkey FOREIGN KEY (teacher_id) REFERENCES public.teachers(id) ON DELETE CASCADE;


--
-- Name: google_oauth_states google_oauth_states_teacher_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.google_oauth_states
    ADD CONSTRAINT google_oauth_states_teacher_fk FOREIGN KEY (teacher_id) REFERENCES public.teachers(id) ON DELETE CASCADE;


--
-- Name: grading_audit grading_audit_session_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.grading_audit
    ADD CONSTRAINT grading_audit_session_fk FOREIGN KEY (session_key) REFERENCES public.exam_sessions(session_key) ON DELETE CASCADE;


--
-- Name: grading_audit grading_audit_teacher_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.grading_audit
    ADD CONSTRAINT grading_audit_teacher_fk FOREIGN KEY (teacher_id) REFERENCES public.teachers(id) ON DELETE RESTRICT;


--
-- Name: invite_send_counters invite_send_counters_teacher_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invite_send_counters
    ADD CONSTRAINT invite_send_counters_teacher_fk FOREIGN KEY (teacher_id) REFERENCES public.teachers(id) ON DELETE CASCADE;


--
-- Name: issues issues_org_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.issues
    ADD CONSTRAINT issues_org_id_fkey FOREIGN KEY (org_id) REFERENCES public.organizations(id);


--
-- Name: issues issues_teacher_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.issues
    ADD CONSTRAINT issues_teacher_fk FOREIGN KEY (teacher_id) REFERENCES public.teachers(id) ON DELETE SET NULL;


--
-- Name: issues issues_teacher_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.issues
    ADD CONSTRAINT issues_teacher_id_fkey FOREIGN KEY (teacher_id) REFERENCES public.teachers(id);


--
-- Name: org_invites org_invites_invited_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.org_invites
    ADD CONSTRAINT org_invites_invited_by_fkey FOREIGN KEY (invited_by) REFERENCES public.teachers(id);


--
-- Name: org_invites org_invites_org_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.org_invites
    ADD CONSTRAINT org_invites_org_id_fkey FOREIGN KEY (org_id) REFERENCES public.organizations(id);


--
-- Name: question_bank question_bank_teacher_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.question_bank
    ADD CONSTRAINT question_bank_teacher_fk FOREIGN KEY (teacher_id) REFERENCES public.teachers(id) ON DELETE CASCADE;


--
-- Name: questions questions_teacher_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.questions
    ADD CONSTRAINT questions_teacher_fk FOREIGN KEY (teacher_id) REFERENCES public.teachers(id) ON DELETE RESTRICT;


--
-- Name: questions questions_teacher_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.questions
    ADD CONSTRAINT questions_teacher_id_fkey FOREIGN KEY (teacher_id) REFERENCES public.teachers(id);


--
-- Name: student_group_members student_group_members_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.student_group_members
    ADD CONSTRAINT student_group_members_group_id_fkey FOREIGN KEY (group_id) REFERENCES public.student_groups(id) ON DELETE CASCADE;


--
-- Name: student_group_members student_group_members_teacher_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.student_group_members
    ADD CONSTRAINT student_group_members_teacher_fk FOREIGN KEY (teacher_id) REFERENCES public.teachers(id) ON DELETE CASCADE;


--
-- Name: student_groups student_groups_teacher_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.student_groups
    ADD CONSTRAINT student_groups_teacher_fk FOREIGN KEY (teacher_id) REFERENCES public.teachers(id) ON DELETE CASCADE;


--
-- Name: student_invites student_invites_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.student_invites
    ADD CONSTRAINT student_invites_group_id_fkey FOREIGN KEY (group_id) REFERENCES public.student_groups(id) ON DELETE SET NULL;


--
-- Name: student_invites student_invites_teacher_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.student_invites
    ADD CONSTRAINT student_invites_teacher_fk FOREIGN KEY (teacher_id) REFERENCES public.teachers(id) ON DELETE RESTRICT;


--
-- Name: students students_account_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.students
    ADD CONSTRAINT students_account_fk FOREIGN KEY (account_id) REFERENCES public.student_accounts(id) ON DELETE SET NULL;


--
-- Name: students students_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.students
    ADD CONSTRAINT students_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.student_accounts(id);


--
-- Name: students students_org_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.students
    ADD CONSTRAINT students_org_id_fkey FOREIGN KEY (org_id) REFERENCES public.organizations(id);


--
-- Name: students students_teacher_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.students
    ADD CONSTRAINT students_teacher_fk FOREIGN KEY (teacher_id) REFERENCES public.teachers(id) ON DELETE RESTRICT;


--
-- Name: students students_teacher_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.students
    ADD CONSTRAINT students_teacher_id_fkey FOREIGN KEY (teacher_id) REFERENCES public.teachers(id);


--
-- Name: subscriptions subscriptions_org_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscriptions
    ADD CONSTRAINT subscriptions_org_id_fkey FOREIGN KEY (org_id) REFERENCES public.organizations(id);


--
-- Name: teachers teachers_org_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.teachers
    ADD CONSTRAINT teachers_org_fk FOREIGN KEY (org_id) REFERENCES public.organizations(id) ON DELETE SET NULL;


--
-- Name: teachers teachers_org_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.teachers
    ADD CONSTRAINT teachers_org_id_fkey FOREIGN KEY (org_id) REFERENCES public.organizations(id);


--
-- Name: usage_records usage_records_org_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.usage_records
    ADD CONSTRAINT usage_records_org_fk FOREIGN KEY (org_id) REFERENCES public.organizations(id) ON DELETE CASCADE;


--
-- Name: violations violations_session_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.violations
    ADD CONSTRAINT violations_session_fk FOREIGN KEY (session_key) REFERENCES public.exam_sessions(session_key) ON DELETE CASCADE;


--
-- Name: violations violations_teacher_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.violations
    ADD CONSTRAINT violations_teacher_fk FOREIGN KEY (teacher_id) REFERENCES public.teachers(id) ON DELETE RESTRICT;


--
-- Name: violations violations_teacher_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.violations
    ADD CONSTRAINT violations_teacher_id_fkey FOREIGN KEY (teacher_id) REFERENCES public.teachers(id);


--
-- Name: admin_audit_log; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.admin_audit_log ENABLE ROW LEVEL SECURITY;

--
-- Name: admin_audit_log admin_audit_log_teacher_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY admin_audit_log_teacher_select ON public.admin_audit_log FOR SELECT USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: answers; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.answers ENABLE ROW LEVEL SECURITY;

--
-- Name: answers answers_student_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY answers_student_insert ON public.answers FOR INSERT WITH CHECK ((session_key ~~ (( SELECT students.roll_number
   FROM public.students
  WHERE ((students.account_id)::text = public.get_my_student_account_id())
 LIMIT 1) || '_%'::text)));


--
-- Name: answers answers_student_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY answers_student_select ON public.answers FOR SELECT USING ((session_key ~~ (( SELECT students.roll_number
   FROM public.students
  WHERE ((students.account_id)::text = public.get_my_student_account_id())
 LIMIT 1) || '_%'::text)));


--
-- Name: answers answers_student_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY answers_student_update ON public.answers FOR UPDATE USING ((session_key ~~ (( SELECT students.roll_number
   FROM public.students
  WHERE ((students.account_id)::text = public.get_my_student_account_id())
 LIMIT 1) || '_%'::text)));


--
-- Name: answers answers_teacher_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY answers_teacher_select ON public.answers FOR SELECT USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: api_keys; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.api_keys ENABLE ROW LEVEL SECURITY;

--
-- Name: api_keys api_keys_teacher_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY api_keys_teacher_delete ON public.api_keys FOR DELETE USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: api_keys api_keys_teacher_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY api_keys_teacher_insert ON public.api_keys FOR INSERT WITH CHECK (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: api_keys api_keys_teacher_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY api_keys_teacher_select ON public.api_keys FOR SELECT USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: api_keys api_keys_teacher_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY api_keys_teacher_update ON public.api_keys FOR UPDATE USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: appeals; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.appeals ENABLE ROW LEVEL SECURITY;

--
-- Name: appeals appeals_student_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY appeals_student_insert ON public.appeals FOR INSERT WITH CHECK (((student_id)::text = public.get_my_student_account_id()));


--
-- Name: appeals appeals_student_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY appeals_student_select ON public.appeals FOR SELECT USING (((student_id)::text = public.get_my_student_account_id()));


--
-- Name: appeals appeals_teacher_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY appeals_teacher_delete ON public.appeals FOR DELETE USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: appeals appeals_teacher_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY appeals_teacher_select ON public.appeals FOR SELECT USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: appeals appeals_teacher_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY appeals_teacher_update ON public.appeals FOR UPDATE USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: auth_events; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.auth_events ENABLE ROW LEVEL SECURITY;

--
-- Name: auth_sessions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.auth_sessions ENABLE ROW LEVEL SECURITY;

--
-- Name: consent_records; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.consent_records ENABLE ROW LEVEL SECURITY;

--
-- Name: consent_records consent_records_student_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY consent_records_student_insert ON public.consent_records FOR INSERT WITH CHECK (((user_type = 'student'::text) AND ((user_id)::text = public.get_my_student_account_id())));


--
-- Name: consent_records consent_records_student_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY consent_records_student_select ON public.consent_records FOR SELECT USING (((user_type = 'student'::text) AND ((user_id)::text = public.get_my_student_account_id())));


--
-- Name: consent_records consent_records_teacher_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY consent_records_teacher_insert ON public.consent_records FOR INSERT WITH CHECK (((user_type = 'teacher'::text) AND ((user_id)::text = public.get_my_teacher_id())));


--
-- Name: consent_records consent_records_teacher_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY consent_records_teacher_select ON public.consent_records FOR SELECT USING (((user_type = 'teacher'::text) AND ((user_id)::text = public.get_my_teacher_id())));


--
-- Name: demo_requests; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.demo_requests ENABLE ROW LEVEL SECURITY;

--
-- Name: demo_requests demo_requests_anon_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY demo_requests_anon_insert ON public.demo_requests FOR INSERT WITH CHECK (true);


--
-- Name: demo_requests demo_requests_teacher_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY demo_requests_teacher_select ON public.demo_requests FOR SELECT USING ((public.get_my_teacher_id() IS NOT NULL));


--
-- Name: exam_group_assignments ega_teacher_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ega_teacher_delete ON public.exam_group_assignments FOR DELETE USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: exam_group_assignments ega_teacher_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ega_teacher_insert ON public.exam_group_assignments FOR INSERT WITH CHECK (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: exam_group_assignments ega_teacher_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ega_teacher_select ON public.exam_group_assignments FOR SELECT USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: email_otps; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.email_otps ENABLE ROW LEVEL SECURITY;

--
-- Name: exam_config; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.exam_config ENABLE ROW LEVEL SECURITY;

--
-- Name: exam_config exam_config_anon_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY exam_config_anon_select ON public.exam_config FOR SELECT USING ((auth.role() = 'anon'::text));


--
-- Name: exam_config exam_config_student_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY exam_config_student_select ON public.exam_config FOR SELECT USING (((teacher_id)::text IN ( SELECT (students.teacher_id)::text AS teacher_id
   FROM public.students
  WHERE ((students.account_id)::text = public.get_my_student_account_id()))));


--
-- Name: exam_config exam_config_teacher_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY exam_config_teacher_delete ON public.exam_config FOR DELETE USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: exam_config exam_config_teacher_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY exam_config_teacher_insert ON public.exam_config FOR INSERT WITH CHECK (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: exam_config exam_config_teacher_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY exam_config_teacher_select ON public.exam_config FOR SELECT USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: exam_config exam_config_teacher_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY exam_config_teacher_update ON public.exam_config FOR UPDATE USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: exam_group_assignments; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.exam_group_assignments ENABLE ROW LEVEL SECURITY;

--
-- Name: exam_sessions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.exam_sessions ENABLE ROW LEVEL SECURITY;

--
-- Name: exam_sessions exam_sessions_student_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY exam_sessions_student_insert ON public.exam_sessions FOR INSERT WITH CHECK ((roll_number IN ( SELECT public.get_my_roll_numbers() AS get_my_roll_numbers)));


--
-- Name: exam_sessions exam_sessions_student_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY exam_sessions_student_select ON public.exam_sessions FOR SELECT USING ((roll_number IN ( SELECT public.get_my_roll_numbers() AS get_my_roll_numbers)));


--
-- Name: exam_sessions exam_sessions_student_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY exam_sessions_student_update ON public.exam_sessions FOR UPDATE USING ((roll_number IN ( SELECT public.get_my_roll_numbers() AS get_my_roll_numbers)));


--
-- Name: exam_sessions exam_sessions_teacher_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY exam_sessions_teacher_delete ON public.exam_sessions FOR DELETE USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: exam_sessions exam_sessions_teacher_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY exam_sessions_teacher_select ON public.exam_sessions FOR SELECT USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: exam_sessions exam_sessions_teacher_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY exam_sessions_teacher_update ON public.exam_sessions FOR UPDATE USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: exam_templates; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.exam_templates ENABLE ROW LEVEL SECURITY;

--
-- Name: exam_templates exam_templates_teacher_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY exam_templates_teacher_delete ON public.exam_templates FOR DELETE USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: exam_templates exam_templates_teacher_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY exam_templates_teacher_insert ON public.exam_templates FOR INSERT WITH CHECK (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: exam_templates exam_templates_teacher_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY exam_templates_teacher_select ON public.exam_templates FOR SELECT USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: exam_templates exam_templates_teacher_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY exam_templates_teacher_update ON public.exam_templates FOR UPDATE USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: google_classroom_links gcl_teacher_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY gcl_teacher_delete ON public.google_classroom_links FOR DELETE USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: google_classroom_links gcl_teacher_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY gcl_teacher_insert ON public.google_classroom_links FOR INSERT WITH CHECK (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: google_classroom_links gcl_teacher_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY gcl_teacher_select ON public.google_classroom_links FOR SELECT USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: google_auth_tokens; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.google_auth_tokens ENABLE ROW LEVEL SECURITY;

--
-- Name: google_auth_tokens google_auth_tokens_teacher_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY google_auth_tokens_teacher_delete ON public.google_auth_tokens FOR DELETE USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: google_auth_tokens google_auth_tokens_teacher_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY google_auth_tokens_teacher_insert ON public.google_auth_tokens FOR INSERT WITH CHECK (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: google_auth_tokens google_auth_tokens_teacher_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY google_auth_tokens_teacher_select ON public.google_auth_tokens FOR SELECT USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: google_auth_tokens google_auth_tokens_teacher_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY google_auth_tokens_teacher_update ON public.google_auth_tokens FOR UPDATE USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: google_classroom_links; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.google_classroom_links ENABLE ROW LEVEL SECURITY;

--
-- Name: google_oauth_states; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.google_oauth_states ENABLE ROW LEVEL SECURITY;

--
-- Name: google_oauth_states google_oauth_states_teacher_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY google_oauth_states_teacher_delete ON public.google_oauth_states FOR DELETE USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: google_oauth_states google_oauth_states_teacher_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY google_oauth_states_teacher_insert ON public.google_oauth_states FOR INSERT WITH CHECK (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: google_oauth_states google_oauth_states_teacher_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY google_oauth_states_teacher_select ON public.google_oauth_states FOR SELECT USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: grading_audit; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.grading_audit ENABLE ROW LEVEL SECURITY;

--
-- Name: grading_audit grading_audit_teacher_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY grading_audit_teacher_select ON public.grading_audit FOR SELECT USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: invite_send_counters; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.invite_send_counters ENABLE ROW LEVEL SECURITY;

--
-- Name: invite_send_counters invite_send_counters_teacher_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY invite_send_counters_teacher_insert ON public.invite_send_counters FOR INSERT WITH CHECK (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: invite_send_counters invite_send_counters_teacher_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY invite_send_counters_teacher_select ON public.invite_send_counters FOR SELECT USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: invite_send_counters invite_send_counters_teacher_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY invite_send_counters_teacher_update ON public.invite_send_counters FOR UPDATE USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: issues; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.issues ENABLE ROW LEVEL SECURITY;

--
-- Name: issues issues_teacher_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY issues_teacher_insert ON public.issues FOR INSERT WITH CHECK (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: issues issues_teacher_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY issues_teacher_select ON public.issues FOR SELECT USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: org_invites; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.org_invites ENABLE ROW LEVEL SECURITY;

--
-- Name: org_invites org_invites_teacher_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY org_invites_teacher_insert ON public.org_invites FOR INSERT WITH CHECK (((org_id)::text IN ( SELECT (teachers.org_id)::text AS org_id
   FROM public.teachers
  WHERE (((teachers.id)::text = public.get_my_teacher_id()) AND (teachers.org_id IS NOT NULL)))));


--
-- Name: org_invites org_invites_teacher_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY org_invites_teacher_select ON public.org_invites FOR SELECT USING (((org_id)::text IN ( SELECT (teachers.org_id)::text AS org_id
   FROM public.teachers
  WHERE (((teachers.id)::text = public.get_my_teacher_id()) AND (teachers.org_id IS NOT NULL)))));


--
-- Name: org_invites org_invites_teacher_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY org_invites_teacher_update ON public.org_invites FOR UPDATE USING (((org_id)::text IN ( SELECT (teachers.org_id)::text AS org_id
   FROM public.teachers
  WHERE (((teachers.id)::text = public.get_my_teacher_id()) AND (teachers.org_id IS NOT NULL)))));


--
-- Name: organizations; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.organizations ENABLE ROW LEVEL SECURITY;

--
-- Name: organizations organizations_teacher_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY organizations_teacher_select ON public.organizations FOR SELECT USING ((((id)::text IN ( SELECT (teachers.org_id)::text AS org_id
   FROM public.teachers
  WHERE ((teachers.supabase_uid)::text = (auth.uid())::text))) OR (auth.role() = 'service_role'::text)));


--
-- Name: organizations organizations_teacher_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY organizations_teacher_update ON public.organizations FOR UPDATE USING (((id)::text IN ( SELECT (teachers.org_id)::text AS org_id
   FROM public.teachers
  WHERE ((teachers.supabase_uid)::text = (auth.uid())::text))));


--
-- Name: question_bank; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.question_bank ENABLE ROW LEVEL SECURITY;

--
-- Name: question_bank question_bank_teacher_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY question_bank_teacher_delete ON public.question_bank FOR DELETE USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: question_bank question_bank_teacher_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY question_bank_teacher_insert ON public.question_bank FOR INSERT WITH CHECK (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: question_bank question_bank_teacher_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY question_bank_teacher_select ON public.question_bank FOR SELECT USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: question_bank question_bank_teacher_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY question_bank_teacher_update ON public.question_bank FOR UPDATE USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: questions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.questions ENABLE ROW LEVEL SECURITY;

--
-- Name: questions questions_student_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY questions_student_select ON public.questions FOR SELECT USING (((teacher_id)::text IN ( SELECT (students.teacher_id)::text AS teacher_id
   FROM public.students
  WHERE ((students.account_id)::text = public.get_my_student_account_id()))));


--
-- Name: questions questions_teacher_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY questions_teacher_delete ON public.questions FOR DELETE USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: questions questions_teacher_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY questions_teacher_insert ON public.questions FOR INSERT WITH CHECK (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: questions questions_teacher_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY questions_teacher_select ON public.questions FOR SELECT USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: questions questions_teacher_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY questions_teacher_update ON public.questions FOR UPDATE USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: student_group_members sgm_teacher_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sgm_teacher_delete ON public.student_group_members FOR DELETE USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: student_group_members sgm_teacher_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sgm_teacher_insert ON public.student_group_members FOR INSERT WITH CHECK (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: student_group_members sgm_teacher_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sgm_teacher_select ON public.student_group_members FOR SELECT USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: student_accounts; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.student_accounts ENABLE ROW LEVEL SECURITY;

--
-- Name: student_accounts student_accounts_insert_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY student_accounts_insert_own ON public.student_accounts FOR INSERT WITH CHECK (((supabase_uid)::text = (auth.uid())::text));


--
-- Name: student_accounts student_accounts_select_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY student_accounts_select_own ON public.student_accounts FOR SELECT USING (((supabase_uid)::text = (auth.uid())::text));


--
-- Name: student_accounts student_accounts_update_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY student_accounts_update_own ON public.student_accounts FOR UPDATE USING (((supabase_uid)::text = (auth.uid())::text));


--
-- Name: student_group_members; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.student_group_members ENABLE ROW LEVEL SECURITY;

--
-- Name: student_groups; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.student_groups ENABLE ROW LEVEL SECURITY;

--
-- Name: student_groups student_groups_teacher_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY student_groups_teacher_delete ON public.student_groups FOR DELETE USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: student_groups student_groups_teacher_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY student_groups_teacher_insert ON public.student_groups FOR INSERT WITH CHECK (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: student_groups student_groups_teacher_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY student_groups_teacher_select ON public.student_groups FOR SELECT USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: student_groups student_groups_teacher_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY student_groups_teacher_update ON public.student_groups FOR UPDATE USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: student_invites; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.student_invites ENABLE ROW LEVEL SECURITY;

--
-- Name: student_invites student_invites_anon_token_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY student_invites_anon_token_select ON public.student_invites FOR SELECT TO anon USING (((status <> 'revoked'::text) AND ((expires_at IS NULL) OR (expires_at > now()))));


--
-- Name: student_invites student_invites_teacher_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY student_invites_teacher_delete ON public.student_invites FOR DELETE USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: student_invites student_invites_teacher_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY student_invites_teacher_insert ON public.student_invites FOR INSERT WITH CHECK (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: student_invites student_invites_teacher_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY student_invites_teacher_select ON public.student_invites FOR SELECT USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: student_invites student_invites_teacher_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY student_invites_teacher_update ON public.student_invites FOR UPDATE USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: students; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.students ENABLE ROW LEVEL SECURITY;

--
-- Name: students students_student_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY students_student_select ON public.students FOR SELECT USING (((account_id)::text = public.get_my_student_account_id()));


--
-- Name: students students_teacher_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY students_teacher_delete ON public.students FOR DELETE USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: students students_teacher_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY students_teacher_insert ON public.students FOR INSERT WITH CHECK (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: students students_teacher_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY students_teacher_select ON public.students FOR SELECT USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: students students_teacher_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY students_teacher_update ON public.students FOR UPDATE USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: subscriptions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.subscriptions ENABLE ROW LEVEL SECURITY;

--
-- Name: subscriptions subscriptions_teacher_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY subscriptions_teacher_select ON public.subscriptions FOR SELECT USING ((((org_id)::text IN ( SELECT (teachers.org_id)::text AS org_id
   FROM public.teachers
  WHERE ((teachers.supabase_uid)::text = (auth.uid())::text))) OR (auth.role() = 'service_role'::text)));


--
-- Name: teachers; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.teachers ENABLE ROW LEVEL SECURITY;

--
-- Name: teachers teachers_insert_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY teachers_insert_own ON public.teachers FOR INSERT WITH CHECK (((supabase_uid)::text = (auth.uid())::text));


--
-- Name: teachers teachers_select_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY teachers_select_own ON public.teachers FOR SELECT USING (((supabase_uid)::text = (auth.uid())::text));


--
-- Name: teachers teachers_update_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY teachers_update_own ON public.teachers FOR UPDATE USING (((supabase_uid)::text = (auth.uid())::text));


--
-- Name: usage_records; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.usage_records ENABLE ROW LEVEL SECURITY;

--
-- Name: usage_records usage_records_teacher_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY usage_records_teacher_select ON public.usage_records FOR SELECT USING (((org_id)::text IN ( SELECT (teachers.org_id)::text AS org_id
   FROM public.teachers
  WHERE (((teachers.id)::text = public.get_my_teacher_id()) AND (teachers.org_id IS NOT NULL)))));


--
-- Name: violations; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.violations ENABLE ROW LEVEL SECURITY;

--
-- Name: violations violations_student_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY violations_student_insert ON public.violations FOR INSERT WITH CHECK ((session_key ~~ (( SELECT students.roll_number
   FROM public.students
  WHERE ((students.account_id)::text = public.get_my_student_account_id())
 LIMIT 1) || '_%'::text)));


--
-- Name: violations violations_teacher_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY violations_teacher_delete ON public.violations FOR DELETE USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- Name: violations violations_teacher_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY violations_teacher_select ON public.violations FOR SELECT USING (((teacher_id)::text = public.get_my_teacher_id()));


--
-- PostgreSQL database dump complete
--

\unrestrict d6bIkeM5mOCueFX7vBkNe0QeCieLnNvtKkjM1Le8EGvdYRdUU7GqDdYZMz8ZT6r

