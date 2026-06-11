--
-- PostgreSQL database dump
--

\restrict TUfDIbHMJDK8XpVbxCKI3Hd4dozHMvDWB5Au0ug4fo9scFKlZicrISGhkeLfAWQ

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
-- Data for Name: schema_migrations; Type: TABLE DATA; Schema: public; Owner: procta
--

INSERT INTO public.schema_migrations VALUES ('phase10_invite_clicks.sql', '2026-05-17 06:18:17.585102+00');
INSERT INTO public.schema_migrations VALUES ('phase10_invite_reminders.sql', '2026-05-17 06:18:17.586453+00');
INSERT INTO public.schema_migrations VALUES ('phase10_question_bank.sql', '2026-05-17 06:18:17.587624+00');
INSERT INTO public.schema_migrations VALUES ('phase10_scorecard_emailed.sql', '2026-05-17 06:18:17.58839+00');
INSERT INTO public.schema_migrations VALUES ('phase10_student_groups.sql', '2026-05-17 06:18:17.5898+00');
INSERT INTO public.schema_migrations VALUES ('phase10_student_invites.sql', '2026-05-18 14:12:35.999223+00');
INSERT INTO public.schema_migrations VALUES ('phase11_questions_full_schema.sql', '2026-05-18 14:12:36.004045+00');
INSERT INTO public.schema_migrations VALUES ('phase11_questions_image_url.sql', '2026-05-18 14:12:36.005577+00');
INSERT INTO public.schema_migrations VALUES ('phase11_scorecard_insight.sql', '2026-05-18 14:12:36.006444+00');
INSERT INTO public.schema_migrations VALUES ('phase12_short_answer.sql', '2026-05-18 14:12:36.00774+00');
INSERT INTO public.schema_migrations VALUES ('phase13_indexes_constraints.sql', '2026-05-18 14:14:00.483094+00');
INSERT INTO public.schema_migrations VALUES ('phase14_exam_templates.sql', '2026-05-18 14:14:00.48828+00');
INSERT INTO public.schema_migrations VALUES ('phase15_invite_cap_rpc.sql', '2026-05-18 14:14:00.489308+00');
INSERT INTO public.schema_migrations VALUES ('phase17_claim_scorecard_rpc.sql', '2026-05-18 14:14:00.491052+00');
INSERT INTO public.schema_migrations VALUES ('phase1_student_accounts.sql', '2026-05-18 14:14:00.492247+00');
INSERT INTO public.schema_migrations VALUES ('phase20_organizations.sql', '2026-05-18 14:14:00.493058+00');
INSERT INTO public.schema_migrations VALUES ('phase24_scorecard_claim_ttl.sql', '2026-05-18 14:14:00.4967+00');
INSERT INTO public.schema_migrations VALUES ('phase30_phone_camera.sql', '2026-05-18 14:14:00.497846+00');
INSERT INTO public.schema_migrations VALUES ('phase31_email_verification.sql', '2026-05-18 14:14:00.498675+00');
INSERT INTO public.schema_migrations VALUES ('phase32_auth_events.sql', '2026-05-18 14:14:00.499539+00');
INSERT INTO public.schema_migrations VALUES ('phase32_google_classroom.sql', '2026-05-18 14:14:00.50079+00');
INSERT INTO public.schema_migrations VALUES ('phase33_totp_2fa.sql', '2026-05-18 14:14:00.501428+00');
INSERT INTO public.schema_migrations VALUES ('phase34_email_otp.sql', '2026-05-18 14:14:00.502255+00');
INSERT INTO public.schema_migrations VALUES ('phase35_sessions.sql', '2026-05-18 14:14:00.503226+00');
INSERT INTO public.schema_migrations VALUES ('phase3_api_keys.sql', '2026-05-18 14:14:00.503974+00');
INSERT INTO public.schema_migrations VALUES ('phase40_grading_audit.sql', '2026-05-18 14:14:00.505108+00');
INSERT INTO public.schema_migrations VALUES ('phase49_exam_sessions_student_id.sql', '2026-05-18 14:14:00.505876+00');
INSERT INTO public.schema_migrations VALUES ('phase50_privacy.sql', '2026-05-18 14:14:00.506346+00');
INSERT INTO public.schema_migrations VALUES ('phase51_appeals.sql', '2026-05-18 14:14:00.507157+00');
INSERT INTO public.schema_migrations VALUES ('phase52_backfill_student_id.sql', '2026-05-18 14:14:00.507785+00');
INSERT INTO public.schema_migrations VALUES ('phase53_indexes_perf.sql', '2026-05-18 14:14:00.510766+00');
INSERT INTO public.schema_migrations VALUES ('phase54_confidence_score.sql', '2026-05-18 14:14:00.511512+00');
INSERT INTO public.schema_migrations VALUES ('phase55_dashboard_reporting_indexes.sql', '2026-05-18 14:14:00.512272+00');
INSERT INTO public.schema_migrations VALUES ('phase56_proctoring_sensitivity.sql', '2026-05-18 14:14:00.521703+00');
INSERT INTO public.schema_migrations VALUES ('phase57_usage_tracking.sql', '2026-05-18 14:14:00.523672+00');
INSERT INTO public.schema_migrations VALUES ('phase60_local_auth.sql', '2026-05-18 14:14:00.529122+00');
INSERT INTO public.schema_migrations VALUES ('phase61_refresh_tokens.sql', '2026-05-18 14:14:00.529936+00');
INSERT INTO public.schema_migrations VALUES ('rls_policies.sql', '2026-05-18 14:14:00.530789+00');
INSERT INTO public.schema_migrations VALUES ('phase62_teachers_status.sql', '2026-05-20 18:05:07.10007+00');
INSERT INTO public.schema_migrations VALUES ('phase63_violations_created_at.sql', '2026-05-21 04:44:36.855841+00');
INSERT INTO public.schema_migrations VALUES ('phase64_exam_config_created_at.sql', '2026-05-21 04:57:42.016946+00');
INSERT INTO public.schema_migrations VALUES ('phase65_schema_reconciliation.sql', '2026-05-21 05:17:53.21061+00');
INSERT INTO public.schema_migrations VALUES ('phase66_upsert_constraints.sql', '2026-05-21 14:15:27.407657+00');
INSERT INTO public.schema_migrations VALUES ('phase67_drop_redundant_uniques.sql', '2026-05-21 22:29:27.224455+00');
INSERT INTO public.schema_migrations VALUES ('phase68_email_2fa.sql', '2026-05-24 06:26:48.574955+00');
INSERT INTO public.schema_migrations VALUES ('phase69_invite_token_hash.sql', '2026-05-24 06:48:59.636763+00');
INSERT INTO public.schema_migrations VALUES ('phase70_backfill_password_changed_at.sql', '2026-05-24 07:04:15.433371+00');
INSERT INTO public.schema_migrations VALUES ('phase70_issues.sql', '2026-05-24 09:57:52.478647+00');
INSERT INTO public.schema_migrations VALUES ('phase71_drop_invite_token_plaintext.sql', '2026-05-24 17:39:50.447034+00');
INSERT INTO public.schema_migrations VALUES ('phase72_org_logo.sql', '2026-05-28 03:19:36.303218+00');
INSERT INTO public.schema_migrations VALUES ('phase73_violations_dismiss.sql', '2026-05-28 13:52:30.568751+00');
INSERT INTO public.schema_migrations VALUES ('phase74_session_pause_terminate.sql', '2026-05-29 17:45:31.105495+00');
INSERT INTO public.schema_migrations VALUES ('phase75_exam_audio_keywords.sql', '2026-05-29 17:50:38.145679+00');
INSERT INTO public.schema_migrations VALUES ('phase77_student_email_verified.sql', '2026-05-31 13:50:18.472278+00');
INSERT INTO public.schema_migrations VALUES ('phase79_student_reminder_preferences.sql', '2026-06-01 14:25:35.761111+00');
INSERT INTO public.schema_migrations VALUES ('phase80_tenant_fk_constraints.sql', '2026-06-01 14:55:28.837219+00');
INSERT INTO public.schema_migrations VALUES ('phase81_extended_fk_constraints.sql', '2026-06-01 15:58:30.091751+00');
INSERT INTO public.schema_migrations VALUES ('phase82_normalize_uuid_columns.sql', '2026-06-02 00:20:56.184667+00');
INSERT INTO public.schema_migrations VALUES ('phase83_rls_extended.sql', '2026-06-02 00:42:45.585365+00');
INSERT INTO public.schema_migrations VALUES ('phase84_fk_indexes.sql', '2026-06-02 00:46:18.079363+00');
INSERT INTO public.schema_migrations VALUES ('phase85_enum_check_constraints.sql', '2026-06-02 00:55:22.926934+00');
INSERT INTO public.schema_migrations VALUES ('phase86_ttl_sweeper.sql', '2026-06-02 01:00:55.604989+00');
INSERT INTO public.schema_migrations VALUES ('phase87_teachers_status_check.sql', '2026-06-02 02:45:40.687769+00');
INSERT INTO public.schema_migrations VALUES ('phase88_composite_unique_keys.sql', '2026-06-02 03:13:02.991101+00');
INSERT INTO public.schema_migrations VALUES ('phase89_exam_sessions_unique.sql', '2026-06-02 03:17:43.689313+00');
INSERT INTO public.schema_migrations VALUES ('phase90_org_student_quota_trigger.sql', '2026-06-02 03:45:39.044547+00');
INSERT INTO public.schema_migrations VALUES ('phase91_quota_trigger_race_fix.sql', '2026-06-02 03:45:39.048514+00');
INSERT INTO public.schema_migrations VALUES ('phase92_admin_audit_log.sql', '2026-06-02 09:17:37.656059+00');
INSERT INTO public.schema_migrations VALUES ('phase93_case_insensitive_account_email_uniqueness.sql', '2026-06-03 18:47:18.098053+00');
INSERT INTO public.schema_migrations VALUES ('phase94_appeals_flag_link.sql', '2026-06-05 14:26:40.261603+00');
INSERT INTO public.schema_migrations VALUES ('phase95_session_status_invariants.sql', '2026-06-06 09:40:24.362657+00');
INSERT INTO public.schema_migrations VALUES ('phase96_billing_enterprise.sql', '2026-06-10 17:02:44.341548+00');


--
-- PostgreSQL database dump complete
--

\unrestrict TUfDIbHMJDK8XpVbxCKI3Hd4dozHMvDWB5Au0ug4fo9scFKlZicrISGhkeLfAWQ

