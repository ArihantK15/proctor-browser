# Strategic Audit — 2026-06-14

VC/angel-lens strategic review (prior session). Preserved verbatim; a verified-
corrections addendum from the next session is appended at the end.

## A. Executive Summary

**What it is:** Procta — an AI exam-proctoring SaaS for the Indian market. Electron
"secure browser" client + on-device AI (face/audio/phone-cam detection) → FastAPI/
Postgres/Redis backend → Razorpay billing → React marketing site (Vercel) + an HTML
ops dashboard. Single docker-compose box.

**Biggest opportunities:**
1. India/DPDP as a wedge. Real DPDP work (guardian consent, data export/deletion,
   breach incidents, encrypted Mumbai S3). Western incumbents (Proctorio, Honorlock,
   ProctorU) are privacy-toxic and US-data-resident. "India-resident, DPDP-native
   proctoring" is a defensible regional position the giants won't prioritize.
2. Coaching-institute market (NEET/JEE/UPSC prep) is enormous, underserved, price-
   sensitive — tiered Razorpay pricing + /coaching page already target it. Better ICP
   than universities (faster sales cycle, less procurement).
3. Ships fast. Solo operator + AI agents cleared ~30 enterprise gaps, built billing,
   kiosk attestation, release pipeline in weeks. Velocity is an asset.

**Biggest threats (existential, ranked):**
1. The core guarantee is bypassable and you know it. Client-side kiosk + attestation
   "raises the bar, not bulletproof." One viral "I beat Procta with a VM/second laptop"
   video destroys trust.
2. Single box = single death. API, workers, Postgres, Redis, Caddy, cron all on one
   docker-compose host. One disk-full / OOM / kernel panic during a live exam = total
   outage, no failover. Not 10x-able as-is.
3. Bus factor 1 + AI-built debt. Codebase substantially built by a cheap implementer
   whose summaries repeatedly claimed "done" while shipping silently-broken code (now()
   DataError, dead invoice cache, dead coupon endpoint, attestation secret never baked).
   Quality rests on one human reviewer.
4. Market is commoditized and reputationally radioactive. Lawsuits (ADA/accessibility,
   face-detection bias on dark skin), student revolts, Western university bans.

## B. Top 10 highest-impact improvements (by ROI)
1. Verify + safely enable the attestation already shipped (dormant + unverified). One
   confirmed live round-trip → flip enforcement = headline integrity claim becomes real.
2. Get off the single box. Managed Postgres (read replica) + object storage for
   screenshots/frames + a second API host behind Caddy.
3. Make integrity provable, not just claimed. Server-side behavioral evidence is the
   real moat. Tamper-evident, exportable per-session "integrity report."
4. Replace mocked-DB unit tests with real-Postgres integration coverage on money/
   security paths (billing, attestation, auth, scoring). "Green but broken" bit ~6×.
5. Kill the AI-debt feedback loop — integration tests as the gate, or slow down.
6. Notarize the macOS build (~$99/yr + notarize step already stubbed).
7. Self-serve onboarding + a real trial (verify TRIAL_DAYS wired into Razorpay).
8. Accessibility/fairness story (build on per-student time-extension; publish statement).
9. Observability (Sentry/B2 activation + metrics: attestation success, completion, error).
10. Finish LTI properly (deep-link broken by form-action 'self') or pull the claim.

## C. Quick wins
- Notarize macOS (~1 day). Confirm/enable attestation (one round-trip + flag).
  Fix or de-advertise LTI deep-link (~half day, or remove claim in 10 min). Wire trial →
  Razorpay if intended. Status/health page during exams. Resolve the requirements.lock
  stash. Disk-full guard + rotation on the single box before S3 lands.

## D. Deep technical improvements
- Stateless-API horizontal scale (2+ API containers behind Caddy; SSE already Redis-
  backed — verify no connection affinity assumed).
- Object storage for screenshots + phone-cam frames (finish encrypted S3-Mumbai; CDN).
- Postgres read replica for heavy reads; pgbouncer already present. Audit unbounded list
  at app/routers/api.py:97-124.
- Scoring queue correctness: A3 (flush/scoring cross-queue race) + A2 (TOCTOU double-
  submit) become real at >1 scoring worker. Make scoring depend on flush, or read-cache-
  then-DB.
- Test strategy overhaul: mocked _atable doesn't reflect asyncpg types/JSON/pagination —
  root cause of "green but broken." Gate money/security paths on the integration leg.
- Attestation hardening: baked secret is plaintext in asar; bytenode/obfuscation un-taken;
  TPM/App Attest is the real ceiling.
- Tamper-evident violation/risk trail (hash-chain or signed records) → legally defensible
  integrity report = the moat.

## E. Feature roadmap (prioritized)
- Must-have: provable integrity report per session; org-wide live proctor war-room (SSE
  admin-only-own-sessions limitation); reliability/SLA.
- Retention: question bank + reuse (have versioning); LMS done right; cohort analytics.
- Wow: on-device AI privacy story; DPDP-native one-click compliance report; adaptive/
  risk-based proctoring.
- DON'T build yet (YAGNI): SMS/push, webhook platform, feature-flag infra, i18n.

## F. Final verdict
- As a VC: No, not today. Single-box infra, bypassable/unverified integrity, commoditized
  legally-fraught market; bus-factor-1 + AI-debt compound it.
- As an angel / bootstrapped regional SaaS: Cautiously yes, small. India/DPDP wedge real,
  coaching TAM large, on-device AI a genuine differentiator. Plausible ₹1–5Cr ARR regional
  business; not venture-scale.
- Flips to "fund it": (1) attestation verified+enabled with published benchmark; (2) off
  the single box + SLA + 2–3 reference institutions at national scale; (3) tamper-evident
  integrity-report moat + clean accessibility/fairness posture; (4) a second engineer.

---

## Reviewer addendum (next session — verified against code)
- **A1 scoring bug is FIXED** — `scoring.py:147` now passes `exam_id=exam_id`; multi-exam+
  shuffle mis-scoring resolved. (Was the highest-severity correctness bug.)
- **Infra host discrepancy** — this audit says "Contabo KVM"; project memory says
  **Hostinger** (corrected repeatedly). Confirm the actual host; infra recommendations are
  provider-agnostic regardless.
- **Trial** — confirmed `TRIAL_DAYS=14` is NOT passed to Razorpay `subscription.create`
  (`trialing` only appears as a recognized status). Only a bug if the intended model is a
  *trialing subscription*; fine if "trial" = card-free free access before subscribing.
  Decide the model before "fixing."
- **Two classes of "green but broken" → two gates.** DB-shape bugs (now() DataError, dead
  invoice cache, A1) → real-Postgres integration tests catch them. Dead-feature/logic-
  contradiction bugs (coupon endpoint unreachable, attestation secret never baked, LTI
  form-action, --no-kiosk bypass) → integration tests will NOT catch them; they need
  end-to-end "can a real user complete this flow?" verification. The audit's "promote
  integration tests" is necessary but not sufficient.
- **Re-rank:** the verification gate (above) is the true #1 meta-fix — without it, enabling
  attestation can silently break and 403 every student. Verify-attestation comes right after.
- **Attestation is deterrence, not proof** even when enabled (plaintext secret in asar).
  Market as "tamper-resistant," not "tamper-proof"; the real moat is the tamper-evident
  server-side evidence trail (audit item B3/D).
