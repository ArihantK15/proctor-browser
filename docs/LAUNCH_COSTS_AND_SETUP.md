# Procta — Real Cost to Launch & Operate

**Author:** Arihant Kaul
**Date:** 2026-06-19 (Windows code-signing and Pvt Ltd figures re-verified and corrected 2026-07-03)
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

## 2. Code signing — DECISION (revised 2026-07-03, was wrong)

The desktop app must be code-signed or end users hit scary OS warnings.

> **Correction (2026-07-03):** the 2026-06-19 research below was wrong on price and wrong on availability. EV was priced here as "~$300+/yr, full stop... priced for funded companies" and deferred; separately, the investment PDF (`scripts/gen_investment_pdf.py`) budgeted a $10/month Azure Trusted Signing subscription as the actual chosen path. **Azure Trusted Signing is not available to us at all** (confirmed below), so that budget line was never buildable — and EV, once actually priced against a live vendor quote, turns out to be *affordable*, not "for funded companies."

### Windows
- **Azure Trusted Signing (~$10/mo) is NOT available to us:** individual developers are limited to **USA/Canada**; India is excluded and individual onboarding is paused. Organizations limited to US/Canada/EU/UK with 3+ years history — so incorporating in India won't unlock it either. **This path cannot be budgeted, full stop — it isn't a matter of waiting for a cheaper tier.**
- **Corrected EV price, verified 2026-07-03: Certum EV Code Signing ≈ $289.99/yr** (sslcertshop.com, list price $329) — instant SmartScreen-clean, no reputation-building wait. Multi-year discounts exist (2yr ≈ $285.99/yr avg, 3yr ≈ $279.99/yr avg), but a CA/Browser Forum rule effective **23 February 2026 caps every new code-signing certificate at 459 days (~15 months)** regardless of the term nominally purchased — so in practice this is a ~$290 purchase every ~15 months, not a clean annual renewal.
- **Revised chosen path → EV, not OV.** At ~$290 per ~15-month issuance (≈₹27,550 at ₹95/$, ≈$232/yr-equivalent) EV costs roughly $100–175/yr more than the OV budget path below (which ranges $116–189/yr), but removes the SmartScreen warning from the first download — materially better for cold outreach to coaching institutes who have never heard of Procta and will not tolerate a scary installer warning. The OV path remains documented below as the fallback if capital is tighter than expected.
- **Fallback budget path → [Certum Cloud Code Signing **Individual**](https://www.sslmentor.com/certum/certumcodecloudindividual)** (~$116–189/yr ≈ ₹10–16k/yr):
  - Sold *only* to individuals (no company needed); validates Indian individuals.
  - **SimplySign cloud signing** — no USB token, works in GitHub Actions CI.
  - **Tradeoff:** OV-class, *not* EV → SmartScreen reputation **builds over downloads/time**, not instant. Sign early so reputation accrues before the GA push.

### macOS — LAUNCHING DAY ONE (corrected 2026-07-03, was wrong)

> **Correction:** this section previously said "launch Windows-only, add macOS post-revenue." That decision is reversed — **macOS ships in the same day-one launch as Windows, not deferred.** Apple Developer Program enrollment is a required pre-launch purchase, in the same category as the Windows EV certificate: budgeted, not yet completed, needs to happen before GA — not a "someday" item.

- Apple Developer Program ($99/yr, ~₹9,500) is **not yet enrolled — this is an open pre-launch action item**, exactly like the Windows EV cert purchase. Without it, mac builds are ad-hoc signed and Gatekeeper blocks downloaded copies; this must close before GA, not after. Verified 2026-07-03: it's worse than a Gatekeeper prompt — Squirrel.Mac (electron-updater's mac auto-update mechanism) **requires a real Developer ID signature to auto-update at all**. An ad-hoc-signed release would install fine but then never receive another update, silently, for every Mac student who installed it — no crash, no error, just a permanently stale app with no way back except a fresh manual reinstall. Confirmed via `gh secret list` that none of `CSC_LINK`/`APPLE_ID`/`APPLE_APP_SPECIFIC_PASSWORD`/`APPLE_TEAM_ID` are configured as repo secrets yet. Added a hard CI gate (`.github/workflows/build.yml`, "Refuse unsigned macOS publish") that fails any tagged release build if these secrets are still missing, so this can't ship silently — but the secrets still need to actually be set before the first real macOS release tag is pushed.
- **Decision: launch both Windows and macOS together.** Budgeted in `Procta_Investment_Requirement.pdf` (Section 3, Annual Recurring) as a day-one cost, not a deferred one.
- Enrolling as an **Organization** account (rather than Individual) needs a D-U-N-S number and, once incorporated, ties to the Pvt Ltd entity from Section 3 below — sequence the D-U-N-S application early since it has its own lead time, separate from the $99/yr fee itself.
- `build/entitlements.mac.plist`, `build/icon.icns`, and the `mac:` target (dmg + zip, hardened runtime) already exist in `package.json` — the packaging side is ready. What's missing is (a) the paid enrollment itself, (b) wiring `CSC_LINK`/notarization credentials into CI once enrolled, and (c) auditing the kiosk-lockdown code paths for macOS parity with Windows (window management, fullscreen enforcement, process/app monitoring — these likely use different OS APIs per platform and need independent verification, not an assumption that "it compiles for mac" means "it locks down a mac exam the same way it locks down a Windows one").

### Decision summary
- **For the pilot:** minimize spend, but do not skip platform parity — walk hand-onboarded institutes past SmartScreen/Gatekeeper on a call if signing isn't live yet, on **either** platform equally.
- **For GA (day one):** Certum EV (Windows) + Apple Developer Program ($99/yr, macOS) are both required, both budgeted, both pre-launch action items — not sequenced one after the other.
- **CI wiring:** SimplySign (Windows) and the mac `CSC_LINK`/notarization path are separate build.yml steps; both need to be live before the day-one release, not just one of them.

---

## 3. Company formation — DECISION NEEDED

> **Figures re-verified 2026-07-03** against 2026 market surveys (RegisterKaro, PatronAccounting, Kanakkupillai, IndiaFilings, Vakilsearch et al.). The Pvt Ltd setup range was understated before (₹8–15k) — a realistic 2026 all-in quote for a standard two-director filing is higher. LLP and Sole Proprietorship figures held up and are essentially unchanged.

Currently operating as an individual (student). This works for a pilot and for Certum Individual signing, but a real paid business eventually needs an entity for: a business bank account, GST invoicing at scale, contracts/liability protection, investor funding, and Razorpay KYC as a business.

| Option | Setup (approx) | Annual compliance (approx) | Notes |
|---|---|---|---|
| **Sole Proprietorship** | ~₹2–5k (GST + Udyam reg) | Low — just GST/ITR via a CA (~₹10–20k/yr) | Cheapest. No liability shield. Fine to *start* charging. Razorpay accepts. |
| **LLP** | ~₹5–15k (govt fee ₹1.2–6k + DSC ₹0.8–1.5k/partner + stamp duty ₹0.5–10k+ + professional fee ₹4–9k) | ~₹15–30k/yr (ROC + audit thresholds) | Liability shield, lighter compliance than Pvt Ltd. Poor fit for equity funding. |
| **Private Limited (Pvt Ltd)** | **~₹15–25k typical, up to ₹32k** (SPICe+ filing is free under ₹15L capital; the variable cost is stamp duty ₹0.2–12.6k by state, DSC ₹1.5–2.5k/director, name reservation ₹1k, professional fees ₹5–20k) | **~₹25–55k/yr** for a small pre-revenue company (ROC filings, statutory audit, CA retainer) — can run to ₹1.5L/yr once GST filing + bookkeeping scale with revenue | **Required for VC/angel equity funding.** Heaviest compliance. |

**Recommendation:**
- **Now → Sole Proprietorship** (or stay individual + GST) to start the pilot and even take first payments. Minimal cost, unblocks a business bank account + GST invoices.
- **Before raising / at GA scale → Private Limited.** Investors fund Pvt Ltds, not individuals — **incorporation is a precondition of actually closing a round, not a discretionary later expense.** Budget ~₹18k setup + ~₹30k/yr compliance (pre-revenue) as representative figures; see `Procta_Investment_Requirement.pdf` for how this is folded into the capital ask.
- *[All figures approximate — confirm with a CA/CS. Costs vary by state and service provider.]*

---

## 4. Platform & developer accounts

| Item | Cost | Blocks what? | Status |
|---|---|---|---|
| Apple Developer Program | $99/yr (~₹9.5k) | Signed/notarized macOS build | **Required at launch, day one alongside Windows** — open pre-launch action item; budgeted in `Procta_Investment_Requirement.pdf` |
| Windows code signing — EV (chosen path, revised 2026-07-03) | ~$289.99 per ~15-month issuance (Certum EV, verified vs sslcertshop.com) | Instant SmartScreen-clean Windows installs, no reputation-building wait | Required at launch; budgeted in `Procta_Investment_Requirement.pdf` |
| Windows code signing — OV (fallback if capital is tight) | ~$116–189/yr (Certum Individual) | SmartScreen-clean, but builds reputation over downloads/time | Not chosen — kept as a cheaper fallback option |
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
| **One-time floor** | **~₹2–5k** | (Pvt Ltd ~₹15–25k, up to ₹32k, later at the raise — revised 2026-07-03, was understated) |

### Recurring (annual run-rate at GA, excl. servers)
| Item | Approx/yr | Notes |
|---|---|---|
| Windows signing — EV, chosen path (revised 2026-07-03) | ~₹22k/yr-equivalent (~₹27,550 per ~15-month issuance) | Instant SmartScreen-clean; OV fallback below if capital is tight |
| Windows signing — OV, fallback | ~₹10–16k | Cheaper, but SmartScreen reputation builds over time instead of instant |
| GST/ITR compliance (CA) | ~₹15–30k | Once charging customers |
| Domain/email | ~₹2–6k | |
| Apple Developer Program | ~₹9.5k/yr ($99) | Required at launch, day one alongside Windows — not deferred |
| Google CASA | ₹0 | Confirmed no restricted scopes |
| **Recurring floor, EV path (beyond ₹8.4k/yr servers)** | **~₹48–68k/yr** + payment fees (~2% of revenue) | Excludes Pvt Ltd compliance (~₹25–55k/yr), which only starts once incorporated at the raise |

**Takeaway:** the *true* operating floor to run Procta as a paid Windows-and-macOS product (both launching day one) is roughly **₹48–68k/year** beyond servers (excluding Pvt Ltd compliance and SOC 2), plus ~2% of revenue in payment fees. Once Pvt Ltd is live (at the raise), add ~₹25–55k/yr for ROC/audit/CA on top. That's still tiny against even ₹5L/mo revenue — the unit economics survive intact — but it is **not** "₹700/month, 95% margin."

---

## 8. How to go about it — sequenced

1. **Now (pilot, ~₹0):** run the pilot as an individual on both Windows and macOS; walk users past SmartScreen/Gatekeeper on a call where signing isn't live yet. Buy nothing yet, but don't build pilot infra as if macOS is optional.
2. **Start the slow clock now:** decide on `classroom.profile.emails`, then submit **Google OAuth verification** (unblocked — signup/RLS fixed). Enroll in **Apple Developer Program** and price **Certum EV** — both are day-one launch blockers, start both clocks together (Apple D-U-N-S lookup has its own lead time).
3. **As first revenue appears:** register a **Sole Proprietorship** + GST + business bank account; buy the Certum EV cert and confirm the Apple Developer enrollment is live — both must be done before GA, not sequenced after revenue.
4. **Before the raise / at GA scale:** incorporate **Private Limited**; budget SOC 2 from raised funds.
5. **Update the investment model** with the §7 floor and the ~90% (not 95%) margin.

### Decisions — resolved 2026-06-19
- [x] **Incorporation:** stay an individual for now; register a sole proprietorship only when the first paying customer appears. Pvt Ltd waits until the raise.
- [x] ~~**Apple/macOS:** not funded → **launch Windows-only**; add macOS post-revenue.~~ **Reversed 2026-07-03** — see below.
- [x] **Google CASA:** not required (no restricted scopes confirmed in console).
- [x] ~~**Certum cert:** affordable (~₹800/mo) but deferred to GA, as agreed.~~ **Superseded 2026-07-03** — see below.

### Decisions — resolved 2026-07-03
- [x] **Windows signing tier: EV, not OV.** Verified Certum EV at ~$289.99 per ~15-month issuance (≈₹27,550) is affordable enough to budget directly rather than defer — the earlier "$300+/yr, for funded companies" framing was wrong. Instant SmartScreen-clean beats OV's reputation-building wait for cold outreach to institutes who've never heard of Procta. OV remains the fallback if capital comes in tighter than planned.
- [x] **Azure Trusted Signing confirmed dead, not just deferred.** The investment PDF had separately budgeted this as the actual chosen path ($10/month); it isn't available to Indian individuals at all. Removed from the capital ask and replaced with the EV line above.
- [x] **Pvt Ltd cost figures corrected upward.** 2026 market rate for standard two-director filing is ~₹15–25k (up to ₹32k), not ₹8–15k. Annual compliance ~₹25–55k/yr for a small pre-revenue company was already roughly right and is unchanged.
- [x] **Pvt Ltd incorporation is now budgeted directly in the capital ask**, not deferred as a vague "later" line — accepting outside equity requires an entity to issue it into, so incorporation is a precondition of this raise closing, not a discretionary future expense.
- [x] **macOS launch decision reversed: ships day one alongside Windows, not deferred.** The 2026-06-19 "Windows-only" decision is wrong and is retracted. Apple Developer Program ($99/yr) moves from "not funded, post-revenue" to a required, budgeted, open pre-launch action item — same status and urgency as the Windows EV certificate purchase. This changes the recurring floor (§7) from ~₹39–58k/yr to ~₹48–68k/yr. Downstream consequence: every "Windows-only" claim in the investor materials (pitch deck, README, features PDF) needs auditing for the same correction, and the actual macOS kiosk-lockdown code paths need verification for parity with Windows — packaging (entitlements, icon, dmg/zip target) already exists in `package.json`, but that is not the same claim as "the lockdown enforcement works identically on both platforms."

### Still open
- [ ] **Drop `classroom.profile.emails`?** If Procta's own invite flow already covers emails, dropping it leaves only non-sensitive scopes and lightens Google verification. Decide before submitting.
- [ ] **Submit Google OAuth verification now** — signup + RLS are fixed, so it's unblocked. This is the calendar-bound clock to start.
