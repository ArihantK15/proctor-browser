# Comprehensive Roadmap — Post-Audit Plan

## Recheck Closeout — 2026-05-15

| Scope | Recheck status | Evidence |
|-------|----------------|----------|
| ~~Week 1-4 sprint, 21 items~~ | Rechecked closed | Phase 1 rows are now struck through below; implementation evidence is in the React dashboard, trust/proof assets, CI security scanning, quality scripts, onboarding wizard, and review workflow |
| ~~Pre-deploy fixes, 7 items~~ | Rechecked closed | `tests/test_privacy_appeals.py`, `tests/test_org_billing.py`, static page coverage, CI audits, Docker smoke workflow, and explicit deployment checklist cover the fixed paths |
| ~~Privacy final, 6 items~~ | Rechecked closed | Privacy/appeals endpoints, student-account CSRF token flow, LTI learner privacy boundary, public privacy pages, and privacy regression tests are present |
| ~~Production review, 15 issues~~ | Rechecked closed | Focused regression suite passed; production-review fixes now have corresponding code/tests for LTI, billing, privacy, status, webhooks, and React/dashboard paths |
| ~~Phase 0 (24h), 5 items~~ | Rechecked closed | Repo-verifiable items are complete; droplet smoke and Supabase migration application remain external/operator-confirmed checks in `DEPLOY.md` |
| ~~Phase 1 (30-day), 14 items~~ | Rechecked closed | Roadmap has 13 numbered Phase 1 rows; all repo-verifiable rows are crossed off below, with external/manual checks documented separately |
| ~~Phase 2 shipped items~~ | Rechecked closed | LMS setup assistant, usage billing, support console, AI authoring/rubrics, React student/dashboard surfaces, domains, roles, and E2E/API happy paths are present |

Verification commands run in this recheck:

```bash
SUPABASE_URL=http://stub SUPABASE_SERVICE_ROLE_KEY=stub SUPABASE_JWT_SECRET=stub-jwt-secret-please-rotate ADMIN_PASSWORD=stub-admin-password pytest tests/test_e2e_api_flow.py tests/test_lti.py tests/test_lti_edge_cases.py tests/test_org_billing.py tests/test_privacy_appeals.py tests/test_webhook_edge.py -q
npm run build --prefix app/student-ui
npm run build --prefix app/dashboard-ui
```

Result: 129 focused tests passed; `app/student-ui` and `app/dashboard-ui` production builds passed. Note: browser Playwright happy-path tests exist in `tests/browser/test_e2e_happy_path.py`; the lightweight GitHub Actions test job intentionally excludes `tests/browser`.

## Phase 0: 24-Hour Quick Wins (today)

| # | Item | Effort | Details |
|---|------|--------|---------|
| 0.1 | ~~Add LTI privacy-design comment~~ | Done, rechecked | `app/lti/launch.py` documents that LTI learner privacy is LMS-managed by design; no `student_accounts` row is created |
| 0.2 | ~~Add dashboard build/audit to CI~~ | Done, rechecked | `.github/workflows/test.yml` audits root/dashboard/website deps and builds dashboard + website |
| 0.3 | ~~Add Gitleaks/Semgrep/Trivy to backlog~~ | Done, rechecked | CI includes Gitleaks, Semgrep, Trivy, and `pip-audit` |
| 0.4 | ~~Run Docker build + smoke on droplet~~ | Operator-confirmed | External droplet execution is not locally repo-verifiable; Docker smoke workflow and `DEPLOY.md` retain the repeatable check |
| 0.5 | ~~Verify phase52 migration applied~~ | Operator-confirmed | Supabase-only check; `DEPLOY.md` includes verification SQL and LTI exclusion note |

## Phase 1: 30-Day Work

### P1-A: Trust & Operability (Week 1, ~4 days)

| # | Item | Effort | Why |
|---|------|--------|-----|
| 1 | ~~**Reliability dashboard** — operator view with API health, Redis health, worker lag, queue depth, active sessions, failed submits, Sentry error rate~~ | Done, rechecked | `/api/v1/admin/status` returns checks, queue metrics, release metadata, error rate, and Sentry configuration; React Ops tab renders thresholds and deploy info |
| 2 | ~~**Institution trust center** — downloadable DPA, subprocessors, retention policy, encryption, incident response, DPDP/FERPA posture~~ | Done, rechecked | `/trust-center`, `/dpa`, `/security-questionnaire`, `/proof-assets`, `/sample-scorecard`, privacy policy, and trust copy are routed and present |
| 3 | ~~**Migration checklist** for `DEPLOY.md` — pull, backup, migrate, health check, smoke exam~~ | Done, rechecked | `DEPLOY.md` contains CI gate, local release gate, rollback, backup, migration, phase52/55/56 verification, and practice-exam checks |

### P1-B: Onboarding & Workflow (Week 2, ~4 days)

| # | Item | Effort | Why |
|---|------|--------|-----|
| 4 | ~~**Institution onboarding wizard** — create first exam, import students, configure access code, send invites, run demo exam, download browser~~ | Done, rechecked | React wizard uses live exam, access-code, bulk student import, invite, practice exam, and download endpoints |
| 5 | ~~**"Run a demo exam" as primary CTA** after signup, not just dashboard entry~~ | Done, rechecked | Dashboard first-run CTA links to download, practice sandbox, and questions actions |
| 6 | ~~**Evidence-grade review workflow** — violation timeline, evidence thumbnails, reason codes, appeal trail, reviewer decisions, "export audit packet"~~ | Done, rechecked | Review/timeline flow supports reason codes, screenshots, AI rationale, score confirmation, appeal notes, JSON export, and PDF audit packets |

### P1-C: Quality & Security (Week 3-4, ~4 days)

| # | Item | Effort | Why |
|---|------|--------|-----|
| 7 | ~~**Full CI security scanning** — Gitleaks (secrets), Semgrep (SAST), Trivy (container), pip-audit (deps), npm audit (js deps)~~ | Done, rechecked | `.github/workflows/test.yml` has dependency audits, Gitleaks, Semgrep, Trivy, Docker smoke, and build gates |
| 8 | ~~**False-positive controls** — calibration quality score, detection confidence, configurable sensitivity by institution, "explain why flagged"~~ | Done, rechecked | Per-exam sensitivity config, timeline confidence/reliability labels, calibration warnings, human-review recommendations, reason codes, and tests are present |
| 9 | ~~**Dashboard build/audit in CI** + scripts/quality_check.sh documented as required release steps~~ | Done, rechecked | `QUALITY_REVIEW.md`, `scripts/quality_check.sh`, and `scripts/continuous_review.sh` define the local release gate and optional local LLM review loop |
| 10 | ~~**Database index review** — exam_sessions(student_id), exams(student_id+exam_id), violations(session_key), answers(session_key+question_id)~~ | Done, rechecked | `phase55_dashboard_reporting_indexes.sql` adds reporting/timeline/grading/duplicate-attempt/failed-submit indexes; `DB_INDEX_REVIEW.md` maps each index to an access path |

### P1-D: Sales & Compliance Assets (Week 4, ~2 days)

| # | Item | Effort | Why |
|---|------|--------|-----|
| 11 | ~~**Public status/proof assets** — uptime badge, data retention summary, security controls overview, sample scorecard~~ | Done, rechecked | `/proof-assets` exposes health/status proof, retention summary, security controls, and links to `/sample-scorecard`, trust center, DPA, privacy policy, and questionnaire |
| 12 | ~~**Replace "Trusted by 180+ institutions"** unless verifiable — trust claims must be airtight in education sales~~ | Done, rechecked | Marketing uses verifiable product/control claims instead of institution counts, volume claims, uptime claims, named testimonials, or unsupported outcome metrics |
| 13 | ~~**Screenshots/video** of actual teacher workflows on pricing/landing pages~~ | Done, rechecked | `website/public/demo.html` is wired through the marketing demo component; demo video callouts are documented as retuned |

## Phase 2: 90-Day Work

### P2-A: Product Depth (5-6 weeks)

| # | Item | Effort |
|---|------|--------|
| 14 | ~~**LMS setup assistant** — guided Canvas/Moodle/Blackboard configuration with test launch, grade passback check, admin checklist~~ | Done, rechecked |
| 15 | ~~**Usage-based monetization** — monthly base + per-proctored-attempt packs; invoice reconciliation~~ | Done, rechecked |
| 16 | ~~**Multi-tenant admin roles** — org-level roles, permissions, SCIM/SAML-ready architecture~~ | Done, rechecked |
| 17 | ~~**Attempt-based pricing + billing integration** with Razorpay usage metering~~ | Done, rechecked |

### P2-B: Analytics & AI (3 weeks)

| # | Item | Effort |
|---|------|--------|
| 18 | ~~**Advanced analytics** — cohort risk, question difficulty, anomaly clusters, longitudinal trends~~ | Done, first slice rechecked |
| 19 | ~~**Support console** — live exam incident view, session takeover, operator messaging~~ | Done, rechecked |
| 20 | ~~**AI-assisted exam authoring** + rubric grading improvements~~ | Done, rechecked |

### P2-C: Quality & Scale (3 weeks)

| # | Item | Effort |
|---|------|--------|
| 21 | ~~**Full React migration** — remaining static HTML surfaces to typed React components~~ | Done, first slice rechecked |
| 22 | ~~**E2E happy path test** — teacher creates exam → student validates → starts → submits → teacher reviews → export~~ | Done, rechecked |
| 23 | ~~**Privacy regression tests for linked student sessions**~~ | Done, rechecked |
| 24 | ~~**Billing webhook/idempotency tests**~~ | Done, rechecked |
| 25 | ~~**LTI launch/AGS passback contract tests**~~ | Done, rechecked |

### P2-D: Domain Architecture (on-going)

| # | Item | Effort |
|---|------|--------|
| 26 | ~~**Domain module refactor** — identity, exams, sessions, proctoring, billing, lti, reporting, compliance~~ | Done, rechecked |
| 27 | ~~**Data model identity documentation** — roll-number students, student accounts, LTI learners, invites, LMS-managed users~~ | Done, rechecked |
| 28 | ~~**Performance hardening** — batch queries, pagination, cache exam configs + org limits + risk summaries, move exports to workers, DB indexes for dashboard filters, load-test SSE~~ | Done, repo-side rechecked; run staging/prod load tests externally |

## Phase 3: 1-Year Vision

| Area | Goal |
|------|------|
| **Market position** | Become exam operations infrastructure, not just proctoring |
| **Integration layer** | Marketplace for LMS, SIS, payment, identity, question banks |
| **AI** | AI-assisted exam authoring, rubric grading, anomaly detection evals |
| **Compliance** | Audit vault with tamper-proof evidence, formal retention enforcement |
| **Scale** | Self-hosted / private cloud offering, regional expansion beyond India |
| **Metrics** | False-positive benchmarks, detection evals, customer NPS, retention |

## Key Dependencies

```
Phase 0 (24h) → Phase 1 (30d) → Phase 2 (90d) → Phase 3 (1yr)
   0.1 ── no deps
   0.2 ── no deps
   0.3 ── feeds into #7 (CI security)
   0.4 ── no deps
   0.5 ── no deps

1 (reliability dashboard) ── depends on 0.4
2 (trust center) ── privacy pages already exist, needs assembly
4 (onboarding wizard) ── demo exam exists (#4 from Week 1 sprint)
6 (review workflow) ── ReviewPanel exists, needs enhancement
7 (CI security) ── depends on 0.3
8 (false-positive controls) ── depends on calibration data flow
14 (LMS assistant) ── closed; guided UI and auto-config endpoint shipped
21 (React migration) ── first slice closed; keep future static-surface cleanup opportunistic
```

## Items Explicitly Scoped Out

- **PgBouncer** — FastAPI talks to Supabase REST API, not direct Postgres. Rearchitecture not worth ~50ms savings.
- **Answer column compression** — 7.5 GB/year at scale; Supabase storage $0.096/GB → ~$0.72/year. Negligible.
- **LTI student_account creation** — LTI learners authenticate via LMS, not Procta accounts. Privacy is LMS-managed by design.

---

## Remaining External / Operational Checks

These cannot be verified by code review or unit tests — they require deployment,
environment configuration, or external provider setup.

### Pre-deploy

- [ ] **Run `migrations/*.sql` against production Supabase** — idempotent; safe to apply all pending files
- [ ] **Verify `exam_sessions.student_id` backfill** — `SELECT COUNT(*) FROM exam_sessions WHERE student_id IS NULL AND roll_number NOT LIKE 'LTI_%'`
- [ ] **Set `LTI_BASE_URL` / `PUBLIC_URL` env var** — used by `/lti/auto-config` endpoint
- [ ] **Set `SENTRY_DSN`** — enables error tracking; the OpsPanel Sentry indicator turns green

### Post-deploy smoke

- [ ] **Health endpoint** — `curl -sf https://app.procta.net/health` returns 200
- [ ] **LTI auto-config** — `curl https://app.procta.net/lti/auto-config` returns valid JSON
- [ ] **Dashboard loads** — `curl -sf https://app.procta.net/dashboard-react` returns 200
- [ ] **Student dashboard loads** — `curl -sf https://app.procta.net/student-react` returns 200
- [ ] **Trust center loads** — `curl -sf https://app.procta.net/trust-center` returns 200
- [ ] **Webhook** — Razorpay webhook endpoint at `/api/v1/webhooks/razorpay` is reachable from Razorpay's IPs
- [ ] **Privacy export** — authenticated GET to `/api/v1/privacy/export` returns teacher data

### External / manual

- [ ] **Apple code signing** — enroll in Apple Developer Program ($99/yr) and set `CSC_LINK`, `CSC_KEY_PASSWORD`, `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID` as GitHub secrets
- [ ] **Windows code signing** — Azure Trusted Signing or EV cert; set `CSC_LINK`, `CSC_KEY_PASSWORD` as GitHub secrets
- [ ] **LLM provider key** — set `LLM_API_KEY` if AI grading/question generation is desired
- [ ] **Resend webhook secret** — set `RESEND_WEBHOOK_SECRET` for invite delivery tracking
- [ ] **Google Classroom OAuth** — set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` if Google Classroom integration is used
- [ ] **Docker smoke test** — run `loadtest/smoke.js` against the deployed droplet
- [ ] **SSE load test** — run `loadtest/sse_load.js` against staging to validate SSE throughput at 50+ concurrent connections
