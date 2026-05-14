# OAuth Setup — Supabase Sign-In + Google Classroom

This document covers two **separate** OAuth integrations that share zero
configuration:

| Integration | What it does | OAuth flows through |
|---|---|---|
| **Part 1 — Supabase Auth** | "Continue with Google" + "Continue with Microsoft" buttons on signup / login | Supabase Auth |
| **Part 2 — Google Classroom API** | Teacher links their Classroom course, syncs roster, pushes grades back | Our backend directly (no Supabase involvement) |

> They're separate **Google Cloud projects** with separate OAuth clients on
> purpose. Sign-in needs only basic profile/email scopes (clean consent
> screen → high conversion). Classroom needs roster + grade scopes which
> trigger a scary consent prompt unless the user actually wants Classroom.

---

# Part 1 — Supabase Auth (Google + Microsoft Sign-In)

## The flow

```
1. User clicks "Continue with Google" on procta.net
2. → our /api/v1/auth/oauth/start redirects to Supabase
3. → Supabase redirects to Google
4. → User authorises on Google's screen
5. → Google sends user back to Supabase's callback
6. → Supabase sends user back to our /api/v1/auth/oauth/callback
7. → Our backend exchanges the code, binds to teacher/student, issues JWT
```

You're wiring three things: Google Cloud, Microsoft Entra, and Supabase.
Supabase is the hub — both providers point at Supabase's callback, then
Supabase points at ours.

## Step 1 — Google Cloud Console (~10 min)

Open https://console.cloud.google.com

### 1a. Create a new project

- Top bar → project dropdown → **New Project**
- Name: `Procta Sign-In` (keep it separate from any existing Classroom project)
- **Create** → wait 10s → switch to the new project

### 1b. Configure OAuth consent screen

- Left menu → **APIs & Services** → **OAuth consent screen**
- User type: **External** → Create
- Fill in:
  - **App name:** `Procta`
  - **User support email:** your email
  - **App logo:** upload a 120×120 PNG (or skip while testing)
  - **App domain → Application home page:** `https://procta.net`
  - **App domain → Privacy policy link:** `https://procta.net/privacy`
  - **App domain → Terms of service link:** `https://procta.net/terms`
  - **Authorized domains:** add `procta.net` (Google rejects subdomains here — they want the apex)
  - **Developer contact email:** your email
- **Scopes** screen: click **Add or Remove Scopes**, tick exactly three:
  - `.../auth/userinfo.email`
  - `.../auth/userinfo.profile`
  - `openid`

  Do **not** add Classroom scopes here. Those go on the separate
  Classroom OAuth client in Part 2.
- **Test users** screen: add your own Gmail address while testing.
- **Save and Continue** all the way through.

### 1c. Create the OAuth Client ID

- Left menu → **APIs & Services** → **Credentials**
- **+ Create Credentials** → **OAuth client ID**
- Application type: **Web application**
- Name: `Procta Sign-In Web Client`
- **Authorized JavaScript origins:**
  - `https://procta.net`
  - `https://app.procta.net`
- **Authorized redirect URIs:** exactly one
  ```
  https://ynzpcxoxbiwpheqmdnaj.supabase.co/auth/v1/callback
  ```
  This is the Supabase callback, **not** our `/api/v1/auth/oauth/callback`.
  Supabase brokers OAuth, then sends the user to us after.
- **Create**. Copy **Client ID** and **Client Secret** immediately.

### 1d. Publish (removes the "unverified app" warning)

- OAuth consent screen → **Publishing status: Testing** → **Publish App**
- "In production" mode. No verification needed for the 3 basic scopes.

## Step 2 — Microsoft Entra ID (~10 min)

Open https://entra.microsoft.com — sign in with any Microsoft account.

### 2a. Register the app

- Left menu → **Identity** → **Applications** → **App registrations**
- **+ New registration**
- **Name:** `Procta Sign-In`
- **Supported account types:** select
  **"Accounts in any organizational directory (Multitenant) and personal Microsoft accounts"**

  Critical for the university segment. Single-tenant only works for your own org.
- **Redirect URI:**
  - Platform: **Web**
  - URI: `https://ynzpcxoxbiwpheqmdnaj.supabase.co/auth/v1/callback`
- **Register**

### 2b. Create a client secret

- App page → left menu → **Certificates & secrets**
- **Client secrets** tab → **+ New client secret**
- Description: `Procta Production`
- Expires: **24 months** (max). Set a calendar reminder to rotate.
- **Add** → copy the **Value** column immediately. After page reload it's gone forever.

### 2c. Note the IDs

- App **Overview** page. Copy:
  - **Application (client) ID** — UUID
  - **Directory (tenant) ID** — UUID

### 2d. API permissions

- Should show `Microsoft Graph → User.Read` (auto-added).
- Don't add anything. Supabase reads basic profile via OpenID Connect.

## Step 3 — Supabase dashboard (~2 min)

Open https://supabase.com/dashboard/project/ynzpcxoxbiwpheqmdnaj

### 3a. Enable Google

- Left menu → **Authentication** → **Sign In / Up** (or **Providers**) → **Google**
- Toggle **Enable Sign in with Google** ON
- Paste **Client ID** + **Client Secret** from step 1c
- Leave **Skip nonce checks** OFF
- **Save**

### 3b. Enable Azure (Microsoft)

- Same screen → **Azure (Microsoft)**
- Toggle ON
- Paste **Application (Client) ID** + **Secret Value** from step 2
- **Azure Tenant URL:**
  ```
  https://login.microsoftonline.com/common
  ```
  Literal word `common` — makes the app multi-tenant. Don't paste your
  tenant UUID unless you want to lock to your org only.
- **Save**

### 3c. Whitelist our callback URL

- Left menu → **Authentication** → **URL Configuration**
- **Site URL:** `https://app.procta.net`
- **Redirect URLs:** add all three:
  ```
  https://app.procta.net/api/v1/auth/oauth/callback
  https://procta.net/**
  https://app.procta.net/**
  ```
- **Save**

## Step 4 — Verify (~5 min)

### 4a. OAuth URL resolves

```
https://app.procta.net/api/v1/auth/oauth/start?provider=google&intent=teacher&return_to=https://procta.net/
```

You should:
1. See a Supabase URL briefly
2. Land on Google's account picker
3. Pick your account
4. See "Procta wants to access your email and profile" (only — no Classroom mention)
5. Get redirected to `https://procta.net/#access_token=eyJ...`

### 4b. Microsoft

```
https://app.procta.net/api/v1/auth/oauth/start?provider=azure&intent=teacher&return_to=https://procta.net/
```

### 4c. Verify the teacher row

In Supabase SQL Editor:

```sql
select id, email, full_name, supabase_uid, email_verified_at, created_at
  from teachers
  order by created_at desc
  limit 5;
```

OAuth signup row should be at the top with `email_verified_at` populated.

## Common gotchas

| Symptom | Cause | Fix |
|---|---|---|
| Google: "redirect_uri_mismatch" | Mismatched Supabase callback URL | Must be `https://ynzpcxoxbiwpheqmdnaj.supabase.co/auth/v1/callback` exactly — no trailing slash |
| Google: "This app isn't verified" | Step 1d not done | Publish, or add your account as a test user |
| Microsoft: "AADSTS50011: reply URL doesn't match" | Wrong redirect URI in step 2a | Same fix |
| Microsoft: "AADSTS90072: only personal Microsoft accounts" | Single-tenant in step 2a | App registration → Authentication → change to multi-tenant + personal |
| Our callback: 400 "Invalid or expired state" | State JWT TTL 10 min; consent took too long | Just try again |
| Our callback: 400 "code exchange failed" | Supabase provider not enabled | Re-check step 3a/3b — Save often forgotten |

---

# Part 2 — Google Classroom API

> **Status:** OAuth scaffolding is complete (auth + callback + token storage
> + course listing + roster sync). **Grade passback to Classroom is NOT
> wired** — only the LTI 1.3 AGS grade passback is. Auto-creating
> CourseWork items in Classroom is also NOT wired. See "What's actually
> wired" below for the honest line-by-line state.

## What's actually wired (code audit)

### ✅ Wired end-to-end

| Feature | Code location |
|---|---|
| OAuth start (`GET /api/v1/google-classroom/auth`) | `app/routers/google_classroom.py:21` |
| OAuth callback (`GET /api/v1/google-classroom/callback`) | `app/routers/google_classroom.py:41` |
| Token storage (encrypted in `google_auth_tokens` table) | service `exchange_code` + DB write |
| Disconnect (`POST /api/v1/google-classroom/disconnect`) | `app/routers/google_classroom.py:85` |
| List teacher's Classroom courses (`GET /api/v1/google-classroom/courses`) | `app/routers/google_classroom.py:96` |
| Link a Procta exam → a Classroom course (`POST /link-exam`) | `app/routers/google_classroom.py:126` |
| Unlink (`POST /unlink-exam`) | `app/routers/google_classroom.py:156` |
| One-shot roster sync (`POST /sync-roster`) — pulls students into Procta | `app/routers/google_classroom.py:167` |
| `google_classroom_links` + `google_auth_tokens` + `google_oauth_states` tables | `migrations/phase32_google_classroom.sql` (applied) |

### ⚠️ Skeleton only — function exists but never called

| Feature | Code location | Why not working |
|---|---|---|
| Grade passback to Classroom | `app/services/google_classroom.py:156` (`push_grade`) | Function works, but no endpoint or scheduled job calls it after exam submission. Currently only the LTI 1.3 path in `exam.py:628 (_try_ags_grade_passback)` actually pushes grades. |

### ❌ Not started

| Feature | What needs to be built |
|---|---|
| Auto-create Classroom CourseWork on link | When a teacher links a Procta exam to a Classroom course, create a corresponding CourseWork item in Classroom so it shows up in the students' Classroom feed |
| Announcement push when exam published | Post a Classroom Announcement on publish: "New exam: CS301 Mid-sem, opens Friday at 10 AM" |
| Periodic roster refresh | `sync-roster` is a one-shot button. A nightly cron should re-pull rosters so drops/adds don't silently desync |
| Frontend dashboard panel | No UI for: connecting Classroom, viewing linked courses, triggering sync, seeing last sync status. Currently only the API exists |
| Per-student grade granularity | `push_grade` pushes the auto-computed Procta total. Doesn't yet support exporting short-answer rubric scores, only the overall percentage |

## Setup steps to make the wired parts work

### Step C1 — Google Cloud Console (separate project from Part 1)

You need a **separate Google Cloud project + OAuth client** because the
Classroom integration requires Google verification (broader scopes = manual
review). Mixing Sign-In + Classroom into one client means Sign-In also gets
delayed by Classroom verification.

- https://console.cloud.google.com → **New Project**
- Name: `Procta Classroom Integration`
- Switch to this project

### Step C2 — Enable the Classroom API

- Left menu → **APIs & Services** → **Library**
- Search "Google Classroom API" → **Enable**

### Step C3 — OAuth consent screen (Classroom-specific)

- **APIs & Services** → **OAuth consent screen**
- User type: **External**
- App name: `Procta · Classroom Integration`
- Same support email + privacy/terms links as the Sign-In project
- **Authorized domains:** `procta.net`
- **Scopes:** add these four (this is where Classroom verification is needed
  before going to production):
  - `.../auth/classroom.courses.readonly` — list courses
  - `.../auth/classroom.rosters.readonly` — list students
  - `.../auth/classroom.coursework.students` — read/write coursework
  - `.../auth/classroom.coursework.me` — write the teacher's own coursework
- **Test users:** add yourself (and any teacher who will pilot it). Up to
  100 testers allowed without verification.
- Save through.

### Step C4 — Create the Classroom OAuth Client

- **APIs & Services** → **Credentials**
- **+ Create Credentials** → **OAuth client ID**
- Application type: **Web application**
- Name: `Procta Classroom Web Client`
- **Authorized redirect URIs:** exactly one
  ```
  https://app.procta.net/api/v1/google/callback
  ```
  Note: this is **our** callback (`google_classroom.py:41`), not Supabase's.
  Classroom OAuth bypasses Supabase entirely.
- Copy Client ID + Client Secret.

### Step C5 — Set env vars on the droplet

In your `.env` (or `docker compose` env block):

```
GOOGLE_CLASSROOM_CLIENT_ID=<from step C4>
GOOGLE_CLASSROOM_CLIENT_SECRET=<from step C4>
GOOGLE_CLASSROOM_REDIRECT_URI=https://app.procta.net/api/v1/google/callback
```

These are read in `app/services/google_classroom.py:21-23`.

### Step C6 — Verify the OAuth flow works

Hit, while logged in as a teacher:

```
GET https://app.procta.net/api/v1/google-classroom/auth
```

(Or build a "Connect Google Classroom" button on the dashboard — see TODO list).

You should see Google's consent screen with **all four** Classroom scopes
listed. Approve.

Verify the token landed:

```sql
select id, teacher_id, email, created_at
  from google_auth_tokens
  where teacher_id = '<your-teacher-id>';
```

One row should exist.

### Step C7 — Smoke test the wired endpoints

With your teacher JWT:

```bash
# 1. List your Classroom courses
curl https://app.procta.net/api/v1/google-classroom/courses \
  -H "Authorization: Bearer <teacher-jwt>"
# → expect JSON list of courses

# 2. Link an exam to a course
curl -X POST https://app.procta.net/api/v1/google-classroom/link-exam \
  -H "Authorization: Bearer <teacher-jwt>" \
  -H "Content-Type: application/json" \
  -d '{"course_id": "<google_course_id>", "exam_id": "<procta_exam_id>"}'

# 3. Pull the roster
curl -X POST https://app.procta.net/api/v1/google-classroom/sync-roster \
  -H "Authorization: Bearer <teacher-jwt>" \
  -H "Content-Type: application/json" \
  -d '{"course_id": "<google_course_id>", "exam_id": "<procta_exam_id>"}'
# → expect {"imported": N}; check `students` table for new rows
```

## What still needs to be built for "real" Classroom integration

In rough order of school-procurement impact:

### B1. Grade passback to Classroom (~1 day)

The `push_grade` function exists at `app/services/google_classroom.py:156`
but is never called. To wire it:

```python
# in app/routers/exam.py after submit_exam() finishes scoring,
# next to the existing _try_ags_grade_passback() call (~line 838):

asyncio.create_task(_try_classroom_grade_passback(
    teacher_id=tid,
    exam_id=eid,
    student_email=student.email,
    score=server_score,
    max_score=server_total,
))
```

The new helper looks up `google_classroom_links` for the exam, gets the
course_id + coursework_id, calls `push_grade` from the service. Needs:

- A `google_coursework_id` column on `google_classroom_links` (currently
  only stores `google_course_id`)
- An auto-create-coursework step at link time (see B2)

### B2. Auto-create CourseWork on link (~1 day)

When a teacher clicks "Link Procta exam to this Classroom course", the
backend should create a Classroom CourseWork item that shows up in the
students' Classroom feed:

```python
# app/services/google_classroom.py — new function
async def create_coursework(creds, course_id, title, description, due_at, max_points):
    service = build("classroom", "v1", credentials=creds, cache_discovery=False)
    return service.courses().courseWork().create(
        courseId=course_id,
        body={
            "title": title,
            "description": description,
            "workType": "ASSIGNMENT",
            "state": "PUBLISHED",
            "maxPoints": max_points,
            "dueDate": {...},
        },
    ).execute()
```

Call from `link_exam` route. Save the returned `coursework.id` to
`google_classroom_links.google_coursework_id`.

### B3. Frontend dashboard panel (~1.5 days)

Where the existing "Tools" tab lives, add a Classroom card:

- "Connect Google Classroom" button → opens `/api/v1/google-classroom/auth`
- After connect: shows list of courses (from `/courses` endpoint)
- Per-course: "Link to exam …" dropdown + "Sync roster" button + last-sync timestamp
- Disconnect button

Probably 200-300 lines of HTML/JS in `dashboard.html` following the
existing tool-card pattern at lines 1100-1300ish.

### B4. Periodic roster refresh (~0.5 day)

Cron job (use existing RQ worker) that runs nightly:

```python
# app/jobs/classroom_jobs.py
async def refresh_all_rosters():
    links = await _atable("google_classroom_links").select("*").execute()
    for link in links.data:
        # ... pull token, list_students, upsert into students table ...
```

Add to `app/main.py` startup tasks or wire as a scheduled RQ job.

### B5. Announcement push on exam publish (~0.5 day)

When a teacher publishes an exam (currently no explicit publish action —
"questions saved + start_at in future" implicitly publishes), post a
Classroom Announcement so students see "New exam available" in their feed.

```python
# app/services/google_classroom.py — new function
async def post_announcement(creds, course_id, text, materials=None):
    service = build("classroom", "v1", credentials=creds, cache_discovery=False)
    return service.courses().announcements().create(
        courseId=course_id,
        body={"text": text, "materials": materials or [], "state": "PUBLISHED"},
    ).execute()
```

Hook into wherever exams get published.

## Verification scopes & Google's review process

You can launch in **Testing** mode with up to 100 test users without Google
verification. Pilot schools count — add their teachers as test users one at
a time. Once you have a real pilot, request **Sensitive Scopes
Verification** in the OAuth consent screen settings:

- Required for production with the Classroom scopes
- Google takes 1-4 weeks typically
- You'll need: privacy policy URL, terms URL, homepage screenshot showing
  the Classroom button, video demo of the OAuth flow (3-5 min screen
  recording), and a domain-verified email

Until verified, users outside the test-user list see "Procta has not
completed Google's verification process" — they can still proceed via
"Advanced" → "Go to Procta (unsafe)" but the friction is real.

## Common gotchas (Classroom-specific)

| Symptom | Cause | Fix |
|---|---|---|
| "Error 400: invalid_scope" on consent | One of the 4 scopes wasn't added in step C3 | Add all four in OAuth consent screen → Scopes |
| Token works once then fails | Refresh token wasn't stored | Check `_build_credentials` returns refresh_token; re-auth fixes |
| `push_grade` returns False | Student hasn't submitted in Classroom (no `studentSubmissions` row yet) | Students must submit at least once in Classroom first, or use `studentSubmissions.modifyAttachments` to create one |
| Roster sync imports 0 students | Wrong `course_id` (you passed the Classroom UI alias, not the API ID) | Use the `id` field from `/courses` endpoint response, not the `name` |
| "calendar.readonly" needed but not requested | Scope creep — Classroom API now wants calendar access for due-date display | Add `.../auth/calendar.readonly` scope in C3 if Google requests it |

---

# Summary

| What | Status | Manual work |
|---|---|---|
| Supabase Sign-In (Google + Microsoft) — backend | ✅ Code complete | Steps 1-3 + verify in step 4 |
| Supabase Sign-In — frontend (Signup.jsx + student.html) | ✅ Code complete | Same |
| Supabase Sign-In — frontend (dashboard.html teacher login) | ⚠️ Pending | (Same plan, second commit) |
| Classroom OAuth + roster sync | ✅ Code complete | Steps C1-C7 |
| Classroom grade passback | ⚠️ Service exists, not called | Build B1 + B2 |
| Classroom CourseWork auto-create | ❌ Not started | Build B2 |
| Classroom announcements | ❌ Not started | Build B5 |
| Classroom dashboard panel | ❌ Not started | Build B3 |
| Periodic roster refresh | ❌ Not started | Build B4 |

**Total remaining engineering work for Classroom**: ~3-4 days for B1-B5
end-to-end. Setup work (C1-C7) is ~30 minutes for someone who's done
Google Cloud before.
