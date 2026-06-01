# Tenancy Hardening Runbook

Apply order for the multi-tenant isolation work shipped in this audit
series. Each step is idempotent; skipping a step is safe but reduces
the level of protection.

## 1. Audit existing data first

Run the data-audit script against a **read replica** (or in a transaction
you'll roll back). It only `SELECT`s — no writes.

```bash
DATABASE_URL='postgresql://readonly:...@host/db' \
  python3 scripts/audit_tenancy.py --verbose
```

Expected output:

```
✓ students_with_mismatched_account_email: 0 row(s)
✓ students_with_orphan_teacher_id: 0 row(s)
✓ students_unlinked_but_account_exists: 0 row(s)
✓ exam_sessions_teacher_mismatch: 0 row(s)
✓ violations_teacher_mismatch: 0 row(s)
✓ answers_teacher_mismatch: 0 row(s)
✓ teachers_with_orphan_org_id: 0 row(s)
✓ duplicate_student_accounts_per_email: 0 row(s)
OK — no tenancy violations found across 8 checks.
```

If any check reports rows: investigate before proceeding. Common causes:

- **students_unlinked_but_account_exists**: pre-existing rows from before
  the auto-link was added. Safe — login will lazily fix on next exam
  load (the `student_exams` handler claims these on read).
- **students_with_orphan_teacher_id**: a teacher was hard-deleted but
  their students rows weren't cleaned. Fix: either restore the teacher
  row or `DELETE FROM students WHERE teacher_id = '<deleted-teacher>'`.
- **`*_teacher_mismatch`**: cross-tenant write happened under old
  unscoped code. Investigate the specific session_key — the row likely
  needs to be re-stamped to the correct teacher_id.
- **duplicate_student_accounts_per_email**: signup race. Pick the older
  account, merge the newer's enrollment links to it, delete the newer.

## 2. Apply phase79 — student reminder preferences

```bash
psql "$DATABASE_URL" -f migrations/phase79_student_reminder_preferences.sql
```

This adds `student_accounts.email_reminders_enabled BOOLEAN NOT NULL
DEFAULT TRUE`. Existing rows default to true (current behaviour). The
app code has a fallback that returns `True` if the column is missing,
so this can land at any time without coordinating with a deploy.

## 3. Apply phase80 — tenant FK constraints (NOT VALID)

```bash
psql "$DATABASE_URL" -f migrations/phase80_tenant_fk_constraints.sql
```

Each constraint is added with `NOT VALID` so the migration is fast and
doesn't fail on legacy rows. The constraint takes effect for **new**
writes immediately; existing rows are grandfathered.

What this catches going forward:

- An `INSERT INTO students` with a `teacher_id` that doesn't exist → FK
  violation, query rejected.
- A `DELETE FROM teachers` with active references → blocked.
- A `DELETE FROM exam_sessions` cascades to its violations + answers.
- A `DELETE FROM student_accounts` sets `students.account_id = NULL`
  (anonymisation by design).
- An `INSERT INTO teachers` with `org_id` of a deleted org → FK violation.

## 4. Validate constraints when audit is clean

After step 1 reports OK (i.e., no legacy violations remain), validate
each constraint to apply it to existing rows too:

```bash
psql "$DATABASE_URL" <<'SQL'
ALTER TABLE students        VALIDATE CONSTRAINT students_teacher_fk;
ALTER TABLE students        VALIDATE CONSTRAINT students_account_fk;
ALTER TABLE exam_sessions   VALIDATE CONSTRAINT exam_sessions_teacher_fk;
ALTER TABLE violations      VALIDATE CONSTRAINT violations_session_fk;
ALTER TABLE violations      VALIDATE CONSTRAINT violations_teacher_fk;
ALTER TABLE answers         VALIDATE CONSTRAINT answers_session_fk;
ALTER TABLE answers         VALIDATE CONSTRAINT answers_teacher_fk;
ALTER TABLE exam_config     VALIDATE CONSTRAINT exam_config_teacher_fk;
ALTER TABLE questions       VALIDATE CONSTRAINT questions_teacher_fk;
ALTER TABLE student_invites VALIDATE CONSTRAINT student_invites_teacher_fk;
ALTER TABLE teachers        VALIDATE CONSTRAINT teachers_org_fk;
ALTER TABLE exam_templates  VALIDATE CONSTRAINT exam_templates_teacher_fk;
SQL
```

Each `VALIDATE` takes a `SHARE UPDATE EXCLUSIVE` lock — readers proceed,
concurrent writers are briefly blocked on that table. Run during a
maintenance window for the largest tables (`violations`, `answers`).

## 5. Future work (not in this pass)

- **App-level RLS via session variables**: have asyncpg set
  `app.current_teacher_id` on every connection acquire, and add RLS
  policies that gate `SELECT` / `UPDATE` on that. Stronger guarantee
  than constraint-based FKs because it catches **read leaks** too, not
  just write integrity.
- **Schema-level UNIQUE on `(teacher_id, exam_id)` in `exam_config`**:
  prerequisite for adding `exam_sessions(teacher_id, exam_id) →
  exam_config` composite FK.
- **Periodic re-audit**: schedule `audit_tenancy.py` as a daily cron
  with alert-on-non-zero so any future regression is caught within 24h.
