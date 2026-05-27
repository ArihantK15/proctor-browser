# Procta — Engineering & Business Roadmap

> Updated 2026-05-14. Covers Phases 0–5 (shipped) and the post-product
> strategic roadmap. Phases 0–5 are kept as history; strategy starts
> at "Strategic Roadmap" below.

---

## Post-Audit Execution Queue

Source plan: `.opencode/plans/08-comprehensive-roadmap.md`.

### In Progress

- CI release gate now includes dashboard build/audit, website audit/build,
  root npm audit, Python tests, Docker smoke, and a dedicated security scan
  job for `pip-audit`, Gitleaks, Semgrep, and Trivy.
- LTI learner privacy boundary is documented in code: LMS-managed learners
  intentionally do not create Procta `student_accounts`.
- Deploy checklist now calls out migration/rollback/health/smoke checks and
  the phase52 student-session backfill verification query.
- Reliability dashboard first slice is live: `/api/v1/admin/status` now
  returns service checks plus queue/session/submit-failure metrics, and the
  React dashboard has an Ops tab.

### Next Build Targets

1. Reliability dashboard next slice: Sentry error rate, worker retry details,
   deploy version, and clearer production health thresholds.
2. Trust center: DPA, subprocessors, retention policy, encryption posture,
   incident response, DPDP/FERPA summary, and sample scorecard.
3. Onboarding wizard: create first exam, import students, configure access
   code, send invites, run demo exam, and download browser.
4. Evidence-grade review: violation timeline, reason codes, reviewer
   decisions, appeal trail, and exportable audit packet.
5. False-positive controls: calibration quality, confidence, sensitivity
   profile, and explainable flagging.

### Manual / Environment-Gated

- Run Docker build and production smoke test on the droplet after pull.
- Apply pending migrations in Supabase and verify
  `exam_sessions.student_id` backfill state.
- Capture real product screenshots/video from a production-like exam flow.

---

## Current Architecture

| Layer | Modules | Notes |
|-------|---------|-------|
| **Routers** (`app/routers/`) | 22 files | admin, exam, auth, billing, chat, grading, lti, public, question_bank, sse, + domain routers |
| **Services** (`app/services/`) | 13 files | billing, calibration, chat, i18n, invite_landing, practice, release, risk, scorecard, scoring, sessions |
| **Repositories** (`app/repositories/`) | 3 files | questions, sessions, (more as needed) |
| **Models** (`app/models/`) | 9 files | Pydantic schemas for billing, exam, groups, invites, lti, org, student, teacher |
| **Auth** (`app/auth/`) | 3 files | JWT tokens (AuthCtx, require_auth, extract_auth), admin/student auth |
| **Jobs** (`app/jobs/`) | 2 files | email_jobs, helpers (Redis RQ background workers) |
| **LTI 1.3** (`app/lti/`) | 7 files | AGS, Deep Linking, NRPS, key mgmt, launch, registration |
| **Static** (`app/static/`) | 14 files | HTML surfaces, CSS design system, JS helpers |
| **Electron** (`renderer/`) | 1 file | index.html (kiosk exam window) |

**Key metrics:**
- `app/main.py`: ~420 lines (down from 7,642)
- `app/dependencies.py`: ~193 lines (down from 2,091) — pure re-export hub
- Test suite: 482 passed, 21 skipped across 20 test files, **0 warnings**
- Rate limiting: 172/172 routes protected (30/min each)
- CSRF: Mandatory for all JWT-authenticated POST/PUT/DELETE
- Design system: 3 themes (dark/OLED/light), 155 component classes, Periwinkle Blue accent
- Deployment Readiness Score: **10/10**

---

## Phase 0 — Foundation ✅

*Shipped 2026-05-11 (`b24fea1`)*

1. **`dependencies.py` split** — 2,091 → 193 lines. Auth → `app/auth/`, models → `app/models/`, business logic → `app/services/`, data access → `app/repositories/`, utilities → `app/utils/`.
2. **`admin.py` split** — 3,264 lines → 22 domain routers under `app/routers/`.
3. **Background jobs** — Redis RQ queue with `app/jobs/` and separate worker container. Scorecard email bulk-send migrated.
4. **Test infrastructure** — `EMAIL_PROVIDER=noop`, conftest helpers, shared Supabase mock.

---

## Phase 1 — Self-Service & Revenue ✅

*Shipped across 2 commits (`64cd7a9`, `23a1eab`)*

5. **Org/tenant model** — organizations table, org_id propagation, per-org billing plans (free/pro/enterprise). Org Overview, Members, and Billing panels on dashboard.
6. **Razorpay billing** — `app/services/billing.py` with Razorpay subscription creation, webhook verification (HMAC-SHA256), and sandbox mode (no keys needed for local dev). `app/routers/billing.py` serves plan listing, subscription creation (returns Razorpay short_url), webhooks, and invoice history. Dashboard billing panel shows plan/status/usage with upgrade buttons.
7. **Free trial gate** — 14-day trial with countdown banner on Org Overview panel.

---

## Phase 2 — Hardening & Design System ⚡

*Shipped across 5 commits (`50e9c7a`, `79048a7`, `82ee2a1`, `05399de`, and prior)*

### 2.0 Structural Hardening

52 deferred items from TODO.md §2 and §2.A audited and fixed:

| Category | Items completed |
|----------|----------------|
| Race conditions | Scorecard claim on hard-kill, sessionId orphan recovery, validate-student TOCTOU |
| Performance | ChatHub socket cap + idle eviction, async Supabase hot paths, 2 uvicorn workers |
| Security | Caddy HSTS/X-Frame-Options, filesystem path traversal guards, cross-tenant roll_number isolation, /login rate-limiting (10 req/min) |
| Code quality | `except Exception` audit, naive datetime → aware, Status → StrEnum, `AuthCtx` typed auth, action-btn → .btn migration (90 instances) |
| UX | Focus-traps on modals, WCAG AA contrast fix, spinner/disabled on slow exports, mobile breakpoints |
| Infra | Backup story (restic + cron), disk-fill prevention (screenshot retention), dependency pins |

### 2.1 Codebase Refactoring

- **Escape helpers** — consolidated `chatEscape`/`chatJsEscape` into `_safe.js`, removed inline duplicates
- **CSS extraction** — ~900 lines moved from dashboard.html → `dashboard.css`
- **Long functions split** — `_build_scorecard_pdf` (201→70 LOC), `_render_invite_landing` (166→50 LOC), `export_pdf` (284→120 LOC)
- **Cross-tenant isolation** — `validate_student` chains through invite access_code token; all queries scoped by teacher_id + exam_id
- **Naming normalization** — `safe_tid`→`safe_teacher_id`, `AuthCtx` dataclass, `extract_auth()` helper
- **i18n infrastructure** — `app/services/i18n.py` + `app/static/_i18n.js` with `t()` helpers and ~40 string keys
- **btn migration** — all 90 `action-btn` references converted to `.btn`/`.btn-primary`/`.btn-secondary`/`.btn-ghost`; old CSS rules removed
- **Rate limiting** — Caddy auth endpoint rate limiting (10 req/min per IP)

### 2.2 Visual Redesign (§1.6 — 16 surfaces ported)

The new Periwinkle Blue design system (OKLCH space, IBM Plex fonts, 3 themes) is live across all surfaces:

| Surface | Status | Notes |
|---------|--------|-------|
| Live Sessions | ✅ | stats-bar, table-toolbar, severity row accents |
| Analytics | ✅ | stat-chip strip, 2-col ax-card grid, histogram |
| Results | ✅ | stat-tile strip, table-toolbar with risk filter |
| Tools | ✅ | tool-card design system cards |
| Questions | ✅ | 3-column shell (sidebar | content | AI/Bank) |
| History | ✅ | stats-bar + table-toolbar, detail drill-down |
| Org Overview | ✅ | stat-tile strip, trial banner |
| Chat | ✅ | btn classes, broadcast modal design tokens |
| Members | ✅ | stats-bar + table-toolbar + design token table |
| All Orgs | ✅ | stats-bar + table-toolbar + design token table |
| Org Settings | ✅ | card wrapper, input class, btn-primary |
| Billing | ✅ | stat-tile top, tool-card plans |
| Student Lobby | ✅ | exam cards with card chrome |
| Marketing Landing | ✅ | full vanilla HTML port (hero, features, pricing) |
| Renderer (Electron) | ✅ | accent recolor (emerald→periwinkle), tokens embedded inline |
| Calibration | ✅ | concentric-ring dots, calmer pulse |

**Design system assets:** `tokens.css`, `components.css`, `theme.css`, `logo.svg`, `dashboard.css`

---

## Phase 3 — Institutional Sales ✅

*Shipped 2026-05-12 (`05399de`)*

8. **LTI 1.3 integration** — `app/lti/` implements AGS (grade passback), Deep Linking (content selection), NRPS (roster sync), key management, OIDC launch, and dynamic registration. Canvas/Moodle/Blackboard compatible. Router at `app/routers/lti.py`.
9. **Public REST API** — `app/routers/api.py` with X-API-Key auth. Endpoints: list exams, get exam details, list/session students, session drill-down. API key management at `/api/v1/admin/api-keys` (create/list/revoke). `app/auth/api_auth.py` handles key generation (SHA-256 hashed, prefix-identified) and authentication middleware.
10. **Alembic migrations** — `alembic.ini` + `migrations/alembic/env.py` configured for PostgreSQL (reads `DATABASE_URL` or constructs from `SUPABASE_*` vars). Existing raw SQL migrations coexist in `migrations/*.sql`.
11. **Typed repository** — `app/repositories/base.py` provides `Repository` base class with fluent `QueryBuilder` (select/insert/update/delete with .eq/.neq/.gt/.gte/.lt/.lte/.in_/.order/.limit/.offset/.maybe_single) and `QueryResult` typed wrapper. Table-specific repositories can subclass with `table = "tablename"`.

---

## Phase 4 — Scale & Polish ✅

*Shipped 2026-05-13 (`f8bcf37`, `22f4ca5`)*

10. **Observability** — Structured JSON logging via `python-json-logger`. Every `logger.info/warning/error` across the entire codebase emits JSON automatically with `request_id`, `method`, `path` via `contextvars` propagation. `trace_span()` async context manager for timing DB/API calls. RequestIDMiddleware sets trace context on every request.

11. **Live proctoring** — Phone camera room monitoring:
   - Exam toggle (`phone_camera_enabled`) per exam
   - `renderer/phone-cam.html`: mobile capture page with `getUserMedia` (rear camera), `WakeLock` API, WebSocket JPEG streaming at 1fps, heartbeat every 8s
   - `sse.py`: `/ws/v1/room-frame/{session_id}` — connection singleton, auto-reconnect, offline detection (>20s no beat → `room_cam_offline` violation)
   - Teacher dashboard: Room Cam button in Live Sessions table, live frame polling, Approve/Reject flow
   - QR code on exam screen — student scans with phone to pair
   - Privacy consent screen, basic frame validation (JPEG header, minimum size)

---

## Phase 5 — Identity & Verification Hardening ✅

*Shipped across multiple commits 2026-05-13 → 2026-05-14*

A two-week push to close the auth gaps that would have blocked any
school IT security review. All items below have ship code + tests +
docs.

### 5.1 — Auth baseline (P0)

| # | Item | Where |
|---|------|-------|
| 1 | Mandatory email verification before login | `services/email_verification` + migration `phase31_email_verification.sql` |
| 2 | Account lockout after failed logins (Redis-backed, 5 in 15min → 15min lock) | `services/auth_lockout.py` |
| 3 | Password complexity rules + offline HIBP top-1000 check | `services/passwords.py` + `data/breached_top1000.txt` |
| 4 | Persistent auth audit log | `services/auth_events.py` + migration `phase32_auth_events.sql` |
| 5 | Student-side password reset (parity with teachers) | `routers/auth.py:1094` |

### 5.2 — Auth advanced (P1)

| # | Item | Where |
|---|------|-------|
| 6 | TOTP 2FA — mandatory after 30-day grace, backup codes, Fernet-encrypted secrets | `services/totp.py` + migration `phase33_totp_2fa.sql` |
| 7 | Email OTP service (2FA fallback, step-up auth, recovery) | `services/email_otp.py` + migration `phase34_email_otp.sql` |
| 8 | Capture phone from Razorpay payments (informational, free signal) | `routers/checkout.py` |
| 9 | Session revocation ("sign out other devices") with `jti` claim + DB-backed allowlist | migration `phase35_sessions.sql` + `cache.py` |
| 10 | Re-auth for sensitive actions (delete account, change email, view audit log, change 2FA) | `auth/tokens.py:issue_reauth_token` |

### 5.3 — Auth UX + bot protection (zero-cost Pro-tier alternatives)

| # | Item | Where |
|---|------|-------|
| 11 | **Google Sign-In** (teachers + students) — server-side PKCE via Supabase Auth | `services/auth_oauth.py` + `routers/auth.py:/oauth/start,/oauth/callback` |
| 12 | **Microsoft Sign-In** (Azure provider via Supabase Auth — same handler as Google) | Same files; just second provider |
| 13 | **Cloudflare Turnstile** (invisible Managed mode) on signup + login + password-reset + resend-verification | `services/turnstile.py` + `hooks/useTurnstile.js` |
| 14 | **HIBP k-anonymity client-side check** (replaces Supabase Pro's leaked-password protection) | `website/src/lib/hibp.js` |
| 15 | **Disposable-email blocklist** (120 curated domains, ~95% of real abuse) | `data/disposable_email_domains.txt` + `services/passwords.py:is_disposable_email` |
| 16 | **Suspicious-login email** — new-device detection vs last 30 days of `auth_events` | `services/suspicious_login.py` + `emailer.py:send_suspicious_login_email` |

**Documentation:** `docs/OAUTH_SETUP.md` walks through Supabase Sign-In setup (Google Cloud + Microsoft Entra + Supabase dashboard, ~25 min) and Google Classroom API integration (separate OAuth client, honest state audit — wired vs skeleton vs not-started, build plan with effort estimates).

**Decisions taken:**
- Existing users force-re-verify email on next login (cleaner posture; accept the support burden)
- Email OTP replaces SMS OTP everywhere — zero marginal cost via Resend
- TOTP 2FA mandatory for everyone after 30-day grace period (warning banner from day 1)
- Separate Google OAuth consumer client (not reused from Classroom — clean consent screen → better signup conversion)
- Turnstile in invisible Managed mode (silent unless bot signal is high)
- Microsoft ships alongside Google (+0.5 day marginal effort; pre-positions university segment)

**Migrations applied (production DB):** phase31-35 + phase41-42 (RLS hardening on the new tables). All confirmed via `mcp__supabase__list_migrations` on 2026-05-14.

**Manual setup outstanding** (~30 min of dashboard work, no code changes):
1. Google Cloud Console → new consumer OAuth client → paste into Supabase Auth dashboard
2. Microsoft Entra → app registration → paste into Supabase Auth dashboard
3. Cloudflare Turnstile → register site → set `TURNSTILE_SITE_KEY`/`SECRET_KEY` env vars + `VITE_TURNSTILE_SITE_KEY` for frontend
4. Supabase Auth → URL Configuration → whitelist `https://app.procta.net/api/v1/auth/oauth/callback`

Full step-by-step in `docs/OAUTH_SETUP.md`.

---

## Phase 6 — React Dashboard + AI Audit Trail ✅

*Shipped across 7 commits 2026-05-14 (`2c885d1` → `331e1b8`) and
`7bf2bf8` for the audit trail.*

The two biggest "Backlog" items from prior plans both shipped in a
single push.

### 6.1 — Dashboard React migration (the XSS class is gone)

The 6,500-line vanilla-JS `app/static/dashboard.html` has been
migrated to a real React SPA in `app/dashboard-ui/`. 13 panels
ported, all behind the same auth + design system:

| Panel | File | LOC |
|---|---|---|
| Live Sessions (with SSE + camera view) | `LiveSessionsPanel.jsx` | 217 |
| Tools | `ToolsPanel.jsx` | 217 |
| Questions (3-column editor) | `QuestionsPanel.jsx` | 200 |
| Results | `ResultsPanel.jsx` | 161 |
| Security (2FA + sessions) | `SecurityPanel.jsx` | 140 |
| History (student drill-down) | `HistoryPanel.jsx` | 134 |
| Org Overview | `OrgPanel.jsx` | 109 |
| Members | `MembersPanel.jsx` | 105 |
| Org Settings | `OrgSettingsPanel.jsx` | 53 |
| + Billing, Chat, Analytics, All Orgs | — | — |

**Why this matters strategically:**
- Permanently closes the entire `innerHTML = ${template}` XSS class
- Reuses the design system from `website/` (no more code drift)
- 3× feature velocity on dashboard work going forward
- Looks professional to school IT (vanilla-JS dashboard was a
  procurement red flag)

This was ranked **#6 in Top 10 highest-ROI improvements**. Shipped.

### 6.2 — AI grading audit trail

`7bf2bf8` — every grade confirmation logged to `grading_audit`
table (`migrations/phase40_grading_audit.sql`, applied
2026-05-14). Schema captures: `teacher_id`, `exam_id`,
`session_key`, `answer_id`, `question_id`, `ai_score`,
`ai_confidence`, `teacher_score`, `max_score`, `action`
(`confirmed | bulk_accept | bulk_reject | overridden`),
`created_at`.

Bulk-review UI ships in the new React dashboard's Results /
Grading panel. Was ranked Backlog "AI audit trail — bulk review
UI for AI-suggested grades". Shipped.

---

## Strategic Roadmap

> The engineering phases are done. From here the bottleneck shifts to
> distribution, pricing, compliance, and operational maturity — not
> code. This section is the CTO/CEO/PM-level view of what closes a
> Series A round in 12-18 months.

### Where Procta stands today

| Dimension | State |
|---|---|
| Product | Working, deployed, 70+ shipped features (see Procta_Features.pdf) |
| Code | 9.5/10 production-readiness. Cleaner than most pre-seed startups |
| Operation | **Bus factor 1.** Single founder. Single droplet. No co-founder. |
| Revenue | Razorpay subscriptions + one-shot checkout shipped. Pricing unpublished. |
| Customers | Pilots, no public logos |
| Funding | Pre-seed ready. Series A blocked on bus factor, pricing, logos. |

### Strategic verdict

- **Fundable at pre-seed** (₹1.5-3 cr for 12-18 mo runway)
- **Not fundable at Series A** until: 2nd founder + ₹2-5 cr ARR from 10+ schools + SOC 2 in progress + sharp differentiation positioning
- **One named district / coaching-institute deal (₹50L+ ARR)** is the single thing that closes Series A

### Where Procta wins

1. **India compliance moat** — DPDP Act compliance from day one. US-built competitors (Proctorio, Honorlock, ProctorU) can't move fast enough on Indian data residency.
2. **Phone-cam room monitoring** — genuine differentiator. Procta + phone is a more rigorous exam than Procta alone, which is what schools want to sell to parents.
3. **AI short-answer grading with teacher-confirm** — wedge into university segment that incumbents serve poorly.
4. **Coaching institute go-to-market** — Allen, Aakash, Vedantu, PW run more proctored exams per month than universities run per year. They have edtech budgets and zero loyalty to incumbents.

### Where Procta loses today

- "AI proctoring" is undifferentiated marketing copy. Every competitor has YOLO+gaze.
- No published pricing → every sales conversation starts at zero
- No SOC 2, no DPA, no privacy policy hosted → procurement blocker for any enterprise deal
- Vanilla-JS 7,000-line dashboard looks "homemade" to IT buyers and is the only continuous source of XSS bugs
- Single droplet on DigitalOcean — first board exam = first ops crisis
- One founder = unfundable at Series A regardless of product quality

---

## Top 10 highest-ROI improvements (ranked)

| # | Improvement | Effort | Why this rank |
|---|---|---|---|
| 1 | Hire / find a co-founder (sales OR engineering) | high | Solves bus factor. Halves cycle. Tier-1 VC filter. |
| ~~2~~ | ~~Pricing page + 3 plans + Razorpay-wired checkout~~ | ✅ Done | Live on `/pricing` (₹149 / ₹999 / ₹2,499) |
| 3 | Windows code signing (Azure Trusted Signing $10/mo) | 2-3 days | Unblocks 70% of TAM (school IT) |
| 4 | Rewrite hero + demo around phone-cam + AI grading | 0 (copy) | Generic AI proctoring positioning is fatal |
| 5 | 3 case-study pages with named schools | 2 weeks (need school) | Schools buy from schools |
| ~~6~~ | ~~Migrate dashboard to React (kill vanilla JS)~~ | ✅ Done (Phase 6.1) | 13 panels migrated, XSS class eliminated |
| 7 | k6 / locust load test, 500 concurrent students | 2 days | "300 concurrent" is unmeasured |
| 8 | DPA template + privacy policy + DPDP one-pager | 1 week (legal) | Procurement blocker for any ₹2L+ contract |
| 9 | LMS integrations (Canvas + Moodle + Google Classroom) | 3-4 weeks | Classroom roster sync done; grade passback B1+B2 pending (see `docs/OAUTH_SETUP.md`) |
| 10 | Backup restoration drill + hot-standby droplet | 1 week | Untested backups are hope, not a strategy |

**3 of 10 shipped.** Remaining critical path: co-founder, Windows
signing, hero rewrite. Then case studies + load test + DPA pack
unblock procurement.

---

## Quick wins (<1 day each)

- ✅ ~~Publish `/pricing` route~~ — live at `website/src/pages/Pricing.jsx`, plans ₹149/₹999/₹2,499
- ✅ ~~Add CI step: `cd website && npm ci && npm run build`~~ — `.github/workflows/test.yml`
- ✅ ~~Demo video callouts~~ — re-tuned for all 6 scenes
- ✅ ~~Drop `python-jose` from `requirements.txt`~~ — gone; app fully on PyJWT
- ✅ ~~Warn on short SECRET_KEY~~ — boot warning fires in dev (`[boot] SUPABASE_JWT_SECRET is N chars …`)
- ⚠️ **Hero rewrite** — partial; could lean harder on phone-cam + AI grading + DPDP (3 hr)
- ⚠️ **Drop dead jose suppress filter** in `main.py` — function check shows no `from jose` in app code, suppress filter probably no longer needed (15 min)
- ⚠️ **Migrate 6 test files off jose** — `tests/conftest.py`, `test_auth_and_sessions.py`, `test_lti_edge_cases.py`, `test_pending_verifications_filter.py`, and 2 others still import `from jose import jwt as jose_jwt` (30 min sed swap to `import jwt as jose_jwt`)
- ⚠️ **Wire one-shot Razorpay checkout button into a real page** — `RazorpayCheckoutButton.jsx` exists, never imported anywhere. Wire to a credit-top-up or pay-per-exam page when use case crystallises (2 hr)
- ⚠️ **Status page at status.procta.net via Uptime Robot** (3 hr) — operational, no code
- ⚠️ **Pre-fill a CAIQ-lite security questionnaire** (half day) — needed before first enterprise sale
- ⚠️ **Verify Sentry alert routing actually reaches your phone** (1 hr) — operational
- ⚠️ **Cap dashboard tables at 200 rows + paginate** (2 hr) — needed for cohorts >500 students

---

## Deep technical improvements

### Architecture

- **Dashboard React migration** — single biggest tech-debt item. 7,000 lines of `innerHTML = ${template}` → modern component model. Reuse design system from `website/`. 3-4 weeks, ideally 2 engineers.
- **Multi-region deploy** — DigitalOcean App Platform or Fly.io. At minimum: hot standby droplet + Cloudflare LB. ₹4,000/month for the second droplet.
- **Background job queue maturity** — Redis RQ shipped, but PDF generation, email sends, LLM grading, and scorecard ZIPs still mostly inline. Move all blocking work to workers.
- **Finish `dependencies.py` migration** — currently a re-export hub with deprecation warning fires constantly. Delete it once nothing imports from it.
- **Silent except audit** — 97 unnamed `except Exception:` blocks. Most intentional (cache, logger fallbacks); add `logger.debug(exc_info=True)` so production debugging isn't blind.
- **Single PyJWT** — finish migration off `python-jose` (LTI module already done; test helpers still import it).

### Performance

- **Live frame rate** — drop dashboard mirroring from 30 fps to 5 fps; keep 30 fps only for the AI pipeline inside Electron. 90% bandwidth saved.
- **SSE init payload** — paginate. Top 50 by risk first; lazy-load rest on scroll.
- **Dashboard table virtualisation** — `react-window` or `IntersectionObserver` lazy rows.
- **LLM grading concurrency** — currently serial inside batches; move to worker queue.
- **Score recalc cache** — already partial; extend to 5 min TTL per (teacher_id, exam_id).

### Security & reliability

- ✅ ~~**JWT-in-URL** in `renderer/index.html` chat WS~~ — fixed; uses `new WebSocket(url, [token])` subprotocol header pattern
- ✅ ~~**CSRF tokens** absent on state-changing dashboard endpoints~~ — shipped in Phase 2; mandatory for all JWT-authed POST/PUT/DELETE
- ✅ ~~**Vanilla JS dashboard XSS class**~~ — eliminated by Phase 6 React migration (13 panels, all JSX-escaped)
- ⚠️ **Test credentials** in `.env` (Razorpay test_*, TOTP encryption key, test SUPABASE_JWT_SECRET) — rotate before any production traffic
- ⚠️ **Rate-limit coverage audit** — `@limiter.limit` count: 51 in auth.py, 13 in admin.py, 31 in exam.py. Spot-check the remaining 19 routers for any state-changers without limits
- **Retries with backoff** on Razorpay + Supabase calls (`tenacity`)
- **Circuit breaker** around LLM provider — Groq outage shouldn't hang grading API
- **Separate `/health/liveness` vs `/health/readiness`** — Kubernetes-compatible if you ever migrate

### Testing

- **Stratify** unit vs integration vs e2e with pytest markers
- **Add browser smoke tests** — playwright excluded from CI today; add one happy-path
- **Weekly load test** against staging — k6 / locust

---

## Feature roadmap (prioritised by school-procurement leverage)

### Immediate (this week)
- Pricing page + Razorpay checkout wire-up
- Windows code signing
- Hero rewrite
- Status page

### 30 days
- Pre-built CAIQ + DPDP compliance pack
- 3 LMS integrations (Canvas / Moodle / Google Classroom)
- Live load test + tuning
- Dashboard pagination + virtualisation
- Bulk exam scheduling (clone to N sections × M slots)
- Webhook outbound (let SIS systems subscribe to events)
- **Co-founder or first hire** (engineering OR sales)

### 90 days
- React dashboard rewrite
- Mobile PWA (camera-only proctoring, low-stakes)
- AI exam generator from syllabus PDF
- Inbuilt coding questions (Pyodide for Python, LLM-judged for C++/Java)
- Adaptive testing (IRT-based difficulty)
- 2FA for teacher login (TOTP)
- **First 3 paying schools signed** — case studies for the round

### 1 year (Series A bar)
- Federated SSO for K-12 districts (SAML + OIDC)
- Native iOS + Android apps
- Teacher AI assistant ("chat with your gradebook")
- Cross-cohort plagiarism on short answers (embedding cosine sim)
- Live invigilator marketplace (outsourced human review, two-sided revenue)
- Public API + SDK
- **20-30 schools + ₹3-5 cr ARR → Series A ₹15-30 cr at ₹100-150 cr post**
- Expand to Southeast Asia (Vietnam, Indonesia — similar exam-heavy systems, no compliant local player)

---

## Risks (live, must mitigate)

| Risk | Severity | Mitigation |
|---|---|---|
| **Bus factor 1** | Critical | Find co-founder OR first engineering hire within 6 months |
| **Test Razorpay + TOTP keys in `.env`** | High | Rotate to live keys + production-grade `TOTP_ENCRYPTION_KEY` before any real payment / 2FA enrollment |
| **DPDP non-compliance** | High | DPA template + privacy policy hosted + `auth_events` audit log ✅ shipped, 90-day retention cron still TODO |
| **Single droplet ops failure** | High | Hot-standby droplet + DNS failover; backup restoration drill done in daylight |
| ~~**Vanilla JS dashboard XSS**~~ | ✅ Resolved | React migration shipped in Phase 6; 13 panels in `app/dashboard-ui/` |
| **Generic "AI proctoring" positioning** | Medium | Lead with phone-cam + AI grading + DPDP; not features in general |
| **Procurement cycle stalls without case studies** | Medium | Sign 1 named pilot, publish case study, even if free |
| ~~**Pricing undefined**~~ | ✅ Resolved | `/pricing` live with 3 tiers (₹149 / ₹999 / ₹2,499) |
| **Email verification migration-day blast** | Medium (one-time) | Existing users force re-verify on next login; ~1-2 weeks of support tickets expected. Email blast must warm Resend domain first |
| **30-day TOTP grace expires for users on Mon morning before board exam** | Medium | Nightly cron should shift `totp_grace_started_at` so expiry never falls within 48h of a scheduled exam |

---

## Notes (operational)

- Redis with Append-Only File persistence is the only stateful service. RQ workers process email + report jobs.
- Caddy reverse-proxies HTTPS with auto-renewing Let's Encrypt certificates.
- Screenshots auto-delete after 90 days (`SCREENSHOT_RETENTION_DAYS`).
- Room-camera frames auto-delete after 24 hours (FERPA / DPDP shorter retention).
- All HTML surfaces load the design token system; 16 of 16 panels on Phase 2 layout.
- Razorpay test credentials in `.env` — rotate to live keys before any real payment.
- `TOTP_ENCRYPTION_KEY` in `.env` — rotate before production 2FA rollout.
- Strategic snapshot in memory: `~/.claude/.../memory/strategic_audit_2026_05.md`
- Auth-baseline residuals in memory: `~/.claude/.../memory/audit_residuals_2026_05.md`
- OAuth + Classroom setup walkthroughs: `docs/OAUTH_SETUP.md`
- Test suite: 406 passed, 17 skipped, 0 failures. Runs in ~6s.
- DB migrations applied to prod (2026-05-14): phase31-35 (auth baseline) + phase41-42 (RLS on new tables) + phase24 (scorecard claim TTL) + phase40 (grading audit). All confirmed via Supabase MCP list_migrations.

---

## CTO Audit Addendum (2026-05-13)

*Fresh analysis from a codebase audit conducted 2026-05-13 covering product, market, code, and business dimensions. Complements the Strategic Roadmap above — does not repeat it.*

### Competitive pricing delta (the real weapon)

| Procta | ProctorU | Honorlock | Mettl |
|--------|----------|-----------|-------|
| ₹5/student (Starter ÷ 30) | $15-30/student (~₹1,200) | $8-15/student (~₹650) | ₹150-500/student |
| Self-hosted, DPDP-compliant | US-hosted, no DPDP | US-hosted, no DPDP | Indian, cloud-only |
| Phone cam room monitoring | No | No | No |

**₹5 vs ₹1,200 per student is not a feature fight — it's a market structure advantage.** No competitor can match this without changing their cost base. The challenge is that no school knows this price exists (no pricing page).

### Three gaps the roadmap doesn't address

1. **Self-serve trial → paid loop** — Signup creates a 14-day trial but zero automated onboarding emails, no usage nudges, no upgrade prompts. Every SaaS benchmark shows trial-to-paid conversion doubles with a 5-email sequence. This is \$0 engineering and immediate revenue leverage.

2. **Mobile is the #1 deal blocker, not LTI** — Schools ask for BYOD before they ask for Canvas integration. A lightweight PWA that streams camera frames without kiosk-mode (low-stakes exams) would close more deals than any LMS feature.

3. **Hindi/regional language** — "Hindi UI coming Q3 2026" is on the marketing site already. Shipping this would differentiate against every competitor for tier-2/3 institutions where faculty English is the buying barrier.

### Coaching institutes: the wedge market

Allen, Aakash, Vedantu, Physics Wallah run **more proctored exams per month than most universities run per year**. They have:
- Edtech budgets (₹50L-2Cr/year for proctoring)
- Zero loyalty to incumbents (Mettl is expensive, ProctorU doesn't sell to India)
- Willingness to try self-hosted (they all have DevOps teams)
- Brand names that become case studies

**One coaching chain deal at ₹50L ARR is the single highest-leverage action in the next 90 days.**

### Solo founder constraint

482 passing tests, 0 warnings, 172/172 routes rate-limited, mandatory CSRF, structured JSON logging — the codebase is Series A quality. The **bus factor is the only thing keeping this from being fundable.** A co-founder (engineering OR sales) within 6 months changes the fundraising narrative from "single founder risk" to "strong founding team."

### Verdict

Would fund at pre-seed (₹1.5-3Cr) contingent on: (a) pricing page live within 1 week, (b) co-founder search started within 30 days, (c) mobile PWA started within 60 days. The product risk is already retired — the remaining risk is distribution, not code.

---

## What's Left (Priority Order)

| Priority | Item | Effort | Depends on |
|----------|------|--------|------------|
| **High** | OAuth + Turnstile manual setup (Supabase dashboard + Cloudflare) | 30 min | Walkthrough in `docs/OAUTH_SETUP.md` |
| **High** | Wire OAuth buttons + Turnstile widget on `dashboard.html` teacher login | 1 hr | OAuth backend already deployed |
| **High** | Run email-verification migration-day blast (warm Resend domain first) | half day | DKIM/SPF/DMARC verified |
| **Medium** | Migrate 6 test files off `jose` (sed swap) + drop dead suppress filter in main.py | 30 min | — |
| **Medium** | Hero copy rewrite — lead with phone-cam + AI grading + DPDP | 3 hr | — |
| **Medium** | `auth_events` 90-day retention cron | 2 hr | — |
| **Medium** | TOTP-grace-expiry shift cron (never within 48h of a scheduled exam) | 3 hr | — |
| **Low** | macOS/Windows code signing (EV cert) | 1 week | Apple Developer acct + cert |
| **Low** | Google Classroom API — wire grade passback + auto-create CourseWork (B1+B2 in `docs/OAUTH_SETUP.md`) | 2 days | Manual setup done |
| **Low** | Rotate test Razorpay + TOTP keys to live values before any real payment | 15 min | — |
| **Backlog** | Mobile PWA / app — BYOD phone exam-taking | 2-3 months | — |
| ~~AI audit trail — bulk review UI~~ | ✅ Shipped (Phase 6.2) | — | — |
| ~~React dashboard rewrite~~ | ✅ Shipped (Phase 6.1) | — | — |
