# Procta — Enterprise Gap Analysis

**Date:** 2026-06-12
**Scope:** Full codebase audit across billing, auth/org, exam/student management, privacy/compliance, notifications, and infrastructure/operations.
**Total gaps found: 66** (11 Critical, 22 High, 19 Medium, 14 Low)

---

## 🔴 Critical (revenue risk, security, or customer-blocking)

### 1. No mid-cycle plan change with proration
The current system forces cancel-first-then-resubscribe. An org on Growth (₹12K/mo, 150 students) that needs Pro (₹30K/mo, 500 students) in month 2 of 12 loses the remaining 10 months of Growth value. No proration exists anywhere in the codebase. **Blocks exactly the upsell scenario that drives SaaS revenue.**
- **Affected:** `app/routers/billing.py`, `app/services/billing.py`, `app/dashboard-ui/src/panels/BillingPanel.jsx`
- **Fix:** Add `POST /api/v1/billing/change-plan` with proration calculation + Razorpay plan switch. ~3 days.

### 2. No payment method management API
The dunning email says "Update your payment method" and links to `dashboard#billing`, but the BillingPanel has no payment-method UI and there's no server endpoint to generate a Razorpay customer portal link. A customer whose card expires has no self-serve path to fix it. **Every failed payment becomes a support ticket.**
- **Affected:** `app/routers/billing.py`, `app/emailer.py:1089`, `app/dashboard-ui/src/panels/BillingPanel.jsx`
- **Fix:** Add Razorpay customer portal link endpoint. ~1 day.

### 3. No reactivation / undo-cancellation path
Cancel sets status to `cancelling`. If an admin clicks "Cancel" accidentally (the UI `confirm()` dialog is the only guard), there's no undo. The org must call sales or wait for the period to end and resubscribe. No `POST /api/v1/billing/reactivate` endpoint exists.
- **Affected:** `app/routers/billing.py` (cancel endpoint only), `app/dashboard-ui/src/panels/BillingPanel.jsx`
- **Fix:** Add `POST /api/v1/billing/reactivate`. ~1 day.

### 4. Overage is computed but never billed
The usage endpoint correctly calculates `overage_amount = overage × price_per_student`, but no code path ever charges for it. It's a dashboard decoration. An org with 180 students on Starter (30-limit) sees ₹12,000/month in overage displayed but never invoiced.
- **Affected:** `app/routers/billing.py:493-494`, `app/services/billing.py`
- **Fix:** Wire overage into Razorpay invoice on period-end webhook or hard block when overage exceeds threshold. ~2 days.

### 5. No org deletion endpoint
Orgs cannot be deleted through the API. Superadmin can view all orgs at `GET /api/v1/admin/all-orgs` but has no delete or suspend capability. Signup rollback code deletes orgs, but no admin-facing endpoint exists.
- **Affected:** `app/routers/admin_org.py`
- **Fix:** Add `DELETE /api/v1/admin/org/{org_id}` (superadmin only) with safety checks and cascade logic. ~1 day.

### 6. No session inactivity timeout
Auth sessions last until TTL expiry (30d refresh token) regardless of user activity. No idle session reaper. A logged-in session on a shared machine persists.
- **Affected:** `app/auth/admin_auth.py`, `app/services/auth_events.py`, `app/services/auth_lockout.py`
- **Fix:** Add idle timeout check on each `require_admin()` call + background stale-session reaper. ~1 day.

### 7. Exam deletion lacks active-session check
`DELETE /api/v1/admin/exams/{exam_id}` checks ownership and "only exam" constraint but does NOT check if `exam_sessions` are `IN_PROGRESS`. Could delete an exam while students are actively testing.
- **Affected:** `app/routers/admin_exams.py:137-185`
- **Fix:** Add `IN_PROGRESS` session check before deletion. ~0.5 day.

### 8. Hard-coded pass mark (40%) in scorecard PDF
`passed = pct >= 40` at `app/services/scorecard.py:560`. Not configurable per-exam. All exams have the same passing threshold regardless of subject, difficulty, or teacher preference.
- **Affected:** `app/services/scorecard.py`, `app/models/exam.py` (no `pass_mark` field on exam_config)
- **Fix:** Add `pass_mark` field to exam_config, default 40, per-exam override. ~0.5 day.

### 9. No consent withdrawal mechanism
GDPR Art 7(3) and DPDP Act §7(4) require consent withdrawal to be as easy as giving consent. `POST /api/v1/privacy/consent` records consent but there is no endpoint to withdraw it. The `consent_records` table is append-only with no "withdrawn" state.
- **Affected:** `app/routers/privacy.py`, `app/static/privacy.html`, `app/static/privacy-app.js`
- **Fix:** Add `POST /api/v1/privacy/consent/withdraw` + negation record insertion + UI button. ~1 day.

### 10. No cookie consent banner/disclosure
The app uses functional auth cookies (`procta_access`, `procta_refresh`, `procta_student_access`, `procta_student_refresh`) with no cookie consent banner, no cookie policy page, and no disclosure. Required under ePrivacy Directive / GDPR cookie rules.
- **Affected:** `app/static/privacy-policy.html`, `app/renderer/index.html` (Electron), `app/dashboard-ui/`, `website/`
- **Fix:** Deploy cookie consent modal disclosing auth cookies. ~1 day.

### 11. No rollback for migrations
Forward-only migrations with expand-contract pattern. No `down` migrations exist. Code rollback is safe (additive changes only) but convention is not enforced by tooling. A destructive change (e.g., DROP COLUMN) applied alongside the code change cannot be reverted.
- **Affected:** `migrations/*.sql`, `scripts/run_postgres_migrations.py`
- **Fix:** Add `down` migration convention or tooling enforcement. Higher effort: add integration test that applies all migrations fresh from baseline each CI run. ~2 days.

---

## 🟠 High (operationally significant, growth-blocking)

### 12. No multi-stage dunning
Single email on first `pending` event, no follow-up. No escalation at 3/7/14 days. No SMS fallback. Once `past_due` is set, Razorpay handles retries internally, but the customer communication is a one-shot.
- **Affected:** `app/routers/billing.py:183-196`, `app/services/billing.py`, `app/emailer.py`
- **Fix:** Add scheduled dunning escalation (day 1/3/7) with configurable intervals. ~2 days.

### 13. No support/admin overrides
When an enterprise customer has a billing dispute, support needs direct DB access to extend limits, grant free days, or adjust `max_students`. No API or admin panel exists. The `all-orgs` view is read-only.
- **Affected:** `app/routers/admin_org.py`, `app/routers/billing.py`
- **Fix:** Add superadmin endpoints for: adjust `max_students`, grant free days, apply credits, all with audit trail. ~2 days.

### 14. No billing contact management
Dunning emails go to the first admin returned by `teachers` table (`limit(1)` with no ordering). If that person left the org, the payment failure notification lands in a dead inbox. No dedicated `billing_email` field on organizations. No CC support.
- **Affected:** `app/routers/billing.py:183-196`, `app/routers/admin_org.py`, `migrations/`
- **Fix:** Add `billing_email` column to `organizations`. Fall back to first admin. ~1 day.

### 15. No annual billing option
Subscriptions are `total_count: 12` (monthly renewals). No annual discount, no annual subscription variant. Enterprise procurement often requires annual contracts.
- **Affected:** `app/services/billing.py:69`, `app/constants.py`, `app/dashboard-ui/src/panels/BillingPanel.jsx`
- **Fix:** Add annual plan variants to PLANS dict + Razorpay plan creation with annual billing. ~2 days.

### 16. Invoice history has no local cache
Invoices are fetched live from Razorpay's API. If Razorpay is unreachable, the invoice endpoint returns `{"invoices": [], "error": "Failed to fetch invoices"}`. No fallback to locally-stored invoice data, even though `billing_events` has the raw payloads.
- **Affected:** `app/routers/billing.py:388-434`
- **Fix:** Build local invoice cache from `billing_events` payloads; fall back to Razorpay API. ~1 day.

### 17. No concurrent session limit
Unlimited parallel sessions per user. No max-sessions-per-user policy. A compromised token can be used from unlimited locations simultaneously.
- **Affected:** `app/auth/admin_auth.py`, `app/routers/auth.py`
- **Fix:** Add configurable max active sessions per user + evict oldest on breach. ~1 day.

### 18. Role changes and member removal not audited
`set_member_role()` and `remove_member()` in `admin_org.py` do NOT call `log_admin_action()` from `admin_audit.py`. The only record is the DB update and `clear_teacher_cache()`.
- **Affected:** `app/routers/admin_org.py`
- **Fix:** Add `log_admin_action()` calls to role changes and member removal. ~0.5 day.

### 19. `viewer` role undefined in `OrgRole` enum
`set_member_role` accepts `"viewer"` as a valid role string but `OrgRole` enum in `app/models/org.py` has no `VIEWER` value. The role is stored in the DB but no code checks for it — its behavior is undefined.
- **Affected:** `app/routers/admin_org.py:205`, `app/models/org.py`
- **Fix:** Add `viewer` to `OrgRole` enum or remove the accepted value. ~0.25 day.

### 20. No MFA enforcement
Email-OTP 2FA is optional. Teachers can enable/disable freely. No policy to mandate MFA for org admins or enterprise accounts.
- **Affected:** `app/routers/auth.py`, `app/models/teacher.py`
- **Fix:** Add per-org MFA policy flag + enforcement in `require_admin()` middleware. ~1 day.

### 21. No brute-force protection on OTP verify
Account lockout (`auth_lockout.py`) only applies to password login. 2FA OTP verify endpoints have no global throttling — only per-code attempt tracking (5 attempts per code, but attacker can request new codes).
- **Affected:** `app/services/email_otp.py`, `app/routers/auth.py`
- **Fix:** Extend lockout to OTP verify failure tracking per user. ~1 day.

### 22. Cannot extend time for individual students
Duration is per-exam on `exam_config`. `paused_secs_total` exists for teacher-pause only. Students with accommodations (extra time, disability) need individual duration overrides.
- **Affected:** `app/models/exam.py`, `app/routers/admin_exams.py`, `app/routers/exam.py:1368-1389`
- **Fix:** Add per-student `time_extension_seconds` override on `exam_sessions`. ~1 day.

### 23. No makeup exam flow
No concept of alternative exam window or makeup session. If a student misses the window, the only option is to reset their session or re-invite. No separate makeup exam link or retake scheduling.
- **Affected:** `app/routers/admin_sessions.py`, `app/routers/exam.py`
- **Fix:** Add "makeup window" field to exam_config + separate student flow. ~2 days.

### 24. No exam archiving
No `archived` or `is_active` flag on `exam_config`. Exams remain in listings forever. Only option to remove is hard delete (which destroys data).
- **Affected:** `app/models/exam.py`, `app/routers/admin_exams.py`
- **Fix:** Add `archived_at` timestamp + filter in list queries. ~0.5 day.

### 25. No automated breach notification workflow
DPA and Trust Center document breach notification obligations ("without undue delay", "72 hours"). No `breach_notifications` table, no `notify_breach()` function, no email template for regulator/data-subject notification.
- **Affected:** `app/services/`, `app/emailer.py`
- **Fix:** Create breach notification module + tables + email templates. ~2 days.

### 26. Data retention not fully automated
Violation logs and answers have no automated purge. Privacy policy says 1 year for violations and "duration of account" for exam answers, but no cron/SQL enforces these limits. Retention is only enforced at account deletion time.
- **Affected:** `app/services/ttl_sweeper.py`, `migrations/`
- **Fix:** Add TTL sweeper rules for violations (>1 year) and answers (>account deletion). ~1 day.

### 27. Screenshot cleanup on account delete is deferred
Privacy deletion endpoint handles DB records only. Screenshots on the filesystem are only cleaned by the periodic cron script (90-day rotation via `entrypoint.sh`). If a user requests deletion immediately, their screenshots remain on disk until the next cron run (up to 6h worst case).
- **Affected:** `app/routers/privacy.py`, `entrypoint.sh`
- **Fix:** Have deletion endpoint also remove screenshot directory or mark for immediate cleanup. ~1 day.

### 28. No teacher/admin notification preferences
Only students have a single `email_reminders_enabled` preference. Teachers/admins cannot configure what types of notifications they want (email vs. in-app vs. none), frequency (immediate vs. digest), or severity thresholds for alerts.
- **Affected:** `app/emailer.py`, `app/models/teacher.py`, `app/dashboard-ui/`
- **Fix:** Add notification preferences table per teacher + UI. ~2 days.

### 29. No SMS or push notification channel
Email-only communication channel. No Twilio integration for SMS, no FCM/APNs for push notifications, no Slack/Discord/Telegram integration for alert delivery.
- **Affected:** `app/emailer.py` (abstract backend could extend)
- **Fix:** Add SMS provider backend (Twilio) + push notification backend (FCM). ~3 days.

### 30. No external webhook for system integration
No outbound webhook endpoint that external systems can subscribe to for violation/alert/exam-lifecycle events. The only webhooks are inbound (Resend email tracking, Razorpay billing).
- **Affected:** `app/routers/` (no webhook router)
- **Fix:** Add webhook subscription management + event delivery system. ~3 days.

### 31. Baseline schema not committed
`MIGRATIONS.md` acknowledges the original Supabase-generated schema was never committed to the migrations directory. The `schema-from-scratch` CI gate in `test.yml` is a no-op until the baseline is captured. A migration that doesn't apply cleanly on top of prod's real schema would only be caught in production.
- **Affected:** `migrations/`, `.github/workflows/test.yml`
- **Fix:** Dump current schema as `phase0_baseline.sql` and wire CI to build from scratch using it. ~1 day.

### 32. No feature flag infrastructure
Feature toggles are ad-hoc env vars (`ASYNC_SCORING_ENABLED`, `TTL_SWEEPER_DISABLED`, `REMINDER_LOOP_DISABLED`). No centralized feature flag system. Cannot gradually roll out features or perform A/B testing.
- **Affected:** `app/main.py`, `app/constants.py`
- **Fix:** Add feature flag service (DB-backed or env-configurable) with per-org/global toggles. ~2 days.

### 33. No canary/phased deployment strategy
API deploy is single-shot `git pull → build → up -d` with healthcheck polling. No gradual rollout, no traffic splitting, no blue-green deployment. A bad deploy affects all users simultaneously.
- **Affected:** `.github/workflows/deploy.yml`
- **Fix:** Add phased deploy (10% → 50% → 100% traffic shifting) or blue-green with automated rollback. ~3 days.

---

## 🟡 Medium (growth enablers, operational polish)

### 34. Trial constant exists but is dead code
`TRIAL_DAYS = 14` is defined in `constants.py` but never passed to Razorpay's `subscription.create()`. No trial state machine, no trial-expiry webhook handling, no trial-ending reminder email.
- **Affected:** `app/constants.py:234`, `app/services/billing.py:69`
- **Fix:** Wire `trial_period_days` into Razorpay subscription creation + handle `subscription.completed` for trial expiry. ~1 day.

### 35. No discount/coupon/promo system
No way to offer promotional pricing, educational/non-profit discounts, or partner deals through the API. Only full-price subscriptions.
- **Affected:** `app/services/billing.py`, `app/routers/billing.py`, `app/constants.py`
- **Fix:** Add coupon codes table + Razorpay coupon integration + discount application logic. ~2 days.

### 36. No per-plan feature gating
All plans have identical features (only student count differs). No mechanism to gate advanced features (LTI integration, SSO, custom branding, API access, advanced analytics) behind higher tiers.
- **Affected:** `app/constants.py`, `app/routers/`, `app/dashboard-ui/`
- **Fix:** Add feature flags per plan tier + middleware check. ~2 days.

### 37. One-shot checkout (`checkout.py`) is dead code
`POST /api/v1/checkout/order` and `POST /api/v1/checkout/verify` exist, accept real Razorpay payments, verify HMAC signatures correctly — but persist nothing. The code comment says "Procta currently doesn't have a one-shot-payments table — only subscriptions — so we just return success."
- **Affected:** `app/routers/checkout.py` (entire file, 206 lines)
- **Fix:** Either remove the endpoints entirely or wire to a `payments` table. ~0.5 day.

### 38. Supabase Auth legacy fallback path
Legacy Supabase Auth paths remain in the codebase for deployments that haven't fully migrated to local auth. These bypass local bcrypt hashing and can cause auth divergence.
- **Affected:** `app/auth/tokens.py`, `app/routers/auth.py`
- **Fix:** Remove Supabase Auth fallback or gate behind env var for clean migration. ~1 day.

### 39. No student session visibility
Teachers can't see student auth sessions. Students don't have a session management UI. No visibility into active student logins.
- **Affected:** `app/routers/auth.py`
- **Fix:** Add student session listing for teachers + student self-service session management. ~1 day.

### 40. No student grouping for bulk operations
Groups exist (`student_groups`) but only for exam access control. No group-based operations like "email all Group A" or "export scores for Group A only."
- **Affected:** `app/routers/admin_students.py`, `app/routers/admin_invites.py`
- **Fix:** Add group-based filtering to invite, export, and communication endpoints. ~1 day.

### 41. No concurrent exam limit for students
No check preventing a student from being in two exam sessions simultaneously. A student could open two tabs and take two exams at once (if both allow the same window).
- **Affected:** `app/routers/exam.py:validate-student`
- **Fix:** Add active session check per student: reject if another session is IN_PROGRESS. ~0.5 day.

### 42. No question versioning
Questions are live-edited via UPSERT. No history of changes, no rollback capability, no audit trail for question modifications.
- **Affected:** `app/routers/question_bank.py`
- **Fix:** Add question version table with diff tracking. ~2 days.

### 43. Exam config cache TTL is 24 hours
`load_exam_config` in `app/repositories/questions.py` caches for 86400s. If exam settings are changed (schedule, sensitivity, shuffle), missed cache bust paths serve stale config for up to a day.
- **Affected:** `app/repositories/questions.py`
- **Fix:** Reduce cache TTL to 60s or audit all mutation paths for cache bust calls. ~0.5 day.

### 44. No automated right-to-object endpoint
Privacy policy mentions "Object to processing" as a right. No API endpoint or UI exists to exercise it. No mechanism to restrict processing of specific data categories.
- **Affected:** `app/routers/privacy.py`, `app/static/privacy.html`
- **Fix:** Add `POST /api/v1/privacy/object` endpoint + UI. ~1 day.

### 45. No DPIA document
GDPR Art 35 requires a Data Protection Impact Assessment for high-risk processing (biometric proctoring — face detection, voice analysis, screen recording). No DPIA document exists.
- **Affected:** `docs/` directory
- **Fix:** Draft DPIA covering biometric data processing, risk assessment, mitigating controls. ~2 days.

### 46. Student data export capped at 500 sessions
Privacy export truncates at 500 session keys. Large-scale students with many exam sessions may get incomplete exports without warning.
- **Affected:** `app/routers/privacy.py`
- **Fix:** Paginate or increase limit with warning. ~0.5 day.

### 47. No email template management
All email HTML/CSS is hardcoded as Python f-strings in `emailer.py` (1370 lines). No template engine (Jinja2), no template files, no ability to modify email designs without code changes and redeployment.
- **Affected:** `app/emailer.py`
- **Fix:** Extract templates to files + use Jinja2 rendering. ~2 days.

### 48. No batched/digest notifications
All alerts sent immediately. No system to batch violation alerts into periodic digests (e.g., "5 violations in the last hour from 3 sessions").
- **Affected:** `app/services/risk.py`, `app/routers/sse.py`
- **Fix:** Add configurable digest intervals for non-critical alerts. ~2 days.

### 49. No unsubscribe mechanism
None of the transactional emails include `List-Unsubscribe` header, one-click unsubscribe link (RFC 8058), or unsubscribe preference management.
- **Affected:** `app/emailer.py`
- **Fix:** Add `List-Unsubscribe` header and preference management for non-transactional emails. ~1 day.

### 50. No alert fatigue prevention
`publish_critical_alert()` filters by severity and `_CRITICAL_TYPES` but has no rate-limiting (max N alerts per minute per teacher), no cooldown for repeated same-type violations, and no smart suppression. Room camera offline is the only dedup example.
- **Affected:** `app/services/risk.py`, `app/routers/sse.py`
- **Fix:** Add per-teacher alert rate limit + cooldown per (session, violation_type). ~1 day.

### 51. No formal circuit breaker patterns
Only retry/backoff exists (Redis event bus: 0.5s-10s max). No formal circuit breaker library (`pybreaker`) for external dependencies (Redis, Postgres, Supabase, email provider, LLM API, Razorpay).
- **Affected:** `app/services/`, `app/database.py`, `app/event_bus.py`
- **Fix:** Add circuit breaker wrapper for external HTTP calls and DB connections. ~2 days.

### 52. No incident response runbook
`OBSERVABILITY.md` has a triage cheatsheet but no formal incident response process with severity levels (SEV1-SEV3), escalation paths, contact tree, or post-mortem templates.
- **Affected:** `docs/` directory
- **Fix:** Create INCIDENT_RESPONSE.md with severity matrix, escalation, runbooks for common scenarios. ~1 day.

---

## 🔵 Low (nice-to-have / hygiene)

### 53. Enterprise plan is a constant, not configurable
`students: 999999, price_inr: 0` is hardcoded in `constants.py`. No mechanism to define custom per-org limits, pricing, or feature overrides for enterprise contracts.
- **Fix:** Add per-org plan override table (max_students, price, features). ~2 days.

### 54. No billing analytics (MRR, churn)
No visibility into revenue metrics (MRR, ARPU, churn rate, active subscription count, payment success rate). Only per-org usage display exists.
- **Fix:** Add superadmin billing dashboard with aggregate metrics. ~2 days.

### 55. No org ownership transfer
No endpoint to transfer admin ownership to another member. If the only admin leaves, there's no self-serve path to transfer.
- **Fix:** Add `POST /api/v1/org/transfer-ownership`. ~1 day.

### 56. No member self-removal ("leave org")
Only admin can remove members. Teachers who want to leave an org must ask an admin. No "leave org" for regular teachers.
- **Fix:** Add `POST /api/v1/org/leave` for non-admin members. ~0.5 day.

### 57. No global session revocation for superadmin
No superadmin endpoint to revoke ALL sessions for a user or org. Useful for security incidents.
- **Fix:** Add `POST /api/v1/admin/revoke-sessions`. ~0.5 day.

### 58. No exam reordering or pinning
No sort priority, pinning, or folder organization for exams. As exam count grows, teachers have no way to organize.
- **Fix:** Add `sort_order` + `pinned_at` fields to exam_config. ~1 day.

### 59. No cohort/batch management
No academic-year, semester, or section grouping for students. Large institutions need cohort-level reporting.
- **Fix:** Add `batch`/`cohort` field to students + filtering. ~1 day.

### 60. No idempotency on exam creation
`POST /api/v1/admin/exams` has no idempotency key. Duplicate submissions (network retry) could create multiple exams.
- **Fix:** Add `Idempotency-Key` header support to exam creation. ~0.5 day.

### 61. No data retention for payment records (7 years)
Policy states 7 years for payment records but no automated enforcement or purge mechanism.
- **Fix:** Add `billing_events` purge rule (>7 years) with legal hold flag. ~0.5 day.

### 62. Redis frame buffers not cleared on account delete
Phone camera frames in Redis (24h TTL) are not explicitly deleted when a student deletes their account. Only expired via TTL.
- **Fix:** Have deletion flow explicitly clear Redis keys for the session. ~0.5 day.

### 63. No email delivery analytics dashboard
No aggregate bounce rate, open rate, or delivery failure visibility despite Resend providing delivery data through webhook.
- **Fix:** Build delivery metrics from webhook data + simple dashboard widget. ~1 day.

### 64. No localization/i18n
All email templates, UI text, and notification content is English-only. No internationalization framework for serving localized content.
- **Fix:** Add i18n framework + extract strings. ~3 days.

### 65. No automated secrets rotation
Secrets rotation is documented in `docs/SECRETS.md` with playbooks for zero-downtime rotation, but all rotations are manual. No scheduled rotation.
- **Fix:** Add rotation scripts for JWT signing keys + database passwords. ~1 day.

### 66. No DPIA creation workflow
GDPR Art 35 requires DPIA for high-risk processing. Biometric proctoring qualifies.
- **Fix:** Create DPIA document template + review workflow. ~2 days.

---

## Recommended Action Plan (First 15)

| # | What | Domain | Effort | Impact |
|---|---|---|---|---|
| 1 | Add `POST /api/v1/billing/reactivate` to undo cancellation | Billing | 1 day | Medium — customer retention |
| 2 | Add `POST /api/v1/billing/change-plan` with proration | Billing | 3 days | High — unblocks upsell |
| 3 | Add Razorpay customer portal link endpoint | Billing | 1 day | High — reduces support tickets |
| 4 | Remove or wire dead `checkout.py` code | Billing | 0.5 day | Hygiene |
| 5 | Add `billing_email` field to organizations | Billing | 1 day | Medium — dunning reliability |
| 6 | Add multi-stage dunning (day 1/3/7 escalation) | Billing | 2 days | Medium — improves payment recovery |
| 7 | Cache invoice data locally from webhook payloads | Billing | 1 day | Low — belt-and-suspenders |
| 8 | Add active-session check to exam deletion | Exam | 0.5 day | High — prevents data loss |
| 9 | Add `pass_mark` field to exam_config | Exam | 0.5 day | Medium — teacher configurability |
| 10 | Add consent withdrawal endpoint | Privacy | 1 day | High — GDPR compliance |
| 11 | Extend lockout to OTP verify attempts | Auth | 1 day | High — security hardening |
| 12 | Add idle session timeout | Auth | 1 day | Medium — session security |
| 13 | Add feature flag infrastructure | Infra | 2 days | High — enables safe rollouts |
| 14 | Capture and commit baseline schema | Infra | 1 day | High — CI reliability |
| 15 | Add incident response runbook | Infra | 1 day | Medium — operational maturity |

---

## Billing-Specific Gaps

### 🔴 Critical

**1. No mid-cycle plan change with proration** — The current system forces cancel-first-then-resubscribe. An org on Growth (₹12K/mo, 150 students) that needs Pro (₹30K/mo, 500 students) in month 2 of 12 loses the remaining 10 months of Growth value. No proration exists anywhere in the codebase. **Blocks exactly the upsell scenario that drives SaaS revenue.**

**2. No payment method management API** — The dunning email says "Update your payment method" and links to `dashboard#billing`, but the BillingPanel has no payment-method UI and there's no server endpoint to generate a Razorpay customer portal link. A customer whose card expires has no self-serve path to fix it. **Every failed payment becomes a support ticket.**

**3. No reactivation / undo-cancellation path** — Cancel sets status to `cancelling`. If an admin clicks "Cancel" accidentally (the UI `confirm()` dialog is the only guard), there's no undo. The org must call sales or wait for the period to end and resubscribe. No `POST /api/v1/billing/reactivate` endpoint exists.

**4. Overage is computed but never billed** — The usage endpoint correctly calculates `overage_amount = overage × price_per_student`, but no code path ever charges for it. It's a dashboard decoration. An org with 180 students on Starter (30-limit) sees ₹12,000/month in overage displayed but never invoiced.

### 🟠 High

**5. No multi-stage dunning** — Single email on first `pending` event, no follow-up. No escalation at 3/7/14 days. No SMS fallback. Once past_due is set, Razorpay handles retries internally, but the *customer communication* is a one-shot.

**6. No support/admin overrides** — When an enterprise customer has a billing dispute, support needs direct DB access to extend limits, grant free days, or adjust `max_students`. No API or admin panel exists. The `all-orgs` view is read-only.

**7. No billing contact management** — Dunning emails go to the first admin returned by `teachers` table (`limit(1)` with no ordering). If that person left the org, the payment failure notification lands in a dead inbox. No dedicated `billing_email` field on organizations. No CC support.

**8. No annual billing option** — Subscriptions are `total_count: 12` (monthly renewals). No annual discount, no annual subscription variant. Enterprise procurement often requires annual contracts.

**9. Invoice history has no local cache** — Invoices are fetched live from Razorpay's API. If Razorpay is unreachable, the invoice endpoint returns `{"invoices": [], "error": "Failed to fetch invoices"}`. No fallback to locally-stored invoice data (even though `billing_events` has the raw payloads).

### 🟡 Medium

**10. Trial constant exists but is dead code** — `TRIAL_DAYS = 14` is defined in constants but never passed to Razorpay's `subscription.create()`. There's no trial state machine, no trial-expiry webhook handling, no trial-ending reminder email.

**11. No discount/coupon/promo system** — No way to offer promotional pricing, educational/non-profit discounts, or partner deals through the API.

**12. No per-plan feature gating** — All plans have identical features (only student count differs). No mechanism to gate advanced features (LTI, SSO, custom branding, API access, advanced analytics) behind higher tiers.

**13. One-shot checkout (`checkout.py`) is dead code** — `POST /api/v1/checkout/order` and `/verify` exist, accept real payments, verify signatures correctly — but persist nothing. The code comment says "Procta currently doesn't have a one-shot-payments table — only subscriptions — so we just return success." Should be removed or wired to a payments table.

### 🔵 Informational / Minor

**14. Enterprise plan is a constant, not configurable** — `students: 999999, price_inr: 0` is hardcoded. No mechanism to define custom per-org limits, pricing, or feature overrides for enterprise contracts.

**15. No billing analytics** — No MRR, churn, active subscription count, or payment success rate visibility anywhere.

---

### Billing Recommended Action Plan

| # | What | Effort | Impact |
|---|---|---|---|
| 1 | Add `POST /api/v1/billing/reactivate` to undo cancellation | 1 day | Medium — customer retention |
| 2 | Add `POST /api/v1/billing/change-plan` with proration | 3 days | High — unblocks upsell |
| 3 | Add Razorpay customer portal link endpoint for payment method management | 1 day | High — reduces support tickets |
| 4 | Remove or wire dead checkout.py code | 0.5 day | Hygiene |
| 5 | Add `billing_email` field to organizations + use it for dunning | 1 day | Medium — dunning reliability |
| 6 | Add multi-stage dunning (day 1/3/7 escalation) | 2 days | Medium — improves payment recovery |
| 7 | Cache invoice data locally (from webhook payloads) | 1 day | Low — belt-and-suspenders |
