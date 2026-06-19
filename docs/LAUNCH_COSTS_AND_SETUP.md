# Procta — Real Cost to Launch & Operate

**Author:** Arihant Kaul
**Date:** 2026-06-19
**Status:** Living document — single source of truth for launch/operating costs and the legal/account setup that gates a real launch.

> **Why this exists:** [INVESTMENT_READINESS_PLAN.md](INVESTMENT_READINESS_PLAN.md) modelled cost as "~₹700/month infra, >95% gross margin." That only counts servers. It omitted code signing, company formation, payment-gateway fees, GST/compliance, the Apple/Google developer accounts, and legal docs. This document is the corrected, complete picture. **Treat the numbers as realistic ranges to verify, not quotes.**

---

## 1. The headline correction

| Claim in the old plan | Reality |
|---|---|
| Infra ₹700/mo, "flat" | True *for servers only*. Total run-rate to operate a paid product is higher. |
| Gross margin >95% | Overstated. Razorpay takes **~2% + GST** per transaction off the top, before any compliance/account costs. Real gross margin is still healthy (~90%+) but not 95%+. |
| (Not mentioned) | Code signing, incorporation, GST/CA compliance, Apple/Google accounts, legal docs — **none were in the model.** |

The business is still attractive (software margins, tiny infra). The point is honesty: there is a **one-time setup cost** and a **recurring floor** beyond servers, and some of it has lead time (signing validation, OAuth verification, incorporation).

---

## 2. Code signing — DECISION (this is settled)

The desktop app must be code-signed or end users hit scary OS warnings. Findings from the 2026-06-19 research:

### Windows
- **Instant SmartScreen-clean = EV cert = ~$300+/yr, full stop.** No cheaper option removes the "Windows protected your PC" warning on day one. EV is priced for funded companies.
- **Azure Trusted Signing (~$10/mo) is NOT available to us:** individual developers are limited to **USA/Canada**; India is excluded and individual onboarding is paused. Organizations limited to US/Canada/EU/UK with 3+ years history — so incorporating in India won't unlock it either.
- **Chosen budget path → [Certum Cloud Code Signing **Individual**](https://www.sslmentor.com/certum/certumcodecloudindividual)** (~$116–189/yr ≈ ₹10–16k/yr):
  - Sold *only* to individuals (no company needed); validates Indian individuals.
  - **SimplySign cloud signing** — no USB token, works in GitHub Actions CI.
  - **Tradeoff:** OV-class, *not* EV → SmartScreen reputation **builds over downloads/time**, not instant. Sign early so reputation accrues before the GA push.

### macOS — NOT at launch (decided 2026-06-19)
- Apple Developer Program ($99/yr) is **not active and not funded.** Without it, mac builds are ad-hoc signed and Gatekeeper blocks downloaded copies.
- **Decision: launch Windows-only.** The Indian coaching-institute market is overwhelmingly Windows, so dropping macOS removes the $99/yr cost rather than deferring a problem. Add macOS (Apple membership) **post-revenue**.
- When mac is added later, an Apple Developer **Organization** account (post-incorporation) needs a D-U-N-S number. Same $99/yr.

### Decision summary
- **For the pilot:** buy nothing. Walk hand-onboarded institutes past SmartScreen on a call.
- **For GA:** Certum Individual (Windows) + Apple Developer ($99/yr, macOS). Optionally buy Certum early during the pilot so Windows reputation builds before launch.
- **CI wiring:** SimplySign (Windows) is a separate step from the mac `CSC_LINK` path — to be added when credentials exist.

---

## 3. Company formation — DECISION NEEDED

Currently operating as an individual (student). This works for a pilot and for Certum Individual signing, but a real paid business eventually needs an entity for: a business bank account, GST invoicing at scale, contracts/liability protection, investor funding, and Razorpay KYC as a business.

| Option | Setup (approx) | Annual compliance (approx) | Notes |
|---|---|---|---|
| **Sole Proprietorship** | ~₹2–5k (GST + Udyam reg) | Low — just GST/ITR via a CA (~₹10–20k/yr) | Cheapest. No liability shield. Fine to *start* charging. Razorpay accepts. |
| **LLP** | ~₹7–12k | ~₹15–30k/yr (ROC + audit thresholds) | Liability shield, lighter compliance than Pvt Ltd. Poor fit for equity funding. |
| **Private Limited (Pvt Ltd)** | ~₹8–15k | ~₹25–50k/yr (ROC filings, statutory audit, CA, compliances) | **Required for VC/angel equity funding.** Heaviest compliance. |

**Recommendation:**
- **Now → Sole Proprietorship** (or stay individual + GST) to start the pilot and even take first payments. Minimal cost, unblocks a business bank account + GST invoices.
- **Before raising / at GA scale → Private Limited.** Investors fund Pvt Ltds, not individuals. Time this with the raise so you don't pay Pvt Ltd compliance during pre-revenue months.
- *[All figures approximate — confirm with a CA/CS. Costs vary by state and service provider.]*

---

## 4. Platform & developer accounts

| Item | Cost | Blocks what? | Status |
|---|---|---|---|
| Apple Developer Program | $99/yr (~₹8.5k) | Signed/notarized macOS build | **NOT at launch** — Windows-only; add post-revenue |
| Windows code signing (Certum Individual) | ~$116–189/yr | SmartScreen-clean Windows installs | Deferred to GA (affordable, not yet needed) |
| Google OAuth verification (Classroom) | **Free** (review time only) | Classroom features at GA | Ready to submit (signup/RLS now fixed) — [GOOGLE_CLASSROOM_VERIFICATION.md](GOOGLE_CLASSROOM_VERIFICATION.md) |
| Google CASA security assessment | **₹0 — NOT required** | — | **Confirmed 2026-06-19:** Cloud Console Data Access shows **no restricted scopes** (only non-sensitive + one sensitive). CASA is triggered only by *restricted* scopes. |
| Domain + transactional email | ~₹1–5k/yr + email send costs | — | Confirm provider/costs |

**Google scopes (verified in console 2026-06-19):** non-sensitive — `classroom.courses.readonly`, `classroom.coursework.students`, `classroom.rosters.readonly`, `classroom.coursework.me`; sensitive — `classroom.profile.emails`; restricted — none. The single *sensitive* scope is what forces the heavier (still free) verification review. **Optimization:** Procta already collects student emails via its own invite flow, so consider **dropping `classroom.profile.emails`** — that would leave only non-sensitive scopes and can make verification much lighter or skippable.

> **Lead-time warning:** Google OAuth verification and Certum identity validation are **calendar-bound** (days–weeks of someone else reviewing). Start them *before* the code is "done," or they become the launch bottleneck. Signup + RLS are now fixed, so the Google submission is unblocked — start that clock.

---

## 5. Payment & tax (the gross-margin correction)

- **Razorpay fee:** ~**2% + 18% GST on the fee** per transaction (standard domestic). On ₹80/student that's ~₹1.9/student off the top. Material at volume — fold into unit economics.
- **GST:** Once charging, GST registration + periodic filing (CA ~₹1–3k/mo). Output GST is collected from customers (pass-through), but filing is a real compliance cost and time sink.
- **Effective gross margin:** still strong (~88–92% after payment fees + infra), **not** ">95%." Update the pitch number to a defensible ~90%.

---

## 6. Legal & compliance docs (mostly time, some cost)

| Item | Cost | Why |
|---|---|---|
| Terms of Service + Privacy Policy | ₹0 (DIY/template) to ₹15–40k (lawyer) | Required before public signup; you handle exam-taker PII. |
| Data Processing Agreement (DPA) | Template or lawyer | Institutes (B2B) will ask for it. |
| DPDP compliance posture | Mostly architectural (already on-device ML) | [DPIA.md](DPIA.md) exists — good. Keep current. |
| SOC 2 | $10–40k (future) | Already flagged in the investment plan as a fund-use item; enterprise/university procurement gate. Not a pre-launch cost. |

---

## 7. Consolidated cost picture

### One-time / setup (before GA)
| Item | Approx | Required for |
|---|---|---|
| Sole prop registration (GST/Udyam) | ₹2–5k | Taking payments as a business |
| ToS/Privacy (DIY) | ₹0 | Public signup |
| **One-time floor** | **~₹2–5k** | (Pvt Ltd ₹8–15k later, with the raise) |

### Recurring (annual run-rate at GA, excl. servers)
| Item | Approx/yr | Notes |
|---|---|---|
| Windows signing (Certum Individual) | ~₹10–16k | Needed at GA; can buy earlier to build reputation |
| GST/ITR compliance (CA) | ~₹15–30k | Once charging customers |
| Domain/email | ~₹2–6k | |
| Apple Developer | ₹0 at launch | Windows-only launch; ~₹8.5k/yr when mac is added post-revenue |
| Google CASA | ₹0 | Confirmed no restricted scopes |
| **Recurring floor (beyond ₹8.4k/yr servers)** | **~₹27–52k/yr** + payment fees (~2% of revenue) | |

**Takeaway:** the *true* operating floor to run Procta as a paid Windows-only product is roughly **₹30–60k/year** beyond servers (excluding the optional Pvt Ltd compliance, future macOS, and SOC 2), plus ~2% of revenue in payment fees. That's still tiny against even ₹5L/mo revenue — the unit economics survive intact — but it is **not** "₹700/month, 95% margin."

---

## 8. How to go about it — sequenced

1. **Now (pilot, ~₹0):** run the pilot as an individual, Windows-only; walk users past SmartScreen. Buy nothing.
2. **Start the slow clock now:** decide on `classroom.profile.emails`, then submit **Google OAuth verification** (unblocked — signup/RLS fixed). Price Certum Individual for later.
3. **As first revenue appears:** register a **Sole Proprietorship** + GST + business bank account; buy the Certum Individual cert so Windows reputation builds during the pilot.
4. **Before the raise / at GA scale:** incorporate **Private Limited**; revisit EV signing and Azure Trusted Signing (once India individual support opens); budget SOC 2 from raised funds.
5. **Update the investment model** with the §7 floor and the ~90% (not 95%) margin.

### Decisions — resolved 2026-06-19
- [x] **Incorporation:** stay an individual for now; register a sole proprietorship only when the first paying customer appears. Pvt Ltd waits until the raise.
- [x] **Apple/macOS:** not funded → **launch Windows-only**; add macOS post-revenue.
- [x] **Google CASA:** not required (no restricted scopes confirmed in console).
- [x] **Certum cert:** affordable (~₹800/mo) but deferred to GA, as agreed.

### Still open
- [ ] **Drop `classroom.profile.emails`?** If Procta's own invite flow already covers emails, dropping it leaves only non-sensitive scopes and lightens Google verification. Decide before submitting.
- [ ] **Submit Google OAuth verification now** — signup + RLS are fixed, so it's unblocked. This is the calendar-bound clock to start.
