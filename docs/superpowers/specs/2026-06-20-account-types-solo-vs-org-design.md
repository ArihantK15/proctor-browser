# Account Types: Solo Teacher vs Organization (manager-only admin) — Design

**Date:** 2026-06-20
**Status:** Approved (brainstorm), pre-implementation

## Problem

Today every signup creates a 1-person org with `org_role='admin'`, then the
dashboard **force-downgrades** a solo owner's UI role to `teacher`
(`dashboard-app.js:1244`) because `compute_is_solo` treats a ≤1-member org as
solo. The invite UI lives only in the `Members` tab, gated `data-roles="admin"`
(`dashboard.html:356`). So a solo owner:

- **Can never invite anyone** — the invite UI is hidden while solo, and they
  can't stop being solo without inviting. A hard **deadlock**; no self-signup
  account can become an organization.
- Is a **"super-teacher"** (admin who also teaches) once an org does form —
  which a real institution doesn't want (the exam-cell admin shouldn't author
  papers).

A mid-life "convert solo → org" flow is a trap: it requires migrating all of a
teacher's `teacher_id`-keyed data (exams, exam_config, questions, exam_sessions,
violations, answers, roster, appeals, analytics) to **another teacher account
that doesn't exist yet** (the invitee hasn't joined) — an unavoidable
chicken-and-egg with fragile half-migrated state.

## Decision

**Differentiate account type at signup. No mid-life conversion.** This dissolves
both problems: an org admin starts empty (a manager), teachers build their own
data under their own seats, so nothing is ever stranded or remapped; and an org
admin sees the invite UI from second one, so there is no deadlock.

Retire the super-teacher: **org admin is manager-only.**

## Account model

| | Solo teacher | Org admin | Invited teacher |
|---|---|---|---|
| `org_role` | `teacher` | `admin` | `teacher` |
| Owns billing | **Yes** (own 1-person org) | **Yes** (org subscription) | No |
| Create exams / manage students | **Yes** | **No** | **Yes** |
| Invite / remove teachers, transfer data | No | **Yes** | No |
| Org-wide read-only oversight | n/a | **Yes** | No |
| Dashboard tabs | teacher tabs + Billing | Members, Billing, Org Settings, oversight (read-only Results/Analytics) — **no exam-authoring tabs** | teacher tabs (no Billing/Members) |

`teacher` role covers both solo and invited teachers; the difference is **billing
ownership**, not role.

## Signup flow (`auth.py` `teacher_signup`)

Add an explicit choice: **"I'm a solo teacher"** vs **"I'm setting up an
organization,"** with clear copy ("admins manage teachers & billing — they don't
run exams themselves").

- **Solo** → create the 1-person org, `org_role='teacher'`, mark them the org
  owner (billing). Permanent teacher UI; no invite chrome ever.
- **Org** → `org_role='admin'` (manager-only), org owner (billing). Invite UI
  visible from day one; **no** exam-authoring surface.

## Billing-owner decoupling (the wrinkle to lock in planning)

Billing access is currently `isBillingOwner = org_role∈{admin,superadmin}`
(`dashboard-app.js:1261`). A solo teacher is `org_role='teacher'` but **owns
their billing** — so billing ownership must decouple from `org_role`.
**Recommended:** drive it off org ownership (e.g. `organizations.owner_teacher_id`
or an `is_owner` flag on the membership), set for both solo teachers and org
admins at signup. Lock the exact mechanism during planning.

## Card-on-signup — must work for BOTH variations

`CARD_ON_SIGNUP_ENFORCED` gates the **billing owner** behind the onboarding
card/mandate gate (subscription starts `created`, no entitlement, until a card is
added — `checkOnboardingGate`). Both signup variations create a billing owner, so
the gate must fire for **solo teacher** and **org admin** alike, and must **not**
fire for invited teachers (covered by the org subscription). This rides directly
on the billing-owner mechanism above — once that's correct, the existing gate
applies uniformly. **Acceptance:** test both signup paths with the flag ON (card
required) and OFF (free trial); invited-teacher signup never sees the gate.

## Manager-only admin dashboard

- **Hide** exam-authoring/teacher surfaces for `org_role='admin'`: exam
  create/duplicate/archive, Questions tab, roster/Tools authoring, Review
  (appeals are resolved by… see open question), Chat-as-teacher.
- **Show**: Members (invite/remove + reassign), Billing, Org Settings, and
  **org-wide read-only** Results/Analytics/Student History for oversight.
- Retire the solo-downgrade branch (`currentOrgRole` override) — role is now
  honest, set at signup.

## Teacher hand-off / reassign (admin-only — follow-on, not blocking)

Not needed for the signup model, but a real org need (a teacher **leaves** →
reassign their classes). Scope when we build it:

- **Admin-only.** Only an org admin can add/remove teachers and transfer a
  teacher's data. (Per user.)
- **Target must already be a member of the organization** — surfaced as a clear
  UI line ("the receiving teacher must already have joined your organization").
- Mechanics: a transactional remap of `teacher_id` A→B across every owned table,
  scoped within the org (RLS-safe, admin-elevated). Idempotent + audited.

## Tradeoffs / out of scope

- **No self-serve solo → org upgrade.** A solo teacher who later wants an
  institution starts an org account fresh (old data stays on the solo account; a
  one-off support migration if truly needed). Accepted: rare, far safer than a
  conversion+migration for everyone.
- No automatic data migration anywhere in the core flow.

## Decisions (resolved)

1. **Billing-owner mechanism** — planning to pick `owner_teacher_id` vs `is_owner`
   flag (my call during planning; must satisfy the card-on-signup acceptance).
2. **Appeals / flag review stay with the owning teacher.** RESOLVED — no change
   to the resolution path; the admin gets **read-only oversight only**, never an
   appeal-resolve action.
3. **Teachers self-serve exams; the admin only oversees.** RESOLVED — no
   admin→teacher exam assignment.
4. **Existing-account migration is low-risk — all current accounts are test
   accounts.** RESOLVED — remap solo owners to the solo-teacher shape; any test
   multi-teacher orgs can be reset/grandfathered freely. Run a read-only sanity
   count first (below), but there is no production data at risk.

Sanity count to run on prod before the backfill (read-only):

```sql
SELECT o.id,
       COUNT(t.id) FILTER (WHERE t.org_role = 'admin')   AS admins,
       COUNT(t.id) FILTER (WHERE t.org_role = 'teacher') AS teachers,
       COUNT(t.id)                                       AS members
FROM organizations o
JOIN teachers t ON t.org_id = o.id
GROUP BY o.id
HAVING COUNT(t.id) > 1
ORDER BY members DESC;
```

## Build order

1. Billing-owner decoupling + signup `account_type` (backend + the org/teacher
   role shape) + existing-account backfill.
2. Signup UI choice + copy.
3. Manager-only admin dashboard gating (hide authoring, show oversight); retire
   solo-downgrade.
4. (Later) admin-only teacher reassign/offboarding tool.
