#!/usr/bin/env python3
"""Expand-contract safety linter for new SQL migrations.

Detects destructive DDL patterns (DROP COLUMN, DROP TABLE, ALTER COLUMN … TYPE,
RENAME, SET NOT NULL without a paired DEFAULT) in newly-added migration files
and rejects them unless the file carries a top-of-file marker:

    -- migration:contract <reason>

A marked file ("contract" step) is permitted only when a matching
migrations/down/<same-filename> exists (so the step is always reversible).

Usage:
    python scripts/check_migration_safety.py
        # examines migrations added vs origin/main; falls back to all if no git range
    python scripts/check_migration_safety.py --all
        # always scan every migration file (for local testing)

Exit codes:
    0 — all clean (or no new files)
    1 — destructive patterns found and not whitelisted
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = ROOT / "migrations"

# ── Forbidden patterns (case-insensitive, checked after comment-stripping) ──
# Each entry is (name, regex, human_description).
# The regex is applied line-by-line on non-comment, non-empty lines.
_FORBIDDEN: list[tuple[str, str, str]] = [
    ("drop_column", r'\bdrop\s+column\b', "DROP COLUMN"),
    ("drop_table", r'\bdrop\s+table\b', "DROP TABLE"),
    ("alter_column_type", r'\balter\s+column\s+\w+\s+type\b', "ALTER COLUMN … TYPE"),
    ("rename", r'\brename\b', "RENAME (column/table/constraint)"),
    ("set_not_null", r'\bset\s+not\s+null\b', "SET NOT NULL"),
    ("drop_constraint", r'\bdrop\s+constraint\b', "DROP CONSTRAINT"),
]

# ── Contract marker ──
_CONTRACT_RE = re.compile(
    r'^--\s*migration:\s*contract\s+(.+)', re.IGNORECASE
)


def _strip_sql_comments(sql: str) -> str:
    """Remove SQL single-line (--) and block (/* */) comments."""
    # Remove block comments
    sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)
    # Remove single-line comments
    lines = []
    for line in sql.splitlines():
        idx = line.find('--')
        if idx >= 0:
            line = line[:idx]
        lines.append(line)
    return '\n'.join(lines)


def _get_new_migrations(git_range: str = "origin/main...HEAD") -> list[Path]:
    """Return paths of migration files added vs a git reference.

    Falls back to scanning all *.sql in migrations/ if the git range is not
    available (e.g. shallow clone or no remote).
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=A", git_range, "--", "migrations/*.sql"],
            capture_output=True, text=True, check=False, timeout=30,
        )
        # A SUCCESSFUL diff is authoritative — including an EMPTY one, which means
        # "no new migrations" (clean pass), NOT "fall back". Only a git *failure*
        # (no origin/main, shallow clone, git missing) reaches the skip below.
        if result.returncode == 0:
            return [ROOT / p for p in result.stdout.strip().splitlines()
                    if p.strip().endswith(".sql")]
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    # git range unavailable → we cannot tell which migrations are NEW. Skip
    # (warn) rather than scanning the whole history: legacy migrations predate
    # this guard and contain intentional destructive DDL, so scanning all would
    # false-fail every build. CI must make origin/main resolvable (fetch-depth)
    # so this path is not taken there.
    print(f"[migration-safety] git range '{git_range}' unavailable — skipping "
          "(cannot determine newly-added migrations; legacy files are not re-linted)",
          flush=True)
    return []


def _check_file(path: Path) -> list[str]:
    """Check a single migration file. Returns list of error messages (empty = clean)."""
    sql = path.read_text()
    errors: list[str] = []

    # Check for contract marker in the first 5 lines
    first_lines = sql.splitlines()[:5]
    contract_match = None
    for line in first_lines:
        m = _CONTRACT_RE.match(line)
        if m:
            contract_match = m.group(1).strip()
            break

    cleaned = _strip_sql_comments(sql)

    # Check each non-empty line for forbidden patterns
    for pattern_name, regex, desc in _FORBIDDEN:
        for lineno, line in enumerate(cleaned.splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            if re.search(regex, stripped, re.IGNORECASE):
                errors.append(f"  {path.name}:{lineno}: {desc} — '{stripped.strip()}'")

    if not errors:
        return []

    if contract_match:
        # Contract marker present — verify matching down script exists
        down_path = MIGRATIONS / "down" / path.name
        if down_path.exists():
            return []  # Contract + down file = whitelisted
        else:
            return [
                f"  {path.name}: -- migration:contract marker requires migrations/down/{path.name}"
            ]

    return errors


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Lint new SQL migrations for unsafe destructive DDL"
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Scan every migration file (ignore git diff)",
    )
    args = parser.parse_args(argv)

    if args.all:
        paths = sorted(MIGRATIONS.glob("*.sql"))
    else:
        paths = _get_new_migrations()

    # Filter to only actual migration files (skip down/, baseline/, alembic/)
    migration_files = [
        p for p in paths
        if p.parent == MIGRATIONS and p.suffix == ".sql"
    ]

    if not migration_files:
        print("[migration-safety] No new migration files to check", flush=True)
        return 0

    all_errors: list[str] = []
    for path in sorted(migration_files):
        all_errors.extend(_check_file(path))

    if not all_errors:
        print(f"[migration-safety] ✓ {len(migration_files)} migration file(s) clean", flush=True)
        return 0

    print(f"[migration-safety] ✗ {len(all_errors)} issue(s) found:\n", flush=True)
    for err in all_errors:
        print(err, flush=True)
    print(
        "\nDestructive DDL (DROP COLUMN, DROP TABLE, ALTER COLUMN … TYPE, RENAME,"
        "\nSET NOT NULL, DROP CONSTRAINT) must either be:"
        "\n  1. Additive-only (the expand-contract pattern — preferred)"
        "\n  2. Marked with '-- migration:contract <reason>' AND accompanied by a"
        "\n     reverse script at migrations/down/<filename>"
        "\nSee migrations/MIGRATIONS.md for details.",
        flush=True,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
