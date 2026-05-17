"""Auth helpers: JWT issue/verify, admin/student auth."""
from .tokens import (
    AuthCtx, extract_auth,
    create_token, require_auth, require_teacher_auth, verify_student_token,
    issue_admin_token, issue_student_auth_token,
    _check_session_ownership,
)
from .admin_auth import (
    _get_teacher_by_id, _get_teacher_by_uid,
    verify_admin_token, require_admin,
    _get_student_account_by_id, _get_student_account_by_uid,
    verify_student_auth_token, require_student_account,
)

__all__ = [
    "AuthCtx", "extract_auth",
    "create_token", "require_auth", "require_teacher_auth", "verify_student_token",
    "issue_admin_token", "issue_student_auth_token",
    "_check_session_ownership",
    "_get_teacher_by_id", "_get_teacher_by_uid",
    "verify_admin_token", "require_admin",
    "_get_student_account_by_id", "_get_student_account_by_uid",
    "verify_student_auth_token", "require_student_account",
]
