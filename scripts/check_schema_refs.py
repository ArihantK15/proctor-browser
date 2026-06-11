#!/usr/bin/env python3
"""Schema-reference guard.

Statically extracts every (table, column) the app code touches — SELECT
column lists (string-literal *and* the SESSION_COLS / STUDENT_COLS /
_EXAM_CONFIG_COLUMNS-style constants), `.eq()` filters, and every
insert / upsert / update payload (dict literals + `row["col"] = ...`
subscript adds + named-dict vars) — and checks them against a committed
snapshot of the live Postgres schema (schema/columns.json).

This catches the bug class that caused a string of production 500s:
commit 2f2d5af swapped `select(*)` for explicit column lists that named
columns the table never had (students.exam_id, student_invites.phone,
appeals.email, ...). Because the test stubs ignore schema, nothing in CI
noticed until a real DB raised UndefinedColumnError -> uncaught -> 500.

Usage:
    python scripts/check_schema_refs.py              # fail (exit 1) on any mismatch
    python scripts/check_schema_refs.py --report-only  # print, always exit 0

The snapshot is produced from prod by scripts/dump_schema.py. If it is
absent the check SKIPS with a notice (exit 0) rather than guessing.
"""
from __future__ import annotations

import ast
import collections
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = ROOT / "app"
SNAPSHOT = ROOT / "schema" / "columns.json"

# Tables the checker should not flag even if absent from the snapshot:
# RPC targets, health probes, or objects created outside the tracked
# schema. Add sparingly and with a reason.
IGNORE_TABLES = {
    "things",          # connectivity/health probe table
    "table_name",      # literal in a docstring example (repositories/base.py)
}

# Per-(table, column) refs that already existed in the code when the schema
# snapshot was first seeded from prod (2026-06-11). Baselined so the guard
# ENFORCES against NEW drift immediately, while these pre-existing ones are
# triaged separately — they are a mix of real-but-wrapped soft-failures (the
# query sits inside try/except, so it degrades rather than 500s) and likely
# mis-attributions from chained multi-table queries. Remove each entry once the
# ref is fixed, a migration adds the column, or it's confirmed a false positive.
IGNORE_REFS = {
    ("exam_sessions", "current_question"),     # admin_sessions triage select; wrapped in try/except
    ("exam_sessions", "id"),                   # PK is session_key — triage the ref
    ("students", "lti_user_id"),               # LTI AGS passback; wrapped — verify column/table
    ("teachers", "lti_user_id"),               # LTI; verify column/table
    ("students", "status"),                    # likely mis-attributed .eq("status") from a chained exam_sessions query
    ("student_accounts", "updated_at"),        # pre-existing; triage
    ("auth_sessions", "id"),                   # PK is jti — triage the ref
    ("auth_sessions", "password_changed_at"),  # pre-existing; triage
}

_TBL = re.compile(r'_atable\(\s*"([^"]+)"\s*\)')
_SEL = re.compile(r'\.select\(\s*"([^"]+)"')
_EQ = re.compile(r'\.eq\(\s*"([^"]+)"')
# Module-level column-list constants, e.g.  STUDENT_COLS = "a,b,c"
_CONST = re.compile(r'^([A-Z_]+(?:COLS|COLUMNS))\s*=\s*\(?\s*"', re.M)


def _cols_from_select(literal: str) -> list[str]:
    """Split a PostgREST select() string into real column names,
    resolving `alias:real_column` to real_column and dropping `*`."""
    out = []
    for part in literal.split(","):
        part = part.strip()
        if not part or part == "*":
            continue
        # alias:column  -> column ; column::cast -> column
        part = part.split(":")[-1].split("::")[0].strip()
        # embedded resource select like "exam_config(exam_title)" — skip
        if "(" in part or ")" in part:
            continue
        if part:
            out.append(part)
    return out


def _resolve_constants(src: str) -> dict[str, list[str]]:
    """Find `NAME = "col,col,..."` (incl. implicitly-concatenated string
    literals across lines) column-list constants in a module."""
    consts: dict[str, list[str]] = {}
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
           and isinstance(node.targets[0], ast.Name) \
           and node.targets[0].id.endswith(("COLS", "COLUMNS")):
            val = node.value
            text = None
            if isinstance(val, ast.Constant) and isinstance(val.value, str):
                text = val.value
            elif isinstance(val, ast.JoinedStr):
                continue
            else:
                # implicit concat compiles to a single Constant; tuples of
                # str literals -> evaluate
                try:
                    text = ast.literal_eval(val)
                    if isinstance(text, tuple):
                        text = "".join(str(t) for t in text)
                except Exception:
                    text = None
            if isinstance(text, str) and "," in text:
                consts[node.targets[0].id] = _cols_from_select(text)
    return consts


def _table_of(call: ast.Call):
    node = call.func.value if isinstance(call.func, ast.Attribute) else None
    while node is not None:
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
           and node.func.id in ("_atable", "async_table"):
            if node.args and isinstance(node.args[0], ast.Constant):
                return node.args[0].value
            return None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            node = node.func.value
        elif isinstance(node, ast.Attribute):
            node = node.value
        else:
            return None
    return None


def _dict_keys(node):
    if isinstance(node, ast.Dict):
        return {k.value for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    return None


def extract_refs() -> dict[str, set[str]]:
    """Return {table: set(columns)} referenced anywhere in app/."""
    refs: dict[str, set] = collections.defaultdict(set)
    for p in sorted(APP.rglob("*.py")):
        src = p.read_text()
        consts = _resolve_constants(src)

        # ---- reads: chain string-literal .select()/.eq() to nearest table
        events = ([(m.start(), "tbl", m.group(1)) for m in _TBL.finditer(src)]
                  + [(m.start(), "sel", m.group(1)) for m in _SEL.finditer(src)]
                  + [(m.start(), "eq", m.group(1)) for m in _EQ.finditer(src)])
        events.sort()
        cur = None
        for _, kind, val in events:
            if kind == "tbl":
                cur = val
            elif cur:
                if kind == "sel":
                    for c in _cols_from_select(val):
                        refs[cur].add(c)
                else:
                    refs[cur].add(val)

        # ---- reads via .select(CONSTANT)
        tree = ast.parse(src, str(p))
        for c in ast.walk(tree):
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) \
               and c.func.attr == "select" and c.args \
               and isinstance(c.args[0], ast.Name) and c.args[0].id in consts:
                tbl = _table_of(c)
                if tbl:
                    for col in consts[c.args[0].id]:
                        refs[tbl].add(col)

        # ---- writes: insert/upsert/update payload keys (per-function scope)
        for fn in [n for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            named: dict[str, set] = collections.defaultdict(set)
            for s in ast.walk(fn):
                if isinstance(s, ast.Assign):
                    dk = _dict_keys(s.value)
                    for t in s.targets:
                        if isinstance(t, ast.Name) and dk is not None:
                            named[t.id] |= dk
                        if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) \
                           and isinstance(t.slice, ast.Constant) and isinstance(t.slice.value, str):
                            named[t.value.id].add(t.slice.value)
            for c in ast.walk(fn):
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) \
                   and c.func.attr in ("insert", "upsert", "update") and c.args:
                    tbl = _table_of(c)
                    if not tbl:
                        continue
                    cols = _dict_keys(c.args[0])
                    if cols is None and isinstance(c.args[0], ast.Name):
                        cols = named.get(c.args[0].id)
                    if cols:
                        refs[tbl] |= cols
    return refs


def main(argv: list[str]) -> int:
    report_only = "--report-only" in argv

    if not SNAPSHOT.exists():
        print(f"::notice::{SNAPSHOT.relative_to(ROOT)} not found — schema-ref "
              f"check SKIPPED. Seed it with scripts/dump_schema.py against prod.")
        return 0

    schema = {t: set(cols) for t, cols in json.loads(SNAPSHOT.read_text()).items()}
    refs = extract_refs()

    missing: list[tuple[str, str]] = []
    unknown_tables: set[str] = set()
    for table in sorted(refs):
        if table in IGNORE_TABLES:
            continue
        if table not in schema:
            unknown_tables.add(table)
            continue
        for col in sorted(refs[table]):
            if col not in schema[table] and (table, col) not in IGNORE_REFS:
                missing.append((table, col))

    if unknown_tables:
        print("::notice::tables referenced in code but absent from snapshot "
              f"(not checked): {', '.join(sorted(unknown_tables))}")

    if not missing:
        print(f"✓ schema-ref check: all {sum(len(v) for v in refs.values())} "
              f"column references exist in {SNAPSHOT.name}")
        return 0

    print(f"✗ schema-ref check: {len(missing)} column reference(s) do NOT "
          f"exist in the live schema:\n")
    for table, col in missing:
        print(f"    {table}.{col}")
    print("\nThese will raise UndefinedColumnError -> 500 at runtime. Either "
          "the column was misspelled/removed, or the snapshot is stale "
          "(refresh via scripts/dump_schema.py).")
    return 0 if report_only else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
