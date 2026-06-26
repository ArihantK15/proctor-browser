# Account Types: Solo Teacher vs Organization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make account type an explicit choice at signup — *solo teacher* vs *organization (manager-only admin)* — retiring the solo-downgrade hack and the invite deadlock, with no mid-life conversion or data migration.

**Architecture:** Billing ownership decouples from `org_role` via a new `organizations.owner_teacher_id`. Signup branches on a new `account_type`: solo → `org_role='teacher'` + owner; org → `org_role='admin'` (manager-only) + owner. The dashboard gates exam-authoring surfaces off `org_role==='admin'` and gates Billing off owner-ship rather than role. The card-on-signup gate keys off billing ownership, so it fires for both owners uniformly.

**Tech Stack:** FastAPI + Postgres (asyncpg), phase-numbered SQL migrations, vanilla-JS dashboard (`dashboard.html`/`dashboard-app.js`), React marketing signup (`website/`). pytest + the schema-ref guard + `integration_tests/schema.sql`.

**Spec:** `docs/superpowers/specs/2026-06-20-account-types-solo-vs-org-design.md`

---

## Reference reading (the implementer MUST read these before starting)

- `app/routers/auth.py:189-292` — `_create_teacher_signup_postgres_tx` (org + subscription + teacher INSERT). The `org_role` is hardcoded `'admin'` at ~line 266.
- `app/routers/auth.py:498-600` — `teacher_signup` endpoint + `org_name` handling.
- `app/models/` — `TeacherSignupIn` (the signup request model).
- `app/auth/scope.py:35-52` — `compute_is_solo`; `:55-75` — `org_is_solo`.
- `app/static/dashboard-app.js:1231-1276` — role/solo/billing-owner gating; `:1198` tab loaders; `dashboard.html:341-362` — the tab buttons + `data-roles`.
- `app/repositories/questions.py:13-19` — `_EXAM_CONFIG_COLUMNS` (pattern for adding a column to a snapshot-guarded table).
- **Schema-guard rule (learned the hard way, see git #135):** any new column referenced in code must ALSO be added to `schema/columns.json` AND `integration_tests/schema.sql`, or `pytest` (schema-ref guard) and `integration` CI fail.

---

## File map

- **Create** `migrations/phaseNNN_org_owner_teacher.sql` — `organizations.owner_teacher_id UUID` (NULL ok; FK to teachers, NOT VALID then VALIDATE). NNN = next free (check `ls migrations | grep -oE 'phase[0-9]+' | sed 's/phase//' | sort -n | tail -1` + 1).
- **Modify** `schema/columns.json` — add `owner_teacher_id` to `organizations`.
- **Modify** `integration_tests/schema.sql` — add the column to the `organizations` CREATE TABLE.
- **Modify** the `TeacherSignupIn` model — add `account_type: Literal['solo','org'] = 'solo'`.
- **Modify** `app/routers/auth.py` — `teacher_signup` + `_create_teacher_signup_postgres_tx`: branch on `account_type`; set `org_role` + `owner_teacher_id`.
- **Modify** `app/auth/scope.py` — add `is_billing_owner(teacher)` helper from `owner_teacher_id`; keep `compute_is_solo` only for any legacy callers (or retire if unused after dashboard change).
- **Modify** `app/routers/auth.py` profile/`/me` payload — expose `is_billing_owner` (and stop relying on `is_solo` for gating).
- **Modify** `app/static/dashboard-app.js` — gate authoring tabs off `org_role==='admin'`; gate Billing off `is_billing_owner`; delete the solo-downgrade override (1244).
- **Modify** `app/static/dashboard.html` — add `data-roles`/hide rules so authoring tabs are hidden for `admin`; ensure Members/Org/Settings shown for `admin`.
- **Modify** website signup (`website/src/pages/` signup component) — add the Solo vs Organization choice + copy; POST `account_type`.
- **Tests:** `tests/test_account_types_signup.py` (new); extend `tests/test_auth_and_sessions.py` if needed; `integration_tests/test_signup_account_types_integration.py` (new, real PG).

---

## Phase 1 — Backend role model + signup branching

### Task 1: Migration + schema snapshots for `owner_teacher_id`

**Files:**
- Create: `migrations/phaseNNN_org_owner_teacher.sql`
- Modify: `schema/columns.json`, `integration_tests/schema.sql`

- [ ] **Step 1: Write the migration** (house style — EXCEPTION-handled DO blocks, NOT VALID → VALIDATE; mirror `migrations/phase134_early_join_window.sql`):

```sql
-- Phase NNN: org billing owner — decouple billing ownership from org_role so a
-- solo teacher (org_role='teacher') can own their own subscription. See
-- docs/superpowers/specs/2026-06-20-account-types-solo-vs-org-design.md
DO $$ BEGIN
  ALTER TABLE organizations ADD COLUMN IF NOT EXISTS owner_teacher_id UUID;
EXCEPTION WHEN undefined_table THEN RAISE NOTICE 'organizations absent; skip';
          WHEN duplicate_column THEN RAISE NOTICE 'owner_teacher_id exists; skip'; END $$;

DO $$ BEGIN
  ALTER TABLE organizations
    ADD CONSTRAINT fk_org_owner_teacher FOREIGN KEY (owner_teacher_id)
    REFERENCES teachers(id) ON DELETE SET NULL NOT VALID;
EXCEPTION WHEN duplicate_object THEN RAISE NOTICE 'fk exists; skip';
          WHEN undefined_table THEN RAISE NOTICE 'table absent; skip'; END $$;

DO $$ BEGIN
  ALTER TABLE organizations VALIDATE CONSTRAINT fk_org_owner_teacher;
EXCEPTION WHEN undefined_object THEN RAISE NOTICE 'constraint absent; skip';
          WHEN undefined_table THEN RAISE NOTICE 'table absent; skip'; END $$;
```

- [ ] **Step 2: Add to `schema/columns.json`** — insert `"owner_teacher_id"` into the `"organizations"` array (keep it alphabetically sorted, matching the file's convention).

- [ ] **Step 3: Add to `integration_tests/schema.sql`** — add `owner_teacher_id UUID,` to the `CREATE TABLE ... organizations (` block.

- [ ] **Step 4: Run the schema-ref guard**

Run: `python3 scripts/check_schema_refs.py`
Expected: `✓ schema-ref check: all N column references exist in columns.json`

- [ ] **Step 5: Commit**

```bash
git add migrations/phaseNNN_org_owner_teacher.sql schema/columns.json integration_tests/schema.sql
git commit -m "feat(db): organizations.owner_teacher_id for billing-owner decoupling"
```

### Task 2: `account_type` on the signup model

**Files:**
- Modify: the file defining `TeacherSignupIn` (find: `grep -rn "class TeacherSignupIn" app/models`)
- Test: `tests/test_account_types_signup.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_account_types_signup.py
import os, sys
os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

def test_signup_model_defaults_to_solo():
    from app.models import TeacherSignupIn
    m = TeacherSignupIn(full_name="A", email="a@x.com", org_name="A", password="Str0ng!Passw0rd")
    assert m.account_type == "solo"

def test_signup_model_accepts_org():
    from app.models import TeacherSignupIn
    m = TeacherSignupIn(full_name="A", email="a@x.com", org_name="A",
                        password="Str0ng!Passw0rd", account_type="org")
    assert m.account_type == "org"
```

- [ ] **Step 2: Run it — expect FAIL** (`account_type` unknown / attribute error). `python3 -m pytest tests/test_account_types_signup.py -q`

- [ ] **Step 3: Add the field** to `TeacherSignupIn`:

```python
from typing import Literal
account_type: Literal["solo", "org"] = "solo"
```

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Commit** `feat(auth): account_type on TeacherSignupIn`.

### Task 3: Branch signup on account_type (role + owner)

**Files:**
- Modify: `app/routers/auth.py` (`_create_teacher_signup_postgres_tx` ~189-292; `teacher_signup` ~498-600)
- Test: `tests/test_account_types_signup.py`

- [ ] **Step 1: Read** `_create_teacher_signup_postgres_tx` fully so the INSERT columns/params are exact (it RETURNINGs the teacher row inside a single asyncpg transaction).

- [ ] **Step 2: Write the failing test** (endpoint-level, mock the supabase chain + supabase-auth + email like `tests/test_privacy_appeals.py` does; assert the response shape):

```python
def test_solo_signup_makes_teacher_owner(client_and_mocks):
    # ... drive POST /api/v1/auth/signup with account_type="solo"
    # assert returned teacher org_role == "teacher" and is_billing_owner is True
    ...

def test_org_signup_makes_manager_admin(client_and_mocks):
    # account_type="org" -> org_role == "admin", is_billing_owner True
    ...
```
(Model the mock harness on the existing signup test in `tests/test_auth_and_sessions.py`; if none, build a table-aware `_atable`/asyncpg-conn mock per `tests/test_privacy_appeals.py` `_Q` pattern.)

- [ ] **Step 3: Run — expect FAIL.**

- [ ] **Step 4: Implement the branch.** In `_create_teacher_signup_postgres_tx`, take `account_type` and:
  - `org_role = 'admin' if account_type == 'org' else 'teacher'` — use in the teacher INSERT (replace the hardcoded `'admin'`).
  - After the teacher row is returned, set `owner_teacher_id` on the org: `await conn.execute("UPDATE organizations SET owner_teacher_id=$1 WHERE id=$2", teacher_id, org_id)` (or include it in the org INSERT if the teacher id is known first — but it isn't, so a follow-up UPDATE in the same tx is correct).
  - Thread `account_type` through `teacher_signup` → `_create_teacher_signup_postgres_tx(..., account_type=body.account_type)`.

- [ ] **Step 5: Run — expect PASS.**

- [ ] **Step 6: Commit** `feat(auth): branch signup on account_type (solo=teacher+owner, org=admin+owner)`.

### Task 4: `is_billing_owner` + expose it on the profile

**Files:**
- Modify: `app/auth/scope.py` (add helper), `app/routers/auth.py` (the `/me`/profile/login payloads that the dashboard reads `is_solo` from today)
- Test: `tests/test_account_types_signup.py`

- [ ] **Step 1: Write the failing test** — a teacher whose `id == organizations.owner_teacher_id` is billing owner; an invited teacher is not.

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Implement** `is_billing_owner(teacher)` in `scope.py` (compare `teacher['id']` to the org's `owner_teacher_id`; superadmin → False/own rules). Add `is_billing_owner` to the same response dicts that currently carry `is_solo` (grep `is_solo` in `auth.py`/scope to find them).

- [ ] **Step 4: Run — PASS.**

- [ ] **Step 5: Commit** `feat(auth): is_billing_owner derived from org owner_teacher_id`.

### Task 5: Integration test (real Postgres) — end-to-end signup shapes

**Files:**
- Create: `integration_tests/test_signup_account_types_integration.py`

- [ ] **Step 1: Write** a test that POSTs both signup variants against the real PG (mirror an existing `integration_tests/*_integration.py` for setup) and asserts: solo → `teachers.org_role='teacher'` & `organizations.owner_teacher_id=teacher.id`; org → `org_role='admin'` & owner set; both create a `subscriptions` row.

- [ ] **Step 2: Run** `python3 -m pytest integration_tests/test_signup_account_types_integration.py -p no:randomly -q` — expect PASS (schema.sql already has the column from Task 1).

- [ ] **Step 3: Commit** `test(integration): solo vs org signup shapes`.

---

## Phase 2 — Dashboard: manager-only admin + owner-based billing

### Task 6: Gate authoring off `admin`, Billing off ownership; delete solo-downgrade

**Files:**
- Modify: `app/static/dashboard-app.js` (1231-1276 gating block), `app/static/dashboard.html` (tab `data-roles`)

- [ ] **Step 1: Read** `dashboard-app.js:1231-1276` and the tab list in `dashboard.html:341-362` so the exact role/tab wiring is clear.

- [ ] **Step 2: Implement** (no JS unit harness in repo for this — verify in a headless-Chrome harness per the project's established pattern, see Task 8):
  - Set `currentIsBillingOwner = !!(teacher && teacher.is_billing_owner)`; replace the `isBillingOwner = requested∈{admin,superadmin}` line (1261) with it.
  - **Delete** the solo-downgrade override (1244): `currentOrgRole = requested;` (honest role now).
  - Mark exam-authoring tabs (Questions, Tools, Review, Chat, plus the exam selector/New-Exam/Duplicate/Archive/Delete bar) to **hide when `currentOrgRole==='admin'`**. Live Sessions / Results / Student History / Analytics stay (admin oversight, read-only). Add a small helper that hides `[data-hide-for-admin]` elements when admin; tag the authoring tabs + exam-mgmt bar with `data-hide-for-admin`.
  - Ensure Members / Org Settings / Security show for `admin` (they already use `data-roles="admin"`).
  - Billing tab visibility → `currentIsBillingOwner` (not role).

- [ ] **Step 3: Commit** `feat(dashboard): manager-only admin view + owner-based billing; drop solo-downgrade`.

### Task 7: Server-side enforcement (defense-in-depth)

**Files:**
- Modify: the exam-authoring endpoints' auth (e.g. `require_admin`/teacher guards on create-exam, get-questions-as-teacher, roster tools)
- Test: `tests/test_account_types_signup.py`

- [ ] **Step 1: Write the failing test** — an `org_role='admin'` token calling an exam-authoring endpoint (e.g. POST create-exam) gets **403** (admins don't author).

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Implement** a guard `assert_can_author(teacher)` that 403s when `org_role=='admin'`, and apply it on the authoring endpoints. (UI hiding is not security.)

- [ ] **Step 4: Run — PASS.**

- [ ] **Step 5: Commit** `feat(auth): 403 exam-authoring for manager-only admins`.

---

## Phase 3 — Website signup UI + card wiring + verify

### Task 8: Signup choice on the marketing page

**Files:**
- Modify: the signup component under `website/src/` (find: `grep -rln "Start Your Free Trial\|/api/v1/auth/signup" website/src`)

- [ ] **Step 1:** Add a two-option control (Solo teacher / Organization) above the form with the spec copy ("admins manage teachers & billing — they don't run exams"). Default **solo**. Relabel/keep "Organization Name" (solo: "Your name or class"; org: "Institution name").

- [ ] **Step 2:** Include `account_type` in the POST body to `/api/v1/auth/signup`.

- [ ] **Step 3: Commit** `feat(web): solo vs organization choice on signup`.

### Task 9: Card-on-signup acceptance (both paths)

- [ ] **Step 1:** With `CARD_ON_SIGNUP_ENFORCED=1`, manually verify (or add an endpoint test) that BOTH solo and org signups land subscription `created` and hit the onboarding gate (billing owner), while an **invited teacher** never does. With the flag off, both get `trialing`.

- [ ] **Step 2: Commit** any fix needed so the gate keys off `is_billing_owner`.

### Task 10: Visual verification (headless Chrome harness)

- [ ] **Step 1:** Build a harness (per the project's established pattern — load real `tokens.css`/`theme.css`/`dashboard.css` + a stubbed `teacher` payload) rendering the dashboard tab bar for: solo teacher, org admin, invited teacher. Screenshot each.
- [ ] **Step 2:** Confirm: solo = teacher tabs + Billing, no Members; admin = Members/Billing/Org + read-only oversight, **no** Questions/Tools/Review/exam-bar; invited teacher = teacher tabs, no Billing/Members.

### Task 11: Existing-account backfill (test data)

- [ ] **Step 1:** Run the read-only count from the spec on prod to confirm the (test-only) landscape.
- [ ] **Step 2:** Write a one-time backfill SQL: set `organizations.owner_teacher_id` to the org's admin teacher for existing orgs; remap solo orgs' admin → `org_role='teacher'`. Since all are test accounts, resetting is acceptable. Put SQL in a standalone block for the user to run on prod (per their psql workflow).
- [ ] **Step 3: Commit** the backfill SQL under `migrations/` (or `scripts/`), documented as one-time.

---

## Out of scope (this plan)

- Admin-only **teacher reassign/offboarding** tool (separate later plan; admin-only; receiving teacher must already be an org member).
- Any self-serve solo → org conversion.

## Self-review notes

- Spec coverage: signup branching (T2-3), billing-owner decouple (T1,4), card-on-signup (T9), manager-only dashboard (T6) + server enforcement (T7), appeals stay with owning teacher (no change needed — T6 just doesn't expose admin resolve), website choice (T8), existing-account backfill (T11). ✓
- The `phaseNNN` number is resolved at execution (Task 1 step notes the command). Not a placeholder — it's an explicit lookup.
- `is_billing_owner` name used consistently in T4/T6/T9.
