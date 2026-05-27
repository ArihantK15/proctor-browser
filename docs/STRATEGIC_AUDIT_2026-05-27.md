# Procta Strategic Audit — 2026-05-27

> Honest assessment from a CTO/CEO/PM/Engineer hat. Not a marketing piece.
> Generated at the close of the security + repo-health hardening sprint
> (commits `da5a1b2` → `3dcb78e`, ~30 commits).

---

## A. Executive Summary

**Biggest opportunity**: India's coaching-institute exam market is a ₹5,000+
crore TAM dominated by Mercer Mettl, Talview, and HirePro — all priced for
enterprise. Coaching institutes (Allen, Aakash, PW, Unacademy, plus thousands
of regional players) need AI proctoring at ₹50-150/student, which nobody
currently sells. Procta technically delivers ₹80. **If you sign 2-3 named
coaching-institute customers in the next 90 days, the rest of the playbook is
execution.**

**Biggest threat**: You. Bus-factor-1 + ₹3,000/month budget + no sales motion
= the product can be perfect and still fail. Every incumbent has a sales team
of 20+. You have you. The technical work shipped in the last week is
genuinely good — and worth zero rupees until someone signs.

**Honest 30-second pitch you don't have yet**:
*"Procta is the proctoring stack for Indian coaching institutes. ₹80/student,
phone-cam included, INR + GST invoicing, deployed in 10 minutes. We replace
Mettl at 1/8th the cost."*

Use that. The current README pitch ("AI-powered exam proctoring") could be
any of 20 competitors.

---

## B. Top 10 highest-impact improvements (ranked by ROI)

| # | Item | Effort | Why it matters |
|---|---|---|---|
| **1** | **Publish pricing on procta.net + add 1 named customer logo** | 1 day | Coaching-institute IT heads filter by pricing transparency. No pricing = no inbound. No logo = no trust. |
| **2** | **Sign 1 paying coaching institute (any size)** | 30 days | Validates the entire thesis. Until then everything else is speculation. |
| **3** | **Migrate Razorpay one-off Orders → Subscriptions w/ UPI autopay** | 1 week | India SMBs renew monthly only when frictionless. UPI autopay is the single biggest retention lever. |
| **4** | **Hire/find a co-founder (sales/business)** | 60 days | Bus-factor-1 kills the company at funding stage. Engineering co-founder you don't need; commercial one you do. |
| **5** | **WhatsApp Business API integration** | 2 weeks | Indian coaching institutes communicate with students 95% via WhatsApp. Invite links + reminders + scorecards via WA = retention. |
| **6** | **Aadhaar e-KYC at signup (DigiLocker free tier)** | 2 weeks | Identity verification is a hard requirement for govt exams. Free moat. Free regulatory tailwind. |
| **7** | **Bulk student import wizard + CBSE/JEE roll-number presets** | 1 week | Coaching institutes don't have clean CSVs. Make the import unbreakable. |
| **8** | **DPDP Act + data localization compliance pass** | 2 weeks | Supabase is hosted abroad. Govt customers and enterprise will reject. Move primary data to AWS Mumbai or migrate to a domestic Postgres. |
| **9** | **Live "teacher cam-pop-in" — 2-second join into a flagged session** | 3 days | Wow-factor feature. Demoable. Differentiator vs every competitor. |
| **10** | **One-page "Migrating from Mettl" guide on the marketing site** | 1 day | Catches every "we're shopping for cheaper" search. Long-tail SEO. Costs nothing. |

---

## C. Quick wins (low effort, high impact)

| Hours | Item |
|---|---|
| 2 | Add Razorpay Standard Checkout subscription option (already 80% done, just expose). |
| 2 | "Compare to Mettl/Talview" table on `/pricing`. |
| 1 | Branded PDF scorecard — pull org logo into the PDF (data already there). |
| 1 | Weekly "Your students this week" email digest (cron via existing rq queue). |
| 1 | Enable GitHub Discussions + Topics tags for SEO/discoverability. |
| 1 | Demo video on the landing page (Loom recording, no production budget needed). |
| 2 | "Powered by Procta" branding on every invite link + scorecard PDF → free viral loop. |
| 4 | Sample-data / "first exam in 60 seconds" guided tour on teacher signup. |
| 4 | NPS prompt in-app after 10th exam (cheap, surfaces churn risk early). |
| 8 | A 90-second product video — voice-over your own demo, post to YouTube + LinkedIn. |

Total: ~26 hours, probably 2-3x your weekly net-new feature velocity. Do
them before any new feature.

---

## D. Deep technical improvements

### Architecture

1. **The `app/routers/auth.py` monolith (2000+ lines)** — split into
   `auth_login.py`, `auth_signup.py`, `auth_invite.py`, `auth_session_mgmt.py`,
   `auth_2fa.py`, `auth_password.py`. Tests already organized this way; the
   code isn't.
2. **`app/static/dashboard-app.js` is 6000 lines of vanilla JS being migrated
   to React** — finish the migration. Dual-existence debt is poisonous: every
   feature gets built twice or only in one place, and bugs diverge.
3. **`app/domains/` vs `app/routers/` overlap** — pick one structure. Either
   domain-driven (everything in `domains/<domain>/{router,service,repo}.py`)
   or layered (`routers/`, `services/`, `repositories/`). The current half-way
   is the worst of both.
4. **Function-body imports everywhere** (`from ..auth.admin_auth import X`
   inside handler bodies) — these are circular-import workarounds. Means
   your module graph has cycles. Untangle the cycles; the imports go back
   to module top.
5. **No service mesh / no horizontal scaling story** — single FastAPI on a
   single KVM. Fine at 1k students, dead at 100k. Plan: containerize, put
   a reverse proxy that's not a single Caddy box, move stateful pieces
   (sessions, sessions-of-sessions) to Redis Cluster.

### Performance

1. **LLM grading runs sync per answer**. Batch per exam — same RPC cost,
   5-10x latency drop. Easy win.
2. **Phone-cam JPEG frames hit Redis raw bytes**. Add server-side recompress
   at quality 60 before the cache write — already done for live frames
   (`sse.py`), missing for room frames. ~40% bandwidth/storage cut.
3. **React dashboards bundle as monolith**. Vite supports route-level
   code-splitting; you're not using it. Each panel should be a lazy import.
4. **`(teacher_id, exam_id, status)` composite index missing on
   `exam_sessions`** — every admin Live/Results query scans more than it
   should. Check `EXPLAIN` on a real-prod query.
5. **The Caddy + FastAPI hop terminates TLS once on the box** — add
   Cloudflare's full proxy mode and offload TLS. Free, ~50ms shave per
   request.
6. **`pre-commit` runs on every commit including docs** — fine for solo
   dev, but when a second contributor lands, this becomes friction. Add
   `files:` patterns to scope each hook.

### Security (what's actually still real after the 0-alert sweep)

1. **The 87 log-injection dismissals don't suppress the rule** — new code
   that bypasses `safe()` re-triggers. Either model `safe()` properly via
   CodeQL MAD (the work we deferred) or accept that you'll have to dismiss
   again periodically. Write a Semgrep rule that fails CI if a logger call
   passes a non-literal without `safe()` — that's cheaper than MAD.
2. **Refresh tokens still live in `localStorage` on the student side**
   (P2.1 partial). Cookie migration was done for teacher dashboard, not
   student. Finish it.
3. **No WAF / no DDoS protection** beyond Cloudflare basic. At scale you'll
   get scraped, brute-forced, and resource-exhausted. Cloudflare Pro is
   ₹1700/month — worth it from day one.
4. **No anomaly detection on session events** — a single attacker creating
   500 fake sessions wouldn't trip anything. Add a simple "10 sessions/
   hour/IP" alert via Sentry.
5. **Razorpay webhook idempotency trusts the event ID** — if Razorpay
   replays an event-id you've seen, you re-process. The `idempotency`
   service handles app-level requests but webhook IDs need separate dedup.
6. **`SECRET_KEY` short-key warning is logged-only**. Make it a startup
   hard-fail in production (you already have `SUPABASE_SKIP_STARTUP_CHECK`
   pattern).
7. **No backups visible in repo or compose**. `screenshots/` dir on the
   KVM is local — if the disk dies, you lose violation evidence. Need
   an S3 / DigitalOcean Spaces sync.
8. **Bus-factor-1 = security risk too**. If you can't access the box for
   2 weeks (illness, travel, exam season), nothing rotates, nothing patches.

### Code quality / testing

1. **Test coverage uneven**: auth/billing/exam/grading are heavy,
   proctor/lti/services thin. Codecov will surface this within a week.
2. **No E2E tests** despite the `tests/browser/` directory existing.
   Playwright is the right tool, ~1 day to wire 3 critical paths
   (signup → invite → take exam).
3. **No load tests** beyond a k6 smoke. Before any "Series A" conversation,
   you need a real load test showing 1000 concurrent exams.
4. **No staging environment**. Every push goes straight to prod. With
   0 customers it's fine; with 5 it's reckless.
5. **47 bare `except Exception: pass` → `logger.debug(exc_info=True)`**
   done last week, but the actual desire is to *handle specific exceptions*.
   The debug log is band-aid. Real fix is per-site exception typing.

---

## E. Feature roadmap (prioritized)

### Now (next 7 days)
- Public pricing page
- Razorpay Subscriptions w/ UPI autopay
- "Powered by Procta" branding on invite links + PDFs
- Demo video on landing
- Branch protection rule on `main`

### Q1 push (30 days)
- 1 paying coaching-institute customer signed
- WhatsApp Business API for invites + scorecards
- Bulk import wizard with CBSE/JEE/NEET roll-number formats
- Sample data + first-exam guided tour
- Razorpay live keys live (if not already)
- Pre-built integrations: Google Classroom

### Mid-term (90 days)
- 3-5 paying customers, ₹50k+ MRR
- Aadhaar e-KYC via DigiLocker free tier
- Mock-test marketplace MVP (creators upload paid mock tests, 20% rev share)
- DPDP Act compliance: data localization to AWS Mumbai or equivalent
- SOC2 Type 1 audit kicked off
- Hire engineer #2 + 1 sales person on contract

### Long term (12 months)
- ₹3-5 lakh MRR
- 1 enterprise customer (₹5+ lakh/year ARR contract)
- Team of 5-7
- SOC2 Type 2 + ISO 27001
- Govt exam pilot (UPSC mock / state PSC tie-up)
- ₹3-5 cr seed round at ₹15-25 cr post-money
- US/SEA expansion scoping

### Wow features to demo (in priority order)
1. **Live teacher cam-pop-in** — flagged session → teacher clicks → 2-second
   WebRTC join. Nobody else has this at this price.
2. **AI-generated practice questions** from a topic (uses your existing
   LLM grading infra)
3. **Automated retake offer** after a flagged session (gives benefit of
   doubt, reduces complaints, generates revenue per retake)
4. **Branded scorecard PDF on WhatsApp** within 60s of exam submit
5. **Aadhaar e-KYC** at signup — instant identity verification, regulatory
   moat

---

## F. Final verdict — would I fund this?

**Pre-seed: yes. At ₹1.5-3 crore on ₹6-12 crore pre-money. Conditional on
the conditions below.**

**Seed (₹15-25 cr): not yet. Come back with 3 paying customers and a
co-founder.**

**Series A: not on the table for 18+ months.**

### Why yes (the bull case)

- **The tech is real**. The proctoring stack is more sophisticated than
  expected from a solo founder. Phone-cam + on-device ML + LLM grading is
  genuinely defensible. Not just gluing OpenAI APIs together.
- **The market is huge and underserved at this price point**. Indian
  coaching institutes spend an order of magnitude more on physical proctors
  than on software. Price elasticity is in our favor.
- **The founder ships**. 100+ commits in 30 days, real security work, real
  audit remediation, real product features. Most founders at this stage
  are still in Figma.
- **The audit findings I would have used to reject a month ago are gone**.
  SECURITY.md, CodeQL clean, P1.2 done, Dependabot wired, supply-chain
  attack caught and patched same-day.

### Why "yes, but conditional"

These are pre-investment requirements, not nice-to-haves:

1. **Co-founder by close of round**. Sales/commercial. Not negotiable.
2. **3 paying customers, even small ones**. Not LOIs. Real Razorpay
   receipts. Even ₹2000/month each is fine — it proves the funnel.
3. **DPDP Act compliance plan documented**. India's data law has teeth
   now. Investors will ask. Have an answer.
4. **A 90-second demo video**. Right now the README is gorgeous and tells
   me nothing. Show an exam happening, a violation flagging, a teacher
   reviewing. Make it impossible to not understand.

### Why I might not fund (the bear case)

- **Great engineer who can't sell**. Most successful founders aren't.
  Co-founder fixes this; no co-founder makes me pass.
- **The big-3 incumbents could drop their price 70% tomorrow** and crush.
  Tech moat is real but not insurmountable. Distribution beats tech.
- **Govt exam tailwinds are a 2-3 year sales cycle**. If runway is 18
  months, you won't survive long enough to close one.
- **Solo founder + Indian student + savings-funded** = vector for burnout.
  At ₹3000/month forever, you crash before you scale.

### The single most important thing in 30 seconds

**Stop building features. Sell what you have.**

The product works. The remaining engineering tasks above are real, but they
don't matter until somebody pays. Spend the next 30 days doing 0%
engineering and 100% sales:

- Cold-email 100 coaching institute IT heads
- Demo-call 20 of them
- Close 3 at any price (₹500/month is fine)
- *Then* fix the architecture monolith, the LFS migration, the React rewrite

The audit work is done. The repo is healthier than 90% of pre-seed startups.
The remaining 10% gap closes itself when you have revenue to hire help.
Go sell.
