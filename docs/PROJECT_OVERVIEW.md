# Procta — Project Overview

**Prepared for academic review · 2026-05-28**

> A complete walkthrough of what Procta is, how it was built, what it
> costs to run, and where it goes next. Written for someone seeing the
> project for the first time. Companion to the strategic audit in
> `docs/STRATEGIC_AUDIT_2026-06-14.md` (commercial / fundability lens).

---

## 1. What is Procta

**Procta is an AI-powered remote exam-proctoring platform built for
the Indian coaching-institute and university market.**

Students take exams from home on a locked-down Electron desktop client.
The client runs on-device computer-vision (face, gaze, head pose,
object detection, voice activity) plus an optional phone-camera room
scan, and streams violation events in real time to the teacher's web
dashboard. Teachers can flag, chat with students mid-exam, force-submit
sessions, and grade short answers with an LLM-assisted suggestion.

Public surfaces:
- `procta.net` — marketing site (React + Vite)
- `app.procta.net` — teacher / admin dashboard + student-account login (React + FastAPI)
- `app.procta.net/student-react` — student dashboard
- Electron client (download from `procta.net/download`) — the locked exam runtime
- `app.procta.net/phone-cam` — phone-cam pairing page (QR-code launched)

---

## 2. Development timeline

| Date | Milestone |
|---|---|
| **2026-03-27** | First commit — bare FastAPI + Electron skeleton |
| **2026-04 mid** | First end-to-end proctored exam (face detect + manual grading) |
| **2026-04 late** | Phone-cam room monitoring shipped |
| **2026-05 early** | Multi-tenant org model, billing scaffolding |
| **2026-05 mid** | LTI 1.3 integration, organization invites, email-OTP 2FA |
| **2026-05-23** | TOTP replaced by email-OTP 2FA; OAuth removed |
| **2026-05-24** | First strategic audit; bus-factor-1 flagged |
| **2026-05-25/26** | P0-P2 audit remediation (cookie auth, CSRF, captcha, plan limits, billing math) |
| **2026-05-26** | Repo health hardening: SECURITY.md, CONTRIBUTING.md, Dependabot, CodeQL, gitleaks, pre-commit |
| **2026-05-27** | CodeQL alerts 133 → 0; Razorpay Subscriptions w/ UPI Autopay; cam-pop-in on violation |
| **2026-05-28** | LLM grading parallelised, webhook idempotency, room-cam recompress, bulk-import CSV |
| **Today** | 583 commits, 614 backend tests passing, 50 DB migrations, 25,840 lines of Python in `app/` |

Roughly **60 days from first commit to a production-deployed multi-tenant
SaaS** with real Razorpay payments, full audit hardening, and CI/CD pipeline.

---

## 3. Infrastructure history

### Phase 1 — DigitalOcean Droplet (April 2026)

| | |
|---|---|
| Box | DigitalOcean Basic Droplet, 2 GB RAM, 1 vCPU |
| Cost | ₹1,500/month (~$18) |
| Used for | First public exam beta, ~20 concurrent sessions max |
| Reason for move | Single shared CPU + 2 GB RAM choked under live JPEG ingest at >10 concurrent sessions. ML inference on student-side helped, but the Redis cache + FastAPI workers + Postgres connection pool together fit within RAM only with no headroom. Disk I/O on burst NVMe was slow under sustained writes (screenshots dir). |

### Phase 2 — Hostinger KVM 4 (May 2026 onward)

| | |
|---|---|
| Box | Hostinger KVM 4 — 4 vCPU, 16 GB RAM, 200 GB NVMe |
| Cost | **₹28,000 for 2 years** (~₹1,167/month, biennial pre-pay — ~22 % cheaper per month than the DO Droplet) |
| Headroom | Sized to comfortably hold the **3,500-student concurrent live-view target** (300 MB Redis cache, 4 uvicorn workers, room for an LLM-grading queue burst) |
| Reverse proxy | Caddy with auto-Let's-Encrypt |
| Deployment | GitHub Actions → SSH → `docker compose pull && up -d` |

### Domain & external services

| Item | Provider | Cost |
|---|---|---|
| `procta.net` domain | Namecheap | **₹800/year** |
| Database | Supabase free tier (Postgres 16, 500 MB included) | ₹0 |
| Email transactional | Resend free tier (3,000/month, 100/day) | ₹0 |
| CDN + DNS | Cloudflare free | ₹0 |
| LLM grading | Groq free tier (30 req/min) | ₹0 |
| Marketing site hosting | Cloudflare Pages | ₹0 |
| Payments | Razorpay (2 % + GST per transaction) | usage-based |
| Code-quality + SAST | GitHub Actions + CodeQL + Semgrep + pip-audit + gitleaks + CodeRabbit (free tiers) | ₹0 |
| Issue & PR management | GitHub free | ₹0 |
| Coverage reporting | Codecov free | ₹0 |

**Total fixed monthly burn: ~₹1,234 (KVM amortised + domain).**
Everything else is free-tier or usage-based. The entire stack is
running on what would otherwise be a single college student's monthly
mobile phone bill.

---

## 4. Tech stack (current)

### Backend
- **FastAPI** 0.110-0.135 (pinned away from 0.136.3 — that release was a supply-chain attack, caught + patched same day on 2026-05-26)
- **Python 3.11**, uvicorn with 4 workers behind Caddy
- **Postgres 16** via Supabase REST API + `async_table` wrapper
- **Redis 7** for cache (live-frames, sessions, CSRF tokens, idempotency keys, room-cam frames)
- **RQ** (Redis Queue) workers for background jobs — scoring, reminders, email send
- **JWT auth** with HS256 + per-purpose key ring (`admin`, `student`, `student_auth`, `room_cam`, `reauth`, `email_verify`, `password_reset`) + key rotation support
- **HttpOnly cookie auth** (P2.1) for teacher dashboard; CSRF token stored server-side keyed on JTI

### ML / Proctoring
- **uniface** (RetinaFace ONNX) for face detection + 5 facial landmarks
- **`weights/resnet18_gaze.onnx`** — gaze estimation (looks-where) — custom model
- **OpenCV** Haar cascade for eye-open/closed
- **OpenCV solvePnP** for head pose
- **ultralytics** YOLOv8n (`yolov8n.pt`) for object detection (phone, paper, second person)
- **sounddevice** + threshold-based VAD for voice detection
- All inference runs **on the student's local machine** — no raw frames leave the box. Only violation events and low-rate JPEG snapshots upload.

### Frontend
- **Electron 42** desktop client (locked-down exam runtime)
- **React 18 + Vite 6** for the two dashboards (`app/dashboard-ui`, `app/student-ui`)
- **React 18 + Vite + react-helmet-async** for the marketing site (`website/`)
- **Tailwind CSS** + IBM Plex Sans / Mono / Display
- **wouter** for marketing-site routing (lighter than React Router)
- **Cloudflare Turnstile** Managed-mode invisible CAPTCHA on every public form

### Payments
- **Razorpay Standard Checkout** (one-off Orders) — used today
- **Razorpay Subscriptions** with UPI Autopay — shipped 2026-05-27, waiting on plan IDs to go live
- HMAC-SHA256 signature verification on every callback + webhook
- Server-pinned `notes.plan_id` to defeat privilege-escalation

### CI/CD
- GitHub Actions:
  - **Tests** (pytest 614 ✅, Semgrep SAST, pip-audit, npm audit, gitleaks, Trivy filesystem scan, docker smoke)
  - **CodeQL** with `security-extended` query pack + custom config
  - **Deploy API** — ssh + `docker compose` restart
- **CodeRabbit** AI code review on every PR (configured to skip Dependabot bumps)
- **Dependabot** scans 5 package ecosystems weekly
- **Codecov** coverage tracking
- **Pre-commit hooks**: gitleaks, trailing-whitespace, EOF, large-file guard (8 MB), private-key detect

---

## 5. Feature inventory

### Student-facing
- Locked-down Electron client — disables alt-tab, copy/paste, fullscreen escape
- Process monitor — detects screen recorders, remote-desktop tools, VMs
- Face calibration before exam start (single-frame baseline)
- Auto-save every 5 seconds + offline resilience
- Phone-camera pairing via QR code; phone acts as room-scan camera
- Real-time risk-score visibility (subtle indicator)
- Per-question timer, navigator, flag-for-review
- Built-in calculator + scratchpad (configurable per exam)
- Branded scorecard PDF on submit (includes the institute's logo)

### Teacher / admin
- **Live tab** — every active session with inline thumbnail on violation; cam-pop-in <1 s
- Multi-tab dashboard: Live, Results, History, Analytics, Questions, Chat, Tools
- LLM-graded short answers (Groq) — teacher confirms before grade lands
- Real-time chat with student during exam
- Force-submit, revoke session (with re-auth gate)
- Question bank with topic tagging
- Exam scheduling, group assignment, duplicate-exam
- CSV roster import with CBSE/JEE/NTA roll-format auto-detection + dry-run preview
- Bulk invite + reminder cron (1 h + 24 h ahead)
- Org member management, RBAC (teacher / admin / superadmin)
- Razorpay billing panel with one-off and UPI Autopay subscription CTAs

### Org / billing
- 4 tiers: Starter ₹2,400 (30 students), Growth ₹12,000 (150), Pro ₹30,000 (500), Enterprise (contact)
- ₹80 / student overage on PAYG
- 14-day free trial on Starter
- INR + GST invoicing
- Per-org quota enforcement on student count
- Per-org "Powered by Procta" attribution on all customer-facing artefacts

### Integrations
- LTI 1.3 deep-link launch (Canvas, Moodle)
- Google Classroom course sync
- Razorpay Standard Checkout + Subscriptions
- Resend email
- Webhook on email bounce / click

### Compliance / security
- **HttpOnly cookie auth** + CSRF token stored server-side
- **Reauth gate** on 8 destructive endpoints (delete exam, kick member, revoke session, force-submit, GDPR delete, change role, clear-live-sessions, 2FA enable/disable)
- **Email-OTP 2FA** (replaced TOTP for simpler UX)
- **Rate limiting** via `slowapi` on every public endpoint
- **Captcha** (Cloudflare Turnstile) on signup, login, password-reset
- **GDPR / DPDP-style** account-delete flow with anonymisation
- **Audit log** (auth_events table)
- **Idempotency** on Razorpay webhooks (24 h dedup on event.id)
- **Backups**: Supabase manages Postgres; screenshots dir on KVM + daily DigitalOcean Spaces sync (planned)

---

## 6. Performance metrics (current)

| Metric | Current | Engineered target |
|---|---|---|
| Concurrent active proctored sessions | tested at 100s | 3,500 (KVM headroom) |
| Live-frame cache cap | 6,500 sessions | 6,500 (env-overridable) |
| Cam-pop-in latency (violation → first frame visible) | <1 s | <2 s |
| LLM short-answer grading | 50 answers in ~3-4 s | parallel @ semaphore=8 |
| Backend test suite | 614 passing in ~13 s | — |
| CodeQL open alerts | **0** | — |
| Dependabot security PRs | weekly, auto-grouped | — |
| Deploy → live cycle | ~3 min via GitHub Actions | — |
| Median API p50 (local) | ~30 ms | — |
| Median API p95 (local) | ~120 ms | — |

---

## 7. Security posture

Audit-driven hardening over the last 4 days reached **CodeQL: 0 open
alerts**. Categories swept:

| Area | Before | After |
|---|---|---|
| Open CodeQL alerts | 133 | 0 |
| Log-injection sites | 87 (sanitiser dismissed) | 71 wrapped via `app/log_safe.safe()` (urllib.parse.quote backend), rule still enabled to catch future regressions |
| Bare `except Exception: pass` | 47 (silent failures) | 0 — all now `logger.warning/debug(exc_info=True)` |
| Refresh tokens in localStorage | yes (teacher) | no — HttpOnly cookies |
| CSRF token | claim inside JWT | server-side keyed on JTI |
| Re-auth gate on destructive actions | none | 8 endpoints |
| Webhook idempotency | trusted event-id | 24 h Redis dedup |
| Stack-trace exposure in responses | yes (privacy delete) | exception class name only |
| Reflective XSS (invite landing) | unescaped `{token}` in JS literal | strict regex gate at the entry point |
| JS prototype-pollution (chat WS) | unguarded `obj[session_id]` | `_isSafeSid()` allow-listed regex |
| Insecure temp file (Electron python-mgr) | predictable path | `crypto.randomBytes(8)` suffix + `flag:'wx'` |
| Pre-commit secret scan | none | gitleaks blocks every commit |
| Push protection | none | GitHub-side enabled |

---

## 8. Codebase shape (numbers, not hand-waving)

- **583 commits** since 2026-03-27
- **614 backend tests** (pytest, ~13 s) + 33 skipped
- **50 SQL migrations** (forward-only, applied automatically on deploy)
- **25,840 lines of Python** in `app/` (excluding generated React bundles, tests, scripts)
- **6,000-line** legacy vanilla-JS teacher dashboard (`app/static/dashboard-app.js`) — being incrementally migrated to React
- **3 React apps** built independently: teacher dashboard, student dashboard, marketing site
- **47 routers / domain modules** across `app/routers/` + `app/domains/`
- **5 Razorpay webhook event types** handled
- **2 ML model files** (`resnet18_gaze.onnx` 45 MB + `yolov8n.pt` 6.5 MB)

---

## 9. What's deferred (and why)

These are technical-debt items I'm choosing not to fix yet, with explicit reasoning:

| Item | Why deferred |
|---|---|
| Auth.py monolith split (2,000 lines) | Works fine. Splitting risks introducing import cycles. Re-evaluate when a second contributor joins. |
| Finish dashboard-app.js → React migration | Each panel takes ~1 day to port; doing piecemeal as features need work. Full rewrite blocks shipping. |
| `domains/` vs `routers/` consolidation | Pure refactor, zero user-visible value. Wait until headcount > 1. |
| WebRTC for live exam audio | 2-4 week project, requires SFU (mediasoup / Janus / managed at ₹15-50k/mo), 1 Gbps egress at 3500 sessions. Not justified until a customer pays for it. Documented as a migration path. |
| Redis Cluster | Single-node Redis handles 100k ops/sec, we need 3500. Migrate at >10k concurrent sessions or when HA failover becomes a contractual requirement. |
| Aadhaar e-KYC integration | Requires registering as an AUA / Sub-AUA with UIDAI. Compliance overhead pays off when a govt exam customer signs. |
| WhatsApp Business API | 2-week project, Meta Cloud API free tier covers 1000 conv/month. Will start when the marketing case for it lands a paying customer. |
| Git LFS migration for `resnet18_gaze.onnx` | LFS free tier caps at 1 GB/month bandwidth, CI clones × 45 MB blows the cap inside a week. Better long-term: move to GitHub Release asset + `fetch_models.py`. |

---

## 10. Roadmap (next 90 days)

### Now → 7 days
1. Configure Razorpay subscription plan IDs in the dashboard → Subscribe UPI Autopay button goes live
2. Cold-email 100 coaching institutes
3. Demo-call 20 institute IT heads
4. Sign 3 paying customers at any price point (₹500/month is fine)
5. Set Branch protection on `main` (status check + linear history)

### 30 days
1. First named customer logo on the marketing site
2. WhatsApp Business API → invite delivery + scorecard PDF over WhatsApp
3. Pre-built Google Classroom + Canvas integration walkthroughs (videos)
4. Sample-data / "first exam in 60 seconds" guided tour on signup
5. SOC 2 Type 1 audit kicked off

### 90 days
1. Mock-test marketplace MVP (creators sell paid mock tests, 20 % revenue share)
2. Aadhaar e-KYC via DigiLocker free tier
3. DPDP Act compliance — primary data to AWS Mumbai
4. Hire engineer #2 + 1 sales contractor
5. ₹50,000+ MRR
6. Govt exam pilot (UPSC / state PSC tie-up)

---

## 11. The numbers a professor would ask

| Question | Answer |
|---|---|
| What did this cost to build? | ~₹1,234/month of infrastructure + the founder's time. Domain ₹800/year. KVM ₹28,000 for 2 years. Everything else free-tier. |
| Who works on it? | One person. Solo founder, Indian student, bus-factor 1 (acknowledged risk; co-founder search is item #4 on the audit). |
| What's the ML doing on-device vs in-cloud? | All proctoring inference (face / gaze / object / VAD) runs on the student's machine. Only violation events + optional low-rate JPEG snapshots reach the server. No raw video leaves the client. |
| Does it actually work? | 614 backend tests passing, 0 CodeQL alerts, production-deployed at app.procta.net since April. Real Razorpay transactions validated. |
| What's the market? | India coaching-institute + university exam-proctoring. Existing vendors (Mercer Mettl, Talview, HirePro) charge ₹500-1,000/student. We charge ₹80. |
| Is it defensible? | Phone-cam at this price point is rare. On-device ML keeps recurring cloud bill low → sustainable margins at ₹80. Data-flywheel (every session improves the gaze model). Indian-built / INR billing is the local-fit moat. |
| What's the biggest risk? | Distribution, not technology. The tech works. Selling it at this price requires a sales motion that doesn't yet exist. Co-founder hire is the gate. |
| Where could it go in 12 months? | Realistic: ₹3-5 lakh MRR, 1 enterprise contract, team of 5-7, SOC 2 Type 2 + ISO 27001 in progress, ₹3-5 crore seed round at ₹15-25 crore post-money. |

---

## 12. Where to look in the code (for a TA / code-review tour)

| Concern | Read this |
|---|---|
| Auth + JWT key ring | `app/auth/tokens.py`, `app/auth/admin_auth.py` |
| Scope (teacher / admin / superadmin) | `app/auth/scope.py` |
| Reauth gate (8 destructive endpoints) | `app/auth/admin_auth.py:require_reauth_or_403` |
| Live-view + cam-pop-in | `app/routers/sse.py`, `app/routers/admin_liveview.py`, `app/dashboard-ui/src/panels/LiveSessionsPanel.jsx` |
| LLM grading (parallelised) | `app/llm.py:grade_short_answer`, `app/routers/grading.py:grade_suggest_bulk` |
| Razorpay (orders + subscriptions + webhook + idempotency) | `app/routers/billing.py`, `app/services/billing.py` |
| Scorecard PDF (org-branded) | `app/services/scorecard.py` |
| Bulk-import roll-format detector | `app/services/roll_formats.py`, `tests/test_roll_formats.py` |
| Log-injection sanitiser | `app/log_safe.py` |
| Live-frame cache (3,500-student tuned) | `app/cache.py:set_live_frame` + `live_frame_stats` |
| Schema history | `migrations/phase01_init.sql` → `migrations/phase71_*.sql` (chronological) |
| Tests (pytest, 614) | `tests/test_*.py` — auth, billing, scope, roll-formats, privacy, … |
| Strategic audit (commercial lens) | `docs/STRATEGIC_AUDIT_2026-06-14.md` |
| Operational deploy notes | `DEPLOY.md` |

---

*Generated 2026-05-28. The numbers here reflect commit `1788a22` and
production deployment as of that revision. Update via a fresh
`scripts/gen_features_pdf.py` run when the next major milestone lands.*
