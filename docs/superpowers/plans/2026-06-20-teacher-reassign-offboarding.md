# Teacher Reassign / Offboarding Tool — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Steps use `- [ ]`.

**Goal:** Admin-only tool to transfer all of one teacher's teaching data to another teacher in the same org (offboarding a departing teacher / consolidating). No account deletion — just a `teacher_id` remap.

**Architecture:** A transactional `teacher_id` A→B remap across the *teaching-data* tables only (exams, sessions, answers, students, questions, …), leaving account-identity/audit rows (auth_events, admin_audit_log, api_keys, google_auth_tokens) bound to the original account. Admin-only, both teachers must belong to the calling admin's org. Audited + idempotent.

**Tech Stack:** FastAPI + asyncpg transaction, RLS (writes need system-context elevation), phase-numbered migration only if a helper table is needed (not expected). Dashboard Members tab (admin-only). pytest + integration_tests (real PG).

**Spec:** `docs/superpowers/specs/2026-06-20-account-types-solo-vs-org-design.md` (the "Teacher hand-off / reassign" section).

---

## The table classification (the heart of this feature)

**MOVE (teaching data — remap teacher_id A→B):**
`exam_config`, `exam_sessions`, `answers`, `violations`, `questions`,
`question_bank`, `question_versions`, `students`, `student_groups`,
`student_group_members`, `student_invites`, `exam_batch_assignments`,
`exam_group_assignments`, `exam_templates`, `exam_time_extensions`, `appeals`,
`grading_audit`, `invite_send_counters`, `google_classroom_links`.

**KEEP (account-identity / audit — do NOT move):**
`auth_events` (the original's login history), `admin_audit_log` (actions *by*
that account — historical record), `api_keys` (account-bound), `google_auth_tokens`
(OAuth bound to the account), `google_oauth_states` (transient), `issues`
(reported by the account). `organizations.owner_teacher_id` is billing ownership —
**never** touched by a teaching-data transfer.

> Lock this list during planning by re-running, against prod columns.json:
> `python3 -c "import json;d=json.load(open('schema/columns.json'));print([t for t,c in d.items() if 'teacher_id' in c])"`
> and classifying any table not listed above before shipping.

## Conflict risk (must decide)

`exam_config` has `UNIQUE(teacher_id, exam_id)` and `students` is effectively
unique per `(teacher_id, roll_number)`. If teacher B already owns a row that
would collide after remap, the UPDATE violates the constraint and the whole
transaction rolls back. **v1 decision:** let it roll back and return a clear
409 ("teacher B already has an exam/student that conflicts — resolve before
transferring"). A merge strategy is out of scope for v1.

---

## File map

- **Create** `app/services/teacher_transfer.py` — `reassign_teaching_data(conn, from_id, to_id) -> dict` (the transactional remap; returns per-table row counts).
- **Modify** `app/routers/admin_org.py` (or a new `app/routers/admin_teachers.py`) — `POST /api/v1/admin/teachers/{from_id}/reassign` endpoint.
- **Modify** `app/static/dashboard.html` + `dashboard-app.js` — a "Transfer data / Offboard" action in the **Members** tab (admin-only), with the clear in-org requirement line.
- **Tests:** `tests/test_teacher_transfer.py` (endpoint authz + the move/keep classification via mocks), `integration_tests/test_teacher_transfer_integration.py` (real-PG remap moves teaching rows, leaves identity rows).

---

## Phase 1 — the remap service (TDD)

### Task 1: `reassign_teaching_data` service

**Files:** Create `app/services/teacher_transfer.py`; Test `tests/test_teacher_transfer.py`

- [ ] **Step 1: Write the failing test** — given a fake asyncpg `conn` recording `execute` calls, `reassign_teaching_data(conn,'A','B')` issues one `UPDATE <table> SET teacher_id='B' WHERE teacher_id='A'` per MOVE table and **none** for any KEEP table.

```python
def test_reassign_updates_only_teaching_tables():
    calls = []
    class _Conn:
        async def execute(self, sql, *a):
            calls.append(sql); return "UPDATE 1"
    import asyncio
    from app.services.teacher_transfer import reassign_teaching_data, _MOVE_TABLES, _KEEP_TABLES
    asyncio.get_event_loop().run_until_complete(reassign_teaching_data(_Conn(), "A", "B"))
    moved = {t for t in _MOVE_TABLES if any(f"UPDATE {t} " in c for c in calls)}
    assert moved == set(_MOVE_TABLES)
    for t in _KEEP_TABLES:
        assert not any(f"UPDATE {t} " in c for c in calls), f"{t} must NOT be moved"
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement.** Define `_MOVE_TABLES` (the list above) + `_KEEP_TABLES` (documentation/guard). The remap (note: table names are a hardcoded allowlist constant — never interpolate caller input — so the f-string is safe; add `# nosemgrep: asyncpg-sqli`):

```python
_MOVE_TABLES = ("exam_config","exam_sessions","answers","violations","questions",
    "question_bank","question_versions","students","student_groups",
    "student_group_members","student_invites","exam_batch_assignments",
    "exam_group_assignments","exam_templates","exam_time_extensions","appeals",
    "grading_audit","invite_send_counters","google_classroom_links")
_KEEP_TABLES = ("auth_events","admin_audit_log","api_keys","google_auth_tokens",
    "google_oauth_states","issues")

async def reassign_teaching_data(conn, from_id: str, to_id: str) -> dict:
    """Remap teacher_id from_id -> to_id across teaching-data tables ONLY.
    Caller MUST wrap this in a transaction and have already authorised that both
    teachers are in the same org. Returns {table: rows_moved}."""
    counts = {}
    for table in _MOVE_TABLES:
        # nosemgrep: asyncpg-sqli  (table is from the hardcoded allowlist above)
        tag = await conn.execute(
            f"UPDATE {table} SET teacher_id = $1 WHERE teacher_id = $2", to_id, from_id)
        counts[table] = int(tag.split()[-1]) if tag and tag.startswith("UPDATE") else 0
    return counts
```

- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `feat(transfer): teaching-data teacher_id remap service`.

## Phase 2 — endpoint (authz + transaction)

### Task 2: `POST /admin/teachers/{from_id}/reassign`

**Files:** Modify a router; Test `tests/test_teacher_transfer.py`

- [ ] **Step 1: Write failing tests** (mock `_atable`/pool like `tests/test_privacy_appeals.py`):
  - non-admin (`org_role='teacher'`) → **403**.
  - admin, `to_teacher_id` NOT in the admin's org → **400/404** ("receiving teacher must already be in your organization").
  - `from_id == to_id` → **400**.
  - happy path → **200** with the per-table counts; `reassign_teaching_data` called inside a transaction.

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement.** `teacher = await require_admin(request)`; 403 unless `org_role in ('admin','superadmin')`; load both teachers, assert **both** `org_id == teacher['org_id']`; reject self-transfer; then in `async with conn.transaction():` call `reassign_teaching_data`; write an `admin_audit_log` row (`action='reassign_teacher'`, before/after = {from,to,counts}); return counts. Reuse the postgres pool the signup tx uses.

- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `feat(admin): admin-only teacher reassign endpoint`.

### Task 3: Integration test (real Postgres)

**Files:** Create `integration_tests/test_teacher_transfer_integration.py`

- [ ] **Step 1:** Seed org + teacher A (with an exam_config + a student + a violation) + teacher B; call the service in a tx; assert those rows now have `teacher_id=B`, and an `auth_events` row for A (seed one) still has `teacher_id=A`.
- [ ] **Step 2:** Run `python3 -m pytest integration_tests/test_teacher_transfer_integration.py -p no:randomly -q`. If any MOVE table is missing from `integration_tests/schema.sql`, ADD it there (+ `schema/columns.json` if a new ref) — same CI guard as before.
- [ ] **Step 3: Commit** `test(integration): teacher reassign moves teaching data, keeps identity`.

## Phase 3 — dashboard (Members tab, admin-only)

### Task 4: Transfer UI

**Files:** Modify `app/static/dashboard.html` (Members panel), `dashboard-app.js`

- [ ] **Step 1:** In the Members list (admin-only — already `data-roles="admin"`), add a per-teacher "Transfer data / Offboard" button. On click, open a confirm modal: a target-teacher `<select>` populated from the org's **existing** members (the receiving teacher must already be in the org — state this in the modal copy), and a clear warning ("This moves all of X's exams, students, sessions and analytics to the selected teacher. It can't be undone.").
- [ ] **Step 2:** On confirm → `authFetch POST /api/v1/admin/teachers/{from}/reassign {to_teacher_id}` → on success show the moved-row summary + reload Members.
- [ ] **Step 3:** CSP-safe (data-action delegation, no inline JS). `node --check`.
- [ ] **Step 4: Commit** `feat(dashboard): admin teacher reassign/offboard UI in Members`.

## Phase 4 — verify

- [ ] Full suite `python3 -m pytest tests/ -q` green; integration green in CI.
- [ ] Headless-Chrome render of the Members transfer modal (admin payload) to confirm it shows + the in-org target list.

## Out of scope (v1)

- Merge strategy on `(teacher_id, exam_id)` / `(teacher_id, roll_number)` conflicts (v1 = 409, resolve manually).
- Deleting/deactivating the offboarded teacher account (separate action; this only moves data).
- Moving account-identity/audit rows.

## Self-review notes

- Spec coverage: admin-only ✓ (T2), target-in-org ✓ (T2 + T4 copy), transactional remap ✓ (T1/T2), audit ✓ (T2). Move/keep classification is the central artifact (T1).
- `_MOVE_TABLES`/`_KEEP_TABLES` names used consistently across T1–T3.
- The one open call flagged inline: conflict handling = 409 in v1.
