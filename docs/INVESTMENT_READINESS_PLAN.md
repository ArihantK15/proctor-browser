# Procta — Investment Readiness Plan (60 Days)

**Author:** Arihant Kaul  
**Date:** 2026-06-15 (metrics and cost figures refreshed 2026-07-03 — see addendum below)  
**Status:** Plan  
**Version:** 1.0

---

## Addendum (2026-07-03): what's changed since this plan was written

This plan was written on Day ~80 of the codebase. It's now ~98 days in, and a lot of the Week 1-2 "security & reliability hardening" items below have since shipped — this addendum records what's actually true today rather than leaving stale claims standing next to a corrected metrics table.

- **Tests: 2,163 passing** (was 1,264 at plan-writing time), plus 27 real-Postgres integration tests and 9 Electron `node --test` suites not counted in the original figure. Strict mypy and 5 custom CI guard scripts also gate every merge now — none of that existed in June.
- **The `except Exception: pass` security-path items in Section 3.1/3.2 are fixed** — `app/routers/exam.py`'s terminal-state and attestation checks now fail closed and log, and `proctor.py`'s cleanup handlers log per-operation instead of silently swallowing.
- **Kiosk attestation (Section 3.4) is live and hardened** — HMAC-SHA256 v2 attestation with nonce anti-replay, session binding, and minimum client version enforcement, plus a separate lighter-weight app-attestation for the lobby's login/signup form (bypasses Cloudflare Turnstile only for genuine signed builds).
- **Access codes are now compulsory on every exam** (server-generated/persisted, dashboard UI, Electron lobby, defense-in-depth 422 on empty) — this wasn't planned in the original doc at all; it shipped as a security hardening pass in late June/early July.
- **A full third-party technical due-diligence audit exists**: `STARTUP_AUDIT_REPORT.md` (repo root), 22 sections, scored, with a verified remediation addendum. Treat it as the current source of truth for security/architecture/production-readiness claims — it supersedes the self-assessment tone of Sections 3-4 below.
- **Cost figures corrected 2026-07-03**, see the footnote immediately below and `LAUNCH_COSTS_AND_SETUP.md`. The Windows code-signing line changed materially (was budgeting an unavailable Azure path; corrected to a verified Certum EV certificate), and Private Limited incorporation costs were revised upward and are now budgeted directly rather than deferred vaguely.

---

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [North Star: The Investor Story](#2-north-star-the-investor-story)
3. [Week 1–2: Security & Reliability Hardening](#3-week-1-2-security--reliability-hardening)
4. [Week 3–4: Product Completeness & Polish](#4-week-3-4-product-completeness--polish)
5. [Week 5–6: Business Artifacts & Demo Prep](#5-week-5-6-business-artifacts--demo-prep)
6. [Week 7–8: Rehearsal & Dry Runs](#6-week-7-8-rehearsal--dry-runs)
7. [The Hard Questions (Prepare Answers)](#7-the-hard-questions-prepare-answers)
8. [Honest Assessment: Is This a Real Business?](#8-honest-assessment-is-this-a-real-business)

---

## 1. Executive Summary

Procta is a full-stack AI-powered exam proctoring platform targeting the Indian coaching-institute market — a ₹5,000+ Cr TAM currently served by enterprise vendors (Mercer Mettl, Talview, HirePro) charging ₹500–1,000/student.

Procta's thesis: **coaching chains need AI proctoring at ₹50–150/student.** The incumbents are priced for enterprise. Procta delivers at ₹80/student with equal or better technology (on-device ML, phone-cam room monitoring, kiosk-mode lockdown browser, real-time dashboards).

**Current state (2026-06-15; figures marked † refreshed 2026-07-03):**

| Metric | Value |
|--------|-------|
| Codebase age | ~80 days at plan-writing, **~98 days today** † (first commit 2026-03-27) |
| Tests | 1,264 passing at plan-writing, **2,163 passing today** † (34 skipped, 0 failed), plus 27 real-Postgres integration tests and 9 Electron unit suites not in that count |
| SQL migrations | 100 at plan-writing, **123 today** † |
| FastAPI routers | 29 at plan-writing, **34 today** † |
| Load test proven | 3,000 concurrent students |
| Billing integration | Full Razorpay (test mode, go-live checklist ready) |
| Infra cost | ~₹700/month servers* (pre-paid through 2028) |
| Team | 1 founder (first-year engineering student) |
| Revenue | Pre-revenue |
| Paying customers | 0 |

> **\* Cost correction, revised 2026-07-03:** "₹700/month" is *servers only*. It excludes code signing, company formation, GST/CA compliance, Apple/Google developer accounts, payment-gateway fees (~2% + GST), and legal docs. As of 2026-07-03 the true operating floor is **~₹40–60k/yr beyond servers** (Windows EV code-signing and Pvt Ltd figures were re-verified against live vendor pricing and corrected upward from an earlier estimate), plus **~₹25–55k/yr once Pvt Ltd compliance starts at the raise**, plus ~2% of revenue in payment fees. Margin is ~90%, not ">95%". The 18-month capital ask that folds all of this in, verified line by line, is **`Procta_Investment_Requirement.pdf`** (generated by `scripts/gen_investment_pdf.py`); the narrative breakdown and sourcing is **[LAUNCH_COSTS_AND_SETUP.md](LAUNCH_COSTS_AND_SETUP.md)**.

**Investment goal:** Pre-seed / angel round. ₹1.5–3 Cr at ₹6–12 Cr pre-money valuation.

---

## 2. North Star: The Investor Story

Every slide, every demo, every conversation must reinforce one narrative:

> *"Procta is the proctoring stack for Indian coaching institutes. ₹80/student, phone-cam included, INR + GST invoicing, deployed in 10 minutes. We replace Mettl at 1/8th the cost. Built by a first-year engineer in 60 days — imagine what we'll do with a team."*

**The three pillars of the story:**

| Pillar | What it proves |
|--------|----------------|
| **Founder** | 18 years old, built this alone in 60 days. Velocity > everything. |
| **Market wedge** | ₹80 vs ₹500. Coaching chains spend crores on Mettl. Procta saves them 80%. |
| **Tech moat** | On-device ML = DPDP compliant. No raw video leaves the student machine. Phone-cam room monitoring included. |

---

## 3. Week 1–2: Security & Reliability Hardening

*Goal: Zero embarrassing security findings. An investor should be able to read the codebase and see professional security hygiene.*

### 3.1 Fix `except Exception: pass` in security paths

**Files:** `app/routers/exam.py`

**Lines 125–126** (`_reject_if_terminal`):
```python
except Exception:
    pass  # <-- if DB fails, a terminal-state session silently proceeds
```

**Lines 138–139** (`_require_attested`):
```python
except Exception:
    pass  # <-- if DB fails, an unattested session silently proceeds
```

**Fix:** Log the error, re-raise or raise HTTP 500. Never silently pass in a security check.

### 3.2 Fix `except Exception: pass` in proctor cleanup

**Files:** `proctor.py`, lines 3606–3634

Seven bare `except Exception: pass` blocks in the shutdown cleanup. Replace each with:
```python
except Exception as e:
    print(f"[PROCTOR] cleanup error ({operation}): {e}")
```

### 3.3 Verify `.env` hygiene

- Confirm `.env` is in `.gitignore`
- Confirm no real secrets were committed in any past commit
- Run `gitleaks` on the full git history:
  ```bash
  gitleaks detect --source . --verbose
  ```
- Document all env vars in `.env.example` with descriptions

### 3.4 Add kiosk attestation enforcement

The attest_nonce infrastructure exists. Complete the chain:

1. Server issues nonce at exam start
2. Client signs it (with `app.isPackaged`, kiosk state, platform)
3. Server verifies signature, sets `kiosk_attested = True` on session
4. All exam endpoints reject un-attested sessions when `KIOSK_ATTESTATION_ENFORCED = 1`

This is your strongest security story. Being able to say *"We cryptographically prove the kiosk was locked"* is a defensibility moat.

### 3.5 Run full security audit

```bash
# Static analysis
semgrep --config=auto .

# Dependency audit
pip-audit

# Secret scanning
gitleaks detect --source .

# CodeQL (already in CI, review findings)
```

Document results. Zero findings is a selling point.

---

## 4. Week 3–4: Product Completeness & Polish

*Goal: End-to-end flows work flawlessly. No broken pages, no dead ends, no placeholder text.*

### 4.1 Walk every user flow

Create a checklist and walk each path:

**Student:**
- [ ] Kiosk downloads and installs
- [ ] Login with roll number + access code
- [ ] VM/remote-desktop detection → blocked
- [ ] ID verification (selfie + ID upload)
- [ ] Phone-camera QR pairing → room scan
- [ ] Gaze calibration (5-dot pattern)
- [ ] Exam: navigate questions, auto-save, mark-for-review
- [ ] Calculator, scratchpad
- [ ] Submit → score display → auto-close

**Teacher:**
- [ ] Login, dashboard loads
- [ ] Create exam with question bank
- [ ] Bulk-import students via CSV
- [ ] Schedule exam, set time window
- [ ] Live monitoring: SSE dashboard, camera thumbnails
- [ ] Violation timeline with evidence screenshots
- [ ] Chat with student
- [ ] Warn/Pause/Resume/End controls
- [ ] Post-exam: scorecard, PDF report, CSV export
- [ ] Appeal workflow

**Admin:**
- [ ] Org settings, teacher management
- [ ] Billing: select plan → Razorpay checkout → subscription active
- [ ] Invoice history
- [ ] Cancel / reactivate subscription
- [ ] Change plan

### 4.2 Fix any broken flows

Document every bug found and fix it. Keep a log showing the bugs you found and fixed — investors love seeing "we caught and fixed 12 issues in our pre-demo audit."

### 4.3 Polish the marketing site

- [ ] Check every page loads correctly
- [ ] Verify pricing table matches `app/constants.py`
- [ ] Ensure comparison pages (Honorlock, Mettl, Proctortrack, Talview) are accurate
- [ ] No broken images, no placeholder text
- [ ] Mobile-responsive on key pages
- [ ] CTA buttons all work (demo request, signup, download)

### 4.4 Verify CSP is production-safe

The CSP middleware at `app/main.py:582` allows:
- `script-src 'self'` + Cloudflare + Razorpay CDNs
- `form-action 'self'`

Test every auth/onboarding path to ensure no inline scripts are blocked. LTI Deep Linking in particular — verify `form-action` allows posting to external LMS URLs.

---

## 5. Week 5–6: Business Artifacts & Demo Prep

*Goal: Everything an investor would ask for exists in a drawer, ready to pull out.*

### 5.1 Build the data room

Create a private folder with:

| Document | Purpose |
|----------|---------|
| **One-pager** | 1-page PDF: problem, solution, market, traction, team, ask |
| **Pitch deck** | 10–12 slides (see outline below) |
| **Financial model** | Simple spreadsheet: pricing, unit economics, growth scenarios |
| **Technical architecture** | 1-page diagram: how data flows, where ML runs, security boundaries |
| **Load test report** | "3,000 concurrent students, p95 submit: 51ms, 0.00% error rate" |
| **Competitive matrix** | Procta vs Mettl vs Talview vs Honorlock vs Proctortrack |
| **Privacy/DPDP compliance** | On-device ML, no video upload, data retention policies |
| **Go-to-market plan** | ICP: coaching chains. Channel: cold email, referrals, LTI integrations |

### 5.2 Pitch deck outline (10–12 slides)

1. **Problem** — Coaching chains overpay for proctoring (₹500–1,000/student). Mettl is built for enterprise, not for them.
2. **Solution** — Procta: AI proctoring at ₹80/student. 10-minute deployment. Full feature parity.
3. **Market** — ₹5,000+ Cr TAM. 50,000+ coaching institutes in India. JEE/NEET/UPSC mock tests at scale.
4. **Product** — 30-second demo video. Kiosk lockdown, on-device ML, phone-cam, real-time dashboards, auto-grading.
5. **Traction** — Waitlist signups, pilot conversations, product stats (2,163 tests, 123 migrations, 3,000 VU proven).
6. **Technology** — Privacy moat: no video leaves student machine. 2,163 automated tests, strict mypy, 5 CI guard scripts. Full CI/CD. 0 CodeQL alerts.
7. **Business model** — ₹2,400–30,000/month tiers. ₹80/student overage. 80% gross margin. Virality via teacher referrals.
8. **Competition** — Procta vs incumbents. Price 1/8th. Privacy-native. India-first. Phone-cam included.
9. **Team** — You. 18 years old. Built this alone in 60 days. Hiring a co-founder (CRO/GTM).
10. **Ask** — ₹1.5–3 Cr pre-seed. 24 months runway. Use: sales team (1 head), compliance (SOC 2), marketing.
11. **Vision** — 3-year: India's default proctoring layer. 5-year: Global AI assessment infrastructure.

### 5.3 Financial model

Build a spreadsheet with:

| Scenario | Year 1 | Year 2 | Year 3 |
|----------|--------|--------|--------|
| Conservative | 10 chains / ₹4.8L ARR | 50 chains / ₹2.4Cr ARR | 200 chains / ₹9.6Cr ARR |
| Base | 20 chains / ₹9.6L ARR | 100 chains / ₹4.8Cr ARR | 500 chains / ₹24Cr ARR |
| Bull | 50 chains / ₹24L ARR | 300 chains / ₹14.4Cr ARR | 1,500 chains / ₹72Cr ARR |

Show unit economics:
- **CAC:** ₹0 (organic/demo-driven) → ₹50,000/year after hiring sales
- **LTV:** ₹4.8L avg per chain (monthly ₹40K × 12 months avg retention)
- **Gross margin:** ~90% after Razorpay fees (~2% + GST) and the operating floor — still strong, but not ">95%". See [LAUNCH_COSTS_AND_SETUP.md](LAUNCH_COSTS_AND_SETUP.md).
- **Payback period:** Immediate (no CAC yet)

### 5.4 Load test documentation

From `loadtest/reports/`, extract key metrics:
- Peak: 3,000 concurrent virtual students
- Submit p95: 51ms
- Heartbeat p95: <200ms
- Scoring p95: 1.5s
- Error rate: 0.00%
- Infra: single 4-vCPU KVM at ₹700/month

The load test architecture (k6 + Locust, real JWT auth, practice mode) should be documented.

---

## 6. Week 7–8: Rehearsal & Dry Runs

*Goal: The demo is boringly reliable. You've run it 10 times without a single glitch.*

### 6.1 Build a demo environment

- Separate demo instance (could be localhost or a cheap secondary VPS)
- Pre-populated with: 1 coaching chain, 3 exams, 50 students, sample violations
- Razorpay test mode connected
- All flows verified end-to-end

### 6.2 Create a demo script (10 minutes)

```
0:00 – 0:30  Introduction: "I'm Arihant, first-year engineering. I built Procta in 60 days."
0:30 – 2:00  The market problem: "Coaching chains pay ₹500/student. Here's what they get."
2:00 – 3:00  Student login → kiosk lockdown → calibration → exam flow
3:00 – 4:00  Show cheating detection: phone appears → violation logged → risk score drops
4:00 – 5:00  Teacher live dashboard: camera thumbnails, violation timeline, chat
5:00 – 6:00  Post-exam: scorecard, PDF report, CSV export
6:00 – 7:00  Billing: select plan → Razorpay checkout → subscription activates
7:00 – 8:00  Technical moat: on-device ML, load test results, test suite
8:00 – 9:00  Business model: unit economics, GTM plan, the ask
9:00 – 10:00 Q&A
```

### 6.3 Practice answers to hard questions

See [Section 7](#7-the-hard-questions-prepare-answers).

### 6.4 Dry runs

- Run the demo for a friend (non-technical if possible)
- Run it for another engineer
- Run it for a faculty member
- Incorporate feedback. Iterate.
- On demo day, have a backup plan (recorded video if live fails)

---

## 7. The Hard Questions (Prepare Answers)

### 7.1 "What if Mettl drops their price to ₹200/student?"

> *"They can't. Mettl has 500+ employees, enterprise sales teams, and shareholders expecting growth. Their cost structure is built for ₹500/student. Dropping to ₹200 would destroy their margins. We're built for ₹80 from day one. Our entire infrastructure costs ₹700/month — we can sustain this price forever. They'd need to restructure their entire company to compete here."*

### 7.2 "How do you prevent a student from bypassing the kiosk?"

> *"Three layers. First, the Electron client enforces fullscreen, blocks shortcuts, overrides screen sharing, and disables dev tools. Second, we cryptographically attest the kiosk state — the client signs its lockdown status with a per-session nonce, and the server rejects un-attested sessions. Third, on-device ML detection catches phones, second monitors, and unauthorized persons even if the kiosk is bypassed. We've also fixed the config.js issue that previously allowed --no-kiosk bypass in development mode."*

### 7.3 "Why you and not a team of 5 IIT graduates?"

> *"Because I've already shipped. In 60 days, alone, I built what teams at Mettl took years and hundreds of engineers to build. 2,163 automated tests, 123 database migrations, full billing integration, real-time dashboards, LTI 1.3, on-device ML with 3 models running at 30fps on consumer laptops. A team of 5 IIT grads would spend 3 months in meetings deciding the tech stack. I shipped."*

### 7.4 "What's your co-founder situation?"

> *"I'm actively looking for a CRO/GTM co-founder. I'm an engineer. I know my gaps. The product ships — what's needed now is someone who can sell it. I'd allocate a significant equity stake (15-25%) for the right person. I'm open to introductions."* (If no co-founder yet, this honest answer is better than pretending.)

### 7.5 "How do you get coaching chains to switch from Mettl?"

> *"We don't need them to switch overnight. We start with one thing Mettl doesn't offer at any price: phone-camera room monitoring included in every plan. Then we offer to run a pilot alongside their existing proctor for free. Once they see the same results at 1/8th the cost, the switch happens naturally. We also integrate with their existing LMS via LTI 1.3 — zero migration cost."*

### 7.6 "What happens when one of your ML models fails?"

> *"Every model is wrapped in try/except with graceful degradation. If YOLO fails, face detection and gaze tracking still work. If the ONNX runtime crashes, the proctor falls back to rule-based detection. The system is designed to degrade gracefully — never catastrophically. We have 2,163 tests covering all critical paths."*

### 7.7 "How do you handle privacy / DPDP compliance?"

> *"All AI inference runs on the student's machine. No raw video or audio leaves the student PC — only event metadata (timestamps, detection types, confidence scores). We don't upload webcam footage to servers. This is DPDP-compliant by architecture, not by policy. For reference, our DPIA (Data Protection Impact Assessment) is in the data room."*

### 7.8 "What's your revenue today?"

> *"We're pre-revenue. We made a deliberate decision to perfect the product before charging. We ship billing to production on investor interest — the entire Razorpay integration is built and tested. Our go-live checklist takes 15 minutes. We wanted to show investors a complete product, not ask them to fund building it."*

---

## 8. Honest Assessment: Is This a Real Business?

**Yes, but let me be specific about what kind of business and under what conditions.**

### 8.1 The bull case (₹10+ Cr ARR within 3 years)

This requires:

1. **You find a co-founder who can sell.** You cannot do this alone. Your job is to build. Their job is to close. If you don't find this person, the cap is ~₹1-2 Cr ARR.

2. **You land 3 anchor coaching chains in the first 6 months.** These become case studies. Every other chain asks for references. You need names they recognize.

3. **You raise a small round (₹1.5-3 Cr) to hire 2-3 salespeople and start SOC 2.** Enterprise procurement requires SOC 2. Without it, you're locked out of universities and large chains.

4. **The on-device ML moat holds.** If incumbents don't match this, you own the privacy narrative. If they do, you compete on price — which you can still win.

### 8.2 The floor case (₹50L–1 Cr ARR, profitable lifestyle business)

Even without venture growth:

- 10–15 coaching chains at ₹40K/month average = ₹4.8–7.2L/month
- Infra costs ₹700/month
- No employees (you + part-time help)
- You're profitable from Month 1
- Zero outside funding needed
- You can run this from your hostel room through graduation

**This is a life-changing outcome for an 18-year-old.** Most startups fail. A ₹50L ARR business at 18 puts you in the top 0.1% of earners your age, with total freedom.

### 8.3 The existential risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Can't sell | Medium | Find a co-founder. Start sales conversations NOW, not after you "feel ready." |
| Mettl drops price | Low | Their cost structure prevents it. Watch for it. |
| Privacy regulation changes | Low | On-device ML is the safest architecture possible. |
| Burnout | Medium | You're 18. Don't work 16-hour days. This is a marathon. |
| Distraction from studies | Medium | Keep grades above water. The degree is insurance. |
| Market doesn't materialize | Low | Coaching chains need proctoring. The question is whether they buy from YOU. |

### 8.4 The bottom line

Procta is a real business with a real market, a real price advantage, and real technology. The question is not whether the product works — it's whether you can sell it.

**The good news:** You have 2 years of runway, zero pressure, and a product that's further along than 90% of funded startups. You can afford to learn sales the hard way.

**The honest advice:** Start sales conversations this week. Not next month. Not after the demo is polished. Now. Each "no" teaches you more than a month of coding. The product is ready. Go sell.

---

*This document was generated as part of Procta's investment readiness preparation.*
