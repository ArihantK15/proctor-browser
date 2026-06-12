# Data Protection Impact Assessment (DPIA) — Procta AI Proctoring

**Status:** v1 · **Date:** 2026-06-12 · **Owner:** Founder/operator (acting DPO)
**Companion docs:** [PRIVACY.md](PRIVACY.md) (retention matrix + SAR flow), [INCIDENT_RESPONSE.md](INCIDENT_RESPONSE.md) (breach runbook), `app/static/dpa.html` (processor commitments), `app/static/trust-center.html` (published posture).

This DPIA assesses the high-risk processing inherent in AI-assisted exam
proctoring. It is a living document: re-run it whenever the processing
materially changes (see §9 — DPIA review workflow). If the retention
windows, controls, or sub-processors here drift from PRIVACY.md /
trust-center.html, those documents — not this one — are authoritative for
the live figures; reconcile them and update this assessment.

---

## 1. Why a DPIA is required (Art 35 / DPDP)

GDPR Art 35(3) mandates a DPIA where processing is "likely to result in a
high risk." Procta's processing triggers **multiple** of the EDPB's nine
high-risk criteria simultaneously:

| Criterion | How Procta hits it |
|-----------|--------------------|
| Systematic monitoring | Continuous proctoring during an exam — webcam, screen, audio, optional phone camera |
| Special-category / biometric data | Facial images used for identity verification (Art 9 biometric where used to uniquely identify) |
| Data on vulnerable subjects | Students, **potentially minors**, in a power-asymmetric exam context |
| Automated decision-making | AI risk-scoring + AI-assisted grading influence assessment outcomes |
| Innovative technology | ML object/face detection, LLM grading |
| Large scale | Multi-institution SaaS processing many students per exam window |

Two or more criteria → DPIA mandatory. This document satisfies that
obligation and the DPDP Act's equivalent expectation for a significant
data fiduciary processing children's / sensitive data.

---

## 2. Description of the processing (Art 35(7)(a))

**Nature.** A locked-down Electron student client captures webcam frames,
screen state, and audio during an exam; a Python proctoring daemon runs
on-device detection (object/person/phone via YOLO-COCO, face detection
for identity verification, audio keyword spotting). Signals are sent to a
FastAPI backend, which stores violation events, periodic screenshots, and
computes a per-session **risk score**. Teachers review flagged sessions on
a web dashboard; an LLM (Groq / configurable) produces **AI-assisted
grading suggestions** that a teacher can override.

**Scope of data.** See PRIVACY.md for the authoritative table. In summary:

| Category | Sensitivity | Where | Retention (authoritative: PRIVACY.md) |
|----------|-------------|-------|----------------------------------------|
| Webcam / facial images (identity verification) | **Biometric (Art 9)** | Client → screenshots dir | Screenshots **30 days**, then deleted (hourly cleanup thread) |
| Screen / exam screenshots | Personal | `screenshots/` filesystem | **30 days** |
| Phone-camera frames | Personal | Redis (transient) | **24h** (Redis TTL) |
| Audio keyword hits | Personal | Violation events | Violation logs **1 year** |
| Violations / answers / scores | Personal (assessment) | Postgres | 1 year / duration of account, anonymised on delete |
| Risk score + AI grading suggestions | Personal (profiling) | Postgres | Duration of account |
| Teacher/admin + billing | Personal | Postgres | Billing **7 years** (tax); profile anonymised on delete |

**Purposes.** Deterring and detecting exam misconduct; producing a
defensible evidence trail for academic-integrity decisions; assisting
grading. **No training on customer data**; LLM prompts are zero-shot.

**Roles.** Procta is a **processor** for student exam data (acting on the
school's instruction) and a **controller** for its own teacher/billing
data. This split governs breach + objection routing (see INCIDENT_RESPONSE.md
§5 and the right-to-object flow).

---

## 3. Necessity & proportionality (Art 35(7)(b))

- **Lawful basis.** Student processing rests on the **school's** basis
  (typically public task / legitimate interest in exam integrity, or
  consent), with Procta as processor under a DPA. Biometric identity
  verification relies on **explicit consent**, captured and provable via
  `consent_records` (retained as proof under DPDP §7(2)). Phone-camera use
  requires a separate recorded consent before the stream starts.
- **Data minimisation.** On-device detection means raw video is analysed
  locally; only violation events + periodic screenshots leave the device.
  Phone frames are transient (Redis TTL, never persisted to disk).
- **Proportionality controls.** Per-exam proctoring sensitivity presets;
  proctoring features (phone camera, audio keywords) are opt-in per exam;
  AI grading is **advisory with mandatory human override** (teacher audit
  trail on every scorecard) — so no Art 22 "solely automated" decision is
  made about a student.
- **Storage limitation.** Retention windows are enforced in code (screenshot
  cleanup thread; TTL sweeper for transient auth rows; 7-year billing purge
  — phase104). The 30-day screenshot window balances dispute-resolution
  utility against minimisation.

---

## 4. Consultation (Art 35(9))

- **Data subjects / schools:** consent flows + the Privacy Center (export,
  delete, consent-withdraw, **right-to-object**) give students and teachers
  a direct channel; objections from students route to their school
  (controller) for decision.
- **DPO:** founder/operator acts as DPO until appointed; reviews this DPIA
  and signs off (§8).

---

## 5. Risk assessment (Art 35(7)(c))

Likelihood (L) × Severity (S), each Low/Med/High → residual risk after
controls. Controls reference existing, shipped mechanisms.

| # | Risk to individuals | L | S | Mitigations (shipped) | Residual |
|---|---------------------|---|---|------------------------|----------|
| R1 | Unauthorised access to biometric/proctoring imagery (breach) | Med | High | TLS 1.3; bcrypt; RLS on 26 tables; superadmin-gated media; 30-day screenshot expiry shrinks the exposure window; breach runbook + `breach_incidents` | Med |
| R2 | One user reading another's data (IDOR) | Low | High | Tenant scoping (`apply_teacher_scope`) + CI guard `check_tenant_scoping`; Privacy Center actions scoped to `_resolve_caller` (no body-supplied IDs) | Low |
| R3 | Over-retention of personal data | Low | Med | Code-enforced windows (screenshots 30d, billing 7y purge, TTL sweeper); retention matrix published | Low |
| R4 | Unfair automated decision against a student | Low | High | AI grading + risk score are **advisory**; mandatory teacher override + audit trail; appeals flow | Low |
| R5 | Processing a minor without valid consent | Med | High | Explicit consent capture (`consent_records`); school-as-controller obtains/verifies guardian consent per its basis; documented in DPA | Med |
| R6 | False-positive proctoring flag harming a student | Med | Med | Human review of every flag; sensitivity presets; evidence retained 30d for dispute; appeals | Low |
| R7 | Excessive surveillance (webcam/screen/audio) beyond purpose | Med | Med | Per-exam opt-in features; on-device analysis; phone frames transient; minimised server payload | Low |
| R8 | Sub-processor exposure (LLM, hosting) | Low | Med | Zero-shot prompts, no training; sub-processor list published; data-residency choices documented | Low |
| R9 | Loss of availability (evidence lost mid-dispute) | Low | Med | Daily pg_dump backups (14-day retention); 30-day screenshot window | Low |

**Highest residual risks: R1 and R5 (Med).** Both are inherent to the
product category and held at Med — not Low — by design; they warrant the
ongoing controls below rather than further build.

---

## 6. Measures to address risk (Art 35(7)(d))

Already shipped: encryption in transit, RLS + tenant-scoping CI guards,
superadmin-gated media/breach tooling, consent capture + proof retention,
code-enforced retention (screenshots/billing/TTL), human-in-the-loop
grading + appeals, breach runbook + Art 33(5) record, right-to-object
routing, daily backups.

**Recommended follow-ups (tracked, not blocking):**
- R1: activate object-storage (B2) with lifecycle expiry + at-rest
  encryption for screenshots, so retention isn't filesystem-only.
- R5: surface an explicit minor/guardian-consent attestation in the
  enrolment flow rather than relying solely on the school's basis.
- R1/R9: confirm disk headroom now that screenshots are retained 30×
  longer than before (see the retention-alignment change).

---

## 7. Residual risk acceptance

After controls, the highest residual risk is **Medium (R1, R5)**,
proportionate to the purpose (defensible exam integrity) and not requiring
prior consultation with a supervisory authority under Art 36 (no *high*
residual risk remains). Accepted, subject to the §6 follow-ups and §9
review cadence.

---

## 8. Sign-off

| Role | Name | Date | Decision |
|------|------|------|----------|
| DPO / operator | _operator_ | 2026-06-12 | Accept with follow-ups |
| Engineering | _operator_ | 2026-06-12 | Controls verified in code |

---

## 9. DPIA review workflow (Gap #66)

A DPIA is not one-and-done. **Re-run this assessment when any of the
following occurs**, before the change ships:

- A new category of personal/biometric data is processed (e.g. voiceprint
  identification, gaze tracking, emotion inference).
- Automated decisioning becomes **solely** automated (removes human
  override) — would trigger Art 22.
- A new sub-processor receives personal data, or data residency changes.
- Retention windows lengthen materially, or a new persistence layer is
  added (e.g. B2 object storage).
- Processing extends to a new vulnerable group or jurisdiction.

**Process:** open a DPIA review (copy the template below), reassess §5's
risk register, get DPO sign-off (§8), and reconcile PRIVACY.md +
trust-center.html. Annual review even absent changes.

### Template (copy for each review)

```
## DPIA review — <feature / change> — <date>
Trigger: <which §9 condition>
Processing delta: <what data/flow/decisioning changes>
New/changed risks: <add rows to the §5 register: L × S → residual>
New controls required: <list; each must ship before the change>
Residual risk: <Low/Med/High> — Art 36 prior consultation needed? <y/n>
DPO sign-off: <name, date>
Docs reconciled: PRIVACY.md [ ] trust-center.html [ ] DPA [ ]
```
