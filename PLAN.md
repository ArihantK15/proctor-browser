# Procta — Engineering & Business Roadmap

> Updated 2026-05-13. Covers Phases 0–4 (shipped) and the post-product
> strategic roadmap. Phases 0–4 are kept as history; strategy starts
> at "Strategic Roadmap" below.

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
| 2 | Pricing page + 3 plans + Razorpay-wired checkout | 1 week | Zero deals close without prices |
| 3 | Windows code signing (Azure Trusted Signing $10/mo) | 2-3 days | Unblocks 70% of TAM (school IT) |
| 4 | Rewrite hero + demo around phone-cam + AI grading | 0 (copy) | Generic AI proctoring positioning is fatal |
| 5 | 3 case-study pages with named schools | 2 weeks (need school) | Schools buy from schools |
| 6 | Migrate dashboard to React (kill vanilla JS) | 3-4 weeks | Removes XSS class, enables 3× feature velocity |
| 7 | k6 / locust load test, 500 concurrent students | 2 days | "300 concurrent" is unmeasured |
| 8 | DPA template + privacy policy + DPDP one-pager | 1 week (legal) | Procurement blocker for any ₹2L+ contract |
| 9 | LMS integrations (Canvas + Moodle + Google Classroom) | 3-4 weeks | LTI scaffolding already half-built |
| 10 | Backup restoration drill + hot-standby droplet | 1 week | Untested backups are hope, not a strategy |

---

## Quick wins (<1 day each, ship this week)

- Publish `/pricing` route — Razorpay subscription buttons already exist (2 hr)
- Hero rewrite: lead with phone-cam, AI grading, or DPDP compliance (3 hr)
- Fix 4 audit hygiene items: drop dead jose suppress, drop python-jose from `requirements.txt`, migrate 4 test files off jose, warn on short SECRET_KEY (45 min)
- Add CI step: `cd website && npm ci && npm run build` (15 min)
- Wire `/checkout` button into a real page — `/buy-credits` or "Top up credits" (2 hr)
- Status page at status.procta.net via Uptime Robot (3 hr)
- Pre-fill a CAIQ-lite security questionnaire (half day)
- Verify Sentry alert routing actually reaches your phone (1 hr)
- Cap dashboard tables at 200 rows + paginate (2 hr)
- Tighten demo video callouts — last review showed alignment issues (3 hr)

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

- **JWT-in-URL** still present in `renderer/index.html` chat WS — needs subprotocol header
- **CSRF tokens** absent on state-changing dashboard endpoints
- **Test credentials** in `.env` — rotate before any production traffic
- **Rate-limit coverage audit** — verify every state-changing endpoint has `@limiter.limit`
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
| **Test Razorpay creds in `.env`** | High | Rotate before prod traffic; move to secret manager (DO secrets / 1Password CLI) |
| **DPDP non-compliance** | High | DPA template + privacy policy hosted + audit log table by month 2 |
| **Single droplet ops failure** | High | Hot-standby droplet + DNS failover; backup restoration drill done in daylight |
| **Vanilla JS dashboard XSS** | Medium | React migration (Phase 5) closes the class permanently |
| **Generic "AI proctoring" positioning** | Medium | Lead with phone-cam + AI grading + DPDP; not features in general |
| **Procurement cycle stalls without case studies** | Medium | Sign 1 named pilot, publish case study, even if free |
| **Pricing undefined** | Medium | Publish 3 plans this week, iterate on real signups |

---

## Notes (operational)

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

- Redis with Append-Only File persistence is the only stateful service. RQ workers process email + report jobs.
- Caddy reverse-proxies HTTPS with auto-renewing Let's Encrypt certificates.
- Screenshots auto-delete after 90 days (`SCREENSHOT_RETENTION_DAYS`).
- Room-camera frames auto-delete after 24 hours (FERPA / DPDP shorter retention).
- All HTML surfaces load the design token system; 16 of 16 panels on Phase 2 layout.
- Razorpay test credentials in `.env` — rotate to live keys before any real payment.
- Strategic snapshot lives in memory: `~/.claude/.../memory/strategic_audit_2026_05.md`.
- Test suite: 482 passed, 21 skipped, 0 failures. Runs in ~11s. **0 deprecation warnings.**
