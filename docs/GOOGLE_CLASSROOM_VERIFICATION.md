# Google Classroom — OAuth App Verification Submission

Everything needed for the Google Cloud OAuth verification (restricted scopes).
Fill the `<…>` placeholders, then paste each section into the matching field of
the **OAuth consent screen** + **Verification** flow in Google Cloud Console.

This is the long pole (restricted scopes → privacy review + a CASA security
assessment, typically several weeks). Until it's granted, the app works for
**Test users** added on the consent screen — enough to pilot.

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

---

## 2. Scopes requested + per-scope justification

Google requires a specific, minimal justification per scope. Use these.

**`https://www.googleapis.com/auth/classroom.courses.readonly`** (sensitive)
> Procta reads the signed-in teacher's list of Google Classroom courses so the
> teacher can pick which course to associate with a Procta proctored exam. We
> read course id and name only; we do not modify courses.

**`https://www.googleapis.com/auth/classroom.rosters.readonly`** (restricted)
> After a teacher links a course, Procta reads that course's student roster
> (student name + email) so those students are automatically enrolled for the
> proctored exam without the teacher re-entering them by hand. We read roster
> entries only for courses the teacher explicitly links; we never modify
> rosters.

**`https://www.googleapis.com/auth/classroom.coursework.students`** (restricted)
> Procta writes the exam result back to Google Classroom as the grade for the
> linked coursework, so teachers see scores in their existing gradebook instead
> of exporting manually. We create/update coursework + grades only for the
> teacher's own linked courses.

**`https://www.googleapis.com/auth/classroom.coursework.me`** (sensitive)
> Reads the teacher's own coursework items so Procta can match a proctored exam
> to the correct assignment when pushing grades.

**Minimality statement (for the form):**
> Each scope maps to one concrete teacher-initiated action (link a course,
> import its roster, push exam grades). We request read-only scopes wherever a
> read suffices and only the two restricted scopes that grade passback strictly
> requires. No scope is used for advertising, sold, or shared with third
> parties.

---

## 3. How the app uses Google user data (data-use narrative)

> Procta is an AI exam-proctoring platform. A teacher connects their Google
> Classroom account so they can (a) link a Classroom course to a Procta exam,
> (b) import that course's roster so students are pre-enrolled, and (c) have
> exam scores written back to the Classroom gradebook.
>
> Data accessed: course id/name, roster entries (student name + email), and
> coursework items/grades — only for courses the teacher explicitly links.
>
> Storage & security: OAuth tokens are stored **encrypted at rest** (Fernet/
> AES) and are scoped per-teacher under database row-level security, so no
> teacher can access another's tokens or roster data. Imported roster data is
> used solely to create exam enrolments. Tokens and roster data are deleted
> when the teacher disconnects Google Classroom or deletes their account.
> Google user data is **never** used for advertising, never sold, and never
> shared with third parties; it is processed only to provide these features.
>
> Access is limited to the authenticated teacher; transport is HTTPS only.

---

## 4. Demo video — script (Google requires a screen recording)

Record a 2–3 min screencast showing the OAuth grant + each scope in use:
1. Sign in to Procta as a teacher → dashboard → **Integrations → Connect Google Classroom**.
2. The Google consent screen appears showing **exactly these scopes** → grant.
3. Back in Procta: the teacher's **course list loads** (courses.readonly).
4. Teacher **links a course to an exam**, clicks **Sync roster** → students appear (rosters.readonly).
5. After an exam is graded, show the **grade pushed into the Classroom gradebook** (coursework scopes).
6. Show **Disconnect** removing the connection (data deletion).
Narrate which scope each step exercises. Upload unlisted to YouTube; paste the link in the form.

---

## 5. Restricted-scope security assessment (CASA)

The two `restricted` scopes (rosters, coursework.students) trigger a **CASA
Tier-2 assessment** by a Google-authorized lab. Prepare:
- The data-flow + storage description above (tokens encrypted, RLS-scoped, deletion on disconnect).
- Evidence of HTTPS-only, no secrets in client, least-privilege access.
- A security contact + incident process.
Budget weeks; start it in parallel with everything else.

---

## 6. Quick reference — prod env this enables

```
GOOGLE_CLASSROOM_CLIENT_ID=<from the OAuth client>
GOOGLE_CLASSROOM_CLIENT_SECRET=<from the OAuth client>
# GOOGLE_CLASSROOM_REDIRECT_URI defaults to https://app.procta.net/api/v1/google/callback
TOTP_ENCRYPTION_KEY=<must be set — encrypts the stored OAuth tokens>
```
Setting the two `GOOGLE_CLASSROOM_*` vars makes the dashboard "Connect Google
Classroom" button appear. RLS support for the google_* tables ships in
phase129 (PR #101).
