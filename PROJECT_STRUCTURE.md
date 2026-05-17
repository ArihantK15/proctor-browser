# Project Structure

## 1. Marketing Website (`website/`)
Vite + React app served at `procta.net`

| Page | Component | Route |
|------|-----------|-------|
| Landing | `Landing.jsx` | `/` |
| Signup | `Signup.jsx` | `/signup`, `/register` |
| Pricing | `Pricing.jsx` | `/pricing` |
| Features | `Features.jsx` | `/features` |
| How It Works | `HowItWorks.jsx` | `/how-it-works` |
| Trust Center | `Trust.jsx` | `/trust` |
| LTI Setup Guide | `LtiSetup.jsx` | `/lti-setup` |
| Blog Index | `Blog.jsx` | `/blog` |
| Blog: AI vs Traditional Proctoring | `BlogAiVsTraditional.jsx` | `/blog/ai-proctoring-vs-traditional-proctoring` |
| Blog: Cheating Prevention | `BlogCheatingPrevention.jsx` | `/blog/online-exam-cheating-prevention-ai-proctoring` |
| Blog: DPDP Act Compliance | `BlogDPDPCompliance.jsx` | `/blog/dpdp-act-compliance-online-proctoring-indian-universities` |
| Privacy Policy | `Privacy.jsx` | `/privacy` |
| Terms of Service | `Terms.jsx` | `/terms` |
| Download | `Download.jsx` | `/download` |

Components: `Navbar`, `Hero`, `Features`, `HowItWorks`, `Problem`, `USPs`, `Comparison`, `UseCases`, `Trust`, `FAQ`, `CTA`, `Demo`, `Footer`, `PrivacySection`, `RazorpayCheckoutButton`, `auth/OAuthButtons.jsx`

---

## 2. API Backend (`app/`)
FastAPI Python server — ~140 HTTP endpoints, 4 WebSocket, 1 SSE.

### 2a. Routers — API Surface

| Router | File | Routes | Purpose |
|--------|------|--------|---------|
| Auth | `routers/auth.py` | 28 | Teacher & student signup, login, logout, password reset, OAuth, TOTP 2FA, sessions |
| Exam / Proctoring | `routers/exam.py` | 15 | Student validate, questions, answers, submit, heartbeat, events, room-cam |
| Admin (umbrella) | `routers/admin.py` | ~55 | Includes 10 sub-routers below |
| Admin — Exams | `routers/admin_exams.py` | — | Exam CRUD, groups, analytics |
| Admin — Students | `routers/admin_students.py` | — | Student search, bulk register, access codes |
| Admin — Scorecards | `routers/admin_scorecards.py` | — | CSV/Excel/PDF export, scorecard email |
| Admin — Invites | `routers/admin_invites.py` | — | Send invites, templates |
| Admin — Settings | `routers/admin_settings.py` | — | Schedule, shuffle, proctoring sensitivity |
| Admin — Org | `routers/admin_org.py` | — | Org details, members, billing info |
| Admin — Verification | `routers/admin_verification.py` | — | ID verification queue |
| Admin — Media | `routers/admin_media.py` | — | Question images, screenshots |
| Admin — Sessions | `routers/admin_sessions.py` | — | Session listing, force-submit, triage |
| Admin — Live View | `routers/admin_liveview.py` | — | Live camera, room camera control |
| Admin — Status | `routers/admin_status.py` | 2 | System status JSON + HTML page |
| Public | `routers/public.py` | 23 | Health, static pages, webhooks, registration, downloads |
| Billing | `routers/billing.py` | 5 | Plans, subscriptions, invoices, usage, Razorpay webhook |
| Privacy | `routers/privacy.py` | 3 | Consent recording, data export, account deletion |
| Appeals | `routers/appeals.py` | 3 | Student appeal submission, admin resolution |
| Grading | `routers/grading.py` | 5 | Pending grades, AI grading, confirm/bulk confirm, audit trail |
| Question Bank | `routers/question_bank.py` | 12 | CRUD bank questions, AI generate/lint/import/export, rubrics |
| SSE & WebSocket | `routers/sse.py` | 6 | SSE streams, WebSocket live-feed, connect tokens |
| Chat | `routers/chat.py` | 2 | Student & teacher chat via WebSocket |
| LTI 1.3 | `routers/lti.py` | 9 | OIDC login, launch, JWKS, AGS, NRPS, Deep Linking |
| LTI Config | `routers/lti_config.py` | 1 | Auto-configuration JSON |
| Google Classroom | `routers/google_classroom.py` | 6 | OAuth, courses, roster sync, exam link |
| Public REST API | `routers/api.py` | 9 | Programmatic API (API key auth) |
| Checkout | `routers/checkout.py` | 3 | **DISABLED** (commented out in main.py) |

### 2b. Auth Layer

| File | Purpose |
|------|---------|
| `auth/tokens.py` | JWT creation (admin, student, exam), CSRF generation & verification, `require_auth()` |
| `auth/admin_auth.py` | `require_admin()` with DB lookup + session revocation, `verify_student_auth_token()` with DB lookup, LRU caches |
| `auth/api_auth.py` | API key generation (`pk_` prefix + SHA-256 hash), request authentication |

### 2c. Services

| Service | File | Purpose |
|---------|------|---------|
| Billing | `services/billing.py` | Razorpay API, subscription creation, webhook verification |
| Sessions | `services/sessions.py` | Plan limit enforcement, session cleanup, screenshot reaper, heartbeat age |
| Risk | `services/risk.py` | Violation risk score computation |
| Chat | `services/chat.py` | WebSocket ChatHub with student/teacher connections |
| TOTP | `services/totp.py` | TOTP secret generation, encryption, verification, backup codes |
| Passwords | `services/passwords.py` | Password validation rules, disposable email check, password hashing |
| Auth Lockout | `services/auth_lockout.py` | Redis-backed login attempt tracking & lockout |
| OAuth | `services/auth_oauth.py` | OAuth bind/login for Google/Microsoft |
| Local Auth | `services/local_auth.py` | Local password management, refresh token rotation |
| Turnstile | `services/turnstile.py` | Cloudflare Turnstile CAPTCHA verification |
| Email OTP | `services/email_otp.py` | Email-based OTP for local auth |
| Suspicious Login | `services/suspicious_login.py` | Suspicious login detection & notification |
| Idempotency | `services/idempotency.py` | Redis-backed idempotency key helpers |
| Autosave | `services/autosave.py` | Redis-backed exam answer snapshots |
| Scorecard | `services/scorecard.py` | PDF scorecard generation |
| Calibration | `services/calibration.py` | Detection calibration |
| False Positive | `services/false_positive.py` | False positive analysis |
| Scoring | `services/scoring.py` | MCQ & short-answer scoring |
| Demo Exam | `services/demo_exam.py` | Seed demo exam on teacher signup |
| Google Classroom | `services/google_classroom.py` | Google Classroom API client |
| i18n | `services/i18n.py` | Internationalization |
| Practice | `services/practice.py` | Practice exam mode |
| Release | `services/release.py` | App release metadata & caching |

### 2d. LTI Module

| File | Purpose |
|------|---------|
| `lti/launch.py` | OIDC login initiation, launch JWT validation, student context storage |
| `lti/registration.py` | Platform registration management & caching |
| `lti/key.py` | JWKS key generation & management |
| `lti/ags.py` | Assignment & Grade Service (grade passback) |
| `lti/nrps.py` | Names & Role Provisioning Service (roster sync) |
| `lti/deeplink.py` | Deep Linking content selection |
| `lti/lti13.py` | LTI 1.3 JWT validation helpers |

### 2e. Middleware (applied in order in `main.py`)

| Middleware | Purpose |
|------------|---------|
| CORSMiddleware | Cross-origin request handling |
| GZipMiddleware | Response compression |
| CSRFMiddleware | CSRF token validation on POST/PUT/DELETE under `/api/` |
| StructuredLogMiddleware | Request-scoped JSON logging |
| RequestIDMiddleware | X-Request-ID propagation |
| SecurityHeadersMiddleware | CSP, HSTS, X-Frame-Options, Permissions-Policy |
| InputValidationMiddleware | SQLi pattern detection, body size limit |
| ETagMiddleware | GET/HEAD caching support |
| Rate Limiter (slowapi) | Per-route rate limiting (JWT or IP keyed) |

### 2f. Domains (Architecture Layer)

| Domain | Directory | Re-exports From |
|--------|-----------|-----------------|
| Identity | `domains/identity/` | `routers.auth` → `auth_router` |
| Proctoring | `domains/proctoring/` | `routers.exam` → `exam_router` |
| Exams | `domains/exams/` | `routers.admin_exams`, `routers.question_bank` |
| Sessions | `domains/sessions/` | `routers.admin_sessions`, `routers.admin_liveview`, `routers.sse` |
| Billing | `domains/billing/` | `routers.billing` → `billing_router` |
| Reporting | `domains/reporting/` | `routers.admin_scorecards` → `admin_scorecards_router` |
| LTI | `domains/lti/` | `routers.lti`, `routers.google_classroom` |
| Compliance | `domains/compliance/` | `routers.privacy`, `routers.appeals` |
| Ops | `domains/ops/` | `routers.admin_status`, `routers.public` |

**Note:** Domain packages are thin re-export shims. No business logic has been migrated into them. ~50% of routers bypass the domain layer and are imported directly from `app.routers` in `main.py`.

---

## 3. Teacher Dashboard (`app/dashboard-ui/`)
React SPA built with Vite, served at `/dashboard-react/` (312 KB, 86 KB gz).

### Panels (Tab-Based Navigation)

| Panel | File | Purpose |
|-------|------|---------|
| Live Sessions | `panels/LiveSessionsPanel.jsx` | Real-time session monitoring, SSE alerts, camera views |
| Operations | `panels/OpsPanel.jsx` | Queue depth, failed jobs, active sessions, service checks |
| Support Console | `panels/SupportConsole.jsx` | Operator tools, session search & terminate |
| Review | `panels/ReviewPanel.jsx` | Pending grades, appeals queue, evidence timeline, audit export |
| Results | `panels/ResultsPanel.jsx` | Score table with search/filter/sort, CSV/Excel/PDF export |
| History | `panels/HistoryPanel.jsx` | Past exam sessions per exam |
| Questions | `panels/QuestionsPanel.jsx` | Create/edit/manage questions, question bank |
| Analytics | `panels/AnalyticsPanel.jsx` | Score distribution, risk distribution, question analysis |
| Chat | `panels/ChatPanel.jsx` | Real-time messaging with students |
| Org | `panels/OrgPanel.jsx` | Org details, plan summary, trial banner |
| Org Settings | `panels/OrgSettingsPanel.jsx` | Org name, configuration |
| Members | `panels/MembersPanel.jsx` | Team invite & management, role assignment |
| Billing | `panels/BillingPanel.jsx` | Plan display, usage, invoices, upgrade |
| Security | `panels/SecurityPanel.jsx` | TOTP 2FA setup, active sessions |
| All Orgs | `panels/AllOrgsPanel.jsx` | Superadmin: all organizations overview |
| Tools | `panels/ToolsPanel.jsx` | API keys, exam templates, exports |

### Components

| Component | File | Purpose |
|-----------|------|---------|
| Timeline View | `components/TimelineView.jsx` | Color-coded violation timeline with evidence, severity, confidence |
| Onboarding Wizard | `components/OnboardingWizard.jsx` | 6-step new-user setup flow |
| Auth | `lib/auth.jsx` | Auth context, token management, login/logout, `authFetch` wrapper |

---

## 4. Student Dashboard — Legacy (`app/static/student.html`)
6600-line monolithic HTML/JS page served at `/student`.

| Feature | Description |
|---------|-------------|
| Login / Signup | Email+password, Google OAuth, Microsoft OAuth |
| Exam Listing | Upcoming, in-progress, completed exams with status badges |
| Access Code Prompt | Modal for exam access code entry |
| Preflight Check | Camera, browser support, bandwidth measurement |
| Exam Launch | Launches Electron kiosk via `window.procta_native.launchExam()` |
| Exam History | Scores, violation counts, risk level, time taken |
| Appeal Submission | Modal to submit violation/grade appeals |
| Privacy Actions | Links to privacy center for data export/delete |

---

## 5. Student Dashboard — React (`app/student-ui/`)
Vite React SPA served at `/student-react/` (198 KB, 62 KB gz).

| Feature | Status |
|---------|--------|
| Login | ⚠️ **Broken** — calls `/api/auth/login` instead of `/api/v1/student/auth/login` |
| Signup | ❌ Not implemented |
| Exam Listing | ✅ Implemented |
| Preflight | ❌ Not implemented |
| History | ❌ Not implemented |
| Appeals | ❌ Not implemented |

---

## 6. Static Pages (`app/static/`)

| Page | File | Auth | Purpose |
|------|------|------|---------|
| Legacy Dashboard | `dashboard.html` (6607 lines) | Teacher JWT | Monolithic teacher dashboard (fallback) |
| Student Dashboard | `student.html` (6600 lines) | Student JWT | Legacy student dashboard |
| Student Registration | `register.html` | None | Teacher lookup, self-registration |
| Download | `download.html` | None | Secure browser download |
| Privacy Center | `privacy.html` | Teacher or Student JWT | Consent list, data export, account deletion |
| Privacy Policy | `privacy-policy.html` | None | Legal privacy document |
| DPA | `dpa.html` | None | Data Processing Addendum template |
| Trust Center | `trust-center.html` | None | Procurement packet (security, privacy, compliance) |
| Security Questionnaire | `security-questionnaire.html` | None | Pre-filled CAIQ-Lite |
| API Docs | `api-docs.html` | None | API documentation landing page |
| Proof Assets | `proof-assets.html` | None | Trust/security proof for sales |
| Sample Scorecard | `sample-scorecard.html` | None | Sample exam scorecard for procurement |
| Dev Server | `_preview_server.py` | — | Dev tooling (not production) |

---

## 7. Email System (`app/emailer.py`)
689 lines. Intended to support 3 email backends (Resend, SMTP, Noop).

| Function | Purpose | Status |
|----------|---------|--------|
| `send_invite_email()` | Exam invite to student | 🔴 **Broken** — calls undefined `_render_invite()` and `_pick_backend()` |
| `send_exam_reminder()` | Exam start reminder | 🔴 **Broken** — calls undefined `_pick_backend()` |
| `send_scorecard_email()` | Scorecard notification | 🔴 **Broken** — calls undefined `_pick_backend()` |
| `send_demo_request_notification()` | Sales lead notification | 🔴 **Broken** — returns wrong type (tuple instead of `SendResult`) |
| `send_email_verification()` | Email verification link | 🔴 **Broken** — calls undefined `_send()` |
| `send_password_reset_email()` | Password reset link | 🔴 **Broken** — calls undefined `_send()` |
| `send_suspicious_login_email()` | Suspicious login alert | 🔴 **Broken** — calls undefined `_send()` |
| `_render_reminder()` | Reminder HTML+text | ✅ Implemented |
| `_render_scorecard_email()` | Scorecard HTML+text | ✅ Implemented |
| `_render_invite()` | Invite HTML+text | ❌ **Missing** (called but never defined) |
| `_pick_backend()` | Email backend selection | ❌ **Missing** (called but never defined) |
| `_send()` | Convenience wrapper | ❌ **Missing** (called but never defined) |
| `_Backend` class | Abstract email backend | ❌ **Missing** (mentioned in docstring only) |
| `ResendBackend` | Resend API implementation | ❌ **Missing** |
| `SMTPBackend` | SMTP implementation | ❌ **Missing** |
| `NoopBackend` | No-op implementation | ❌ **Missing** |

---

## 8. Database Migrations (`migrations/`)
42 `.sql` files (phase1–phase61). Custom runner at `scripts/run_migrations.py`.

| Phase | File | Purpose |
|-------|------|---------|
| — | `phase0_base.sql` | ❌ **Missing** — no base schema file |
| 1 | `phase1_student_accounts.sql` | Cross-teacher student identity |
| 3 | `phase3_api_keys.sql` | API keys table |
| 10a–f | `phase10_*.sql` | Question bank, student groups, invites, scorecards, reminders, clicks |
| 11a–c | `phase11_*.sql` | Question image URL, scorecard insight, full question schema |
| 12 | `phase12_short_answer.sql` | Short-answer grading columns |
| 13 | `phase13_indexes_constraints.sql` | Performance indexes + unique constraints |
| 14 | `phase14_exam_templates.sql` | Reusable exam templates |
| 15 | `phase15_invite_cap_rpc.sql` | Atomic invite-cap claim RPC |
| 17 | `phase17_claim_scorecard_rpc.sql` | Atomic scorecard-claim RPC |
| 20 | `phase20_organizations.sql` | Multi-tenant org model |
| 24 | `phase24_scorecard_claim_ttl.sql` | TTL recovery for scorecard claim |
| 30 | `phase30_phone_camera.sql` | Phone camera monitoring |
| 31 | `phase31_email_verification.sql` | Email verification timestamps |
| 32a–b | `phase32_*.sql` | Auth audit log, Google Classroom |
| 33 | `phase33_totp_2fa.sql` | TOTP 2FA columns |
| 34 | `phase34_email_otp.sql` | Email OTP table |
| 35 | `phase35_sessions.sql` | JWT session revocation table |
| 40 | `phase40_grading_audit.sql` | AI grading audit trail |
| 49 | `phase49_exam_sessions_student_id.sql` | `exam_sessions.student_id` column + backfill |
| 50 | `phase50_privacy.sql` | Privacy consent records |
| 51 | `phase51_appeals.sql` | Student appeals table |
| 52 | `phase52_backfill_student_id.sql` | Backfill `student_id` on old sessions |
| 53 | `phase53_indexes_perf.sql` | Additional performance indexes |
| 54 | `phase54_confidence_score.sql` | Detection confidence on violations |
| 55 | `phase55_dashboard_reporting_indexes.sql` | Dashboard/reporting composite indexes |
| 56 | `phase56_proctoring_sensitivity.sql` | Per-exam proctoring sensitivity |
| 57 | `phase57_usage_tracking.sql` | Usage tracking + upsert function |
| 60 | `phase60_local_auth.sql` | Local auth password columns |
| 61 | `phase61_refresh_tokens.sql` | Refresh token revocation table |
| — | `rls_policies.sql` | Row-Level Security policies |

**Tracking mechanism:** Custom `schema_migrations` table with filename + timestamp. Alembic configured but unused (empty `versions/` directory).

---

## 9. Background Jobs

| Job | Mechanism | File | Purpose |
|-----|-----------|------|---------|
| Email Sending | RQ | `jobs/email_jobs.py` | Async email dispatch via Redis queue |
| Autosave Flush | RQ | `jobs/autosave_jobs.py` | Flush Redis snapshots to database |
| Exam Reminders | Daemon thread | `app/reminders.py` | Send 24h and 1h-before reminders |
| Screenshot Cleanup | Daemon thread | `app/main.py:93` | Delete screenshots older than 48h |

---

## 10. Infrastructure

| File | Purpose |
|------|---------|
| `Caddyfile` | Reverse proxy, TLS termination, static file serving, security headers |
| `Dockerfile` | Container image (Python + dependencies) |
| `docker-compose.yml` | Multi-service orchestration (API, Redis, worker) |
| `entrypoint.sh` | Container startup: run migrations then start uvicorn |
| `.github/workflows/test.yml` | CI pipeline: pytest, docker-smoke, security-scan (Gitleaks/Semgrep/Trivy/pip-audit) |
| `requirements.txt` | Python dependencies |
| `package.json` (root) | Dashboard/student-ui build tooling |

---

## 11. Test Suite (`tests/`)
566 tests, 33 skipped, ~10,645 lines across 41 files.

| Test File | Scope |
|-----------|-------|
| `test_e2e_api_flow.py` | 14-test E2E happy-path (health → plans → exam → results → privacy → appeals → status) |
| `test_privacy_appeals.py` | 14 tests: privacy export/delete/consent, appeals, student_id |
| `test_auth_and_sessions.py` | 648 lines: auth login/signup/refresh/revocation |
| `test_lti.py` | 852 lines: LTI 1.3 launch, AGS, NRPS (largest test file) |
| `test_org_billing.py` | 526 lines: org creation, billing, plan limits |
| `test_endpoints_coverage.py` | 708 lines: endpoint coverage verification |
| `test_supporting_modules.py` | Cache, database, dependencies |
| `test_exam_flow.py` | Exam creation, student registration, validate, submit |
| `test_appeals_flow.py` | Appeal submission & resolution |
| `test_grading.py` | AI grading, confirm/bulk confirm |
| `test_privacy.py` | Data export, deletion, consent |
| `test_sse.py` | SSE stream, connect tokens |
| `test_websockets.py` | Live frame, room camera WebSocket |
| `browser/` | 3 browser E2E tests |
