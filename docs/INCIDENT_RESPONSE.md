# Incident Response & Data-Breach Runbook

**Owner:** Platform/superadmin team · **Last reviewed:** 2026-06-12
**Companion docs:** [OBSERVABILITY.md](OBSERVABILITY.md) (triage cheatsheet), `app/static/dpa.html` (legal commitments), `app/static/trust-center.html` (published security posture).

This runbook is the single source of truth for handling production incidents — from a degraded API to a confirmed personal-data breach. The **Trust Center publishes a summary of this process**; if you change the severity tiers, RTO, or notification timelines here, update `trust-center.html` to match (and vice versa). They must never disagree.

> ⚠️ **Keep this honest.** The Trust Center must only claim controls that actually exist. Current posture: **Sentry** is the detection/alerting tool (activate via `SENTRY_DSN` in prod); **PagerDuty is intentionally not used** (Sentry alerting suffices at this team size); database protection is **daily `pg_dump` backups**, not PITR. Don't promise tooling you haven't wired. See "Tooling reality check" (§9).

---

## 1. Severity classification

Use the same tiers the Trust Center publishes.

| Severity | Definition | Examples | Target first response |
|----------|-----------|----------|----------------------|
| **Critical (SEV1)** | Confirmed/suspected **personal-data breach**, or full service outage | DB compromise, exposed PII, auth bypass, prod down | **Acknowledge ≤ 15 min**, 30-min RTO target |
| **High (SEV2)** | Major degradation or security event without data exposure | Auth failures, partial outage, Cloudflare 525, runaway error rate | ≤ 30 min |
| **Medium (SEV3)** | Non-critical functional bug affecting some users | A broken endpoint, a stuck queue | ≤ 1 business day |
| **Low (SEV4)** | Cosmetic / no user impact | UI glitch, log noise | Backlog |

**If you are unsure whether something is a personal-data breach, treat it as SEV1 until proven otherwise.** The 72-hour regulatory clock starts when you become *aware*, not when you finish investigating.

---

## 2. Roles (small-team model)

- **Incident Commander (IC)** — the on-call operator. Owns the timeline, decisions, and comms. For a solo/small team this is whoever first acknowledges.
- **Scribe** — keeps a timestamped log (can be the IC). Every action + finding gets a line; this log becomes the post-mortem and, for a breach, the Art 33(5) record.
- **DPO / legal contact** — looped in immediately for any suspected personal-data breach (see §5). For Procta this is the founder/operator until a DPO is appointed.

Contact tree (fill in real values, keep out of git if sensitive):

| Role | Primary | Backup |
|------|---------|--------|
| Incident Commander | _on-call_ | — |
| Infra / DB | _operator_ | — |
| Legal / DPO | _operator_ | _counsel_ |
| Razorpay / billing | _operator_ | — |

---

## 3. Incident lifecycle

```
Detect → Triage & classify → Contain → Eradicate → Recover → Document → Post-mortem
```

1. **Detect.** Sources: Cloudflare/uptime alerts, `[AI]`/`proctor.api` error logs, Sentry (if `SENTRY_DSN` set), a customer report, the `client_throttled`/fleet-health signals (see OBSERVABILITY.md).
2. **Triage & classify.** Assign a severity (§1). Open the timeline log. For SEV1, page the DPO/legal contact in parallel — do not wait.
3. **Contain.** Stop the bleeding before you fix root cause (§6 playbooks). Containment ≠ fix.
4. **Eradicate.** Remove the cause (revoke leaked creds, patch the bug, rotate keys).
5. **Recover.** Restore normal service; verify health (§6). Target **30-min RTO** for SEV1.
6. **Document.** Finalize the timeline. For a breach, complete the **Art 33(5) record** (§5).
7. **Post-mortem.** Blameless RCA within 5 business days (§7). Add a regression test/guard so it can't recur — that's the bar that closed real bugs this quarter.

---

## 4. The 72-hour clock (start here for any data exposure)

```
T+0h   Become AWARE of a possible personal-data breach  → clock starts, page DPO
T+0–24h  Contain + assess: what data, whose, how many, what risk
T+≤undue-delay  PROCESSOR notice: tell affected ORG controllers (DPA obligation)
T+≤72h  CONTROLLER notice: where Procta is controller, notify the regulator
        (DPDP Data Protection Board / relevant EU DPA) — or document why not reportable
T+without-undue-delay  Notify affected DATA SUBJECTS if high risk to them
Post   Art 33(5) record complete; post-mortem; Trust Center/DPA reviewed
```

---

## 5. Personal-data breach sub-process (compliance-critical)

A **personal-data breach** = accidental/unlawful destruction, loss, alteration, unauthorized disclosure of, or access to personal data. Examples: DB dump exfiltrated, screenshot directory exposed, a token leak letting one user read another's data (IDOR), a misdirected bulk email.

### 5a. Know your role for the affected data

Procta wears **two hats** — this determines *who you notify*:

| Data category | Procta's role | Who Procta must notify |
|---------------|---------------|------------------------|
| Student exam data (rosters, sessions, violations, screenshots, phone frames) | **Processor**, on behalf of the org/school | The **affected org(s)** — the controllers — *without undue delay* (DPA §3). The org then notifies its regulator + students. |
| Teacher/admin accounts, org/billing data | **Controller** (Procta's own customer data) | The **affected users directly** + the **regulator within 72h** if reportable; data subjects if high risk. |

When in doubt about a mixed breach, do **both** notifications.

### 5b. Assessment checklist (do before notifying)

- [ ] What data categories are involved? (PII, biometric/proctoring imagery = high sensitivity)
- [ ] Whose data — which orgs, which users, how many?
- [ ] Confidentiality / integrity / availability — which was breached?
- [ ] Likelihood + severity of risk to individuals? (biometric/exam imagery → assume high)
- [ ] Is it reportable to a regulator (controller role) or to the controller (processor role)?
- [ ] Is it contained, or ongoing?

### 5c. Notify

- **Processor → controller:** email each affected org's **billing/admin contact** (use `organizations.billing_email`, falling back to the first org admin — see Gap #14). Use the **Controller Breach Notice** template (§8). This is the DPA "without undue delay" commitment.
- **Controller → regulator:** prepare the DPDP Data Protection Board / EU DPA notice (what happened, categories, approx. numbers, likely consequences, measures taken). This step is **manual/legal** — the system stores the record + template, a human files it.
- **Controller → data subjects:** if high risk, bulk-notify affected users via the **Data-Subject Breach Notice** template.

### 5d. Record (Art 33(5) — mandatory even if not reportable)

Every breach is logged in the `breach_incidents` table regardless of whether it's reportable: facts, effects, remedial action, and the notification decisions + timestamps. This record is what an auditor/regulator asks for.

---

## 6. Containment & recovery playbooks (this stack)

Production = Contabo `srv1675832`, `/root/proctor-browser`, docker compose (api/worker/postgres/redis/pgbouncer/caddy), self-hosted Postgres behind PgBouncer, Caddy → Cloudflare → `app.procta.net`, deploy via self-hosted GitHub runner.

- **Roll back a bad deploy:** the previous image is retained; re-run the prior green deploy, or on the box `docker compose up -d --force-recreate api`. Deploys are now **serialized** (deploy.yml concurrency) so you won't stampede the origin — that was the root cause of the 525 on 2026-06-12.
- **Cloudflare 525 / origin down:** check Caddy + the api container are up (`docker compose ps`); a deploy mid-restart can flap TLS briefly — confirm it's not just a settling window before declaring an incident.
- **Suspected token/session compromise:** use the superadmin **revoke-all-sessions** endpoint (Gap #57) to evict every session for a user/org; rotate JWT signing keys per `docs/SECRETS.md` (zero-downtime rotation playbook).
- **Leaked secret:** rotate immediately (`docs/SECRETS.md`), then assess what it could access → feeds the breach assessment in §5b.
- **Data exfil suspicion:** preserve evidence first (don't wipe logs), snapshot the DB, then contain.
- **DB recovery:** restore from the self-hosted Postgres backup/PITR (NOT Supabase — that's legacy). Verify migrations applied, run smoke test.
- **Health verification after recovery:** `GET /dashboard` → 200, `GET /api/v1/admin/status` → 401 (auth required = up). Watch error logs for 5 min before closing.

---

## 7. Post-mortem template

```
## Incident <date> — <one-line title>  (SEVx)
**Timeline:** (UTC) detect → ack → contain → recover → close
**Impact:** who/what, duration, data involved
**Root cause:** the actual cause, not the symptom
**Detection:** how we found it; how long until aware
**What went well / what didn't:**
**Action items:** (owner, due) — at least one must be a test/guard so it can't recur
**Breach?** yes/no → if yes, link the breach_incidents record + notification timestamps
```

Blameless. The goal is a systemic fix (a CI guard, a test, a serialized pipeline), not blame.

---

## 8. Notification templates

**Controller Breach Notice (processor → org):**
> Subject: Security notice regarding your Procta organization
> We are writing to inform you, without undue delay, of a personal-data incident affecting data we process on your behalf. **What happened:** … **Data involved:** … **Individuals affected (approx.):** … **When we became aware:** … **Measures taken/underway:** … As the data controller, you may have notification obligations to your supervisory authority and affected individuals. We are available to assist. Contact: …

**Data-Subject Breach Notice (controller → user, high-risk):**
> Subject: Important security notice about your Procta account
> We're contacting you about a security incident that may have affected your data. **What happened (plain language):** … **What data:** … **What we've done:** … **What you should do:** (e.g., reset password, watch for phishing) … We take this seriously and apologize. Questions: …

---

## 9. Tooling reality check (keep the Trust Center honest)

The Trust Center's Incident Response section must reflect what's actually wired. Status (resolved 2026-06-12):

| Trust Center claim | Reality | Status |
|--------------------|---------|--------|
| Sentry detection (error rate, auth anomalies) | Sentry initializes only if `SENTRY_DSN` is set | **Activating** — set `SENTRY_DSN` in prod `.env`, then configure the alert rules (error-rate, auth-anomaly) in the Sentry UI so the published claim is true |
| On-call via PagerDuty | Not integrated; intentionally **not** used (Sentry alerting → email/Slack is sufficient at this team size) | **Resolved** — removed from the Trust Center |
| Database backups | **Daily `pg_dump`** (ofelia cron, 05:30 IST, 14-day retention, host-mounted `/backups`); **not** PITR | **Resolved** — Trust Center corrected from "Supabase PITR" to "daily Postgres backups" |

Keep claims and reality in lockstep: any future tooling change updates both this table and `trust-center.html`.
