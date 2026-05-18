"""Lightweight i18n string table and t() helper.

Usage:
    from app.services.i18n import t
    raise HTTPException(403, t("exam_not_started", starts_at=fmt_ist(...)))

All user-facing strings should flow through t() so that multi-language
support can be added later by swapping the backing dictionary.
"""

from typing import Any

_STRINGS: dict[str, str] = {
    # ── Exam validation ───────────────────────────────────────────
    "exam_not_started": "The exam has not started yet. It begins at {starts_at}.",
    "exam_window_closed": "The exam window has closed. It ended at {ends_at}.",
    "roll_not_found": "Roll number not found. Please complete registration first.",
    "invalid_access_code": "Invalid exam access code. Ask your examiner for the correct code.",
    "already_submitted": "You have already submitted this exam.",
    "invite_revoked": "This invite has been revoked. Contact your teacher.",
    "invite_expired": "This invite has expired. Contact your teacher.",
    "registration_failed": "Registration failed. Please try again.",
    "student_create_failed": "Failed to create student record. Please try again.",
    "not_in_group": "You are not in a group assigned to this exam. Contact your teacher.",

    # ── Invites ───────────────────────────────────────────────────
    "no_invites_sent": "No invites sent yet.",
    "select_exam_for_invites": "Select an exam to see invites.",
    "invites_load_failed": "Failed to load invites.",
    "invites_network_error": "Network error.",

    # ── Questions / bank ──────────────────────────────────────────
    "no_questions_yet": "No questions yet &#8212; click \"Add\" in the toolbar.",
    "no_matching_questions": "No matching questions.",
    "no_questions_in_bank": "No questions in bank yet.",
    "question_text_empty": "Question text is empty.",

    # ── Groups ────────────────────────────────────────────────────
    "no_groups_yet": "No groups yet.",
    "no_members_yet": "No members yet.",

    # ── Sessions / history ────────────────────────────────────────
    "no_students_found": "No students found.",
    "no_completed_exams": "No completed exams.",
    "history_load_failed": "Failed to load: {msg}",

    # ── Templates ─────────────────────────────────────────────────
    "no_templates_yet": "No templates yet.",
    "templates_load_failed": "Failed to load templates.",

    # ── Chat ──────────────────────────────────────────────────────
    "no_students_online": "No students online yet.",
    "pick_student_to_chat": "Pick a student on the left to start chatting.",
    "no_messages_yet": "No messages yet. Say hi.",
    "message_empty": "Message is empty",

    # ── Analytics ─────────────────────────────────────────────────
    "analytics_load_failed": "Failed to load analytics",

    # ── Timeline ──────────────────────────────────────────────────
    "timeline_loading": "Loading timeline...",
    "timeline_load_failed": "Failed to load timeline: {msg}",
    "timeline_no_events": "No events match the current filter.",

    # ── Auth ──────────────────────────────────────────────────────
    "password_min_length": "Password must be at least 10 characters.",
    "invalid_email_format": "Please provide a valid email address.",
    "name_required": "Full name is required.",
    "email_already_registered": "This email is already registered.",

    # ── General ───────────────────────────────────────────────────
    "loading": "Loading...",
    "error_generic": "Something went wrong. Please try again.",
}


def t(key: str, **kwargs: Any) -> str:
    """Look up a user-facing string by key and interpolate any kwargs.

    Falls back to the key itself if the key is not found (safe by default).
    """
    template = _STRINGS.get(key)
    if template is None:
        return key
    if kwargs:
        try:
            return template.format(**kwargs)
        except (KeyError, ValueError):
            return template
    return template
