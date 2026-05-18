#!/usr/bin/env python3
"""Sanity-check a plain-Postgres Procta cutover before flipping traffic.

The goal is not just "can Postgres connect?", but "does the restored database
look like a production Procta database that can safely replace Supabase?"
This script prints table counts plus integrity checks for auth, orgs, billing,
exams, sessions, and answers. It returns non-zero only for conditions that make
cutover unsafe; softer data-shape concerns are printed as warnings.
"""
from __future__ import annotations

import asyncio
import os
import sys

import asyncpg

REQUIRED_TABLES = {
    "answers",
    "auth_events",
    "auth_sessions",
    "exam_config",
    "exam_sessions",
    "organizations",
    "questions",
    "refresh_tokens",
    "subscriptions",
    "student_accounts",
    "students",
    "teachers",
    "usage_records",
}

REQUIRED_COLUMNS = {
    "answers": {"session_key", "question_id", "answer"},
    "exam_config": {"id", "teacher_id"},
    "exam_sessions": {"session_key", "roll_number", "teacher_id", "status", "student_id"},
    "organizations": {"id", "name", "slug", "max_students"},
    "refresh_tokens": {"jti", "user_id", "kind", "expires_at", "revoked_at", "replaced_by_jti"},
    "student_accounts": {"id", "email", "supabase_uid", "password_hash", "auth_provider", "password_changed_at"},
    "students": {"id", "roll_number", "teacher_id", "org_id"},
    "subscriptions": {"id", "org_id", "plan", "status", "trial_end", "current_period_end"},
    "teachers": {"id", "email", "supabase_uid", "password_hash", "auth_provider", "password_changed_at", "org_id", "org_role"},
    "usage_records": {"org_id", "period_start", "period_end", "exam_attempts", "students_used"},
}

def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is required")
    return url


async def _table_names(conn: asyncpg.Connection) -> set[str]:
    rows = await conn.fetch(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_type = 'BASE TABLE'
        """
    )
    return {str(r["table_name"]) for r in rows}


async def _columns(conn: asyncpg.Connection, table: str) -> set[str]:
    rows = await conn.fetch(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = $1
        """,
        table,
    )
    return {str(r["column_name"]) for r in rows}


async def _count(conn: asyncpg.Connection, table: str) -> int:
    return int(await conn.fetchval(f'SELECT COUNT(*) FROM "{table}"'))


async def _scalar(conn: asyncpg.Connection, sql: str) -> int:
    return int(await conn.fetchval(sql))


async def _rows(conn: asyncpg.Connection, sql: str) -> list[asyncpg.Record]:
    return list(await conn.fetch(sql))


def _print_section(title: str) -> None:
    print(f"[postgres-cutover] {title}:")


async def main_async() -> int:
    conn = await asyncpg.connect(_database_url())
    failures: list[str] = []
    warnings: list[str] = []
    try:
        tables = await _table_names(conn)
        missing_tables = sorted(REQUIRED_TABLES - tables)
        if missing_tables:
            failures.append(f"missing tables: {', '.join(missing_tables)}")

        for table, required in REQUIRED_COLUMNS.items():
            if table not in tables:
                continue
            cols = await _columns(conn, table)
            missing_cols = sorted(required - cols)
            if missing_cols:
                failures.append(f"{table} missing columns: {', '.join(missing_cols)}")

        counts: dict[str, int] = {}
        for table in sorted(REQUIRED_TABLES & tables):
            counts[table] = await _count(conn, table)

        _print_section("table counts")
        for table, count in counts.items():
            print(f"  {table}: {count}")

        if "teachers" in counts:
            if counts["teachers"] == 0:
                failures.append("teachers table is empty after restore")
            if "organizations" in counts and counts["organizations"] == 0 and counts["teachers"] > 0:
                failures.append("teachers exist but organizations table is empty")
            if "subscriptions" in counts and counts["subscriptions"] == 0 and counts["teachers"] > 0:
                failures.append("teachers exist but subscriptions table is empty")

        if "exam_config" in counts and "questions" in counts and counts["exam_config"] > 0 and counts["questions"] == 0:
            warnings.append("exam_config has rows but questions is empty")
        if "exam_sessions" in counts and "answers" in counts and counts["exam_sessions"] > 0 and counts["answers"] == 0:
            warnings.append("exam_sessions has rows but answers is empty")

        if not missing_tables:
            auth_rows = await _rows(
                conn,
                """
                SELECT kind, auth_provider, COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE password_hash IS NULL OR password_hash = '') AS missing_hash
                FROM (
                  SELECT 'teacher' AS kind, auth_provider, password_hash FROM teachers
                  UNION ALL
                  SELECT 'student' AS kind, auth_provider, password_hash FROM student_accounts
                ) auth_rows
                GROUP BY kind, auth_provider
                ORDER BY kind, auth_provider
                """,
            )
            _print_section("auth readiness")
            if auth_rows:
                for row in auth_rows:
                    print(
                        "  "
                        f"{row['kind']} provider={row['auth_provider'] or 'unknown'} "
                        f"total={row['total']} missing_password_hash={row['missing_hash']}"
                    )
                    if row["auth_provider"] == "local" and row["missing_hash"]:
                        failures.append(
                            f"{row['kind']} local-auth rows missing password_hash: {row['missing_hash']}"
                        )
                    if row["auth_provider"] == "supabase":
                        warnings.append(
                            f"{row['kind']} rows still using auth_provider=supabase: {row['total']} "
                            "(these users will need password reset or OAuth/local migration)"
                        )
            else:
                print("  no teacher/student auth rows found")

            org_without_sub = await _scalar(
                conn,
                """
                SELECT COUNT(*)
                FROM organizations o
                LEFT JOIN subscriptions s ON s.org_id = o.id
                WHERE s.id IS NULL
                """,
            )
            teacher_without_org = await _scalar(
                conn,
                "SELECT COUNT(*) FROM teachers WHERE org_id IS NULL",
            )
            student_without_org = await _scalar(
                conn,
                "SELECT COUNT(*) FROM students WHERE org_id IS NULL",
            )
            invalid_subscriptions = await _scalar(
                conn,
                """
                SELECT COUNT(*)
                FROM subscriptions
                WHERE status IS NULL
                   OR status NOT IN ('active', 'trialing', 'cancelling', 'cancelled', 'expired', 'paused', 'past_due')
                """,
            )
            expired_trials = await _scalar(
                conn,
                """
                SELECT COUNT(*)
                FROM subscriptions
                WHERE status = 'trialing'
                  AND trial_end IS NOT NULL
                  AND trial_end < NOW()
                """,
            )
            _print_section("org and billing readiness")
            print(f"  organizations_without_subscription: {org_without_sub}")
            print(f"  teachers_without_org: {teacher_without_org}")
            print(f"  students_without_org: {student_without_org}")
            print(f"  invalid_subscription_statuses: {invalid_subscriptions}")
            print(f"  expired_trialing_subscriptions: {expired_trials}")
            if org_without_sub:
                failures.append(f"organizations without subscription rows: {org_without_sub}")
            if teacher_without_org:
                failures.append(f"teachers without org_id: {teacher_without_org}")
            if student_without_org:
                warnings.append(f"students without org_id: {student_without_org}")
            if invalid_subscriptions:
                failures.append(f"subscriptions with invalid/missing status: {invalid_subscriptions}")
            if expired_trials:
                warnings.append(f"trialing subscriptions already past trial_end: {expired_trials}")

            orphan_exam_teachers = await _scalar(
                conn,
                """
                SELECT COUNT(*)
                FROM exam_config e
                LEFT JOIN teachers t ON t.id::text = e.teacher_id::text
                WHERE e.teacher_id IS NOT NULL AND t.id IS NULL
                """,
            )
            orphan_session_teachers = await _scalar(
                conn,
                """
                SELECT COUNT(*)
                FROM exam_sessions es
                LEFT JOIN teachers t ON t.id::text = es.teacher_id::text
                WHERE es.teacher_id IS NOT NULL AND t.id IS NULL
                """,
            )
            orphan_answers = await _scalar(
                conn,
                """
                SELECT COUNT(*)
                FROM answers a
                LEFT JOIN exam_sessions es ON es.session_key = a.session_key
                WHERE es.session_key IS NULL
                """,
            )
            sessions_missing_student = await _scalar(
                conn,
                """
                SELECT COUNT(*)
                FROM exam_sessions
                WHERE student_id IS NULL
                  AND status IN ('active', 'completed', 'submitted')
                """,
            )
            _print_section("exam data integrity")
            print(f"  exams_with_missing_teacher: {orphan_exam_teachers}")
            print(f"  sessions_with_missing_teacher: {orphan_session_teachers}")
            print(f"  answers_without_session: {orphan_answers}")
            print(f"  active_completed_sessions_missing_student_id: {sessions_missing_student}")
            if orphan_exam_teachers:
                failures.append(f"exam_config rows with missing teacher: {orphan_exam_teachers}")
            if orphan_session_teachers:
                failures.append(f"exam_sessions rows with missing teacher: {orphan_session_teachers}")
            if orphan_answers:
                warnings.append(f"answers without matching exam_sessions row: {orphan_answers}")
            if sessions_missing_student:
                warnings.append(f"active/completed sessions missing student_id: {sessions_missing_student}")

        if warnings:
            print("[postgres-cutover] WARNINGS:")
            for warning in warnings:
                print(f"  - {warning}")

        if failures:
            print("[postgres-cutover] FAILED:")
            for failure in failures:
                print(f"  - {failure}")
            return 1

        print("[postgres-cutover] OK: schema and core data shape are ready for DATABASE_BACKEND=postgres")
        return 0
    finally:
        await conn.close()


def main() -> int:
    try:
        return asyncio.run(main_async())
    except Exception as exc:
        print(f"[postgres-cutover] FATAL: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
