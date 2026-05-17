# User Workflow Audit — Procta Browser

**Date:** 2026-05-16  
**Auditor:** Full-Stack QA / Security / UX Review  
**Test suite:** 566 passed, 33 skipped  
**npm vulns:** 0 across all 4 packages  
**Total route count:** ~140 API endpoints, 4 WebSocket, 1 SSE, 13 static pages, 2 React SPAs, 15 marketing pages

---

## Table of Contents

1. [Production-Readiness Score](#production-readiness-score)
2. [Final Recommendation](#final-recommendation)
3. [Complete User-Flow Map](#complete-user-flow-map)
4. [List of Tested Routes/Pages](#list-of-tested-routespages)
5. [Critical Issues (P0 — Blocks Launch)](#critical-issues-p0--blocks-launch)
6. [High-Severity Issues (P1)](#high-severity-issues-p1)
7. [Medium-Severity Issues (P2)](#medium-severity-issues-p2)
8. [Low-Severity Issues (P3)](#low-severity-issues-p3)
9. [Broken or Risky Workflows](#broken-or-risky-workflows)
10. [Prioritized Fix Roadmap](#prioritized-fix-roadmap)
11. [Dashboard Deep-Dive Recheck](#dashboard-deep-dive-recheck--2026-05-17)
12. [Quick Wins (Fix in <30 min each)](#quick-wins-fix-in-30-min-each)
13. [Security Vuln Summary](#security-vuln-summary)

---

## Production-Readiness Score

| Category | Score | Notes |
|----------|-------|-------|
| Auth & Identity | **4/10** | TOTP 2FA bypassed at login, email verification bypassed, student-ui login 404s |
| Dashboard UX | **5/10** | Silent API failures in 13/16 panels, no URL-based navigation, poor empty states |
| Student Exam Flow | **6/10** | Invite accept broken, no retry on network failure, no auto-submit on disconnect |
| Billing | **5/10** | Trial never expires, webhook failure silently swallowed, no cancellation flow |
| LTI Integration | **3/10** | Auto grade passback silently fails (empty client_id), AGS/NRPS are 501 stubs |
| Privacy/Compliance | **6/10** | Privacy center is 404 (missing route), no student appeal read-back |
| Security Hardening | **5/10** | No server-side logout, CSRF bypass for non-/api/ routes, load test bypass |
| Performance | **7/10** | Some inline style bloat, no pagination in most panels, sync Redis in async |
| Code Quality | **6/10** | Dead code (emailer.py missing functions), undefined vars, wrong imports |
| **OVERALL** | **5.2/10** | **NOT READY — requires fixes before production launch** |

---

## Final Recommendation

**NOT READY — NEEDS FIXES BEFORE LAUNCH**

The application has **3 critical runtime bugs**, **16 high-severity issues**, and systemic UX problems that would create a poor first impression and cause real data/session-level issues for users. The auth bypass (TOTP 2FA not enforced at login, email verification auto-verified) creates material security gaps. The student-facing invite flow and the LTI grade passback are broken-silent (failures are swallowed).

---

## Complete User-Flow Map

### Persona 1: New Teacher (Signup → First Exam)

```
Landing Page (/)
  │
  ├──→ Signup (/signup) → Enter email/password + name
  │     │                     │
  │     │              [Turnstile CAPTCHA]
  │     │                     │
  │     ├──→ Email verification email sent → Click link → Verified
  │     │     (But auto-verified on login — bypasses this)
  │     │
  │     └──→ Login → Dashboard (/dashboard → /dashboard-react/)
  │               │
  │               ├──→ OnboardingWizard (6 steps)
  │               │     ├── Welcome
  │               │     ├── Create exam
  │               │     ├── Import students
  │               │     ├── Send invites
  │               │     ├── Run demo
  │               │     └── Done
  │               │
  │               ├──→ Org Panel (default tab)
  │               │     ├── Org name, plan, usage
  │               │     ├── Members (invite teachers)
  │               │     └── Trial banner
  │               │
  │               ├──→ Exams tab → Create exam
  │               │     ├── Add questions (MCQ / short-answer)
  │               │     ├── Set schedule, access code, sensitivity
  │               │     ├── Import from question bank
  │               │     └── AI-generate questions
  │               │
  │               ├──→ Students tab → Invite / register
  │               │     ├── Send email invites
  │               │     ├── Bulk register
  │               │     ├── Student groups
  │               │     └── Access code
  │               │
  │               ├──→ Live Sessions → Monitor exams
  │               │     ├── SSE stream
  │               │     ├── Live camera view
  │               │     └── Violation alerts
  │               │
  │               ├──→ Results → View scores
  │               │     ├── CSV/Excel/PDF export
  │               │     ├── Scorecard email
  │               │     └── Grade review
  │               │
  │               ├──→ Review → Grade pending answers
  │               │     ├── Bulk accept/reject AI grades
  │               │     ├── Appeals queue
  │               │     └── Evidence timeline
  │               │
  │               ├──→ Analytics → Charts & metrics
  │               │     ├── Score distribution
  │               │     ├── Risk distribution
  │               │     └── Question analysis
  │               │
  │               ├──→ Chat → Student messaging
  │               │
  │               ├──→ Settings → Org, billing, security
  │               │     ├── TOTP 2FA setup
  │               │     ├── Active sessions
  │               │     └── API keys
  │               │
  │               └──→ Tools → Templates, exports, utilities
  │
  └──→ Billing (/api/v1/billing/plans)
        ├── Starter (₹2,400/mo, 30 students)
        ├── Growth (₹12,000/mo, 150 students)
        ├── Pro (₹30,000/mo, 500 students)
        └── Enterprise (custom)
```

### Persona 2: Student (Registration → Exam → Results)

```
Entry Points:
  ├── Invite link (/invite/{token})
  ├── Registration (/register) — teacher email or access code lookup
  └── Direct — student dashboard (/student)

  │
  ├──→ Register or Log in
  │     ├── Email/password or OAuth (Google/Microsoft)
  │     └── Check account exists → signup or login
  │
  ├──→ Student Dashboard (/student or /student-react/)
  │     ├── Upcoming exams with countdown
  │     ├── In-progress exam (resume)
  │     └── Completed exams with history
  │
  ├──→ Start Exam
  │     ├── Access code prompt (if required)
  │     ├── Preflight check (camera, browser, bandwidth)
  │     └── Launch Electron kiosk
  │
  ├──→ Exam Taking (Electron kiosk)
  │     ├── Questions display (MCQ + short-answer)
  │     ├── Auto-save answers (60s debounce)
  │     ├── Heartbeat every 30s
  │     ├── Violation detection (face, objects, audio, etc.)
  │     ├── Room camera (phone)
  │     └── Submit exam
  │
  └──→ Post-Exam
        ├── Score displayed immediately
        ├── Exam history with score/violations/risk
        ├── Appeal violations (if needed)
        └── Data export / account deletion (via /privacy)
```

### Persona 3: LMS Admin (LTI Integration)

```
LTI Setup (/lti-setup or /lti/auto-config)
  │
  ├──→ Configure LMS (Canvas/Moodle/Blackboard)
  │     ├── Auto-config JSON → import in LMS
  │     ├── Manual setup (issuer, JWKS, login URL, launch URL)
  │     └── Deployment registration
  │
  ├──→ LTI Launch (/lti/launch)
  │     ├── OIDC login initiation
  │     ├── Launch JWT validation
  │     ├── Student context storage
  │     └── Exam launch flow
  │
  └──→ Grade Passback (POST /lti/ags/push-grades)
        ├── Manual (from teacher dashboard)
        └── Automatic (on exam submission — BROKEN, see LTI-1)
```

---

## List of Tested Routes/Pages

### API Endpoints Tested (by category)

| Category | Routes | Tested |
|----------|--------|--------|
| Auth & Identity | 28 | ✅ |
| Exam/Proctoring | 15 | ✅ |
| Admin Dashboard | ~50 (10 sub-routers) | ✅ (sample) |
| Question Bank | 12 | ✅ |
| Grading | 5 | ✅ |
| Public/General | 23 | ✅ |
| SSE/WebSocket | 6 | ✅ |
| LTI | 9 | ✅ |
| Google Classroom | 6 | ✅ |
| Billing | 5 | ✅ |
| Privacy | 3 | ✅ |
| Appeals | 3 | ✅ |
| API (programmatic) | 9 | ✅ |

### Static Pages Tested

| Path | File | Result |
|------|------|--------|
| `/` | Redirect to marketing | ✅ |
| `/dashboard` | `dashboard.html` (6607 lines) | ✅ Served |
| `/dashboard-react` | React SPA (312 KB) | ✅ Served |
| `/student` | `student.html` | ✅ Served |
| `/student-react` | React SPA (198 KB) | 🟡 Broken login (wrong endpoint) |
| `/register` | `register.html` | ✅ Served |
| `/download` | `download.html` | ✅ Served |
| `/privacy` | `privacy.html` | 🔴 **404 — MISSING ROUTE** |
| `/privacy-policy` | `privacy-policy.html` | ✅ |
| `/dpa` | `dpa.html` | ✅ |
| `/trust-center` | `trust-center.html` | ✅ |
| `/security-questionnaire` | `security-questionnaire.html` | ✅ |
| `/api-docs` | `api-docs.html` | ✅ |
| `/proof-assets` | `proof-assets.html` | ✅ |
| `/sample-scorecard` | `sample-scorecard.html` | ✅ |

### Marketing Site Pages (website/)

| Path | Component | Result |
|------|-----------|--------|
| `/` | Landing | ✅ |
| `/pricing` | Pricing | ✅ |
| `/signup` | Signup | ✅ (see issues) |
| `/register` | Signup (alias) | ✅ |
| `/features` | Features | ✅ |
| `/how-it-works` | HowItWorks | ✅ |
| `/lti-setup` | LtiSetup | ✅ |
| `/privacy` | Privacy | ✅ |
| `/trust` | Trust | ✅ |
| `/terms` | Terms | ✅ |
| `/blog` | Blog | ✅ |
| `/download` | Download | ✅ |

---

## Critical Issues (P0 — Blocks Launch)

### C-1: TOTP 2FA Bypassed at Login
- **Severity:** Critical
- **Area:** Auth / Security
- **Steps:** Enable 2FA → logout → login again → access token issued immediately without TOTP challenge
- **Expected:** Login endpoint checks `totp_enabled_at` and returns `2FA_REQUIRED`
- **Actual:** No TOTP check exists in `teacher_login` — 2FA provides zero security
- **Files:** `app/routers/auth.py:323-425`, `app/services/totp.py:113-123`

### C-2: Student-UI Login Calls Wrong Endpoint (404)
- **Severity:** Critical
- **Area:** Auth / Student
- **Steps:** Open `/student-react` → enter credentials → `POST /api/auth/login` returns 404
- **Expected:** Calls `POST /api/v1/student/auth/login` (correct endpoint)
- **Actual:** Path is missing `v1/` and `student/` segments
- **Files:** `app/student-ui/src/main.jsx:21`

### C-3: Email Verification Bypassed on Login
- **Severity:** Critical
- **Area:** Auth
- **Steps:** Signup (status: pending_verification) → immediately log in → auto-verified
- **Expected:** Block with `EMAIL_UNVERIFIED` error
- **Actual:** Lines 369-374 auto-set `email_verified_at` for ALL unverified logins, no date-created check
- **Files:** `app/routers/auth.py:367-374`

### C-4: Grade Passback Silently Fails (LTI)
- **Severity:** Critical
- **Area:** LTI
- **Steps:** Student takes exam via LTI → submission → `_try_ags_grade_passback` → `find_registration(iss, "")` never matches
- **Expected:** Grade pushed to LMS automatically
- **Actual:** Empty `client_id` never matches any registration. Failure is swallowed by `except Exception`
- **Files:** `app/routers/exam.py:737`, `app/lti/registration.py:86-89`

### C-5: Signup Partial Rollback Gap (Orphaned Resources)
- **Severity:** Critical
- **Area:** Auth / Data Integrity
- **Steps:** Signup → org created → teacher insert fails → uncaught → orphaned org + subscription
- **Expected:** All-or-nothing transaction
- **Actual:** No compensating rollback for local-auth path; Supabase auth rollback is best-effort only
- **Files:** `app/routers/auth.py:239-296`

### C-6: Privacy Center Route Missing (404)
- **Severity:** Critical
- **Area:** UX / Compliance
- **Steps:** Click any privacy center link (from trust center, DPA, privacy policy) → 404
- **Expected:** Route `/privacy` should serve `privacy.html`
- **Actual:** No `@router.get("/privacy")` exists in `public.py`
- **Files:** `app/routers/public.py` (missing route)

### C-7: Invite Accept Endpoint Does Not Exist
- **Severity:** High (borders Critical)
- **Area:** Student / Registration
- **Steps:** Click invite link → resolve succeeds → frontend calls `POST /api/invite/{token}/accept` → 404
- **Expected:** Invite status updated to "accepted", student linked to exam
- **Actual:** Endpoint missing; invite never accepted; student sees "No exams yet"
- **Files:** `app/static/student.html:1328` (frontend), backend has no matching endpoint

---

## High-Severity Issues (P1)

### H-1: No Server-Side Logout (Token Survives Until Expiry)
- **Severity:** High
- **Area:** Auth / Security
- **Steps:** Login → click Logout → token removed from localStorage but still valid for 12h
- **Expected:** `POST /api/v1/auth/logout` revokes JTI in Redis/DB
- **Actual:** No logout endpoint; `POST /api/v1/auth/sessions/{jti}/revoke` exists but never called
- **Files:** `app/dashboard-ui/src/lib/auth.jsx:70-75`, `app/routers/auth.py`

### H-2: Student Login Has No CAPTCHA
- **Severity:** High
- **Area:** Auth / Security
- **Steps:** Brute-force `/api/v1/student/auth/login` without captcha_token — succeeds
- **Expected:** Turnstile CAPTCHA required like teacher login
- **Actual:** `StudentLoginIn` model has no `captcha_token` field; handler doesn't call `verify_or_403`
- **Files:** `app/models/student.py:55-58`, `app/routers/auth.py:811`

### H-3: OAuth Disabled Under Local Auth (503)
- **Severity:** High
- **Area:** Auth
- **Steps:** Set `AUTH_PROVIDER=local` → click "Continue with Google" → 503
- **Expected:** OAuth should work regardless of password storage method
- **Actual:** Both OAuth endpoints return 503 when `local_auth_enabled()` is true
- **Files:** `app/routers/auth.py:1569-1570,1597-1598`

### H-4: Silent API Failure in Most Dashboard Panels (13/16)
- **Severity:** High
- **Area:** Dashboard UI / UX
- **Steps:** Network failure → panel catches silently via `catch (_) {}` → loading state persists or empty data shown
- **Expected:** User-visible error message with retry button
- **Actual:** 13 out of 16 panels have empty catch blocks. Error is completely invisible to user
- **Files:** `LiveSessionsPanel.jsx`, `ResultsPanel.jsx`, `MembersPanel.jsx`, `HistoryPanel.jsx`, `QuestionsPanel.jsx`, `AnalyticsPanel.jsx`, `OrgPanel.jsx`, `AllOrgsPanel.jsx`, `SecurityPanel.jsx` + more

### H-5: No URL-Based Tab Navigation in Dashboard
- **Severity:** High
- **Area:** Dashboard UI / UX
- **Steps:** Switch tabs → URL unchanged → refresh → back to default tab
- **Expected:** Tab reflected in URL hash (`#results`, `#review`); refresh preserves state
- **Actual:** Pure React state; lost on refresh; no bookmarkable URLs; browser back/forward leaves app
- **Files:** `app/dashboard-ui/src/App.jsx:122`

### H-6: Webhook DB Failure Silently Returns "ok"
- **Severity:** High
- **Area:** Billing
- **Steps:** Razorpay sends webhook → DB write fails → handler returns `{"status": "ok"}` → no retry
- **Expected:** Return 500 on failure so Razorpay retries
- **Actual:** Razorpay considers webhook delivered; subscription state diverges from reality
- **Files:** `app/routers/billing.py` (webhook handler)

### H-7: Trial Never Enforced Post-Expiration
- **Severity:** High
- **Area:** Billing
- **Steps:** Sign up (7-day trial) → wait 7+ days → still have full access
- **Expected:** Trial end is checked; expired trials get downgraded or blocked
- **Actual:** `trial_end` stored but never read for access control; no cron/reaper
- **Files:** `app/routers/billing.py`, `app/services/sessions.py`

### H-8: No Client-Side Retry for Failed API Calls (Student Exam)
- **Severity:** High
- **Area:** Student / Exam
- **Steps:** Network blip during exam → `save-answer` fails → answer silently lost
- **Expected:** Retry queue with exponential backoff; IndexedDB fallback
- **Actual:** No retry logic; individual saves have no fallback
- **Files:** `app/static/student.html` (exam JS)

### H-9: No Heartbeat Timeout / Auto-Submit on Disconnection
- **Severity:** High
- **Area:** Student / Exam
- **Steps:** Student's internet disconnects mid-exam → heartbeat stops → session stays "active" indefinitely
- **Expected:** Reaper marks sessions ABANDONED after 5min missing heartbeat; auto-submit answers
- **Actual:** No timeout enforcement; student could exploit disconnection for extra time
- **Files:** `app/routers/exam.py`, `app/services/sessions.py`

### H-10: Signup Race Condition (Duplicate Org/User)
- **Severity:** High
- **Area:** Auth
- **Steps:** Rapid double-click "Start Free Trial" → two parallel POSTs → both pass `existing` check → two orgs created
- **Expected:** Second submission rejected (idempotency key or DB constraint)
- **Actual:** No DB unique constraint on `teachers.email` or `organizations.slug`; no advisory lock
- **Files:** `app/routers/auth.py:202-296`

### H-11: Rate Limit Bypass via X-Loadtest-Key
- **Severity:** High
- **Area:** Security
- **Steps:** Send `X-Loadtest-Key: <secret>` header → rate limiting completely bypassed per-request
- **Expected:** Load test bypass should be gated behind compile-time flag or disabled in production
- **Actual:** Any request with matching header gets unique per-request key
- **Files:** `app/limiter.py:46-47`

### H-12: CSRF Bypass for Non-/api/ Paths
- **Severity:** High
- **Area:** Security
- **Steps:** POST to any endpoint not under `/api/` prefix → no CSRF check
- **Expected:** CSRF middleware covers all state-changing endpoints
- **Actual:** Middleware only checks paths starting with `/api/`
- **Files:** `app/main.py:410`

### H-13: Body Size Check Trusts Content-Length Header
- **Severity:** High
- **Area:** Security
- **Steps:** Send `Content-Length: 100` with 50MB body → middleware passes, streaming handles actual size
- **Expected:** Enforce actual body size during streaming
- **Actual:** Only checks the client-provided header; OOM risk
- **Files:** `app/main.py:270-272`

### H-14: `require_auth` Accepts Any JWT (No Role Check)
- **Severity:** High
- **Area:** Security
- **Steps:** Use student token on exam endpoint that calls `require_auth` → accepted (no role check)
- **Expected:** `require_auth` checks role claim (student vs teacher vs exam)
- **Actual:** 14+ endpoints in exam.py use bare `require_auth` without role verification
- **Files:** `app/auth/tokens.py:89-96`

### H-15: `_render_invite`, `_pick_backend`, `_send` Missing in emailer.py
- **Severity:** High
- **Area:** Code / Functional
- **Steps:** Any email send triggers NameError for missing functions
- **Expected:** Functions exist to render invites, pick email backend, and send
- **Actual:** 7 call sites reference functions that are never defined anywhere in codebase
- **Files:** `app/emailer.py:72,86,133,181,326,367,445`

### H-16: Webhook verify_webhook Imported from Wrong Module
- **Severity:** High
- **Area:** Code / Functional
- **Steps:** Email webhook fires → `from ..emailer import verify_webhook` → ImportError at runtime
- **Expected:** Import from `..services.billing` where function actually lives
- **Actual:** Wrong import path in `public.py:551`
- **Files:** `app/routers/public.py:551`

---

## Medium-Severity Issues (P2)

### M-1: Refresh Token Rotation Only for Local Auth Path
- **Severity:** Medium
- **Area:** Auth / Security
- **File:** `app/routers/auth.py:461-477,888-903`

### M-2: Lockout Fails Open When Redis Is Down
- **Severity:** Medium
- **Area:** Auth / Security
- **File:** `app/services/auth_lockout.py:38-40,55-57`

### M-3: Resend Verification Rate Limit Too Aggressive (1/min)
- **Severity:** Medium
- **Area:** Auth / UX
- **File:** `app/routers/auth.py:1186`

### M-4: OAuth Callback Not Rate-Limited
- **Severity:** Medium
- **Area:** Auth / Security
- **File:** `app/routers/auth.py:1585`

### M-5: No Logout Button in Student Dashboard (legacy HTML)
- **Severity:** Medium
- **Area:** Auth / UX
- **File:** `app/static/student.html` (topbar)

### M-6: Empty Table States in MembersPanel, AllOrgsPanel, ResultsPanel
- **Severity:** Medium
- **Area:** Dashboard / UX
- **Files:** `MembersPanel.jsx`, `AllOrgsPanel.jsx`, `ResultsPanel.jsx`

### M-7: No "Get Started" CTA on Empty Panels
- **Severity:** Medium
- **Area:** Dashboard / UX
- **Files:** Systemic across all panels

### M-8: No Retry Mechanism in Dashboard Panels
- **Severity:** Medium
- **Area:** Dashboard / UX
- **Files:** All except OpsPanel and SupportConsole

### M-9: Exam Selection Not Persisted Across Refresh
- **Severity:** Medium
- **Area:** Dashboard / UX
- **File:** `app/dashboard-ui/src/App.jsx:123`

### M-10: ChatPanel WebSocket Has No Reconnection
- **Severity:** Medium
- **Area:** Dashboard / Functional
- **File:** `app/dashboard-ui/src/panels/ChatPanel.jsx:24-55`

### M-11: Missing Pagination in Most Panel Tables
- **Severity:** Medium
- **Area:** Dashboard / Performance
- **Files:** `LiveSessionsPanel`, `MembersPanel`, `HistoryPanel`, `AllOrgsPanel`

### M-12: No Submission Confirmation Dialog (Student)
- **Severity:** Medium
- **Area:** Student / UX
- **File:** Electron renderer

### M-13: Preflight Checks Are Advisory Only (No Hard Block)
- **Severity:** Medium
- **Area:** Student / Security
- **File:** `app/static/student.html` (preflight modal)

### M-14: No Student-Facing Appeal Result Read-Back
- **Severity:** Medium
- **Area:** Student / UX
- **Files:** `app/routers/appeals.py`, `app/static/student.html`

### M-15: No Timer/Countdown in Student Dashboard for In-Progress Exams
- **Severity:** Medium
- **Area:** Student / UX
- **File:** `app/static/student.html`

### M-16: CSRF except Exception: pass Silently Swallows Errors
- **Severity:** Medium
- **Area:** Security
- **File:** `app/main.py:425-426`

### M-17: CSP Missing Critical Directives (base-uri, form-action, object-src)
- **Severity:** Medium
- **Area:** Security
- **File:** `app/main.py:301`

### M-18: CORS allow_methods and allow_headers Too Permissive
- **Severity:** Medium
- **Area:** Security
- **File:** `app/main.py:208-209`

### M-19: SQLi Check Only Covers JSON Bodies (Bypass via Form/XML)
- **Severity:** Medium
- **Area:** Security
- **File:** `app/main.py:280`

### M-20: Nested JSON Objects Not Validated for SQLi
- **Severity:** Medium
- **Area:** Security
- **File:** `app/main.py:283-286`

### M-21: Client/Server Password Requirements Mismatch
- **Severity:** Medium
- **Area:** Auth / UX
- **File:** `website/src/pages/Signup.jsx:200`, `app/services/passwords.py:9`

### M-22: No User-Initiated Subscription Cancellation
- **Severity:** Medium
- **Area:** Billing
- **Files:** `app/routers/billing.py`, `BillingPanel.jsx`

### M-23: Hard Block on Student Limit (No Grace Period)
- **Severity:** Medium
- **Area:** Billing
- **File:** `app/services/sessions.py:40-82`

### M-24: Payment Failure Hard-Expires with No Notification
- **Severity:** Medium
- **Area:** Billing
- **File:** `app/routers/billing.py` (webhook handler)

### M-25: Missing Razorpay Credentials Silently Masks as Sandbox
- **Severity:** Medium
- **Area:** Billing
- **File:** `app/services/billing.py:20-30`

### M-26: AGS/NRPS Endpoints Are 501 Stubs
- **Severity:** Medium
- **Area:** LTI
- **Files:** `app/routers/lti.py` (AGS/NRPS routes)

### M-27: Nonce/State Stored In-Memory (Lost Across Process Restarts)
- **Severity:** Medium
- **Area:** LTI
- **File:** `app/lti/launch.py:33-36`

### M-28: `_cache` Import-Mask Pattern Replicated 17+ Times
- **Severity:** Medium
- **Area:** Architecture
- **Files:** 17 modules across `services/`, `routers/`, `auth/`

### M-29: `cache.py` Uses Sync Redis in Async Context
- **Severity:** Medium
- **Area:** Performance
- **File:** `app/cache.py`

### M-30: Domain Packages Are Empty Re-Export Shims
- **Severity:** Medium
- **Area:** Architecture
- **Files:** `app/domains/*`

### M-31: `import time` Missing in logger.py (Runtime NameError)
- **Severity:** Medium
- **Area:** Code
- **File:** `app/logger.py:155,159,164`

### M-32: `_req_errors` Global Declared But Never Written (F824)
- **Severity:** Low (Cosmetic)
- **Area:** Code
- **File:** `app/routers/public.py:119`

### M-33: `_verif_log` Referenced But Never Defined
- **Severity:** Medium
- **Area:** Code
- **File:** `app/routers/admin_verification.py:46`

### M-34: `ts_to_id` Not Imported in exam.py
- **Severity:** Medium
- **Area:** Code
- **File:** `app/routers/exam.py:1138`

### M-35: `send_demo_request_notification` Returns Wrong Type (tuple vs SendResult)
- **Severity:** Medium
- **Area:** Code
- **File:** `app/emailer.py:286`

### M-36: `ON CONFLICT` Without Matching Unique Constraint in phase57
- **Severity:** Medium
- **Area:** Data / Migration
- **File:** `migrations/phase57_usage_tracking.sql:49`

### M-37: `plan::json` Cast Fails for Default 'starter' Value
- **Severity:** Medium
- **Area:** Data / Migration
- **File:** `migrations/phase57_usage_tracking.sql:36`

---

## Low-Severity Issues (P3)

### L-1: Email Existence Leak on Duplicate Signup
- **File:** `app/routers/auth.py:205`

### L-2: Lockout Retry Message Leaks Timing Info
- **File:** `app/routers/auth.py:338`

### L-3: Logout Does Not Clear All Storage Keys
- **File:** `app/dashboard-ui/src/lib/auth.jsx:70-75`

### L-4: No Redirect After Student Logout
- **File:** `app/student-ui/src/main.jsx:33-36`

### L-5: OAuth Error Redirect Leaks Provider Error Code
- **File:** `app/routers/auth.py:1602`

### L-6: Plain Text "Loading..." in All Panels (No Skeletons)
- **Files:** All panel files (systemic)

### L-7: Delete/Revoke Missing Confirmation in SecurityPanel
- **File:** `SecurityPanel.jsx:62`

### L-8: No Debounce on Search Inputs
- **Files:** `LiveSessionsPanel`, `ResultsPanel`, `HistoryPanel`, `QuestionsPanel`, `SupportConsole`

### L-9: Minimal Email Validation (MembersPanel Invite)
- **File:** `MembersPanel.jsx:27`

### L-10: No Student-Facing Scorecard Download
- **File:** `app/static/student.html` (history view)

### L-11: `/health` Bandwidth Check Is Misleading
- **File:** `app/static/student.html` (preflight)

### L-12: Invoice PDF URLs Not Exposed in BillingPanel UI
- **File:** `BillingPanel.jsx:134-155`

### L-13: Usage Counting Scoped Per-Teacher Not Per-Org
- **File:** `app/routers/billing.py:238-239`

### L-14: Overage Calculated But Never Billed
- **File:** `app/routers/billing.py` (usage endpoint)

### L-15: LTI Registrations Cached Forever (No TTL)
- **File:** `app/lti/registration.py:74-83`

### L-16: LTI Endpoints Missing Rate Limiting
- **Files:** `app/routers/lti.py`

### L-17: System-Wide Inline Styles Instead of CSS
- **Files:** All React dashboard panels (systemic)

### L-18: Dead `_new_access_code()` Function
- **File:** `app/invites.py:23`

### L-19: `app/dependencies.py` (188 lines) Is Dead Code Hub
- **File:** `app/dependencies.py`

### L-20: `app/static/_preview_server.py` Lives in Wrong Directory
- **File:** `app/static/_preview_server.py`

### L-21: `__pycache__` Dirs Not in .gitignore (131 .pyc files)
- **File:** `.gitignore`

### L-22: `public.py:551` Wrong Import for `verify_webhook`
- **File:** `app/routers/public.py:551` (duplicate of H-16)

### L-23: 303 FastAPI Handlers Missing Return Type Annotations
- **Files:** All router files (systemic)

### L-24: 71 `Optional[X]` Uses Still Use Legacy Style
- **Files:** 26 files (systemic)

---

## Broken or Risky Workflows

| # | Workflow | Status | Risk |
|---|----------|--------|------|
| 1 | **TOTP 2FA login** | 🔴 Broken — 2FA never challenged | Complete security bypass |
| 2 | **Student login via React SPA** | 🔴 Broken — wrong API path | 404, student cannot login |
| 3 | **Email verification** | 🔴 Broken — auto-verified on login | Verification is cosmetic |
| 4 | **Privacy center `/privacy`** | 🔴 Broken — 404 (missing route) | Compliance gap, broken links |
| 5 | **Invite accept flow** | 🔴 Broken — missing endpoint | Students never linked to exams |
| 6 | **LTI auto grade passback** | 🔴 Broken — empty client_id | Grades lost silently |
| 7 | **Student logout** | 🟡 No redirect → stays on login form | Poor UX |
| 8 | **OAuth sign-in (local auth)** | 🟡 Disabled (503) | Features unavailable |
| 9 | **Password change (student)** | 🟡 No UI path | Cannot change password |
| 10 | **Data deletion cascade** | 🟡 Appeals not anonymized | Data leak post-deletion |
| 11 | **Subscription cancellation** | 🟡 No API/UI | Must contact sales |
| 12 | **Trial expiration enforcement** | 🔴 Never enforced | Free usage indefinitely |
| 13 | **Billing sandbox detection** | 🟡 Silent sandbox mode | Fake charges shown as real |
| 14 | **Dashboard empty states** | 🟡 Missing CTAs for new users | High drop-off risk |
| 15 | **Dashboard error display** | 🔴 13/16 panels have silent failures | Users see broken/empty states |

---

## Prioritized Fix Roadmap

### Phase 0 — Immediate (Blocking Launch) [~8 hr]
```
Priority | Ref | Fix | Est. Time
P0       | C-1 | Add TOTP check to teacher_login endpoint | 1 hr
P0       | C-2 | Fix student-ui login URL path | 5 min
P0       | C-3 | Add created_at check before auto-verifying email | 30 min
P0       | C-4 | Store client_id in LTI context, pass to find_registration | 1 hr
P0       | C-5 | Add compensating rollback for signup failure | 1 hr
P0       | C-6 | Add missing /privacy route | 5 min
P0       | C-7 | Add POST /api/v1/invite/{token}/accept endpoint | 30 min
P0       | H-15 | Implement missing emailer.py functions | 2 hr
P0       | H-16 | Fix verify_webhook import path | 5 min
P0       | H-11 | Gate load test bypass behind COMPILE_FLAG | 30 min
P0       | H-13 | Implement streaming body size enforcement | 1 hr
```

### Phase 1 — Security Hardening [~10 hr]
```
Priority | Ref | Fix | Est. Time
P1       | H-1  | Add POST /api/v1/auth/logout endpoint | 1 hr
P1       | H-2  | Add CAPTCHA to student login | 30 min
P1       | H-12 | Widen CSRF middleware to cover all paths | 1 hr
P1       | H-14 | Add role check to require_auth() | 1 hr
P1       | H-6  | Add retry logic + 500 response to webhook handler | 1 hr
P1       | H-7  | Add trial-expired reaper + access middleware | 1 hr
P1       | M-16 | Remove except Exception: pass in CSRF middleware | 5 min
P1       | M-17 | Add CSP directives (base-uri, form-action, object-src) | 15 min
P1       | M-18 | Restrict CORS methods/headers | 5 min
P1       | M-19 | Extend SQLi check to all content types | 1 hr
P1       | M-1  | Add refresh token rotation for Supabase auth path | 30 min
P1       | M-2  | Fail closed when lockout system is unavailable | 15 min
```

### Phase 2 — Dashboard UX Fixes [~8 hr]
```
Priority | Ref | Fix | Est. Time
P1       | H-4  | Add error handling to all panels (follow OpsPanel pattern) | 2 hr
P1       | H-5  | Implement URL hash-based tab navigation | 1 hr
P2       | M-6  | Add empty state messages to all tables | 30 min
P2       | M-7  | Add "Get Started" CTAs for new users | 1 hr
P2       | M-8  | Add retry buttons to error states | 1 hr
P2       | M-9  | Persist currentExamId in URL query params | 30 min
P2       | M-10 | Add WebSocket reconnection to ChatPanel | 30 min
P2       | M-11 | Add pagination to large-table panels | 1 hr
P2       | L-6  | Replace plain "Loading..." with skeleton components | 30 min
```

### Phase 3 — Student & Exam Fixes [~6 hr]
```
Priority | Ref | Fix | Est. Time
P1       | H-8  | Add client-side retry queue for answer saves | 2 hr
P1       | H-9  | Add heartbeat reaper + auto-submit on disconnect | 2 hr
P2       | M-12 | Add submission confirmation dialog | 30 min
P2       | M-13 | Move camera check to Electron side (hard block) | 1 hr
P2       | M-14 | Add student appeal status read-back endpoint + UI | 1 hr
P2       | M-15 | Add timer/countdown to in-progress exam cards | 30 min
```

### Phase 4 — Billing & LTI Fixes [~6 hr]
```
Priority | Ref | Fix | Est. Time
P2       | M-22 | Add subscription cancellation API + UI | 1 hr
P2       | M-23 | Add 48h grace period before hard block | 1 hr
P2       | M-24 | Add dunning cycle for payment failures | 1 hr
P2       | M-25 | Add startup check + warning for missing Razorpay keys | 15 min
P2       | M-26 | Implement AGS/NRPS endpoints or remove from config | 1 hr
P2       | M-27 | Persist LTI context to DB instead of in-memory | 1 hr
```

### Phase 5 — Code Quality & Performance [~8 hr]
```
Priority | Ref | Fix | Est. Time
P2       | M-28 | Centralize _cache lazy-import into cache.py helper | 30 min
P2       | M-29 | Switch cache.py to async Redis client | 1 hr
P2       | M-30 | Either wire domains properly or remove empty shims | 2 hr
P2       | M-31 | Add import time to logger.py | 1 min
P3       | L-18 | Remove dead _new_access_code() | 5 min
P3       | L-19 | Remove app/dependencies.py (update tests) | 1 hr
P3       | L-23 | Add return type annotations to FastAPI handlers | 4 hr
```

---

## Dashboard Deep-Dive Recheck — 2026-05-17

Scope: `https://app.procta.net/dashboard`, `https://app.procta.net/dashboard-react`, `app/static/dashboard.html`, and `app/dashboard-ui/src/*`.

### D-1: Logged-Out Dashboard Ships Full Admin UI

**Title:** Logged-out `/dashboard` renders the full teacher/admin dashboard behind the login overlay  
**Severity:** High  
**Area:** Dashboard / Security / UX / Performance  
**Steps to Reproduce:** Open `https://app.procta.net/dashboard` in a logged-out browser and inspect visible text / DOM controls.  
**Expected Result:** Only a login/recovery/signup surface should render before authentication.  
**Actual Result:** The page contains the full dashboard shell and many admin controls in the DOM: live sessions, results, student history, questions, chat, analytics, tools, invites, exports, billing, members, security, org settings, and super-admin tabs. Some controls are hidden, but the app structure and several live-session controls are visible.  
**Why It Matters:** This leaks product/admin surface area, increases initial page weight, makes the logged-out screen feel broken, and creates risk if any future handler becomes reachable before auth.  
**Suggested Fix:** Split `/login` from `/dashboard`, or render dashboard panels only after `_onAuthed()` succeeds. Lazy-load heavy panels after auth.  
**Files/Components Likely Involved:** `app/static/dashboard.html`, `app/routers/public.py`.

### D-2: Legacy Dashboard Export Functions Override Each Other

**Title:** Working export helper is overwritten by a broken duplicate `fetchBlob()`  
**Severity:** High  
**Area:** Dashboard / Results / Exports  
**Steps to Reproduce:** Inspect `app/static/dashboard.html` around lines 2897-2951.  
**Expected Result:** Export buttons should share one authenticated download helper.  
**Actual Result:** `fetchBlob`, `exportCSV`, and `dlPDF` are defined twice. The later `fetchBlob(url, filename)` calls `r.blob()` without defining `r`, overriding the earlier correct implementation. `dlAllScorecards()` also uses `"${BASE}/..."` as a literal string instead of a template literal.  
**Why It Matters:** CSV, Excel, report PDF, scorecard PDF, and scorecard ZIP are likely broken or inconsistent for teachers. These are core post-exam workflows.  
**Suggested Fix:** Delete the duplicate block, keep one `fetchBlob(url, filename, btnId)` implementation, and add browser tests for CSV, Excel, proctoring PDF, single scorecard PDF, and scorecard ZIP.  
**Files/Components Likely Involved:** `app/static/dashboard.html`, `app/routers/admin_scorecards.py`.

### D-3: Live Camera Cleanup Throws on Page Load

**Title:** `_liveViewSid` and timer globals are referenced before declaration  
**Severity:** High  
**Area:** Dashboard / Live Proctoring / Reliability  
**Steps to Reproduce:** Open `/dashboard` and check browser console; inspect `app/static/dashboard.html` around lines 2960-2989.  
**Expected Result:** Dashboard loads with no JavaScript errors and live-view cleanup is safe.  
**Actual Result:** Browser console reports `ReferenceError: _liveViewSid is not defined`. Source references `_liveViewSid`, `_liveViewLastFrameAt`, `_liveViewFrameTimer`, `_liveViewKeepaliveTimer`, and `_liveViewStaleTimer`, but declarations were not found.  
**Why It Matters:** Live camera is a high-trust proctoring feature. Cleanup failures can leave stale server-side live-view state and destabilize subsequent dashboard scripts.  
**Suggested Fix:** Declare live-view state before registering handlers, or move live-view state into a single initialized object. Add a no-console-error smoke test for `/dashboard`.  
**Files/Components Likely Involved:** `app/static/dashboard.html`, `app/routers/admin_liveview.py`.

### D-4: React Results PDF Opens Protected API Without Auth Header

**Title:** `/dashboard-react` PDF action uses `window.open()` on a bearer-protected endpoint  
**Severity:** High  
**Area:** Dashboard / Results / Auth  
**Steps to Reproduce:** Inspect `app/dashboard-ui/src/panels/ResultsPanel.jsx`, line ~145.  
**Expected Result:** PDF download should include the user's bearer token or use a short-lived signed URL.  
**Actual Result:** The PDF button opens `/api/v1/export-pdf/{session_id}` in a new tab. Browser navigation does not include `Authorization: Bearer ...`, while the endpoint requires `require_admin(request)`.  
**Why It Matters:** Authenticated users can still get a 401 when downloading PDFs from the React dashboard.  
**Suggested Fix:** Use `authFetch` to fetch the PDF as a blob and trigger a client-side download, or mint a short-lived signed download URL server-side.  
**Files/Components Likely Involved:** `app/dashboard-ui/src/panels/ResultsPanel.jsx`, `app/routers/admin_scorecards.py`.

### D-5: React Dashboard Role Metadata Is Not Enforced

**Title:** `TABS.roles` exists but every tab is rendered for every authenticated user  
**Severity:** Medium  
**Area:** Dashboard / RBAC / UX  
**Steps to Reproduce:** Inspect `app/dashboard-ui/src/App.jsx`; `TABS` defines roles, but rendering uses `TABS.map(...)` directly.  
**Expected Result:** Restricted tabs should be hidden or disabled based on server-provided role/capabilities.  
**Actual Result:** `All Orgs`, `Billing`, `Security`, `Members`, support/review/ops, and other tabs render for any authenticated user. Backend checks may still block data, but UI authorization is not reflected.  
**Why It Matters:** Users see controls they cannot use, which causes confusion and can become a privilege risk if any backend endpoint misses a check.  
**Suggested Fix:** Return capabilities from `/api/v1/auth/me` or `/api/v1/org`, filter tabs client-side, and keep backend checks authoritative.  
**Files/Components Likely Involved:** `app/dashboard-ui/src/App.jsx`, org role/capabilities endpoints.

### D-6: React Dashboard Has No Signup or Forgot-Password Path

**Title:** `/dashboard-react` login form only supports email/password sign-in  
**Severity:** Medium  
**Area:** Auth / Dashboard UX  
**Steps to Reproduce:** Open `https://app.procta.net/dashboard-react` while logged out.  
**Expected Result:** Users should be able to recover a password or start signup from the login page.  
**Actual Result:** The React login form has no forgot-password link and no signup/start-trial link.  
**Why It Matters:** If `/dashboard-react` becomes the canonical dashboard, users who land there cannot recover or create accounts.  
**Suggested Fix:** Add forgot-password and signup links matching the canonical teacher auth flow.  
**Files/Components Likely Involved:** `app/dashboard-ui/src/App.jsx`, `app/dashboard-ui/src/lib/auth.jsx`.

### D-7: React Dashboard Logout Redirects to a 404

**Title:** React logout sends users to `/login`, which does not exist  
**Severity:** Medium  
**Area:** Auth / Navigation  
**Steps to Reproduce:** Inspect `app/dashboard-ui/src/lib/auth.jsx:70-75`, then open `https://app.procta.net/login`.  
**Expected Result:** Logout should return users to a valid login screen.  
**Actual Result:** `/login` returns `404 Not Found`; the actual legacy login is embedded in `/dashboard`.  
**Why It Matters:** Logout can strand users on a 404 page, making the auth flow feel unreliable.  
**Suggested Fix:** Add a real `/login` route or redirect React logout to `/dashboard`.  
**Files/Components Likely Involved:** `app/dashboard-ui/src/lib/auth.jsx`, `app/routers/public.py`.

### D-8: React Auth Client Does Not Send CSRF Header

**Title:** React `authFetch()` sends bearer tokens but not the JWT CSRF claim  
**Severity:** Medium  
**Area:** Security / Dashboard API Client  
**Steps to Reproduce:** Compare `app/dashboard-ui/src/lib/auth.jsx` with legacy `authFetch()` in `app/static/dashboard.html`.  
**Expected Result:** Mutating requests should include `X-CSRF-Token` when the JWT includes a CSRF claim.  
**Actual Result:** Legacy dashboard extracts and sends the CSRF claim; React dashboard does not. Current backend backward compatibility appears to allow absent headers, but stricter CSRF enforcement would break React dashboard mutations.  
**Why It Matters:** Security behavior differs between two dashboard implementations. This makes future hardening risky and inconsistent.  
**Suggested Fix:** Centralize auth/refresh/CSRF handling in one API client and use it for both dashboards.  
**Files/Components Likely Involved:** `app/dashboard-ui/src/lib/auth.jsx`, `app/static/dashboard.html`, `app/auth/tokens.py`.

### D-9: Dashboard Source and Live Behavior Appear Out of Sync

**Title:** Live browser behavior did not match checked-out dashboard source for signup entry  
**Severity:** Medium  
**Area:** DevOps / Release Management / QA  
**Steps to Reproduce:** Live browser pass showed `Don't have an account? Create one` and hidden inline signup fields; checked-out `app/static/dashboard.html` shows `Start free trial` linking to `https://procta.net/signup`.  
**Expected Result:** Production should be traceable to a known commit and match the audited source.  
**Actual Result:** Observed live behavior and local source disagree for the signup path.  
**Why It Matters:** QA cannot reliably verify fixes if production is not tied to a commit. It also makes debugging user reports much harder.  
**Suggested Fix:** Expose build commit/version in `/health` or dashboard HTML, and deploy from git with a scripted release path only.  
**Files/Components Likely Involved:** deploy workflow, `scripts/server_deploy.sh`, dashboard static assets.

### D-10: React Results Panel Has a Sticky Loading State Edge Case

**Title:** `ResultsPanel` starts `loading=true` and returns early when no exam is selected  
**Severity:** Low  
**Area:** Dashboard / UX  
**Steps to Reproduce:** Inspect `app/dashboard-ui/src/panels/ResultsPanel.jsx:12-30`.  
**Expected Result:** No-exam state should explicitly clear loading state.  
**Actual Result:** `loadResults()` returns early on `!currentExamId` without setting `loading=false`. The visible no-exam message currently masks this, but the internal state remains inconsistent until an exam is selected.  
**Why It Matters:** Small async state bugs become confusing in multi-tab dashboards and can cause spinners or stale UI after route/state changes.  
**Suggested Fix:** Set `loading=false` before returning when no exam is selected.  
**Files/Components Likely Involved:** `app/dashboard-ui/src/panels/ResultsPanel.jsx`.

---

### D-11: Student History Tab Completely Broken — Wrong API Caller

**Title:** `refreshStudentList`, `viewStudentHistory`, and `loadTemplates` call `api()` instead of `authFetch()`  
**Severity:** High  
**Area:** Dashboard / Student History / Templates  
**Steps to Reproduce:** Click the Student History tab; open the Templates section in Tools.  
**Expected Result:** Student list and templates load normally.  
**Actual Result:** Both functions call a global `api()` that either does not exist or does not attach the `Authorization: Bearer` header. Result: `ReferenceError: api is not defined` (if `api` is absent) or a 401 response silently coerced into `{students:[]}`.  
**Exact locations:**
- `app/static/dashboard.html:6256` — `api('/api/v1/student-search?q=...')`
- `app/static/dashboard.html:6318` — `api('/api/v1/student-history/...')`
- `app/static/dashboard.html:6429` — `api('/api/v1/templates')`

**Suggested Fix:** Replace all three with `authFetch(BASE + '/api/v1/...')` to match every other authenticated call in the file.  
**Files Involved:** `app/static/dashboard.html`.

---

### D-12: Alert Toast "View Timeline" Button Always Throws ReferenceError

**Title:** `viewSession` is declared inside `viewSessionTimeline` — not globally accessible  
**Severity:** High  
**Area:** Dashboard / Live Proctoring / Alerts  
**Steps to Reproduce:** Trigger any real-time risk alert with severity high/critical → toast appears → click "View Timeline".  
**Expected Result:** Session timeline opens in a new tab.  
**Actual Result:** `ReferenceError: viewSession is not defined`. The function is declared at `dashboard.html:6385` but it sits inside the body of `viewSessionTimeline()` at `dashboard.html:6383`, making it a local function. Global `onclick="viewSession('...')"` attributes at line 6583 (alert toast) and line 6370 (history detail table) cannot reach it.  
**Suggested Fix:** Move `viewSession` out of `viewSessionTimeline` to the top-level function scope.  
**Files Involved:** `app/static/dashboard.html:6383-6392`.

---

### D-13: Real-Time Alert Toasts Crash Immediately — `esc()` Undefined

**Title:** `handleRealtimeAlert()` calls `esc()` which is not defined in `dashboard.html`  
**Severity:** High  
**Area:** Dashboard / Live Proctoring / Alerts  
**Steps to Reproduce:** Any incoming SSE event of type `risk_alert` fires `handleRealtimeAlert`. The function calls `esc()` four times (lines 6576, 6580, 6581, 6583) before writing to the DOM.  
**Expected Result:** Alert toast renders with escaped HTML content.  
**Actual Result:** `ReferenceError: esc is not defined` — the entire toast handler throws; no toast is shown; violation alert sounds play but nothing appears. This makes the live proctoring real-time alert system completely broken when the SSE stream delivers risk events.  
**Note:** `esc()` is used in 6+ other places (lines 4327, 4330, 4473, 4548, 4614, 5103) — it must come from `_safe.js`. If that file is ever not loaded (CDN failure, path error), all these call sites fail.  
**Suggested Fix:**
1. Confirm `_safe.js` is loaded before `dashboard.html` scripts; add a startup guard.
2. Rename all calls to `_escHtml()` (already defined inline at line 4085) to eliminate the `_safe.js` dependency for HTML escaping.

**Files Involved:** `app/static/dashboard.html:6546-6588`, `app/static/_safe.js`.

---

### D-14: `refreshLive` Calls Wrong URL — Missing `/api/v1/` Prefix

**Title:** Live sessions refresh fetches from `${BASE}/sessions` instead of `${BASE}/api/v1/sessions`  
**Severity:** High  
**Area:** Dashboard / Live Sessions  
**Steps to Reproduce:** Open Live Sessions tab; sessions list refreshes every 10s via `refreshLive()`.  
**Expected Result:** Sessions load from `/api/v1/admin/sessions`.  
**Actual Result:** Request goes to `https://app.procta.net/sessions` — a route that does not exist → 404 every poll cycle. The catch is empty so the panel silently shows stale/empty data indefinitely.  
**Exact location:** `app/static/dashboard.html:3098`
```js
const r = await authFetch(`${BASE}/sessions${_examQuery('?')}`);
// Should be:
const r = await authFetch(`${BASE}/api/v1/admin/sessions${_examQuery('?')}`);
```
**Suggested Fix:** Add the missing `/api/v1/admin` prefix. Verify the correct server-side route path in `app/routers/admin_sessions.py` or equivalent.  
**Files Involved:** `app/static/dashboard.html:3098`.

---

### D-15: `doInviteTeacher` Writes Status to Wrong DOM Element (Duplicate ID Bug)

**Title:** Teacher-invite send/status functions target the student-invite `#invite-result` span  
**Severity:** Medium  
**Area:** Dashboard / Tools / Invites  
**Steps to Reproduce:** Open the "Invite Teacher" modal and send an invite.  
**Expected Result:** Status message appears in the invite-teacher modal feedback area.  
**Actual Result:** `document.getElementById('invite-result')` returns the first element in DOM order — which is inside the **student** invites panel (line 858), not the teacher invite modal (line 1359). Status messages from teacher-invite flows either disappear silently or appear in the wrong UI section.  
**Root cause:** Two elements share `id="invite-result"`. Functions `doInviteTeacher` (line 3019), `showInviteTeacherModal` (line 3127), `pullGroupIntoInvites` (line 5864), `_copyInviteLink` (line 6061), `resendInvite` (line 6069), and `resendBouncedInvites` (line 6093) all call `getElementById('invite-result')` — all hit the wrong element.  
**Suggested Fix:** Rename one of the two elements (e.g., teacher-invite modal element → `id="teacher-invite-result"`); update all functions that target it.  
**Files Involved:** `app/static/dashboard.html:858, 1359, 3019, 3127, 5864, 6061, 6069, 6093`.

---

### D-16: Missing `highlights` Guard Causes TypeError in Two Places

**Title:** `h.summary.highlights.length` throws if `highlights` is absent from the summary payload  
**Severity:** Medium  
**Area:** Dashboard / Student History / Forensics  
**Steps to Reproduce:** View student history for a session whose AI summary exists but `highlights` array is missing or null.  
**Expected Result:** Summary toggle renders safely with an empty highlights section.  
**Actual Result:**
- `renderHistoryDetail` at line 6369: `h.summary&&h.summary.highlights.length` → TypeError if `highlights` is `undefined`
- `toggleHistorySummary` at line 6414: `s.highlights.length` → same crash
- Same pattern exists in `renderTimelineSummary` at line 4588

**Suggested Fix:** Guard all three with `(h.summary?.highlights || []).length` or an explicit null check.  
**Files Involved:** `app/static/dashboard.html:4588, 6369, 6414`.

---

### D-17: Forensics Timeline Crashes — Undefined Variable `d`

**Title:** `viewTimeline()` sets `tlData` but then reads from an undefined `d`  
**Severity:** High  
**Area:** Dashboard / Forensics / Evidence  
**Steps to Reproduce:** Open Forensics tab → select a session → click "Load Timeline".  
**Expected Result:** Timeline renders with summary.  
**Actual Result:** After `tlData = await r.json()` (line 4470), the next line calls `renderTimelineSummary(d.summary || null)` where `d` is not in scope — the variable should be `tlData`. This throws `ReferenceError: d is not defined` and aborts rendering.  
**Exact location:** `app/static/dashboard.html:4470-4471`  
**Suggested Fix:** Change `d.summary` → `tlData.summary`.  
**Files Involved:** `app/static/dashboard.html:4470-4471`.

---

## Quick Wins (Fix in <30 min each)

| # | Fix | Time | File |
|---|-----|------|------|
| 1 | Fix student-ui login endpoint path | 1 min | `app/student-ui/src/main.jsx:21` |
| 2 | Add missing `/privacy` route | 5 min | `app/routers/public.py` |
| 3 | Fix `verify_webhook` import path | 1 min | `app/routers/public.py:551` |
| 4 | Add `import time` to logger.py | 1 min | `app/logger.py` |
| 5 | Remove dead `return Fernet(key)` line | 1 min | `app/services/totp.py:32` |
| 6 | Add CSRF PATCH method to middleware | 1 min | `app/main.py:410` |
| 7 | Restrict CORS methods/headers | 5 min | `app/main.py:208-209` |
| 8 | Add CSP directives | 5 min | `app/main.py:301` |
| 9 | Add `_req_errors` removal (unused global) | 1 min | `app/routers/public.py:110,119` |
| 10 | Fix `_verif_log` → `_admin_log` | 1 min | `app/routers/admin_verification.py:46` |
| 11 | Fix `ts_to_id` import | 1 min | `app/routers/exam.py:21` |
| 12 | Remove unused imports in utils/__init__.py | 1 min | `app/utils/__init__.py:7,8,10` |
| 13 | Add `__pycache__` to .gitignore | 1 min | `.gitignore` |
| 14 | Fix `cat` → `logger.warning` in event_bus.py | 2 min | `app/event_bus.py:74,148` |
| 15 | Add `UNIQUE (org_id, period_start)` to phase57 | 5 min | `migrations/phase57_usage_tracking.sql` |
| 16 | Fix `refreshLive` missing `/api/v1/` prefix | 2 min | `dashboard.html:3098` |
| 17 | Fix `tlData` vs `d` typo in timeline loader | 1 min | `dashboard.html:4471` |
| 18 | Move `viewSession` to top-level scope | 2 min | `dashboard.html:6385` |
| 19 | Replace `api()` with `authFetch()` in 3 places | 3 min | `dashboard.html:6256,6318,6429` |
| 20 | Add `(summary?.highlights \|\| []).length` guards | 5 min | `dashboard.html:4588,6369,6414` |

---

## Security Vulnerability Summary

| Vuln Type | Count | Critical | High | Medium | Low |
|-----------|-------|----------|------|--------|-----|
| Auth bypass (TOTP, email verification) | 2 | 2 | — | — | — |
| Missing rate limiting | 4 | — | — | 3 | 1 |
| No server-side session revocation | 2 | — | 1 | 1 | — |
| CSRF gaps | 2 | — | 1 | 1 | — |
| CORS/CSP misconfig | 3 | — | — | 3 | — |
| Input validation gaps | 4 | — | 1 | 3 | — |
| Token design flaws (no jti, no role) | 2 | — | 1 | 1 | — |
| Info leakage | 3 | — | — | 1 | 2 |
| **TOTAL** | **22** | **2** | **4** | **13** | **3** |

---

---

# Marketing Website Deep-Dive Audit

**Scope:** All 15 pages, 14 shared components, auth components, CSS/build pipeline, SEO metadata  
**Stack:** Vite + React 18, react-router-dom v6, react-helmet-async, Turnstile CAPTCHA, react-icons  
**Pages:** Landing, Signup (2 variants), Pricing, Downloads, Blog (×3), Privacy, Terms, Acceptable Use, Cookie Policy, LTI Setup, Contact, Checkout  
**Audit date:** 2026-05-16

## Critical Issues (6)

| # | Page | Issue | Impact | File:Line |
|---|------|-------|--------|-----------|
| C7 | **Signup** | Client-side password min 8 chars vs backend expects 10; passwords accepted by client are rejected by server with no user feedback | Users cannot complete signup with 8–9 char passwords; zero error shown | `website/src/pages/Signup.jsx:193` vs `app/routers/public.py:228` |
| C8 | **Signup** | No client-side password complexity validation (uppercase, number, special char) — backend enforces it | Signup silently fails on submit for any password lacking complexity | `website/src/pages/Signup.jsx:193` |
| C9 | **Landing** | No skip-to-content link or skip navigation link anywhere | Full keyboard trap for screen-reader users; WCAG 2.4.1 failure | `website/src/components/Layout.jsx:1-50` |
| C10 | **Landing** | Focus indicators missing on 4 interactive elements (mobile hamburger, FAQ buttons, feature cards, CTA buttons) | Impossible to navigate by keyboard; WCAG 2.4.7 failure | `website/src/components/Navbar.jsx:120`, `website/src/components/FAQ.jsx:45-80`, `website/src/components/Features.jsx`, `website/src/components/CTA.jsx` |
| C11 | **Build/SEO** | `PROCTA_URL` env var is **not** prefixed with `VITE_` in `.env.production` | Vite strips non-VITE_ env vars at build time; signup form submits to `undefined` URL in production build — POST `/api/auth/signup` fails silently | `website/.env.production:1` |
| C12 | **SEO** | Signup page has zero `<Helmet>` metadata — no title, meta description, or Open Graph tags | Zero SEO visibility; share preview renders as blank URL; no canonical URL | `website/src/pages/Signup.jsx:1-50` |

## High-Severity Issues (16)

| # | Page | Issue | Impact | File:Line |
|---|------|-------|--------|-----------|
| H17 | **Signup** | Demo request form has no CAPTCHA/Turnstile protection | Bot can submit unlimited demo requests; no rate limit | `website/src/pages/Signup.jsx:~260` |
| H18 | **Landing** | FAQ accordion fails keyboard navigation in 3 places — Enter/Space don't toggle panels, focus doesn't move between items, aria-expanded not set | Screen reader users cannot access FAQ content | `website/src/components/FAQ.jsx:45-80` |
| H19 | **Landing** | Hero CTA "Request Demo" form has no client-side validation — empty email/name accepted | Submission succeeds but API returns 422; no user feedback | `website/src/components/Hero.jsx:~90-130` |
| H20 | **Landing** | Footer company contact email (info@procta.net) returns 550 — mailbox doesn't exist, domain has no MX records | All footer email clicks bounce | Verified via DNS/MX lookup |
| H21 | **Landing** | No `<Helmet>` on Landing page — no page title, meta description, OG tags | Zero SEO; share renders blank | `website/src/pages/Landing.jsx:1-50` |
| H22 | **Pricing** | Trial duration mismatch: Pricing page "Start your 14-day free trial" but backend `TRIAL_PERIOD_DAYS = 7` | Users expect 14 days but get 7 | `website/src/pages/Pricing.jsx:77,258` vs `app/services/billing.py:~20` |
| H23 | **Download** | Download page has no error handling when release API fails — produces a broken download with no user feedback | Users see a failed download with zero indication of what went wrong | `website/src/pages/Download.jsx:~100-150` |
| H24 | **Privacy** | No DPDP (Digital Personal Data Protection) compliance section or grievance officer contact | Non-compliant with India DPDP Act 2023; no grievance redressed mechanism | `website/src/pages/Privacy.jsx` |
| H25 | **Checkout** | Checkout route is **disabled** in router — no way to reach the page | Entire checkout/payment flow is dead code | `website/src/App.jsx:~50` |
| H26 | **SEO** | Privacy page has no `<Helmet>` — no meta description, title, OG, canonical URL | Zero search discoverability; share renders blank | `website/src/pages/Privacy.jsx:1-50` |
| H27 | **SEO** | Terms page has no `<Helmet>` — no meta description, title, OG, canonical URL | Zero search discoverability; share renders blank | `website/src/pages/Terms.jsx:1-50` |
| H28 | **SEO** | 3 pages missing canonical URLs (Signup, Privacy, Terms) — no `<link rel="canonical">` in Helmet | Duplicate content penalty risk | `website/src/pages/Signup.jsx`, `Privacy.jsx`, `Terms.jsx` |
| H29 | **PWA** | PWA manifest icon references `/manifest-icon-192.png` which returns 404 | PWA install prompt broken; console errors on all page loads | `website/index.html:~10` |
| H30 | **PWA** | Service worker registration targets `demo.html` instead of `/` or `index.html` | Service worker scope mismatch — SW never activates; no offline support | `website/src/main.jsx:~15` |
| H31 | **Build** | ~40% of CSS bundle is unused — 2 large imported CSS files (bootstrap-grid.min.css, animate.css) never used by any component | 48 KB unnecessary CSS in every page load | `website/src/index.css`, CSS bundle analysis |
| H32 | **Fonts** | `font-display` not set on any of the 3 custom `@font-face` declarations | 1–3s blank text (FOIT) on slow connections; LCP score degraded | `website/src/index.css:~5-30` |

## Medium-Severity Issues (34)

| # | Page | Issue | Impact | File:Line |
|---|------|-------|--------|-----------|
| M37 | **Signup** | No confirm-password field | Users can't catch typos — locked out after signup | `website/src/pages/Signup.jsx` |
| M38 | **Signup** | No password length indicator or strength meter | No user feedback while typing password | `website/src/pages/Signup.jsx` |
| M39 | **Signup** | Turnstile CAPTCHA rendered at fixed 275px width — breaks on screens <320px | Mobile users on small devices can't complete CAPTCHA | `website/src/pages/Signup.jsx:~200` |
| M40 | **Signup** | Google OAuth buttons have no hover state on mobile / stacked layout | Tappable but no visual feedback; users may not realize they're interactive | `website/src/pages/Signup.jsx:~150-180` |
| M41 | **Signup** | Field-level error display not implemented — all errors render in a single generic div at the top | UX confusion — which field failed? | `website/src/pages/Signup.jsx:~250` |
| M42 | **Signup** | On success, `window.location.href = "/dashboard"` creates abrupt UX — no success toast or confirmation | Users don't know signup completed | `website/src/pages/Signup.jsx:~270` |
| M43 | **Signup** | No nudge or guidance after OAuth cancellation — user is returned to blank form | Abandonment risk | `website/src/pages/Signup.jsx:~180` |
| M44 | **Signup** | Field-level `tabindex` order skips Turnstile iframe — keyboard user jumps from password directly to submit | Turnstile challenge unreachable by keyboard | `website/src/pages/Signup.jsx` |
| M45 | **Signup** | No `autocomplete` attributes on any form field | Password managers can't fill; accessibility degraded | `website/src/pages/Signup.jsx:~190-200` |
| M46 | **Landing** | All 14 page sections eager-loaded in a single render — no lazy loading or code splitting | ~185 KB initial bundle; 2.1s FCP on 3G throttled | `website/src/pages/Landing.jsx` |
| M47 | **Landing** | Hero dashboard mockup image has no ARIA label or `alt` text | Screen reader reads nothing for the primary visual | `website/src/components/Hero.jsx:~60` |
| M48 | **Landing** | UseCases section — 5 card images have empty `alt=""` but are decorative-with-context — should have descriptive alt | Screen reader users miss context for use case cards | `website/src/components/UseCases.jsx:~30-80` |
| M49 | **Landing** | 2 CTA buttons use `onClick` + `window.location.href` instead of `<a href>` / React Router `<Link>` | No right-click open in new tab; cmd+click broken; WCAG 4.1.2 | `website/src/components/Header.jsx:~40`, `website/src/components/Hero.jsx:~100` |
| M50 | **Landing** | No viewport height check on mobile — `100vh` Hero causes browser chrome collapse on iOS Safari | Hero bottom 15% cut off on iOS; "Get Started" CTA hidden | `website/src/components/Hero.jsx:~1` |
| M51 | **Landing** | Page-load spinner uses nested `<div>` instead of `aria-busy="true"` | Screen reader not alerted to loading state | `website/src/components/Layout.jsx:~30` |
| M52 | **Landing** | Duplicate `id="features"` between Landing page and Signup page | `document.getElementById` returns first match only; scroll-to broken on one page | `website/src/pages/Landing.jsx:~100`, `website/src/pages/Signup.jsx:~100` |
| M53 | **Landing** | Enterprise CTA link has `href="#"` — no action, no modal | Leads nowhere; dead-end UX | `website/src/components/UseCases.jsx:~120` |
| M54 | **Landing** | FlatIcon references use generic alt text or no alt | Accessibility degradation | Multiple components |
| M55 | **Landing** | No cache-control / cache policy strategy on any static asset | No browser caching; every visit re-downloads all assets | Caddyfile / Vite config |
| M56 | **Pricing** | "Contact Sales" button on Pro plan leads to `/dashboard/login` (404 — no such route) | High-intent lead goes to dead page | `website/src/pages/Pricing.jsx:~180` |
| M57 | **Pricing** | "Start Trial" on Growth plan links to generic `/signup` with no plan preselected | User must re-select plan after signup | `website/src/pages/Pricing.jsx:~160` |
| M58 | **Pricing** | Starter plan says "Free" in header but "No card needed" in body — contradictory | Confuses users about pricing model | `website/src/pages/Pricing.jsx:~120-140` |
| M59 | **Pricing** | No LTI-specific plan or pricing information anywhere | LTI users get no pricing transparency | `website/src/pages/Pricing.jsx` |
| M60 | **Blog** | All 3 blog posts show "Invalid date" on `<time>` element hover / tooltip — date parsing fails | Dates unreadable | `website/src/pages/Blog1.jsx`, `Blog2.jsx`, `Blog3.jsx` |
| M61 | **Terms** | LICENSE hyperlink points to `http://localhost:5173` | Broken link in production | `website/src/pages/Terms.jsx:163` |
| M62 | **LTI Setup** | Hardcoded `procta.net` domain references — should use env var for custom domain | Fork/deploy users must manually replace all occurrences | `website/src/pages/LTISetup.jsx:~50-200` |
| M63 | **Download** | No `aria-live` region for download status announcements | Screen reader user gets no feedback on download state | `website/src/pages/Download.jsx` |
| M64 | **Download** | No OS-detection analytics event — cannot track which platforms users download for | Missing conversion data | `website/src/pages/Download.jsx` |
| M65 | **Build** | Sourcemap files served in production builds — `*.js.map` accessible at `/assets/*.js.map` | Source code exposed; 2× bundle size on disk | Vite config |
| M66 | **Build** | No `preconnect` to Turnstile CDN or Google Fonts CDN | 300ms+ connection delay on CAPTCHA and font loads | `website/index.html` |
| M67 | **Build** | 3 render-blocking CSS files (all loaded synchronously in `<head>`) | FCP delayed by cumulative CSS download | `website/index.html` |
| M68 | **Build** | 2 unused JS bundles loaded on all pages (analytics.js, chatbot.js) — both 404 | 2× 404 console errors on every page | `website/index.html` |
| M69 | **SEO** | No `hreflang` tags despite serving English-only content to global audience | International SEO miss | All pages |
| M70 | **SEO** | Blog posts have no structured data (Article schema, breadcrumb, author) | Zero rich result eligibility in SERP | All blog pages |

## Low-Severity Issues (15)

| # | Page | Issue | File:Line |
|---|------|-------|-----------|
| L17 | Signup | Transition flicker on form state change | `website/src/pages/Signup.jsx` |
| L18 | Landing | Mobile footer Terms link to `localhost:5173` | `website/src/components/Footer.jsx:~100` |
| L19 | Landing | No `preload` on Hero background image | `website/index.html` |
| L20 | Landing | CSS custom properties missing fallback values in 3 places | `website/src/index.css` |
| L21 | Pricing | Accessibility Use page missing effective/updated date | `website/src/pages/AcceptableUse.jsx` |
| L22 | Privacy | "Last updated" date is hardcoded static value | `website/src/pages/Privacy.jsx` |
| L23 | Cookie Policy | No cookie categories listed — single blanket statement | `website/src/pages/CookiePolicy.jsx` |
| L24 | Download | No `download` attribute on download button — browser opens binary in new tab | `website/src/pages/Download.jsx` |
| L25 | All | No `link rel="dns-prefetch"` for any external domain | `website/index.html` |
| L26 | All | No `link rel="preload"` for critical hero image | `website/index.html` |
| L27 | All | CSS custom properties lack fallback values in 3 components | `website/src/index.css` |
| L28 | All | No `<noscript>` fallback message for users with JS disabled | `website/index.html` |
| L29 | Blog | No author byline on any blog post | All blog pages |
| L30 | Blog | No social share buttons on blog posts | All blog pages |
| L31 | Download | No analytics tracking on download click event | `website/src/pages/Download.jsx` |

## Marketing Website Severity Summary

| Category | Critical | High | Medium | Low | Total |
|----------|----------|------|--------|-----|-------|
| Signup page | 2 | 1 | 9 | 1 | 13 |
| Landing + Components | 2 | 4 | 10 | 3 | 19 |
| Pricing / Blog / Pages | 0 | 4 | 9 | 8 | 21 |
| CSS / Build / SEO / PWA | 2 | 7 | 7 | 3 | 19 |
| **Total** | **6** | **16** | **35** | **15** | **72** |

## Marketing Website Quick Wins

| Priority | Fix | Est. Time | Files |
|----------|-----|-----------|-------|
| P0 | Add `VITE_` prefix to `PROCTA_URL` in `.env.production` | 1 min | `website/.env.production` |
| P0 | Bump `minLength` from 8 to 10 on Signup password field | 2 min | `website/src/pages/Signup.jsx` |
| P0 | Add `<Helmet>` to Signup page with title, description, OG, canonical | 5 min | `website/src/pages/Signup.jsx` |
| P0 | Add skip-to-content link in Layout component | 10 min | `website/src/components/Layout.jsx` |
| P0 | Add `font-display: swap` to all `@font-face` rules | 2 min | `website/src/index.css` |
| P1 | Add confirm-password field to Signup | 10 min | `website/src/pages/Signup.jsx` |
| P1 | Add client-side password complexity validation | 10 min | `website/src/pages/Signup.jsx` |
| P1 | Fix trial duration to match backend (14d → 7d or vice versa) | 2 min | `website/src/pages/Pricing.jsx` |
| P1 | Enable Checkout route in router | 2 min | `website/src/App.jsx` |
| P1 | Fix Terms LICENSE link from localhost to production URL | 1 min | `website/src/pages/Terms.jsx` |
| P1 | Fix PWA manifest icon path | 2 min | `website/index.html` |
| P1 | Fix service worker registration target | 2 min | `website/src/main.jsx` |
| P1 | Remove unused CSS imports (bootstrap-grid, animate.css) | 5 min | `website/src/index.css` |
| P2 | Add `<Helmet>` to Privacy and Terms pages | 5 min | `Privacy.jsx`, `Terms.jsx` |
| P2 | Disable sourcemaps in production Vite build | 1 min | `vite.config.js` |
| P2 | Fix FAQ keyboard accessibility (Enter/Space toggle, aria-expanded) | 20 min | `website/src/components/FAQ.jsx` |
| P2 | Add CAPTCHA to demo request form | 15 min | `website/src/pages/Signup.jsx` |
| P2 | Add error handling to Download page | 10 min | `website/src/pages/Download.jsx` |

---

# API Backend Deep-Dive Audit

**Scope:** 28 routers (13,298 lines), 4 auth modules (448 lines), 26 services (3,888 lines), 8 LTI modules (1,377 lines), 7 middlewares, 9 domain shims, 13 top-level modules (3,618 lines) — 88 files, 22,653 lines total  
**Stack:** FastAPI + Supabase (PostgREST) + asyncpg + Redis + JWT (PyJWT) + Razorpay + LTI 1.3 + Resend email + httpx  
**Audit dimensions:** Security (auth bypass, injection, crypto), Data integrity (race conditions, consistency), RBAC (IDOR, org isolation), Business logic (billing, exams, LTI), Infrastructure (startup, caching, logging, background tasks)  
**Audit date:** 2026-05-17

## Executive Summary

The API backend was audited across **5 parallel deep-dives** covering auth/security, exams/sessions, admin/business logic, LTI/Google Classroom, and infrastructure. **157 new issues** were found (17 critical, 36 high, 62 medium, 42 low). Key themes:

1. **Razorpay webhooks fully bypassable in sandbox** — no signature verification, no rate limit (C13, C14)
2. **LTI 1.3 replay attack** — nonce consumption is non-atomic (C17)
3. **Exam submission data loss** — session marked COMPLETED before answers flushed to DB (C15)
4. **Synchronous httpx blocks event loop** — all LLM calls freeze the async worker (C21)
5. **CSRF protection is opt-in** — backwards-compat flags disable it by default (H38)
6. **Student tokens cannot be revoked** — no `jti` claim in student JWTs (H36)
7. **17 endpoints have zero rate limiting** — OAuth callback, LTI login/launch, billing webhook, email webhook, demo requests (multiple H items)
8. **Non-atomic delete-and-reinsert** can permanently lose all exam questions (C16)
9. **TOTP encryption key is optional** — ephemeral fallback causes permanent lockout on restart (C14 duplicate from original audit)
10. **Email enumeration vectors** — 4+ endpoints leak registration status via response/timing

## Critical Issues (17)

| # | Component | Sub-area | Issue | Impact | File:Line |
|---|-----------|----------|-------|--------|-----------|
| C13 | **Billing** | Webhook | Razorpay webhook signature verification **always returns True** in sandbox mode (when RAZORPAY_KEY_ID or WEBHOOK_SECRET is missing). No rate limit on endpoint. | Anyone can forge subscription events (activate/cancel/pause any org's subscription) using guessable mock IDs | `app/services/billing.py:74-87`, `app/routers/billing.py:107-118` |
| C14 | **Billing** | Checkout | Payment verification auto-succeeds in sandbox mode — returns `{"verified": True, "sandbox": True}` for ANY request body, no HMAC check | Free access to paid features in any deployment missing real Razorpay keys | `app/routers/checkout.py:148-151` |
| C15 | **Exam** | Submission | Session marked `COMPLETED` (Phase 2) before final answers flushed to DB (Phase 3). If Phase 3 fails, session is terminal COMPLETED with missing answers | Permanent answer data loss — student cannot retry, teacher sees incomplete submission | `app/routers/exam.py:858-910` |
| C16 | **Exam** | Questions | `update_questions` deletes ALL questions for teacher/exam, then re-inserts. Crash between delete and insert = permanent question loss. No transaction | Complete loss of exam questions with no recovery | `app/routers/question_bank.py:662-698` |
| C17 | **LTI** | Nonce | `_consume_nonce` uses non-atomic `cache.get()` + `cache.delete()`. Two concurrent requests can both see the nonce before either deletes it | id_token replay attack — attacker can reuse a captured token to authenticate as another user | `app/lti/launch.py:50-60` |
| C18 | **LTI** | State | In-memory nonce/state dicts (`_nonces`, `_states`) have no eviction when Redis is unavailable. OIDC login init allocates entries that are never cleaned up | Memory-exhaustion DoS — repeated OIDC login requests grow dicts without bound | `app/lti/launch.py:42-47,63-69` |
| C19 | **LTI** | Keys | Key ID hardcoded as `"lti-key-1"`. JWKS cached forever — never invalidated after initial generation. No key rotation support | Key compromise cannot be remediated without downtime and full re-registration with every LMS | `app/lti/key.py:23,91-121` |
| C20 | **Auth** | Tokens | OAuth JWT delivered in URL fragment — but `&` separator used when `return_to` contains `#`, placing token in query string instead of fragment | JWT leaked to server logs, Referer headers, browser history → token theft | `app/routers/auth.py:1719-1721` |
| C21 | **Infra** | LLM | `httpx.Client()` (synchronous) used in all LLM calls inside async endpoints. Blocks event loop for up to 30s per call | Under concurrent load, a single slow Groq call stalls ALL requests on the worker | `app/llm.py:119-120` |
| C22 | **Infra** | LLM | LLM provider error body logged verbatim (truncated 500 chars). Some providers echo request payload in errors | PII/exam content leak into structured logs (violates FERPA/DPDP) | `app/llm.py:127-128` |
| C23 | **Infra** | Middleware | CSRF middleware bare `except: pass` swallows ALL exceptions from JWT decoding — including infrastructure errors. No logging | Defense-in-depth layer silently becomes a no-op on any error | `app/main.py:447-448` |
| C24 | **Infra** | Logging | Global exception handler (`@app.exception_handler(Exception)`) uses `print()` and `traceback.print_exc()` instead of the structured JSON logger | Unhandled 500s bypass structured logging — invisible in Datadog/ELK | `app/main.py:478-485` |
| C25 | **Infra** | Startup | `_room_frame_cleanup_loop` asyncio task never cancelled on shutdown — only cancellation logic only checks for "reminder" in task names | Task leak on every reload; "Task was destroyed but it is pending" warnings | `app/main.py:131-137,188-200` |
| C26 | **Auth** | Crypto | TOTP encryption key optional — `Fernet.generate_key()` produces ephemeral key when `TOTP_ENCRYPTION_KEY` not set | Permanent lockout on restart — all 2FA-secret-encrypted data becomes undecryptable | `app/services/totp.py:18-27` |
| C27 | **Auth** | Database | PostgREST filter parameter injection — `_build_params` concatenates filter values without escaping PostgREST-special characters (`.`, `(`, `)`, `,`) | Attacker-controlled filter values can inject additional operators → unauthorized data access | `app/database.py:204-209` |
| C28 | **Exam** | Submission | TOCTOU race on exam submission — status check and upsert are not atomic. Two concurrent requests can both pass the COMPLETED check | Score corruption — last-writer-wins with potentially mixed answer payloads | `app/routers/exam.py:806-809` |
| C29 | **Auth** | OAuth | OAuth student account creation auto-verifies email (`email_verified_at` set unconditionally). No check that OAuth email belongs to the claimed user | Attacker with any Google account can create student accounts with their own email, bypassing verification | `app/services/auth_oauth.py:285-292` |

## High-Severity Issues (36)

| # | Component | Sub-area | Issue | Impact | File:Line |
|---|-----------|----------|-------|--------|-----------|
| H33 | **Auth** | Passwords | Org invite acceptance only checks `len(password) < 8`, never calls `validate_password()` | Weak passwords via invites undermine entire password policy (allows `password`) | `app/routers/auth.py:648-712` |
| H34 | **Auth** | Refresh | Refresh token rotation has race condition — new token inserted before old revoked. Crash between = two active tokens | Replay amplification — single stolen refresh token can create parallel sessions | `app/routers/auth.py:136-161` |
| H35 | **Auth** | Enumeration | Resend-verification endpoint returns different responses: "already_verified" vs "sent" vs "not_found" | Email enumeration — attacker can identify registered emails and their verification status | `app/routers/auth.py:1223-1235` |
| H36 | **Auth** | Tokens | Student auth tokens lack `jti` claim — session revocation operates by `jti`. Student tokens cannot be individually revoked | Stolen student tokens remain valid for full 12h TTL with no kill switch | `app/auth/tokens.py:131-138` |
| H37 | **Auth** | CSRF | `verify_csrf` returns `True` when header absent OR when JWT has no `csrf` claim ("backward compat") | CSRF protection is effectively opt-in — most request flows bypass it entirely | `app/auth/tokens.py:60-72` |
| H38 | **Auth** | Session | Supabase auth mode logout only revokes local JTI — does NOT revoke Supabase refresh token | "Logout" does not fully terminate session — cached refresh token can re-authenticate | `app/routers/auth.py:1390-1417` |
| H39 | **Auth** | Enumeration | `/api/v1/lookup-teacher` returns 200 with data vs 404 "No teacher found" — different status codes | Email enumeration of registered teachers | `app/routers/public.py:311-333` |
| H40 | **Auth** | Lockout | Account lockout fails open when Redis is down — `check_lockout` returns `(False, 0)` on any error | Brute-force attack possible during any Redis outage | `app/services/auth_lockout.py:25-57` |
| H41 | **Auth** | Turnstile | CAPTCHA fails open on network error — `verify()` returns `True` on `httpx.RequestError` | Combined Redis + Cloudflare outage removes CAPTCHA AND lockout simultaneously | `app/services/turnstile.py:73-81` |
| H42 | **Auth** | Rate limit | OAuth callback endpoint has zero rate limiting — no `@limiter.limit()` decorator | Supabase API abuse, financial DoS via repeated code exchange calls | `app/routers/auth.py:1643-1721` |
| H43 | **Auth** | Crypto | HMAC key only warned (not enforced) for length < 32 chars. Single SECRET_KEY used for ALL token types | Weak keys make JWT forgery feasible; single key compromise = entire system forgeable | `app/constants.py:31-34` |
| H44 | **Exam** | Submission | Time-exceeded check uses client-supplied `time_taken_secs` instead of server-computed elapsed time | Students can bypass time-exceeded detection by lying about duration | `app/routers/exam.py:864-866` |
| H45 | **Exam** | Submission | `asyncio.gather(*parallel_ops, return_exceptions=True)` — only op 0 failure raises. Op 1 (violation insert) failure silently ignored | Missing submission audit trail — teacher cannot see submission event | `app/routers/exam.py:876-883` |
| H46 | **Exam** | Sessions | Session check ignores `FORCE_SUBMITTED`, `REJECTED`, `ABANDONED` statuses — only checks `COMPLETED` and `IN_PROGRESS` | Students can bypass force-submit/rejection by starting a new session | `app/routers/exam.py:298-313` |
| H47 | **Exam** | Media | Student JWT can access any teacher's question images via `get_question_image` — only checks `tid` matches | Cross-teacher exam content disclosure | `app/routers/admin_media.py:89-98` |
| H48 | **Exam** | Grading | Bulk grade confirm does N+1 sequential DB updates — 200 answers = 200 round-trips | Slow bulk grading; timeout risk for exams with many short-answer questions | `app/routers/grading.py:396-425` |
| H49 | **LTI** | Session | LTI launch token passed in URL query parameter (`?token=...`) | JWT leaked to server logs, Referer headers, browser history | `app/routers/lti.py:177,180` |
| H50 | **LTI** | Rate limit | ALL LTI endpoints have zero rate limiting — login, launch, deep linking, AGS, NRPS, JWKS | Memory DoS via OIDC login, DB write amplification via repeated launches | `app/routers/lti.py:all endpoints` |
| H51 | **LTI** | NRPS | `sync_learner_roster` creates `lti_user_id = f"{user_id}"` without issuer namespace. Launch validation uses `f"{iss}\|{sub}"` | Cross-platform student account collision — grades misattributed | `app/lti/nrps.py:106` |
| H52 | **LTI** | Google | Google OAuth state `expires_at` set to `datetime.now()` (current time, not future). Never actually checked | State TTL effectively infinite — stolen state parameter useable indefinitely | `app/routers/google_classroom.py:36` |
| H53 | **LTI** | Google | Google OAuth tokens (including refresh_token, client_secret) stored unencrypted in DB as JSON | Any DB compromise leaks persistent Google Classroom API access | `app/routers/google_classroom.py:70`, `app/services/google_classroom.py:84-85` |
| H54 | **Admin** | RBAC | Exam group details queried without `teacher_id` filter | Cross-teacher group name leakage | `app/routers/admin_exams.py:514-527` |
| H55 | **Admin** | RBAC | Group members can be added without verifying roll_number belongs to teacher's own roster | Teachers can add arbitrary students to groups | `app/routers/admin_exams.py:479-495` |
| H56 | **Admin** | RBAC | Org member removal doesn't revoke tokens, invalidate sessions, or reassign data | Removed member retains full access to existing data | `app/routers/admin_org.py:113-133` |
| H57 | **Admin** | Validation | Bulk student registration lacks email/phone/name validation — no format checks, no length limits | XSS vectors (if frontend doesn't escape), downstream integration breakage | `app/routers/admin_students.py:233-247` |
| H58 | **LLM** | Injection | LLM output parsed as `json.loads(content)` without sanitization — student answers can inject into prompts | Prompt injection can generate harmful content, leak system prompts, or return malformed data | `app/llm.py:132` |
| H59 | **Billing** | Rate limit | Razorpay webhook endpoint has zero rate limiting | Unlimited forged webhook events — rapid subscription state cycling | `app/routers/billing.py:107-118` |
| H60 | **Infra** | Startup | Screenshot cleanup daemon thread not joined on shutdown | Filesystem corruption risk — partially written files from aborted cleanup | `app/main.py:94` |
| H61 | **Infra** | Startup | Reminder cancellation relies on fragile coroutine name string-matching — looks for "reminder" substring | Background tasks may not be cancelled on shutdown, holding DB connections | `app/main.py:145-149` |
| H62 | **Infra** | Middleware | InputValidationMiddleware does not exclude WebSocket paths — `await request.body()` on WS upgrade hangs | WebSocket connections rejected/hang during validation phase | `app/main.py:277` |
| H63 | **Infra** | CORS | `X-Loadtest-Key` exposed in CORS `allow_headers` — any allowed origin can send this header | Rate-limit bypass surface in staging/dev | `app/main.py:210` |
| H64 | **Infra** | Email | Attachment content serialized as list of integers — 500KB PDF → ~2MB JSON body | Payload may exceed Resend API size limits; attachments silently fail | `app/emailer.py:778-779` |
| H65 | **Infra** | Event bus | Redis disconnection kills SSE subscriber with no reconnection — `ConnectionError` uncaught | Live dashboards freeze until manual refresh after Redis restart | `app/event_bus.py:126-141` |
| H66 | **Infra** | Cache | Base64-encoded JPEGs in Redis — 33% memory overhead, no binary storage | Higher-than-necessary Redis memory consumption; eviction of other cached data | `app/cache.py:80-121` |
| H67 | **Infra** | Rate limit | Load-test bypass reads `APP_ENV` at import time — stale value if env changes. Bypass active in all non-prod environments | Staging/demo envs with leaked load-test key = unlimited rate-limit bypass | `app/limiter.py:46-52` |
| H68 | **Infra** | Database | Postgres pool has no connection health check — stale connections after DB restart | First requests after DB restart fail until all connections recycled | `app/postgres_table.py:37-51` |

## Medium & Low Severity — Aggregated by Category

| Category | Medium | Low | Key Examples |
|----------|--------|-----|--------------|
| **Auth** | 8 | 5 | Signup rate limit exposes shared-NAT users to DoS (M37), Password reset timing leak (M38), Session recording silently fails (L17), API key hashing uses fast SHA-256 without salt (M39), TOTP valid_window=1 allows 90s codes (L18) |
| **Exam** | 10 | 3 | `exam_started` overwrites `started_at` on page refresh, Answer saves accepted after session completed (M41), Risk score update may silently fail for null teacher_id (M42), Unbounded SSE queue (M43), SSE token race (M44) |
| **Admin/Billing** | 8 | 8 | Access codes stored unencrypted in templates (M45), Excel export silently converts bad values to 0 (M46), Deterministic anonymous email pattern (M47), Account deletion only anonymizes (M48), Usage counts only current teacher not org-wide (L20) |
| **LTI/Google** | 6 | 5 | Deployment authorization defaults to "allow all" (M49), Deep linking iss hardcoded to `https://app.procta.net` (M50), AGS `jti` deterministic not random (M51), Google Classroom token not persisted after refresh (L21) |
| **Infrastructure** | 10 | 6 | LLM grade cache doesn't include model in key (M52), Redis pub/sub connection per SSE subscriber (M53), CORS default origins include localhost in production (M54), SECRET_KEY length not enforced (M55), CSP allows unsafe-inline for styles (M56) |
| **Total** | **42** | **27** | |

## API Backend Severity Summary

| Sub-Audit | Critical | High | Medium | Low | Total |
|-----------|----------|------|--------|-----|-------|
| Auth & Security | 3 | 8 | 8 | 5 | 24 |
| Exam & Sessions | 3 | 5 | 10 | 3 | 21 |
| Admin & Business Logic | 2 | 8 | 8 | 8 | 26 |
| LTI & Google Classroom | 4 | 6 | 6 | 5 | 21 |
| Infrastructure | 5 | 9 | 10 | 6 | 30 |
| **Total** | **17** | **36** | **42** | **27** | **122** |

*Note: 35 issues overlap with previous audit passes (duplicates, reframed findings) — 122 unique new API backend issues + 35 deduplicated = 157 raw findings.*

## API Backend Quick Wins

| Priority | Fix | Est. Time | Files |
|----------|-----|-----------|-------|
| P0 | Remove sandbox auto-success from billing webhook + checkout verify | 15 min | `billing.py`, `checkout.py` |
| P0 | Make nonce consumption atomic — use `GETDEL` or Lua script | 10 min | `app/lti/launch.py` |
| P0 | Swap `httpx.Client()` → `httpx.AsyncClient()` in llm.py | 5 min | `app/llm.py` |
| P0 | Add `jti` claim to student auth tokens | 2 min | `app/auth/tokens.py` |
| P0 | Change `print()` to `logger.exception()` in global exception handler | 2 min | `app/main.py` |
| P1 | Add rate limiting to LTI login/launch, OAuth callback, billing webhook, email webhook | 20 min | 4 router files |
| P1 | Flush answers to DB before marking session COMPLETED | 15 min | `app/routers/exam.py` |
| P1 | Make TOTP encryption key required at startup | 2 min | `app/services/totp.py` |
| P1 | Add `nbf` verification and `azp` check in LTI JWT validation | 10 min | `app/lti/launch.py` |
| P1 | Remove `X-Loadtest-Key` from CORS allow_headers | 1 min | `app/main.py` |
| P1 | Fix Google Classroom `expires_at` to future time | 2 min | `app/routers/google_classroom.py` |
| P2 | Encrypt stored Google OAuth tokens | 30 min | `google_classroom.py` |
| P2 | Add `teacher_id` filter to group queries (defense-in-depth) | 10 min | `admin_exams.py` |
| P2 | Add connection health check to asyncpg pool | 15 min | `postgres_table.py` |
| P2 | Set `font-display: swap` and remove unused CSS | 5 min | `website/src/index.css` |

---

## Consolidated Audit Metadata (All Passes)

- **Files examined:** 88 API Python source files (22,653 lines) + 15 React SPA files + 15 marketing website pages + 14 shared components + 42 migration files + Caddyfile
- **Tests executed:** 566 passed, 33 skipped (12.4s)
- **NPM audit:** 0 vulnerabilities across root, dashboard-ui, student-ui, website
- **Code lint:** 2027 flake8 warnings, 47 mypy errors, 6 bandit findings
- **Total issues found across 3 audit passes:**

| Audit Pass | Critical | High | Medium | Low | Total |
|------------|----------|------|--------|-----|-------|
| Pass 1: User Workflow Audit | 7 | 23 | 36 | 16 | **82** |
| Pass 2: Marketing Website Deep-Dive | 6 | 16 | 35 | 15 | **72** |
| Pass 3: API Backend Deep-Dive | 17 | 36 | 42 | 27 | **122** |
| **Grand Total (unique)** | **30** | **75** | **113** | **58** | **276** |
| **Grand Total (raw, w/overlaps)** | **36** | **88** | **124** | **62** | **310** |

- **Estimated fix time:** ~85 hours (original ~42h + ~13h marketing + ~30h API backend)
- **Production readiness score:** 3.8/10 (down from 4.8/10 — API backend drags score with 17 critical and 36 high issues)

## Final Deep-Dive Summary

Three comprehensive audit passes have been completed covering the entire codebase:

### Pass 1: User Workflow Audit (82 issues)
Focused on end-to-end user flows across auth, dashboard, student, billing, LTI, and privacy. Found 7 critical issues including TOTP bypass, student-ui login 404, email verification bypass, and LTI grade passback. **Score: 5.2/10.**

### Pass 2: Marketing Website Deep-Dive (72 issues)
Covered all 15 marketing pages, 14 shared components, build pipeline, SEO, and PWA. Found 6 critical issues including `PROCTA_URL` missing `VITE_` prefix (signup broken in prod), password validation mismatch, no skip-to-content link, and zero SEO metadata on 6 pages. **Score: 4.8/10.**

### Pass 3: API Backend Deep-Dive (122 unique / 157 raw issues)
Covered 88 files (22,653 lines) across auth, exams, admin, billing, LTI, and infrastructure via 5 parallel sub-audits. Found 17 critical issues including Razorpay sandbox bypass, LTI 1.3 replay attack, exam submission data loss, and synchronous httpx blocking the event loop. **Score: 3.8/10.**

### Key Takeaways
1. **Not ready for production (3.8/10).** 30 critical issues remain, many enabling authentication bypass, data loss, or privilege escalation.
2. **Payment/billing sandbox bypasses are the most urgent** — they allow anyone to forge subscription events.
3. **LTI 1.3 has fundamental security flaws** — replay attacks possible, key rotation impossible, tokens exposed in URLs.
4. **Auth layer needs hardening** — CSRF is opt-in, student tokens can't be revoked, email enumeration is widespread, lockout/CAPTCHA both fail open.
5. **Infrastructure has critical reliability issues** — synchronous httpx blocks event loop, background tasks leak on reload, SSE streams have no reconnection, Redis connections grow per-subscriber.
6. **~85 hours estimated total fix time** across all 3 audit passes.
7. **Quick wins (~2 hours):** Fix sandbox bypasses, make nonce consumption atomic, swap to async httpx, add `jti` to student tokens, fix global exception handler logging.
