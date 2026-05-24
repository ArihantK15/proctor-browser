# Procta CTO/CEO Project Audit

## A. Executive Summary

Procta solves a real and painful problem: online exams are hard to trust, institutions are slow to adopt heavy LMS/proctoring tools, and teachers need a practical way to run remote exams without becoming IT administrators.

The core product thesis is strong:

> Remote exams with AI proctoring, automated scoring, student resilience, and proof-ready review packets.

The project has serious breadth for its stage:

- Teacher dashboard
- Student exam and dashboard flows
- Lockdown/proctored browser shell
- Autosave and submit resilience
- Plain Postgres/local auth migration
- Razorpay billing
- LTI and Google Classroom integration
- Issue reporting and triage
- Email flows
- Scorecards and PDF exports
- Privacy and appeals workflows
- Load-test tooling
- Background workers and operational health checks

The biggest opportunity is to become the **"Stripe Checkout for remote exams"** for Indian institutions and training companies: fast setup, clean trust artifacts, low IT burden, and transparent pricing.

The biggest threat is execution sprawl. The product currently has too many overlapping surfaces:

- Legacy static dashboard
- React dashboard
- Static student page
- React student dashboard
- Desktop app flows
- Public marketing site
- Many backend routers and services

Some surfaces are ahead of others. That creates role mismatches, duplicated UX, incomplete flows, and launch risk.

The winning move is not to add more features immediately. It is to harden one golden teacher/admin/student journey until it is boringly reliable, then use proctoring proof and load-test data as the sales wedge.

## B. Top 10 Highest-Impact Improvements

### 1. Unify the dashboard architecture

**ROI:** Very high

The project currently has both a legacy `dashboard.html/dashboard-app.js` dashboard and a React `dashboard-ui`. Some features exist in legacy but not React, and role logic diverges.

Pick one canonical path:

- Make React the future and port missing critical flows, or
- Keep legacy canonical until React reaches feature parity.

Running both as "almost production" is expensive and risky.

### 2. Fix role-based product boundaries

**ROI:** Very high

Teacher, org admin, and superadmin should have different products, not just different tab visibility.

Expected role boundaries:

- **Teacher:** exam operations
- **Org admin:** organization, members, billing, org analytics
- **Superadmin:** global maintenance, issues, org oversight, debug

This is essential for trust, security, and usability.

### 3. Ship one complete student journey

**ROI:** Very high

The student experience must be spotless:

1. Register or login
2. See assigned exams
3. Download/open proctored browser
4. Start exam
5. Autosave through network interruptions
6. Reconnect cleanly
7. Submit
8. See history/status

A broken student route or confusing app handoff immediately damages institutional confidence.

### 4. Turn proof into the core product moat

**ROI:** Very high

The winning artifact is not "AI detected cheating." The winning artifact is a defensible exam review packet:

- Timeline
- Screenshots/events
- Risk explanation
- Confidence level
- Teacher notes
- Appeals
- Exportable PDF
- Audit log

This should become the centerpiece of the product.

### 5. Operationalize proctoring at scale

**ROI:** High

Autosave/submit load tests look strong, but the next bottleneck is mixed workload:

- SSE/live dashboards
- Proctoring event ingestion
- Screenshot/frame uploads
- Chat
- Scorecard generation
- Email
- Billing state

Build an ops dashboard around:

- Queue lag
- Event ingestion rate
- DB latency
- Storage growth
- Failed jobs
- Worker health
- Recent API errors

### 6. Consolidate auth/session architecture

**ROI:** High

Local Postgres auth is the right direction. Finish independence from Supabase/OAuth remnants, move toward HttpOnly cookie sessions, keep short-lived access tokens, and ensure role metadata is consistently returned to the frontend.

### 7. Make billing/trial enforcement production-grade

**ROI:** High

Razorpay checkout exists, but pricing only matters if the lifecycle is enforced everywhere:

- Plan limits
- Trial expiry
- Grace periods
- Failed payments
- Cancellations
- Invoices
- Org status
- Billing notifications

Billing should be a system, not just a payment button.

### 8. Reduce backend router sprawl

**ROI:** Medium-high

The routers are functionally separated but organically grown. Move toward domain modules:

- Identity
- Exams
- Proctoring
- Sessions
- Reporting
- Billing
- Integrations
- Ops

Keep routers thin and put business rules into domain services.

### 9. Create a serious QA matrix

**ROI:** Medium-high

Automate browser tests for:

- Teacher signup/login/reset
- Org admin role
- Superadmin role
- Student exam flow
- Billing
- Issue reporting
- LTI launch
- Bad network/reconnect
- Refresh/back/forward behavior
- Mobile layouts

The product surface is too large to rely on manual QA.

### 10. Sharpen ICP and positioning

**ROI:** Medium-high

Do not sell to everyone.

Best wedge:

- Indian colleges
- Coaching institutes
- Certification providers
- Hiring assessment teams

The strongest message is:

> Run trustworthy remote exams without an IT rollout.

That is stronger than generic "AI proctoring."

## C. Quick Wins

- Fix the React dashboard forgot-password link.
- Fix `/student-react` routing or remove links to it.
- Return `org_role` and `org_id` from teacher login and `/auth/me`.
- Make the React dashboard tab matrix match teacher/admin/superadmin roles.
- Add real pagination to React Live/Results.
- Show visible errors when org/billing bootstrap fetches fail.
- Add a production checklist page for health, DB backend, Razorpay, email, workers, Redis, backups, and recent error count.
- Make the pricing page enterprise CTA go to demo request, not dashboard login.
- Add request IDs visibly to frontend error banners.
- Create one demo institution seed script with teacher, admin, superadmin, students, exam, results, issues, and billing state.

## D. Deep Technical Improvements

### Architecture

The codebase has the right pieces, but too many layers coexist:

- FastAPI backend
- Custom Supabase-like `PostgresTable` abstraction
- Legacy static dashboard
- React teacher dashboard
- Static student page
- React student dashboard
- Electron/proctored browser
- Marketing Vite app
- Load-test suite
- Background workers

Recommended target architecture:

```text
app/domains/identity      auth, sessions, roles, org membership
app/domains/exams         exam config, questions, groups, invites
app/domains/proctoring    events, screenshots, camera, calibration, risk
app/domains/reporting     scorecards, review packets, analytics, appeals
app/domains/billing       plans, limits, Razorpay, invoices, usage
app/domains/integrations  LTI, Google Classroom, API keys
app/domains/ops           health, metrics, backups, support console
```

Routers should become thin HTTP adapters. Business logic should live in services/domains. Repositories should own DB access.

### Database and Scaling

For 10x growth:

- Add explicit indexes for every hot query.
- Add server-side pagination everywhere.
- Avoid "fetch all then slice."
- Move screenshot/frame storage to object storage with signed URLs.
- Use Redis/RQ only for moderate async jobs.

Important hot indexes:

```text
exam_sessions(teacher_id, status, exam_id)
violations(session_key, created_at)
answers(session_key)
students(org_id, teacher_id, roll_number)
issues(status, created_at)
```

For 100x growth:

- Partition event/violation tables by time or exam/org.
- Separate write-heavy proctoring ingestion from read-heavy dashboard queries.
- Batch proctoring events client-side.
- Stream dashboard summaries from materialized/cache tables.
- Add read replicas for reporting.
- Build retention policies for frames, screenshots, logs, and raw proctoring signals.

### Performance

Current risk areas:

- Mixed workload under real proctoring is not proven yet.
- React panels risk loading truncated or redundant data.
- Broad exception handling hides degradation.
- Live dashboards need backpressure and reconnect behavior.
- AI grading/review should never block exam submission.

High-impact optimizations:

- Queue all AI scoring, PDF generation, email, reminders, and heavy analytics.
- Cache org/billing/teacher membership for short TTL.
- Precompute dashboard counters per exam.
- Use SSE only for deltas.
- Keep polling fallback bounded and visible.
- Add object storage/CDN for downloadable reports and assets.
- Add structured metrics for p95/p99 by endpoint.

### Security

Strong progress has been made:

- Local auth
- Lockouts
- CSRF
- Key separation
- Rate limits
- Server-side Razorpay verification
- Hashed org invites
- Request IDs

Remaining risks:

- Tokens in localStorage are still XSS-sensitive.
- Public static bundles expose admin UI structure before auth.
- Role metadata mismatch can produce bad frontend authorization UX.
- Superadmin identity depends on env email promotion.
- Proctoring data is sensitive biometric/behavioral data.
- Screenshot/frame retention needs explicit policy and deletion workflow.

Recommended direction:

- Move refresh/session state to HttpOnly, Secure, SameSite cookies.
- Keep short-lived access tokens.
- Make superadmin a DB-backed role with audit trail.
- Add clear retention/deletion controls for evidence.
- Add privacy export/deletion workflows for institutions.

### Code Quality

The project shows high velocity but also founder-speed debt:

- Broad `except Exception` usage
- Multiple frontends with overlapping responsibilities
- Built assets committed alongside source
- Old artifacts/logs/dist files in repo
- Business rules spread across routers, services, and frontend panels
- Legacy and React implementations drifting

Recommended refactors:

- Create a single API client package for React dashboards.
- Centralize role/permission definitions.
- Replace inline panel styles with reusable components.
- Introduce typed response schemas for high-value endpoints.
- Create contract tests for dashboard data APIs.
- Move static legacy dashboard to deprecated once React reaches parity.

### Testing Gaps

Existing tests are a good foundation, but these areas need stronger coverage:

- Auth E2E with real browser for every role
- Billing E2E with Razorpay test mode
- Student exam network-loss/reconnect/browser-refresh test
- Cross-tenant admin tests across every reporting endpoint
- Browser tests for mobile dashboard tables/modals
- Load test for mixed proctoring workload
- Data retention/deletion/privacy workflow tests

## E. Feature Roadmap

### Must-Have Product Features

1. Review packet with explainable risk score
2. Appeal workflow for flagged students
3. Teacher issue reporting and superadmin triage
4. Bulk import and invite reliability
5. Org admin teacher filter and org-level analytics
6. Student reconnect/offline queue with visible recovery
7. Billing limits and grace-period enforcement
8. LTI grade passback and roster sync
9. Institution trust center with DPA, retention, security docs, and audit logs
10. Test-run mode for teachers before real exam day

### Retention Features

- Exam templates
- Reusable question banks
- Course/student groups
- Semester-level analytics
- Teacher last-mile checklist before launch
- Post-exam cohort reports
- Reusable invite campaigns
- LMS sync
- Risk calibration per institution/exam type

### Competitive Advantage Features

- Hindi/regional-language student instructions
- Low-bandwidth mode for Indian campuses
- Phone-as-second-camera with QR pairing
- Evidence-based scorecard understandable to exam committees
- Institution-level proctoring policies: strict, moderate, light
- Privacy-preserving retention controls
- Exam-day command center for admins

### Wow Factor Features

- AI-generated incident summaries:
  - "3 students need review"
  - "47 clean"
  - "2 connectivity-only"
- Natural-language analytics:
  - "Which exams had unusually high tab-switch risk?"
- One-click audit packet for accreditation/legal review
- Live exam health map:
  - submissions
  - reconnects
  - camera failures
  - high-risk spikes
- Practice exam simulator that predicts readiness before launch

## F. Business and Monetization

Best initial model: B2B SaaS per active student per month or per exam attempt.

Recommended pricing:

- **Starter:** small teachers/tutorials
- **Growth:** departments/coaching institutes
- **Enterprise:** universities, training companies, hiring platforms
- **Add-ons:** high-retention evidence storage, custom retention, LMS/SAML, dedicated support

Avoid pure B2C. Students are not the buyer. Teachers may be users, but institutions are the payer.

### Growth Loops

- Every student invite introduces the Procta brand.
- Scorecards are shareable internally with administrators.
- LTI listing creates LMS discovery.
- Trust center/security docs reduce procurement friction.
- Free practice exam converts teachers.

### Retention Strategies

- Question bank lock-in
- Historical student performance
- LMS integration
- Audit logs and review packets
- Institutional policies and templates

## G. Competitive Analysis

### Compared to OpenAI

**Weakness:** No platform-level developer ecosystem yet.

**Differentiation:** Applied AI workflow, not generic AI.

**Potential moat:** Proprietary exam/proctoring event data and review workflows.

### Compared to Stripe

**Weakness:** Checkout, billing, and activation lifecycle are still young.

**Differentiation:** Make exam setup feel as clean as Stripe Checkout.

**Potential moat:** Trust, reliability, and developer-quality APIs for exams.

### Compared to Notion

**Weakness:** Information architecture and UX consistency.

**Differentiation:** Operational workflow, not documents.

**Potential moat:** Institution workflows and historical exam data.

### Compared to Linear

**Weakness:** Dashboard still has friction and role confusion.

**Differentiation:** Fast, focused exam operations.

**Potential moat:** Polished command-center UX for exam day.

### Compared to Figma

**Weakness:** Collaboration model is limited.

**Differentiation:** Real-time multi-role monitoring during exams.

**Potential moat:** Live teacher/admin/student coordination.

## H. Risk Analysis

### Technical Risks

- Multiple dashboard implementations diverge.
- Proctoring ingestion may bottleneck under mixed workload.
- Raw event/frame data can grow quickly.
- Role/tenant bugs are high-impact.
- LocalStorage tokens increase XSS blast radius.

### Business Risks

- Institutions have long sales cycles.
- AI proctoring is trust-sensitive and controversial.
- False positives can damage reputation.
- Procurement may demand compliance proof early.

### Scaling Risks

- Storage costs from screenshots/video/frame data
- Dashboard live connections during large exams
- Email deliverability for invites/OTP
- Support burden during exam day

### Legal and Privacy Risks

- Biometric/behavioral monitoring concerns
- DPDP/GDPR-style deletion/export requests
- Student consent and retention policy clarity
- Explainability of AI risk scores

### Operational Risks

- Exam-day downtime is catastrophic.
- Payment/plan state mismatch can block institutions.
- Workers/queues need observability.
- Manual deployment/server management can become fragile.

## I. Execution Roadmap

### Immediate Fixes: 24 Hours

- Fix dashboard role metadata and React tab matrix.
- Fix forgot password.
- Fix `/student-react` route or remove links.
- Add server pagination use in React Live/Results.
- Run a clean smoke test:
  - signup
  - login
  - create exam
  - invite student
  - student exam
  - submit
  - result
  - billing test order

### Short-Term Roadmap: 30 Days

- Pick canonical dashboard path.
- Remove or mark the non-canonical dashboard as deprecated.
- Finish org admin and superadmin role UX.
- Complete React Issues/reporting parity.
- Add mixed workload load test:
  - SSE
  - proctoring events
  - frames
  - autosave
  - teacher dashboard
- Add billing lifecycle enforcement.
- Add browser E2E test matrix.
- Build production ops dashboard.
- Document privacy/retention/security posture.

### Medium-Term Roadmap: 90 Days

- Reach institutional pilot readiness.
- Complete LTI production coverage.
- Move evidence storage to object storage.
- Ship review packet v2 with explainability.
- Add low-bandwidth student mode.
- Add admin analytics by teacher/exam/org.
- Add SSO/SAML or Google Workspace login for institutions.
- Formalize incident/support playbook.

### Long-Term Vision: 1 Year

Become the default lightweight proctoring and assessment integrity layer for mid-market Indian education and training.

Long-term platform goals:

- API platform for exam integrity
- Multi-region infrastructure
- Compliance certifications/security reviews
- Marketplace/integrations with LMS and HR assessment platforms
- Proprietary risk intelligence based on large-scale exam telemetry

## J. Final Verdict

Would I fund this?

**Yes, but not yet as a broad "AI proctoring app."**

I would fund it as a focused exam integrity infrastructure company if the next sprint is disciplined and aimed at product hardening.

The raw potential is strong. The project has more real engineering than many early SaaS products:

- Working auth
- Billing
- Proctoring
- Load tests
- LTI
- Dashboards
- Workers
- Migration discipline

The current risk is execution sprawl. Too many surfaces are partially complete.

Investor answer:

> Fundable after one disciplined hardening sprint.

CEO/CTO answer:

> Stop expanding for a moment. Make one teacher, one student, one org admin, and one superadmin journey excellent. Then the load-test numbers, review packets, and "no IT team required" story become genuinely sellable.

