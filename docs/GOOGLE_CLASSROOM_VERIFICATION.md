# Google Classroom — OAuth App Verification Submission

Everything needed for the Google Cloud OAuth verification. Fill the `<…>`
placeholders, then paste each section into the matching field of the
**OAuth consent screen** + **Verification** flow in Google Cloud Console.

Until verification is granted, the app works for **Test users** added on the
consent screen — enough to pilot and to record the demo video.

> **Scope tier — verify in the console.** Google labels each scope **Sensitive**
> or **Restricted** when you add it on the *Data access* page. Classroom scopes
> are generally **Sensitive** (→ brand + privacy review, **no CASA**), *not*
> Restricted (which would trigger the CASA security assessment). **Trust the
> label the console shows you.** If all five below read "Sensitive," skip the
> CASA section entirely; only do §5 for any that read "Restricted."

---

## 1. OAuth consent screen — basic info

| Field | Value |
|---|---|
| App name | **Procta** |
| User support email | `<support@procta.net>` |
| App logo | 120×120 PNG of the Procta mark (must match the site) |
| Application home page | `https://procta.net` |
| Privacy policy URL | `https://app.procta.net/privacy` (must be reachable + mention Google user data) |
| Terms of service URL | `https://app.procta.net/terms` |
| Authorized domain | `procta.net` |
| Developer contact email | `<you@procta.net>` |
| App type | External, Production |
| Authorized redirect URI | `https://app.procta.net/api/v1/google/callback` |

The OAuth flow uses **PKCE** (auto-handled by `google-auth-oauthlib`) on top of
the confidential web client — no extra console config needed for it.

---

## 2. Scopes requested + per-scope justification

Five scopes, **each tied to a concrete teacher-initiated action that is live in
the product** (and shown in the demo video). Paste one justification per scope.

**`https://www.googleapis.com/auth/classroom.courses.readonly`** (sensitive)
> Procta reads the signed-in teacher's list of Google Classroom courses so the
> teacher can pick which course to associate with a Procta proctored exam. We
> read course id and name only; we never modify courses.

**`https://www.googleapis.com/auth/classroom.rosters.readonly`** (sensitive)
> After a teacher links a course, Procta reads that course's student roster so
> those students are enrolled for the proctored exam without the teacher
> re-entering them by hand. Read-only, and only for courses the teacher
> explicitly links.

**`https://www.googleapis.com/auth/classroom.profile.emails`** (sensitive)
> Procta reads each rostered student's email address — the roster API omits it
> without this scope — to match the Classroom student to their Procta exam
> record (the student's roll/identity is keyed on email). Read-only; emails are
> used solely to create/align the exam enrolment.

**`https://www.googleapis.com/auth/classroom.coursework.students`** (sensitive)
> Procta creates one assignment in the linked course and writes each student's
> proctored-exam score back to it, so teachers see results in their existing
> Classroom gradebook instead of exporting manually. Create/update coursework +
> grades only for the teacher's own linked courses.

**`https://www.googleapis.com/auth/classroom.coursework.me`** (sensitive)
> Lets Procta read/manage the coursework item it created on the teacher's behalf
> so it can post the grade to the correct assignment.

**Minimality statement (for the form):**
> Each scope maps to one concrete teacher-initiated action that is implemented
> and demonstrated: list courses, link a course, import its roster (with email
> matching), and write exam grades back to the linked assignment. We use
> read-only scopes wherever a read suffices. No Google user data is used for
> advertising, sold, or shared with third parties.

---

## 3. How the app uses Google user data (data-use narrative)

> Procta is an AI exam-proctoring platform. A teacher connects their Google
> Classroom account so they can (a) link a Classroom course to a Procta exam,
> (b) import that course's roster (matched by student email) so students are
> pre-enrolled, and (c) have exam scores written back to the Classroom
> gradebook.
>
> Data accessed: course id/name, roster entries (student name + email), and the
> coursework item/grades Procta creates — only for courses the teacher
> explicitly links.
>
> Storage & security: OAuth tokens are stored **encrypted at rest** (Fernet/
> AES) and are scoped per-teacher under database row-level security, so no
> teacher can access another's tokens or roster data. Imported roster data is
> used solely to create exam enrolments. Tokens and roster data are deleted when
> the teacher disconnects Google Classroom or deletes their account. Google user
> data is **never** used for advertising, never sold, and never shared with
> third parties; it is processed only to provide these features. Access is
> limited to the authenticated teacher; transport is HTTPS only.

---

## 4. Demo video — script (Google requires a screen recording)

Record a 2–4 min screencast showing the OAuth grant + **each scope in use**:
1. Sign in to Procta as a teacher → **Tools → Integrations → Connect Google Classroom**.
2. The Google consent screen appears showing **exactly these five scopes** → grant (including "See student email addresses").
3. Back in Procta: the teacher's **course list loads** (`courses.readonly`).
4. Teacher **links a course to an exam**, clicks **Sync roster** → students appear, tagged into a cohort named after the class (`rosters.readonly` + `profile.emails`).
5. A student completes the exam; show the **score appearing as a grade in that course's Classroom gradebook** (`coursework.students` + `coursework.me`).
6. Show **Disconnect** removing the connection (data deletion).
Narrate which scope each step exercises. Upload unlisted to YouTube; paste the link in the form.

> All six steps are implemented and runnable on a **test account** today — you
> do not need verification to record this.

---

## 5. Restricted-scope security assessment (CASA) — only if a scope is labeled "Restricted"

**Skip this section if the console labels all five scopes "Sensitive."** If any
is "Restricted," that scope triggers a **CASA** assessment by a Google-authorized
lab. Prepare:
- The data-flow + storage description above (tokens encrypted, RLS-scoped, deletion on disconnect).
- Evidence of HTTPS-only, no secrets in client, least-privilege access.
- A security contact + incident process.
Budget weeks; start it in parallel.

---

## 6. Quick reference — prod env this enables

```
GOOGLE_CLASSROOM_CLIENT_ID=<from the OAuth client>
GOOGLE_CLASSROOM_CLIENT_SECRET=<from the OAuth client>
# GOOGLE_CLASSROOM_REDIRECT_URI defaults to https://app.procta.net/api/v1/google/callback
TOTP_ENCRYPTION_KEY=<must be set — encrypts the stored OAuth tokens>
```
Setting the two `GOOGLE_CLASSROOM_*` vars makes the dashboard "Connect Google
Classroom" button appear. RLS for the `google_*` tables ships in phase129;
grade-passback columns in phase131.

**Consent-screen Data access must list all five scopes** (including
`classroom.profile.emails`) — adding a scope in code without adding it here
makes the grant silently drop it.
