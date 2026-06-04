# Live-schema snapshot

`columns.json` is a snapshot of the production `public` schema —
`{ "table": ["col", ...], ... }`. It is the source of truth for the
**schema-reference guard** (`scripts/check_schema_refs.py`), which runs
in CI and fails the build if app code reads, filters, or writes a column
that does not exist in the live database.

This exists because the test stubs ignore schema, so column-name bugs
(`students.exam_id`, `student_invites.phone`, `appeals.email`, …) used to
ship undetected and surface as `UndefinedColumnError` → HTTP 500 in prod.

## Seeding / refreshing the snapshot

Run against prod (the box already has `DATABASE_URL`):

```
DATABASE_URL=postgres://… python scripts/dump_schema.py
```

This rewrites `schema/columns.json` deterministically. **Before committing
a fresh snapshot, run the guard locally** so you fix any mismatch it finds
rather than committing a snapshot that immediately red-X's CI:

```
python scripts/check_schema_refs.py            # exit 1 lists offending refs
```

Refresh the snapshot whenever you apply a migration that adds/drops/renames
a column. Until it is first seeded, the guard SKIPS with a notice (CI stays
green), so this tooling is safe to land before the first dump.
