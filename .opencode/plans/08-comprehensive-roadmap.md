# Comprehensive Roadmap — Post-Audit Plan

## Phase 0: 24-Hour Quick Wins (today)

| # | Item | Effort | Details |
|---|------|--------|---------|
| 0.1 | Add LTI privacy-design comment | Done | `app/lti/launch.py:500` documents that LTI learner privacy is LMS-managed by design; no `student_accounts` row is created |
| 0.2 | Add dashboard build/audit to CI | Done | `.github/workflows/test.yml` now audits root/dashboard/website deps and builds dashboard + website |
| 0.3 | Add Gitleaks/Semgrep/Trivy to backlog | Done + started | `PLAN.md` has the post-audit queue and CI now includes Gitleaks, Semgrep, Trivy, and `pip-audit` |
| 0.4 | Run Docker build + smoke on droplet | Manual | Must be run on production droplet after pull; tracked in `DEPLOY.md` pre-deploy checklist |
| 0.5 | Verify phase52 migration applied | Manual | Supabase-only check; `DEPLOY.md` includes verification SQL and LTI exclusion note |

## Phase 1: 30-Day Work

### P1-A: Trust & Operability (Week 1, ~4 days)

| # | Item | Effort | Why |
|---|------|--------|-----|
| 1 | **Reliability dashboard** — operator view with API health, Redis health, worker lag, queue depth, active sessions, failed submits, Sentry error rate | In progress | First slice shipped: backend status metrics + React Ops tab. Remaining: Sentry error rate, retry details, deploy version, thresholds |
| 2 | **Institution trust center** — downloadable DPA, subprocessors, retention policy, encryption, incident response, DPDP/FERPA posture | 1 day | Procurement blocker for any ₹2L+ contract |
| 3 | **Migration checklist** for `DEPLOY.md` — pull, backup, migrate, health check, smoke exam | 1 hr | Prevents deploy-day surprises |

### P1-B: Onboarding & Workflow (Week 2, ~4 days)

| # | Item | Effort | Why |
|---|------|--------|-----|
| 4 | **Institution onboarding wizard** — create first exam, import students, configure access code, send invites, run demo exam, download browser | Done, first slice shipped | "First successful exam" is the core activation event. React wizard now uses live exam, access-code, bulk student import, and invite endpoints |
| 5 | **"Run a demo exam" as primary CTA** after signup, not just dashboard entry | Done, first slice shipped | Dashboard now shows a first-run practice CTA with download, practice sandbox, and questions actions. Onboarding now points to the real student practice page instead of a dead dashboard hash |
| 6 | **Evidence-grade review workflow** — violation timeline, evidence thumbnails, reason codes, appeal trail, reviewer decisions, "export audit packet" | Done, first slice shipped | Teachers can now open session evidence from pending grade review, inspect timeline/reason codes/screenshots/AI rationale, confirm score, add appeal resolution notes, and export JSON or PDF audit packets |

### P1-C: Quality & Security (Week 3-4, ~4 days)

| # | Item | Effort | Why |
|---|------|--------|-----|
| 7 | **Full CI security scanning** — Gitleaks (secrets), Semgrep (SAST), Trivy (container), pip-audit (deps), npm audit (js deps) | Done | Enterprise security baseline is wired in CI |
| 8 | **False-positive controls** — calibration quality score, detection confidence, configurable sensitivity by institution, "explain why flagged" | 1.5 days | Turns AI from scary to accountable |
| 9 | **Dashboard build/audit in CI** + scripts/quality_check.sh documented as required release steps | Done | `QUALITY_REVIEW.md`, `scripts/quality_check.sh`, and `scripts/continuous_review.sh` define the local release gate and optional local LLM review loop |
| 10 | **Database index review** — exam_sessions(student_id), exams(student_id+exam_id), violations(session_key), answers(session_key+question_id) | 4 hr | At 100x data, reporting becomes bottleneck |

### P1-D: Sales & Compliance Assets (Week 4, ~2 days)

| # | Item | Effort | Why |
|---|------|--------|-----|
| 11 | **Public status/proof assets** — uptime badge, data retention summary, security controls overview, sample scorecard | 1 day | Pasted into every sales deck |
| 12 | **Replace "Trusted by 180+ institutions"** unless verifiable — trust claims must be airtight in education sales | 1 hr | Legal/compliance risk |
| 13 | **Screenshots/video** of actual teacher workflows on pricing/landing pages | 4 hr | Converts 2x better than text |

## Phase 2: 90-Day Work

### P2-A: Product Depth (5-6 weeks)

| # | Item | Effort |
|---|------|--------|
| 14 | **LMS setup assistant** — guided Canvas/Moodle/Blackboard configuration with test launch, grade passback check, admin checklist | 1 week |
| 15 | **Usage-based monetization** — monthly base + per-proctored-attempt packs; invoice reconciliation | 1 week |
| 16 | **Multi-tenant admin roles** — org-level roles, permissions, SCIM/SAML-ready architecture | 2 weeks |
| 17 | **Attempt-based pricing + billing integration** with Razorpay usage metering | 1 week |

### P2-B: Analytics & AI (3 weeks)

| # | Item | Effort |
|---|------|--------|
| 18 | **Advanced analytics** — cohort risk, question difficulty, anomaly clusters, longitudinal trends | 1 week |
| 19 | **Support console** — live exam incident view, session takeover, operator messaging | 1 week |
| 20 | **AI-assisted exam authoring** + rubric grading improvements | 2 weeks |

### P2-C: Quality & Scale (3 weeks)

| # | Item | Effort |
|---|------|--------|
| 21 | **Full React migration** — remaining static HTML surfaces to typed React components | 2 weeks |
| 22 | **E2E happy path test** — teacher creates exam → student validates → starts → submits → teacher reviews → export | 1 week |
| 23 | **Privacy regression tests for linked student sessions** | 2 days |
| 24 | **Billing webhook/idempotency tests** | 2 days |
| 25 | **LTI launch/AGS passback contract tests** | 2 days |

### P2-D: Domain Architecture (on-going)

| # | Item | Effort |
|---|------|--------|
| 26 | **Domain module refactor** — identity, exams, sessions, proctoring, billing, lti, reporting, compliance | 3-4 weeks |
| 27 | **Data model identity documentation** — roll-number students, student accounts, LTI learners, invites, LMS-managed users | 2 days |
| 28 | **Performance hardening** — batch queries, pagination, cache exam configs + org limits + risk summaries, move exports to workers, DB indexes for dashboard filters, load-test SSE | 2 weeks |

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
14 (LMS assistant) ── LTI works, needs guided UI
21 (React migration) ── on-going, no hard dependency
```

## Items Explicitly Scoped Out

- **PgBouncer** — FastAPI talks to Supabase REST API, not direct Postgres. Rearchitecture not worth ~50ms savings.
- **Answer column compression** — 7.5 GB/year at scale; Supabase storage $0.096/GB → ~$0.72/year. Negligible.
- **LTI student_account creation** — LTI learners authenticate via LMS, not Procta accounts. Privacy is LMS-managed by design.
