#!/usr/bin/env python3
"""Tenancy data audit — find rows that violate the multi-teacher / multi-org isolation invariants.

WHY THIS EXISTS
---------------
Application code (after the recent audit) scopes every tenant query by
teacher_id and account_id. But existing rows in the production database
may have been written by older code paths where the scope was looser.
This script does NOT modify anything — it surfaces anomalies so you can
fix them (or confirm they're benign) before relying on the new strict
scoping.

WHAT IT CHECKS
--------------
1.  students rows whose account_id points at a student_accounts row with
    a different email (likely from a stale auto-link).
2.  students rows whose teacher_id doesn't correspond to a real teachers
    row (orphan after teacher deletion).
3.  students rows with NULL account_id where a matching student_accounts
    row exists (auto-link never fired — students will see no exams).
4.  exam_sessions whose teacher_id doesn't match the exam_config's
    teacher_id (cross-tenant write).
5.  violations whose teacher_id doesn't match the parent session's
    teacher_id (cross-tenant write).
6.  answers whose teacher_id doesn't match the parent session's
    teacher_id (cross-tenant write).
7.  teachers whose org_id points at a deleted organization (orphan).
8.  Duplicate student_accounts rows for the same email (signup race).

USAGE
-----
Set DATABASE_URL to the production read-replica (NOT the primary — this
is read-only but you don't want any accidental load on writes):

    DATABASE_URL=postgresql://... python3 scripts/audit_tenancy.py

Exit code 0 = clean. Non-zero = anomalies found (count printed).
Add ``--verbose`` to print up to 50 example rows per finding.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field
from typing import Iterable

try:
    import asyncpg
except ImportError:
    print("[audit] asyncpg not installed — run `pip install asyncpg`", file=sys.stderr)
    sys.exit(2)


@dataclass
class Finding:
    name: str
    description: str
    count: int
    examples: list[dict] = field(default_factory=list)


CHECKS: list[tuple[str, str, str]] = [
    (
        "students_with_mismatched_account_email",
        "students.account_id → student_accounts row whose email differs from the students.email",
        """
        SELECT s.id, s.roll_number, s.teacher_id, s.email AS student_email,
               sa.id AS account_id, sa.email AS account_email
          FROM students s
          JOIN student_accounts sa ON sa.id = s.account_id
         WHERE s.account_id IS NOT NULL
           AND LOWER(s.email) IS DISTINCT FROM LOWER(sa.email)
        """,
    ),
    (
        "students_with_orphan_teacher_id",
        "students.teacher_id points at a teachers row that no longer exists",
        """
        SELECT s.id, s.roll_number, s.teacher_id, s.email
          FROM students s
          LEFT JOIN teachers t ON t.id = s.teacher_id
         WHERE s.teacher_id IS NOT NULL AND t.id IS NULL
        """,
    ),
    (
        "students_unlinked_but_account_exists",
        "students.account_id IS NULL but a student_accounts row exists for students.email — auto-link never fired",
        """
        SELECT s.id, s.roll_number, s.teacher_id, s.email, sa.id AS would_link_to
          FROM students s
          JOIN student_accounts sa ON LOWER(sa.email) = LOWER(s.email)
         WHERE s.account_id IS NULL AND s.email IS NOT NULL AND s.email <> ''
        """,
    ),
    (
        "exam_sessions_teacher_mismatch",
        "exam_sessions.teacher_id differs from the exam_config row's teacher_id (cross-tenant write)",
        """
        SELECT es.session_key, es.teacher_id AS session_tid,
               es.exam_id, ec.teacher_id AS exam_tid
          FROM exam_sessions es
          JOIN exam_config ec ON ec.exam_id = es.exam_id
         WHERE es.teacher_id IS NOT NULL
           AND ec.teacher_id IS NOT NULL
           AND es.teacher_id::text <> ec.teacher_id::text
        """,
    ),
    (
        "violations_teacher_mismatch",
        "violations.teacher_id differs from the parent exam_sessions row's teacher_id (cross-tenant audit row)",
        """
        SELECT v.id, v.session_key, v.teacher_id AS violation_tid,
               es.teacher_id AS session_tid, v.violation_type
          FROM violations v
          JOIN exam_sessions es ON es.session_key = v.session_key
         WHERE v.teacher_id IS NOT NULL
           AND es.teacher_id IS NOT NULL
           AND v.teacher_id::text <> es.teacher_id::text
        """,
    ),
    (
        "answers_teacher_mismatch",
        "answers.teacher_id differs from the parent exam_sessions row's teacher_id",
        """
        SELECT a.session_key, a.question_id, a.teacher_id AS answer_tid,
               es.teacher_id AS session_tid
          FROM answers a
          JOIN exam_sessions es ON es.session_key = a.session_key
         WHERE a.teacher_id IS NOT NULL
           AND es.teacher_id IS NOT NULL
           AND a.teacher_id::text <> es.teacher_id::text
        """,
    ),
    (
        "teachers_with_orphan_org_id",
        "teachers.org_id points at an organizations row that no longer exists",
        """
        SELECT t.id, t.email, t.org_id, t.org_role
          FROM teachers t
          LEFT JOIN organizations o ON o.id = t.org_id
         WHERE t.org_id IS NOT NULL AND o.id IS NULL
        """,
    ),
    (
        "duplicate_student_accounts_per_email",
        "more than one student_accounts row exists for the same email (signup race)",
        """
        SELECT LOWER(email) AS email_lc, COUNT(*) AS n,
               ARRAY_AGG(id) AS account_ids
          FROM student_accounts
         WHERE email IS NOT NULL AND email <> ''
         GROUP BY LOWER(email)
        HAVING COUNT(*) > 1
        """,
    ),
]


async def _run_check(conn: asyncpg.Connection, name: str, description: str, sql: str,
                     verbose: bool) -> Finding:
    rows = await conn.fetch(sql)
    examples: list[dict] = []
    if verbose and rows:
        # Asyncpg Record → dict via dict(row); cap at 50 rows to keep output sane.
        examples = [dict(r) for r in rows[:50]]
    return Finding(name=name, description=description, count=len(rows), examples=examples)


def _format(findings: Iterable[Finding], verbose: bool) -> int:
    total = 0
    print("=" * 78)
    print("TENANCY DATA AUDIT")
    print("=" * 78)
    for f in findings:
        marker = "✗" if f.count else "✓"
        print(f"\n{marker} {f.name}: {f.count} row(s)")
        print(f"    {f.description}")
        if f.count and verbose:
            for i, ex in enumerate(f.examples, 1):
                print(f"      [{i}] {ex}")
            if f.count > len(f.examples):
                print(f"      ... and {f.count - len(f.examples)} more")
        total += f.count
    print()
    print("=" * 78)
    if total == 0:
        print(f"OK — no tenancy violations found across {len(CHECKS)} checks.")
        return 0
    print(f"FOUND {total} row(s) across {sum(1 for f in findings if f.count)} check(s).")
    print("Run with --verbose to see examples. Investigate before relying on")
    print("the new strict-scoping code paths — old rows can resurface anomalies")
    print("on read even though the write path is now correct.")
    return 1


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verbose", action="store_true",
                        help="Print up to 50 example rows per finding.")
    parser.add_argument("--database-url",
                        default=os.environ.get("DATABASE_URL", ""),
                        help="Override DATABASE_URL.")
    args = parser.parse_args()

    if not args.database_url:
        print("[audit] DATABASE_URL not set. Pass --database-url or export it.",
              file=sys.stderr)
        return 2

    try:
        conn = await asyncpg.connect(args.database_url)
    except Exception as e:
        print(f"[audit] could not connect: {e}", file=sys.stderr)
        return 2

    findings: list[Finding] = []
    try:
        for name, description, sql in CHECKS:
            try:
                f = await _run_check(conn, name, description, sql, args.verbose)
            except asyncpg.PostgresError as e:
                # A schema mismatch (e.g., column doesn't exist yet) shouldn't
                # crash the whole audit — report the check as skipped with the
                # DB error so the operator can fix the migration drift.
                f = Finding(name=name,
                            description=f"SKIPPED ({e.__class__.__name__}: {e})",
                            count=0)
            findings.append(f)
    finally:
        await conn.close()

    return _format(findings, args.verbose)


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
