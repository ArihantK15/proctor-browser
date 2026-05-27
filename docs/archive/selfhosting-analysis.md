# Self-Hosting Analysis for Procta

> Target customer: Universities/colleges (500–5000 students)
> AI grading model: Proxy through Procta's keys (metered)

---

## 1. Current Readiness

The codebase is surprisingly close to self-hostable thanks to the recent Supabase→Postgres migration.

| Component | Status | Notes |
|-----------|--------|-------|
| Local auth (bcrypt + JWT) | ✅ Works | `AUTH_PROVIDER=local`, no Supabase needed |
| Local Postgres (asyncpg) | ✅ Works | `DATABASE_BACKEND=postgres`, zero Supabase REST |
| Billing sandbox mode | ✅ Works | Runs without Razorpay keys |
| Docker Compose stack | ✅ Works | Single `docker compose up` |
| External Postgres support | ✅ Works | Profile-gated (`--profile postgres`) |
| Config via single `.env` | ✅ Works | All options in one file |
| Electron server URL config | ✅ Works | `PROCTOR_SERVER_URL` env var |
| No hard Supabase dependency | ✅ Works | `_UnavailableSupabase` stub guards |

---

## 2. What Needs Building

### Phase 1 — Self-Hostable Codebase (~10 days)

| Item | Effort | Details |
|------|--------|---------|
| **License key system** | 3 days | Generator (your tooling) + validator (in-app). Key is a signed JWT containing `org`, `max_students`, `features`, `exp`. Validate on startup + periodic check. Grace period support. |
| **SMTP email support** | 1 day | Abstract current Resend-only email into a provider pattern (Resend + SMTP). Schools have their own mail servers. |
| **Optional CAPTCHA** | 0.5 day | `TURNSTILE_ENABLED=false` skips validation. Internal/LAN deployments don't have Cloudflare. |
| **Env var cleanup** | 0.5 day | Rename `SUPABASE_JWT_SECRET` → `JWT_SECRET` (confusing name). Remove any `supabase.co` domain references. |
| **Self-hosted signup flow** | 1 day | Current signup always creates a Razorpay trial subscription. Self-hosted mode: creates license-based plan, no payment. |
| **Server URL hardcodes** | 0.5 day | `https://app.procta.net` defaults in `config.js`, `lobby_preload.js`, `dashboard-ui/src/config.js`, `website/src/config.js`, worker.py |
| **Health endpoint** | 0.5 day | `GET /health` + `GET /readyz` with DB/Redis/worker status for monitoring |
| **LLM proxy endpoint** | 2 days | `POST /api/v1/llm-proxy` on your SaaS — accepts grading requests from self-hosted instances, forwards to Groq/OpenRouter/Cerebras, meters usage |
| **Student cap via license** | 1 day | Replace Razorpay-subscription lookup with license-key-based `max_students` enforcement in `sessions.py` |

### Phase 2 — Distribution (~4 days)

| Item | Effort | Details |
|------|--------|---------|
| **`setup.sh` wizard** | 2 days | Checks Docker/prerequisites, generates secure secrets, prompts for domain/email/license, creates `.env`, starts stack |
| **`update.sh` script** | 1 day | Pulls new images, runs migrations, healthcheck wait, rollback on failure |
| **Backup defaults** | 0.5 day | Generic pg_dump to local disk + optional S3-compatible target; documented restore |
| **Packaging** | 0.5 day | `selfhost-v1.zip` containing `docker-compose.yml`, `.env.template`, `setup.sh`, `update.sh`, `docs/` |

### Phase 3 — Enterprise Features (~Month 2)

| Item | Effort | Details |
|------|--------|---------|
| **Offline license validation** | 2 days | License file (`.lic`) for air-gapped networks; verification via embedded public key; grace period |
| **Local Ollama support** | 2 days | Self-hosted instances can point to local Ollama instead of proxy — critical for strict data-residency requirements |
| **SSO sidecar docs** | 1 day | Document deploying Authentik/Keycloak alongside Procta for LDAP/SAML/OIDC |
| **Helm chart** | 3 days | Kubernetes deployment for universities that don't use Docker Compose |
| **MSI/DMG with config** | 2 days | Electron client build with pre-configured server URL; config file for MDM deployment (SCCM, JAMF) |
| **Update notification** | 1 day | Admin dashboard shows "Update available" badge with changelog |

### Phase 4 — Operations (Month 3+)

- First beta customer onboarding
- Support processes and SLA documentation
- Pricing refinement based on feedback
- Usage analytics dashboard for monitoring self-hosted instances

---

## 3. University-Specific Considerations

### Data Sovereignty
- **#1 concern**: student biometric data (face mesh, behavioral metrics) must stay on-premises
- License validation must work offline (periodic check with grace period)
- LLM proxy sends exam answers to Procta's server — this is a potential dealbreaker
- **Recommendation**: dual-path AI grading — proxy via Procta by default, local Ollama for strict data-residency requirements

### Authentication (SSO)
- Universities use LDAP, Active Directory, SAML, OIDC (Azure AD, Google Workspace)
- Building SSO natively is weeks of work
- **Recommendation**: deploy Authentik or Keycloak as a sidecar container; Procta authenticates against it via OIDC

### Scale & Reliability
- 500–5000 concurrent students
- Current stack proven at 3000 VU (46ms p95 submit) — sufficient for most
- Single-node failure = all exams stop. True HA needs multi-node + Postgres replication
- **Recommendation**: start single-node with documented restore; HA as paid add-on

### Deployment Flexibility
- Some want Docker, some want raw binaries, some want OVA/VM image, some want Kubernetes
- **Recommendation**: Docker Compose first (vast majority), Helm chart later

### Support Expectations
- Business-hours email/phone support
- Documented SLAs (e.g., 8h response for critical issues)
- IT team training
- This is primarily a staffing/business question

---

## 4. LLM Proxy Model

```
[University Self-Hosted Instance]
        → POST /api/v1/llm-proxy (with license key in header)
        → [api.procta.net/llm-proxy]
            → Groq / OpenRouter / Cerebras
        ← response
        ← meter updated
```

- **Auth**: License key embedded in request header; validated by proxy
- **Metering**: Track requests per license; X requests/mo included, then per-request billing
- **Privacy**: Exam content passes through proxy — document in DPA; offer local Ollama opt-out
- **Fallback**: If proxy unreachable, grading falls back to non-LLM scoring (keyword-based)

---

## 5. Licensing Model

### License Key (signed JWT)

```json
{
  "org": "University of Mumbai",
  "max_students": 2000,
  "features": ["llm_grading", "sso", "analytics"],
  "exp": 1735689600,
  "iss": "procta.net",
  "type": "selfhosted"
}
```

### Validation Modes

1. **Online** (default): validate against `license.procta.net` on startup + every 24h
2. **Offline**: key verified locally via embedded public key; 30-day grace period on expiry
3. **Air-gapped**: license file (`.lic`) delivered manually; renewal via file replacement

### Open-Source Considerations

Several models are possible for how the self-hosted code is distributed:

| Model | Description | Pros | Cons |
|-------|-------------|------|------|
| **Fully proprietary** | Binary-only distribution; code never shared | Full control, easiest enforcement | No community contributions; trust barrier for universities; harder to sell |
| **Open-core** | Basic version is OSS (e.g., AGPL); enterprise features (SSO, LLM proxy, multi-node, analytics) are proprietary | Community contributions, easier adoption, trust from universities | More governance overhead; need to maintain two versions |
| **Source-available** (BSL / BUSL) | Code visible to read and audit; commercial use requires paid license | Code transparency + monetization; simpler than dual-license | Less community contribution; less familiar to enterprises |
| **AGPL + Commercial** | AGPL for open-source version (anyone can use, modifications must be shared); commercial license for proprietary use | Strong copyleft protects your work; standard model (GitLab, Mattermost, Sentry) | AGPL can scare some enterprises; must offer commercial terms |
| **MIT/APACHE + Paid** | Fully open-source core; sell support, managed hosting, and enterprise features | Maximum adoption; no licensing friction | Very hard to monetize self-hosted; your SaaS would compete with free self-hosted |

**Strongest recommendation**: AGPL + Commercial license (the GitLab/Sentry/Mattermost model). This:
- Gives universities full code visibility (critical for security reviews)
- Protects your work from being repackaged by competitors
- Allows selling commercial licenses to enterprises that can't use AGPL
- Builds an open-source community that can contribute fixes
- Is a well-understood model in enterprise procurement

### Pricing Principles

- Self-hosted must cost **significantly more** than SaaS to avoid cannibalization
- SaaS: ₹2,400–30,000/month depending on tier
- Self-hosted: ₹1–5 Lakh/year depending on student count + support level
- Included LLM requests: 10k/mo (Pro), 50k/mo (Enterprise)
- Overages: ₹1 per 100 additional grading requests

---

## 6. Operational Process

### Sale → Running

1. **Sale**: Contract signed, annual fee paid. License key generated and sent.
2. **Handoff**: University receives `selfhost-v1.zip` + minimum VM spec (4 vCPU, 16 GB RAM, 80 GB SSD, Docker)
3. **Setup**: `bash setup.sh` → wizard prompts → stack starts in ~5 minutes
4. **Operation**: IT monitors `/health` endpoint; runs `bash update.sh` for upgrades
5. **Telemetry**: Anonymous usage stats (opt-out), Sentry errors (opt-in), periodic license check pings
6. **Renewal**: Email notification before expiry; new key on payment; 30-day grace period

### Managed Tier (optional upsell)

- You SSH into their VM and deploy
- You handle updates, monitoring, and incidents
- Premium: ₹X/month on top of license

---

## 7. Electron Client Distribution

Options for university deployment:

| Method | Effort | Best For |
|--------|--------|----------|
| Pre-built MSI/DMG with baked URL | Build once per university | MDM deployment (SCCM, JAMF) |
| Server URL config file | Add config file support (JSON/TOML) | Manual install, flexibility |
| QR code from admin dashboard | Add QR generator; app scans it | Small deployments |
| First-launch URL dialog | Simplest, no build changes | Evaluation, small scale |

**Recommendation**: config file + pre-built MSI for enterprise. The Electron app already reads `PROCTOR_SERVER_URL` from env; just need to also read from a `.procta-config.json` file next to the binary.

---

## 8. Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| **Support burden** — "Doesn't work on their VMware" | Clear minimum requirements, tested platforms documented |
| **Version fragmentation** — different versions in the wild | Docker image tags (semver), mandatory security update window |
| **Piracy** — license key shared or leaked | Key bound to institution name; offline validation + periodic check; legal terms |
| **Data leak via LLM proxy** — exam content sent to your server | Document in DPA; offer local Ollama; use HTTPS; consider data-processing agreement |
| **Bus factor** — you're solo | Document everything; automate setup/update; open-core for community contributions |
| **SaaS cannibalization** — why pay monthly if they can self-host? | Self-hosted priced 3–5× higher than equivalent SaaS tier |
| **Competing with your own SaaS** — self-hosted lags in features | Core exam features are identical; SaaS gets new features first by 1 release |

---

## 9. Architecture Diagram (Self-Hosted)

```
┌─────────────────────────────────────────────────────┐
│              University Data Center                  │
│                                                      │
│  ┌──────────────┐   ┌──────────────────────────┐    │
│  │  Student PCs  │   │   Procta Server (Docker)  │    │
│  │  (Electron +  │──▶│                          │    │
│  │   AI Proctor) │   │  Caddy → FastAPI          │    │
│  └──────────────┘   │  Postgres + pgbouncer     │    │
│                     │  Redis                     │    │
│                     │  RQ Workers (scoring)      │    │
│                     │  RQ Workers (autosave)     │    │
│                     └──────────┬───────────────┘    │
│                                │                     │
│                     ┌──────────▼───────────────┐    │
│                     │  Optional: Ollama (local  │    │
│                     │  AI grading, no internet) │    │
│                     └──────────┬───────────────┘    │
│                                │                     │
│                     ┌──────────▼───────────────┐    │
│                     │  Optional: Authentik/     │    │
│                     │  Keycloak (SSO proxy)     │    │
│                     └──────────────────────────┘    │
│                                                      │
│  ┌──────────────────────────────────────────────┐   │
│  │  Internet Boundary                            │   │
│  │  ┌─────────────┐   ┌────────────────────┐     │   │
│  │  │ License      │   │ LLM Proxy          │     │   │
│  │  │ Validation   │──▶│ (grading via your  │     │   │
│  │  │ (periodic)   │   │  API keys)         │     │   │
│  │  └─────────────┘   └────────────────────┘     │   │
│  │  Optional: Usage telemetry (anon), Sentry      │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

---

## 10. Recommended Phasing Summary

```
Week 1-2:  Phase 1 — Self-hostable codebase (license, SMTP, CAPTCHA, LLM proxy, etc.)
Week 3:    Phase 2 — Distribution (setup.sh, update.sh, packaging, docs)
Month 2:   Phase 3 — Enterprise features (offline, Ollama, SSO docs, Helm chart, MSI)
Month 3+:  Phase 4 — Operations (beta customer, support, pricing refinement)
```

---

## 11. Open Questions

1. **Open-source model**: Open-core (AGPL + commercial) vs source-available vs fully proprietary? AGPL + commercial is the strongest recommendation — it builds trust with universities, protects against competitors, and enables community contributions.

2. **Air-gapped support**: Should day-one support fully disconnected networks? This adds offline license files, local Ollama as default (not fallback), and no telemetry. Adds ~3 days to Phase 1 but is critical for defense/government universities that have strict no-internet policies.

3. **Electron client strategy**: Single universal client that prompts for URL at first launch, or per-university branded builds? Universal is simpler to maintain; branded builds look more professional in enterprise procurement. A pragmatic middle ground: single build that reads an optional config file, so IT can pre-configure via MDM.

4. **Trial/demo for self-hosted**: Should prospects be able to try self-hosted before buying? Options:
   - Time-limited trial (14-day, no license key required)
   - Feature-limited demo (max 10 students, no LLM grading)
   - Full-featured with watermark ("Unolicensed Procta Instance")
   - Recommend: 14-day full-featured trial, enforced by a trial license generated on the website

5. **Update cadence**: Same release train for SaaS and self-hosted, or self-hosted lags? Options:
   - **Same train** (both get v1.2.0 same day): simpler for you, but risky if a bug slips through
   - **Self-hosted lags by 1 release**: gives you a buffer to catch regressions, but support complexity
   - **Security patches fast, features slow**: security fixes released simultaneously, feature releases quarterly for self-hosted
   - Recommend: security + critical fixes same-day; feature releases lag by 2 weeks

6. **Backup & DR expectations**: Universities will ask "what happens if the server dies mid-exam?" Currently the app handles in-progress exams via autosave, but a full server failure means rescheduling. Document the recovery story clearly.

7. **White-label / branding**: Universities may want to remove "Procta" branding from the dashboard, student UI, and Electron client. This is a common enterprise requirement and could be a paid add-on feature.

---

## 12. Summary of Code Changes Required

| File | Change |
|------|--------|
| `app/main.py` | Add license validation middleware; add health endpoint; detect self-hosted mode |
| `app/constants.py` | Add `SELF_HOSTED_MODE` flag, `LICENSE_CHECK_INTERVAL`, rename `SUPABASE_JWT_SECRET` |
| `app/services/license.py` | **New** — License key generation + validation + caching |
| `app/routers/license.py` | **New** — License validation endpoint (for license server) |
| `app/routers/health.py` | **New** — Detailed health check |
| `app/services/email.py` | Abstract into provider pattern (Resend + SMTP) |
| `app/services/billing.py` | Make no-op when `SELF_HOSTED_MODE=true` |
| `app/services/sessions.py` | Read `max_students` from license instead of subscription |
| `app/routers/auth.py` | Self-hosted signup flow (no Razorpay subscription) |
| `app/routers/billing.py` | Return license info instead of subscription info |
| `app/routers/admin_org.py` | Switch subscription display to license info |
| `app/database.py` | Remove last Supabase client references |
| `config.js` | Use env var or config file, not hardcoded URL |
| `lobby_preload.js` | Same |
| `dashboard-ui/src/config.js` | Same |
| `website/src/config.js` | Same |
| `worker.py` | Same |
| `docker-compose.yml` | Create `docker-compose.selfhost.yml` variant |
| `scripts/setup.sh` | **New** — Setup wizard |
| `scripts/update.sh` | **New** — Update automation |
| `app/routers/llm_proxy.py` | **New** — LLM proxy endpoint |
| `app/services/llm_metering.py` | **New** — Usage metering |
| `entrypoint.sh` | Add license validation on startup |
