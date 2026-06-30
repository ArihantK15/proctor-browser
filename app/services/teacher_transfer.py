"""Transactional teacher_id remap for teacher offboarding / data reassignment.

Usage
-----
    from ..services.teacher_transfer import reassign_teaching_data
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            counts = await reassign_teaching_data(conn, from_id, to_id)

The caller is responsible for:
  - Acquiring a connection and wrapping the call in a transaction.
  - Authorising that both teachers are in the same organisation.
  - Auditing the action (writing an admin_audit_log row).
"""

from typing import Any

_MOVE_TABLES = (
    "exam_config", "exam_sessions", "answers", "violations", "questions",
    "question_bank", "question_versions", "students", "student_groups",
    "student_group_members", "student_invites", "exam_batch_assignments",
    "exam_group_assignments", "exam_templates", "exam_time_extensions", "appeals",
    "grading_audit", "invite_send_counters", "google_classroom_links",
    "coding_submissions", "coding_test_cases",
)

_KEEP_TABLES = (
    "auth_events", "admin_audit_log", "api_keys", "google_auth_tokens",
    "google_oauth_states", "issues",
)


async def reassign_teaching_data(conn, from_id: str, to_id: str) -> dict[str, Any]:
    """Remap teacher_id from_id -> to_id across teaching-data tables ONLY.

    Caller MUST wrap this in a transaction and have already authorised that both
    teachers are in the same org.  Returns ``{table_name: rows_moved}``.
    """
    counts = {}
    for table in _MOVE_TABLES:
        # `table` is from the hardcoded _MOVE_TABLES allowlist above — never
        # caller input — so the f-string is safe. (asyncpg-sqli is excluded in
        # CI globally; the rule that actually fires is the SQLAlchemy one.)
        # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
        tag = await conn.execute(
            f"UPDATE {table} SET teacher_id = $1 WHERE teacher_id = $2", to_id, from_id)
        counts[table] = int(tag.split()[-1]) if tag and tag.startswith("UPDATE") else 0
    return counts
