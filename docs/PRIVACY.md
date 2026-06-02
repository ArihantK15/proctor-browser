# Procta Privacy & Data Subject Rights

How Procta complies with India's **Digital Personal Data Protection
Act 2023** (DPDP Act) and the **EU General Data Protection
Regulation** (GDPR) for users in either jurisdiction.

This doc is both **user-facing reference** (what's collected, how to
exercise your rights) and **ops runbook** (how to handle a Subject
Access Request, what to retain after deletion, legal basis).

## Data we hold, by role

### Teacher (admin) account

| Category | Tables | Purpose | Retention after delete |
|---|---|---|---|
| Profile (name, email, org_role) | `teachers` | Authentication, dashboard ownership | Anonymised; row retained as actor link on audit trails |
| Organisation membership | `teachers.org_id` → `organizations` | Billing, multi-teacher scoping | Anonymised |
| Exams + questions | `exam_config`, `questions` | Operating exams | Anonymised (title blanked, access codes blanked); rows kept so analytics for other org admins still work |
| Roster (students enrolled under teacher) | `students`, `student_groups`, `student_group_members` | Roster management | Anonymised |
| Authored sessions (exam runs they conducted) | `exam_sessions`, `violations`, `answers` | Forensic + analytics for the org | Anonymised |
| OAuth tokens (Google Classroom) | `google_auth_tokens` | Classroom sync | **Hard-deleted** — sensitive, never retained |
| API keys | `api_keys` | Programmatic access | Deactivated (`is_active=false`); row retained for audit |
| Audit trail (auth + admin actions) | `auth_events`, `admin_audit_log` | Security forensics, compliance | **Retained**; teacher_id set to NULL via FK ON DELETE SET NULL (so the audit row survives but no longer points at a person) |
| Email OTPs (active) | `email_otps` | 2FA | Hard-deleted |
| Active sessions + refresh tokens | `auth_sessions`, `refresh_tokens` | Auth | Revoked on delete (immediate) |
| Subscriptions + payments | `subscriptions`, `usage_records` | Billing | **Retained 7 years** for Indian tax/audit compliance (Income-tax Act §44AA) |

### Student account

| Category | Tables | Purpose | Retention after delete |
|---|---|---|---|
| Profile (name, email) | `student_accounts` | Authentication, enrolment matching | Anonymised |
| Enrolments | `students` (rows where `account_id = me`) | Linking the student account to one or more teachers' rosters | Hard-deleted from rows where the student is the only subject |
| Exam sessions | `exam_sessions` | Proctoring evidence for the teacher who ran the exam | Anonymised — name → "Deleted User", email blanked, roll → anon_roll |
| Violations + answers | `violations`, `answers` | Per-session evidence | Anonymised (referenced by anonymised session) |
| Appeals filed | `appeals` | Dispute handling | Anonymised (student_id → anon_id) |
| Forensic frames (screenshots) | `screenshots/` filesystem dir | Manual review by the teacher in cheating disputes | Retained 30 days (rotated by Ofelia) — covered separately, not in this DB-level erasure flow |
| Phone-camera frames | Redis (transient) | Real-time room monitoring | Auto-expire (Redis TTL) |
| Consent records | `consent_records` | Proof we obtained consent | **Retained** — required as proof under DPDP §7(2) |
| Audit trail | `auth_events` | Security forensics | Retained; user_id set to NULL |
| Active sessions + refresh tokens | `auth_sessions`, `refresh_tokens` | Auth | Revoked immediately |

## Endpoints

All three are at `/api/v1/privacy/...` (see `app/routers/privacy.py`).

### `POST /api/v1/privacy/consent`

Records explicit consent for a specific processing purpose. Body:

```json
{ "consent_type": "signup_terms" }
```

Allowed `consent_type` values are enforced by the
`consent_records_consent_type_check` constraint (phase85):
`signup_terms`, `privacy_policy`, `phone_camera`.

### `GET /api/v1/privacy/export`

Returns a JSON dump of everything we have linked to the authenticated
user. Rate-limited to 5 requests/hour per user (an export is heavy;
abuse looks like data scraping).

Format: top-level object with `user_type`, `user_id`, `exported_at`
(ISO-8601 UTC), `format_version`, then one key per category of data.
Each category is an array of rows.

The act of exporting is itself recorded via `auth_events` so we have
a regulator-friendly audit trail.

### `POST /api/v1/privacy/delete`

Erases or anonymises the authenticated user per the retention matrix
above. Requires a fresh **reauth token** (see admin_auth) in addition
to the access token — pass via `X-Reauth-Token` header or `reauth_token`
body field.

Rate-limited to 2 requests/hour per user.

What happens, in order:

1. **Sessions + refresh tokens revoked.** Even if the rest fails, the
   user can no longer authenticate. (Done before any erasure work.)
2. **Identifiers anonymised** (teacher) or thorough delete (student
   — delegates to `auth.py:_track_a_hybrid_delete_student_account` which notifies
   the issuing teacher and handles supabase if applicable).
3. **OAuth tokens hard-deleted** (`google_auth_tokens`).
4. **API keys deactivated** (`api_keys.is_active = false`).
5. **Ephemeral data deleted** (`email_otps`).
6. **Erasure logged to auth_events** so we can show a regulator we
   honoured the request, with timestamps and any errors that occurred.

The endpoint always returns `{"status": "deleted" | "partial",
"errors": [...]}` — `partial` means some sub-step had a transient
failure; the request is considered honoured but flagged for ops
review.

## Internal SAR procedure (ops)

A Subject Access Request (SAR) can arrive via three channels:
direct email to support, the `/api/v1/privacy/export` endpoint
self-served by the user, or via the grievance officer's published
contact.

### When a user self-serves

`/api/v1/privacy/export` covers DPDP §11(1)(a-c). No ops action
needed unless the user reports the export was incomplete — in which
case start at the **manual SAR** flow below.

### When a request arrives by email

Indian DPDP Act §13 gives the data fiduciary up to **30 days** to
respond. Process:

1. **Verify identity.** Ask the requester to confirm via a
   verification link sent to their account email. Do not act on an
   unverified request.
2. **Generate the export** via service-role psql:
   ```sql
   -- For a teacher; user_id = teachers.id (UUID)
   SELECT to_jsonb(t.*) AS teachers
     FROM teachers t WHERE t.id = '<uuid>';
   -- ... repeat for each table in the retention matrix above
   ```
   Or run `/api/v1/privacy/export` against an admin session
   impersonating the user (audited via auth_events).
3. **Deliver** the JSON file via a one-time-link uploaded to a
   pre-signed B2 url (24h expiry). Email the link to the verified
   address. Never email the JSON inline (attachments leak).
4. **Log the SAR** by inserting an auth_event manually with
   `event_type='manual_sar_fulfilled'` and meta containing the
   requester's ticket id.

### When erasure is requested

1. **Verify identity** as above.
2. **Notify the user** in writing of what will be deleted vs retained
   (link them to this doc).
3. **Preferred — have them call `/api/v1/privacy/delete`** themselves
   from the in-app Privacy panel. Carries the strongest audit context
   (they're authenticated; reauth_token proves password possession).
   The student path delegates to `auth.py:_track_a_hybrid_delete_student_account`
   which also notifies the issuing teacher.

4. **Hardship cases — operator-run via `POST /api/v1/admin/sar/delete`**
   when the user can't authenticate (lost MFA device, deceased data
   subject, court order). Superadmin-only. Required body:
   ```json
   {
     "target_user_type": "teacher" | "student",
     "target_user_id": "<uuid>",   // OR target_email
     "reason": "free-text, min 20 chars — appears in audit log",
     "ticket_id": "<optional helpdesk ref>"
   }
   ```
   Writes one row to `admin_audit_log` (your action) and one to
   `auth_events` with `event_type='account_deleted_by_admin'` (the
   target's record). Same retention semantics as the self-service
   path — see the matrix above.

5. **Hardship export — `POST /api/v1/admin/sar/export`** when a user
   emails support requesting their data but can't log in. Returns
   the same JSON shape as `/privacy/export` plus
   `exported_by_operator` + `ticket_id` for traceability. Logged to
   `admin_audit_log`. Hand-deliver the JSON via a 24h-expiry B2
   pre-signed URL (never email inline).
4. **Confirm** to the user that erasure is complete, with the list
   of retained categories from the matrix.

### Retention exemptions a regulator may probe

Be ready to justify each retained category:

| Retained | Why |
|---|---|
| consent_records | DPDP §7(2) requires us to *prove* we got consent. Erasing this would defeat the legal basis for past processing. |
| auth_events, admin_audit_log | Security forensics + DPDP §10 (reasonable security safeguards) — we need an audit trail of who accessed what. Logs are pseudonymised (user_id NULL'd) on erasure. |
| anonymised exam outputs | Once name/email are anonymised, the row is no longer personal data under DPDP §2(t) — the data subject is no longer identifiable. Retained for the *teacher's* legitimate analytics use. |
| Payment / billing records | Indian Income-tax Act §44AA requires 7-year retention of financial records. GDPR Art 17(3)(b) similarly exempts data needed for legal obligations. |
| Forensic frames (screenshots/) | Retained 30 days for dispute resolution; rotated by Ofelia daily backup script. After 30 days they roll off the disk + B2 lifecycle expires them. |

## What's NOT retained (full hard-delete on erasure)

- OAuth tokens to third-party services (Google Classroom). Sensitive +
  no compelling reason to keep.
- Active email OTPs (transient by design).
- Active auth sessions + refresh tokens (revoked, then garbage-collected
  by the TTL sweeper — see `migrations/phase86_ttl_sweeper.sql`).

## Grievance officer

Per DPDP §6(5) + §10(2)(c), Procta's grievance officer is reachable
at the address published on https://procta.net/legal/contact. The
officer must acknowledge a grievance within **7 days** and resolve
within **30 days** (DPDP §13(5)).

## Future work

- Pseudonymisation key — instead of replacing name/email with
  `Deleted User` literals, use a one-way HMAC keyed on a rotating
  secret so anonymised rows become re-linkable for audits but
  impossible to map back to the original identity without the key.
  Bigger change; not done in this pass.
- Quarterly "deletion review" report — count erasures + flag any
  `partial` status rows for ops follow-up.
