from pathlib import Path


MIGRATION = Path("migrations/phase55_dashboard_reporting_indexes.sql")


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8").lower()


def test_dashboard_reporting_index_migration_exists():
    assert MIGRATION.exists()


def test_dashboard_reporting_indexes_cover_hot_paths():
    sql = _sql()

    expected_fragments = [
        "idx_exam_sessions_teacher_status_submitted",
        "on exam_sessions(teacher_id, status, submitted_at desc)",
        "idx_exam_sessions_teacher_exam_status_submitted",
        "on exam_sessions(teacher_id, exam_id, status, submitted_at desc)",
        "idx_exam_sessions_teacher_roll_status_submitted",
        "on exam_sessions(teacher_id, roll_number, status, submitted_at desc)",
        "idx_exam_sessions_teacher_exam_roll_status",
        "on exam_sessions(teacher_id, exam_id, roll_number, status)",
        "idx_violations_session_teacher_created",
        "on violations(session_key, teacher_id, created_at)",
        "idx_violations_teacher_session",
        "on violations(teacher_id, session_key)",
        "idx_violations_teacher_type_created",
        "on violations(teacher_id, violation_type, created_at desc)",
        "idx_answers_session_teacher_question",
        "on answers(session_key, teacher_id, question_id)",
        "idx_answers_teacher_exam_pending_question",
        "on answers(teacher_id, exam_id, question_id)",
        "where teacher_score is null",
    ]

    for fragment in expected_fragments:
        assert fragment in sql
