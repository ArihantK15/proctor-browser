# Make Tenancy Real (Server) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish enforcing the existing `org_role` scope spine server-side so org-admin read views return correct org-wide data and cross-tenant leaks are impossible, with a dedicated isolation test as the regression guard.

**Architecture:** The scope spine already exists — `app/auth/scope.py` (`resolve_scope` → dict, `scope_to_teacher_ids` → list of in-org teacher IDs or None, `assert_session_accessible` → single-session 404-guard) — and is already wired into `admin_sessions/students/exams/verification`. The codebase resolves admin org-reads by **joining through `teachers` to materialise member `teacher_id`s** (no `org_id` backfill on hot tables — this resolves the spec's biggest open question and removes the riskiest migration). This plan: (1) lands the load-bearing isolation test FIRST, then (2) closes the three remaining raw-`teacher_id` read routers (`admin_scorecards`, `admin_liveview`, `admin_media`) by routing them through the same spine, keeping all writes owner-scoped.

**Tech Stack:** FastAPI, asyncpg (plain Postgres; NOT Supabase as DB — Supabase is Auth only), pytest + TestClient, the `_atable()` async query interface, existing `tests/conftest.py` harness (`shared_supabase_mock`, `make_admin_token`).

**Scope note:** This is the SERVER half of the approved spec (`docs/superpowers/specs/2026-06-07-dashboard-trim-tenancy-design.md`). The dashboard UI trim (solo-mode chrome, teacher picker, superadmin-tab removal) is a separate, dependent follow-up plan and is OUT OF SCOPE here.

**Process constraints (HARD RULES):**
- **Self-Review Before Commit** on every changed file: re-read it, audit syntax/runtime/config/cross-ref/auth/failure, and STATE findings in the response BEFORE any commit.
- **The user commits, not the agent.** Do not run `git commit`/`git push`. The "Commit" steps below describe the commit the *user* will make; prepare the change and stop.
- Tenant-isolation test (Task 1) MUST be green before any read-widening (Tasks 2–4) ships.

---

## File structure

- `tests/test_tenant_isolation.py` — **new.** The load-bearing safety net: unit tests over the `scope.py` helpers (the single audit point) + a cross-org 404 assertion. Lives with the other tenancy tests (`test_student_tenancy_boundaries.py`, `test_org_billing.py`).
- `app/repositories/sessions.py` — **modify.** Add `teacher_ids` org-scope support to `stream_csv_results` (it currently only accepts `teacher_id`; `fetch_all_results` already has the pattern to copy).
- `app/routers/admin_scorecards.py` — **modify.** Route the export/zip read paths through `resolve_scope` + `scope_to_teacher_ids`; route per-session PDF paths through `assert_session_accessible`. Keep writes (`email-scorecards` claim/update) owner-scoped.
- `app/routers/admin_liveview.py` — **modify.** Route live-session discovery reads through the scope.
- `app/routers/admin_media.py` — **modify.** Gate per-session media access through `assert_session_accessible`.

---

## Task 1: Tenant-isolation safety net (lands FIRST)

**Files:**
- Create: `tests/test_tenant_isolation.py`

This tests the single audit point (`app/auth/scope.py`) directly — the highest-leverage guard, and groundable without mocking every endpoint. It encodes the access rule from the spec: teacher → self only; admin → own org only, never another org; superadmin → unrestricted.

- [ ] **Step 1: Write the failing test file**

```python
"""Tenant-isolation regression guard for the org-scope spine.

These exercise app/auth/scope.py — the single point every admin read
path funnels through — with two orgs (A, B) and assert that an admin in
org A can never resolve to a teacher in org B. This is the existential
data-leak guard for a B2B exam product; it MUST stay green before any
admin read path widens from teacher_id to org scope.
"""

from __future__ import annotations

import pytest

import app.auth.scope as scope_mod


class _ScopeDB:
    """Minimal _atable() stub backed by an in-memory teachers table.

    Supports the two query shapes scope.py issues:
      • _verify_teacher_in_org: .select("id").eq("id",X).eq("org_id",Y).limit(1)
      • scope_to_teacher_ids:   .select("id").eq("org_id",Y)
    """

    def __init__(self, teachers: list[dict]):
        self._teachers = teachers

    def __call__(self, table_name: str):
        assert table_name == "teachers", f"unexpected table {table_name}"
        return _ScopeChain(self._teachers)


class _ScopeChain:
    def __init__(self, teachers: list[dict]):
        self._rows = teachers
        self._eqs: dict[str, str] = {}

    def select(self, *a, **kw):
        return self

    def eq(self, col, val):
        self._eqs[col] = str(val)
        return self

    def limit(self, n):
        return self

    async def execute(self):
        rows = [
            {"id": t["id"]}
            for t in self._rows
            if all(str(t.get(c)) == v for c, v in self._eqs.items())
        ]
        return type("R", (), {"data": rows})()


class _Req:
    """Stub Request exposing only .query_params.get used by resolve_scope."""

    def __init__(self, teacher_id: str | None = None):
        self._params = {"teacher_id": teacher_id} if teacher_id else {}

    @property
    def query_params(self):
        return _Params(self._params)


class _Params:
    def __init__(self, d):
        self._d = d

    def get(self, key, default=""):
        return self._d.get(key, default)


# Two orgs: A has teachers a1,a2 ; B has teacher b1.
TEACHERS = [
    {"id": "a1", "org_id": "orgA", "org_role": "admin"},
    {"id": "a2", "org_id": "orgA", "org_role": "teacher"},
    {"id": "b1", "org_id": "orgB", "org_role": "admin"},
]


@pytest.fixture
def patched_db(monkeypatch):
    monkeypatch.setattr(scope_mod, "_atable", _ScopeDB(TEACHERS))


@pytest.mark.asyncio
async def test_plain_teacher_locked_to_self(patched_db):
    teacher = {"id": "a2", "org_id": "orgA", "org_role": "teacher"}
    scope = await scope_mod.resolve_scope(teacher, _Req())
    tids = await scope_mod.scope_to_teacher_ids(scope)
    assert tids == ["a2"]


@pytest.mark.asyncio
async def test_teacher_cannot_widen_via_query_param(patched_db):
    """A plain teacher passing ?teacher_id=a1 is ignored, not honored."""
    teacher = {"id": "a2", "org_id": "orgA", "org_role": "teacher"}
    scope = await scope_mod.resolve_scope(teacher, _Req(teacher_id="a1"))
    tids = await scope_mod.scope_to_teacher_ids(scope)
    assert tids == ["a2"]


@pytest.mark.asyncio
async def test_admin_sees_whole_own_org(patched_db):
    teacher = {"id": "a1", "org_id": "orgA", "org_role": "admin"}
    scope = await scope_mod.resolve_scope(teacher, _Req())
    tids = await scope_mod.scope_to_teacher_ids(scope)
    assert set(tids) == {"a1", "a2"}
    assert "b1" not in tids


@pytest.mark.asyncio
async def test_admin_cannot_target_other_org_teacher(patched_db):
    """Admin in A passing ?teacher_id=b1 (org B) is silently dropped and
    falls back to org-A-wide — never resolves to b1."""
    teacher = {"id": "a1", "org_id": "orgA", "org_role": "admin"}
    scope = await scope_mod.resolve_scope(teacher, _Req(teacher_id="b1"))
    tids = await scope_mod.scope_to_teacher_ids(scope)
    assert "b1" not in tids
    assert set(tids) == {"a1", "a2"}


@pytest.mark.asyncio
async def test_admin_can_narrow_to_own_org_teacher(patched_db):
    teacher = {"id": "a1", "org_id": "orgA", "org_role": "admin"}
    scope = await scope_mod.resolve_scope(teacher, _Req(teacher_id="a2"))
    tids = await scope_mod.scope_to_teacher_ids(scope)
    assert tids == ["a2"]


@pytest.mark.asyncio
async def test_superadmin_unrestricted(patched_db):
    teacher = {"id": "x", "org_id": None, "org_role": "superadmin"}
    scope = await scope_mod.resolve_scope(teacher, _Req())
    tids = await scope_mod.scope_to_teacher_ids(scope)
    assert tids is None  # None == "no filter"
```

- [ ] **Step 2: Run the test, expect it to PASS against the existing spine**

Run: `cd /Users/arihantkaul/proctored-browser && python3 -m pytest tests/test_tenant_isolation.py -v`
Expected: all 6 PASS. (`scope.py` already implements this rule; this test pins it so the read-widening tasks can't regress it.)

If `@pytest.mark.asyncio` errors with "asyncio not installed", check the repo's existing async tests for the marker style — `grep -rn "pytest.mark.asyncio\|asyncio_mode" tests/ pyproject.toml setup.cfg pytest.ini tox.ini` — and match whatever is already configured (e.g. `asyncio_mode=auto` means drop the decorators).

- [ ] **Step 3: (user) Commit**

```bash
git add tests/test_tenant_isolation.py
git commit -m "test: tenant-isolation guard over the org-scope spine"
```

---

## Task 2: Org-widen the scorecard/export read paths (`admin_scorecards.py`)

The export endpoints (`export-csv` :41, `export-excel` :51, `scorecard-zip` :471) pass only `teacher["id"]`, so an org admin's exports silently omit other teachers' results. Per-session PDF endpoints (`export-pdf` :343, `scorecard-pdf` :454) gate on raw `teacher_id`, so an admin can't pull a co-teacher's scorecard. Route reads through the spine; keep `email-scorecards` (the write/claim path) owner-scoped.

**Files:**
- Modify: `app/repositories/sessions.py` (`stream_csv_results`)
- Modify: `app/routers/admin_scorecards.py`

- [ ] **Step 1: Add `teacher_ids` support to `stream_csv_results`** (mirror `fetch_all_results`)

In `app/repositories/sessions.py`, change the signature and the filter block of `stream_csv_results` (currently `:131`). Replace:

```python
async def stream_csv_results(teacher_id: str = None, exam_id: str = None, max_rows: int = 5000):
```
with:
```python
async def stream_csv_results(teacher_id: str = None, exam_id: str = None, max_rows: int = 5000,
                             teacher_ids: list[str] | None = None):
```

And inside the `while` loop replace the filter block:
```python
            if teacher_id:
                query = query.eq("teacher_id", teacher_id)
            if exam_id:
                query = query.eq("exam_id", exam_id)
```
with (same precedence rule as `fetch_all_results`: `teacher_ids` > `teacher_id`):
```python
            if teacher_ids is not None:
                if not teacher_ids:
                    query = query.eq("teacher_id", "__none__")
                elif len(teacher_ids) == 1:
                    query = query.eq("teacher_id", str(teacher_ids[0]))
                else:
                    query = query.in_("teacher_id", teacher_ids)
            elif teacher_id:
                query = query.eq("teacher_id", teacher_id)
            if exam_id:
                query = query.eq("exam_id", exam_id)
```

- [ ] **Step 2: Write failing tests for the widened exports**

Add to a new `tests/test_scorecards_scope.py`:

```python
"""Admin org-scope coverage for the scorecard export read paths."""
from __future__ import annotations

from unittest.mock import patch

import app.repositories.sessions as sess_mod


class _CaptureQuery:
    """Records which teacher-filter was applied; returns no rows."""
    captured = {}

    def select(self, *a, **kw): return self
    def in_(self, col, vals):
        if col == "teacher_id":
            _CaptureQuery.captured["in"] = list(vals)
        return self
    def eq(self, col, val):
        if col == "teacher_id":
            _CaptureQuery.captured.setdefault("eq", []).append(str(val))
        return self
    def order(self, *a, **kw): return self
    def range(self, *a, **kw): return self
    def limit(self, *a, **kw): return self
    async def execute(self):
        return type("R", (), {"data": []})()


def test_stream_csv_uses_in_for_multi_teacher_org(monkeypatch):
    _CaptureQuery.captured = {}
    monkeypatch.setattr(sess_mod, "_atable", lambda t: _CaptureQuery())
    import asyncio
    async def run():
        gen = sess_mod.stream_csv_results(teacher_ids=["a1", "a2"])
        async for _ in gen:
            pass
    asyncio.run(run())
    assert _CaptureQuery.captured.get("in") == ["a1", "a2"]
```

Run: `cd /Users/arihantkaul/proctored-browser && python3 -m pytest tests/test_scorecards_scope.py -v`
Expected: PASS once Step 1 is in (the test depends only on Step 1).

- [ ] **Step 3: Route the export endpoints through the scope**

In `app/routers/admin_scorecards.py`, add the import near the top (with the other `..auth`/`..database` imports):
```python
from ..auth.scope import resolve_scope, scope_to_teacher_ids, assert_session_accessible
```

`export_csv` (:41) — change the handler body so it resolves scope and passes `teacher_ids`:
```python
async def export_csv(request: Request, exam_id: str = None):
    teacher = await require_admin(request)   # keep the existing auth call as-is
    scope = await resolve_scope(teacher, request)
    tids = await scope_to_teacher_ids(scope)
    return StreamingResponse(
        _stream_csv_results(teacher["id"], exam_id=exam_id, max_rows=5000, teacher_ids=tids),
        # ...keep the existing media_type / headers args unchanged...
    )
```
> Read the real handler first; preserve its exact `require_admin` call, `StreamingResponse` media-type and `Content-Disposition` headers. Only add `scope`/`tids` and the `teacher_ids=tids` kwarg.

`export_excel` (:51) — change:
```python
    results = await _fetch_all_results(teacher["id"], exam_id=exam_id)
```
to:
```python
    scope = await resolve_scope(teacher, request)
    tids = await scope_to_teacher_ids(scope)
    results = await _fetch_all_results(teacher["id"], exam_id=exam_id, teacher_ids=tids)
```

`scorecard_zip` (:471) — the `sess_q` (:481) hard-codes `.eq("teacher_id", str(tid))`. Replace that single `.eq` with a scope-aware filter:
```python
    scope = await resolve_scope(teacher, request)
    tids = await scope_to_teacher_ids(scope)
    sess_q = _atable("exam_sessions")\
        .select(...)\
        .in_("status", [SessionStatus.COMPLETED, SessionStatus.FORCE_SUBMITTED])
    if tids is not None:
        if len(tids) == 1:
            sess_q = sess_q.eq("teacher_id", str(tids[0]))
        else:
            sess_q = sess_q.in_("teacher_id", tids)
    # else superadmin: no teacher filter
```
> Keep the `.select(...)` column list exactly as in the file. The per-session answer/question loads inside the zip (`_load_questions(teacher_id=tid, ...)`, `answers ... .eq("teacher_id", str(tid))` at :503) must use **the row's own `teacher_id`**, not the caller's — read each session's `teacher_id` from the row and pass that. This is the subtle correctness fix: in a multi-teacher org the zip iterates sessions across teachers, so answers must be loaded per-row-owner.

- [ ] **Step 4: Route per-session PDF endpoints through `assert_session_accessible`**

`export_pdf` (:343) and `scorecard_pdf` (:454) currently use `tid = teacher["id"]` and load by that tid. Replace the implicit ownership with an explicit scope check at the top of each handler:
```python
    scope = await resolve_scope(teacher, request)
    sess = await assert_session_accessible(session_id, scope)   # 404s cross-tenant
    tid = str(sess["teacher_id"])   # load the session's OWNER tid, not the caller's
```
Then use that `tid` for the existing `_pdf_fetch_violations(session_id, tid)`, `_pdf_fetch_answers(session_id, tid)`, `_load_questions(teacher_id=tid, ...)`, `compute_risk_score(session_id, teacher_id=tid)` calls — so an admin pulling a co-teacher's PDF reads that teacher's rows, while a cross-org request 404s before any data load.

- [ ] **Step 5: Run the scope test + the full scorecards-related suite**

Run: `cd /Users/arihantkaul/proctored-browser && python3 -m pytest tests/test_scorecards_scope.py tests/test_tenant_isolation.py -v && python3 -m pytest tests/ -k "scorecard or export or csv" -v`
Expected: PASS. The isolation test (Task 1) must remain green.

- [ ] **Step 6: Self-review, then (user) commit**

Re-read `app/repositories/sessions.py` and `app/routers/admin_scorecards.py`; verify: the per-row-owner `tid` for zip/pdf (not caller tid), `StreamingResponse` headers intact, `email-scorecards` write path still `.eq("teacher_id", tid)`. State findings, then the user commits:
```bash
git add app/repositories/sessions.py app/routers/admin_scorecards.py tests/test_scorecards_scope.py
git commit -m "feat(tenancy): org-scope scorecard exports + per-session PDF access checks"
```

---

## Task 3: Org-widen live-view discovery (`admin_liveview.py`)

Live monitoring should show an admin all in-progress sessions across their org's teachers; today it filters to the caller's own `teacher_id` (17 refs). Per-session liveview actions must 404 cross-tenant.

**Files:**
- Modify: `app/routers/admin_liveview.py`

- [ ] **Step 1: Read the router and classify each `teacher_id` use**

Run: `cd /Users/arihantkaul/proctored-browser && grep -n "teacher_id\|teacher\[.id.\]\|def \|_atable\|session_key" app/routers/admin_liveview.py`
For each: a **list/discovery read** (in-progress sessions) → widen via `scope_to_teacher_ids`; a **single-session** action (open one student's live feed) → guard via `assert_session_accessible`; a **write** (none expected here) → leave owner-scoped.

- [ ] **Step 2: Add the import**
```python
from ..auth.scope import resolve_scope, scope_to_teacher_ids, assert_session_accessible
```

- [ ] **Step 3: Widen the discovery query**

For the list endpoint, replace the `.eq("teacher_id", str(teacher["id"]))` on the in-progress sessions query with the same scope-aware block used in Task 2 Step 3:
```python
    scope = await resolve_scope(teacher, request)
    tids = await scope_to_teacher_ids(scope)
    # ...build the in-progress query, then:
    if tids is not None:
        if len(tids) == 1:
            q = q.eq("teacher_id", str(tids[0]))
        else:
            q = q.in_("teacher_id", tids)
```

- [ ] **Step 4: Guard per-session endpoints**

For any endpoint taking a `session_id`/`session_key` path param, add at the top:
```python
    scope = await resolve_scope(teacher, request)
    await assert_session_accessible(session_id, scope)
```

- [ ] **Step 5: Write a cross-tenant 404 test**

Add `tests/test_liveview_scope.py` modeled on the existing `test_cross_tenant_access_is_denied` in `tests/test_forensics_timeline.py:205` (same `shared_supabase_mock` + stub + `admin_headers` pattern): assert a live-session endpoint for a session owned by `teacher-OTHER` (different org) returns 404.

Run: `cd /Users/arihantkaul/proctored-browser && python3 -m pytest tests/test_liveview_scope.py -v`
Expected: PASS.

- [ ] **Step 6: Self-review, then (user) commit**

Re-read `app/routers/admin_liveview.py`; confirm no write path widened and every session-path endpoint is guarded. State findings, then:
```bash
git add app/routers/admin_liveview.py tests/test_liveview_scope.py
git commit -m "feat(tenancy): org-scope live-view discovery + per-session guards"
```

---

## Task 4: Media access — RESOLVED, no change required (finding)

**Original premise (wrong):** the plan assumed `admin_media.py` had session-keyed media endpoints (`/screenshot/{session_id}/...`) needing `assert_session_accessible` gating.

**Actual reality (verified by reading the file):** there are no session-keyed media routes, and **no cross-tenant media leak is possible** — every media path is namespaced by the *authenticated caller's own* `teacher_id` plus a path-traversal guard:
- `get_screenshot` (`/api/v1/admin/screenshot/{roll}/{filename}`): `tid = str(teacher["id"])` from `require_admin`; serves only from `SCREENSHOTS_DIR/{caller_tid}/{roll}/{file}` with `_assert_within_directory(fpath, SCREENSHOTS_DIR/tid)`. A caller cannot reach another teacher's directory. No `session_id` exists to gate.
- `get_question_image` (`/api/v1/question-image/{tid}/{filename}`): explicit `token.id == tid` check (admin or student) + traversal guard — leak-safe.
- `upload_question_image`: write, owner-scoped (`tid = caller`).

**Disposition:** Task 4's security objective (no cross-tenant media leak) is **already satisfied** by per-caller-tid disk namespacing. No server change is made here; the planned guard is structurally impossible without inventing a `session_id` route param.

**Deferred to the follow-up UI plan (not a leak — a feature gap):** an org admin currently *cannot* view a co-teacher's screenshots, because `get_screenshot` hardcodes the caller's tid. Letting an admin view in-org teachers' media for the roll-up requires changing the screenshot route to carry the owner tid / `session_id`, updating the URL builders in `app/routers/admin.py` and `app/routers/admin_verification.py`, gating the new route with `assert_session_accessible`, and updating the dashboard. This is UI-roll-up scope; added to the follow-up plan below.

- [x] No code change. Finding recorded; functionality gap moved to the follow-up plan.

---

## Task 5: Full-suite regression + audit sweep

**Files:** none (verification only)

- [ ] **Step 1: Run the whole test suite**

Run: `cd /Users/arihantkaul/proctored-browser && python3 -m pytest tests/ -q`
Expected: no new failures vs. the pre-change baseline. The isolation test stays green.

- [ ] **Step 2: Confirm no data router still reads raw `teacher["id"]` on a list path**

Run: `cd /Users/arihantkaul/proctored-browser && for f in app/routers/admin_scorecards.py app/routers/admin_liveview.py app/routers/admin_media.py; do echo "== $f =="; grep -n 'eq("teacher_id"' "$f"; done`
Expected: remaining `.eq("teacher_id", ...)` are only on **writes** (e.g. `email-scorecards` claim/update) or per-row-owner loads — not on list/discovery reads. Document any survivors with a one-line justification.

- [ ] **Step 3: Run the existing tenancy audit script if present**

Run: `cd /Users/arihantkaul/proctored-browser && ls scripts/audit_tenancy.py 2>/dev/null && echo "audit script present (DB-connected; run per runbook)" || echo "no audit script"`
(Informational — the script needs a live `DATABASE_URL` per `docs/archive/TENANCY_HARDENING_RUNBOOK.md`; do not run against prod from here.)

---

## Self-Review (plan vs. spec)

- **Spec §A "Centralize the access predicate":** Already centralized in `app/auth/scope.py` (predates this plan). Tasks 2–4 finish routing the last three read routers through it. The spec's `AccessScope` value object is realized as the existing `scope` dict + `scope_to_teacher_ids`; no new abstraction needed (YAGNI). ✓
- **Spec §A "Tables that carry data need org_id":** Resolved by the existing join-through-`teachers` approach (`scope_to_teacher_ids`). No `org_id` backfill migration — eliminates the spec's "riskiest step." This is a deliberate, grounded deviation from the spec's "backfill preferred" lean, justified by the code already shipping the join approach. ✓
- **Spec §"Verification V1 (tenant isolation)":** Task 1, lands first. ✓
- **Spec §"Verification V2 (admin roll-up correctness)":** Tasks 2–4 prove org-reads return cross-teacher data; the scorecards/liveview/media tests cover it. ✓
- **Spec §B (two-mode UI), §C (pull superadmin tooling):** OUT OF SCOPE — deferred to the follow-up dashboard-trim plan (these are UI-layer and depend on this server work being correct first). Flagged, not dropped. ✓
- **Writes stay owner-scoped:** Enforced in every task (email-scorecards claim, no write widened). ✓
- **No placeholders / type consistency:** `teacher_ids` kwarg signature matches `fetch_all_results`; `scope_to_teacher_ids` returns `list[str] | None` consistently; `assert_session_accessible` returns the session dict used for the owner `tid`. ✓

---

## Follow-up (separate plan, sequenced after this)

`docs/superpowers/specs/2026-06-07-dashboard-trim-tenancy-design.md` §B and §C — the dashboard UI: compute `is_solo` (org member count == 1) in the profile payload, make `applyOrgRole()` (`dashboard-app.js:1110`) honor it to hide all org/admin chrome for solo accounts, add the admin teacher-picker read context, and remove the `all-orgs`/`issues`/`debug` superadmin tabs from `dashboard.html` + their handlers. Write this as its own plan once the server scope here is shipped and verified.

**Carried over from Task 4 (admin screenshot roll-up):** let an org admin view an in-org co-teacher's screenshots. `get_screenshot` in `app/routers/admin_media.py` currently hardcodes the caller's own `teacher_id` in the disk path, so an admin viewing a co-teacher's session timeline gets 404s on the thumbnails. Fix requires: add the owner `teacher_id` (or a `session_id`) to the `/api/v1/admin/screenshot/...` route, gate the new route with `assert_session_accessible` (resolve owner tid from the in-scope session), and update the screenshot URL builders in `app/routers/admin.py` (~:111) and `app/routers/admin_verification.py` (~:63-65) plus the dashboard renderer. This is a read-widening with a cross-file + frontend surface, so it belongs with the UI roll-up work — NOT a security gap (no leak exists today; the path is namespaced by the caller's own tid).
