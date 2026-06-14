#!/usr/bin/env python3
"""Fail the build if an `require_admin` endpoint filters a TENANT-owned table by
the CALLER's OWN teacher_id without going through the org-rollup scope spine —
the "admin silently sees only their own slice" class.

This is the sibling of check_tenant_scoping.py. That guard catches endpoints with
NO scoping at all (cross-tenant leak). THIS guard catches the opposite, subtler
bug: an admin endpoint that IS scoped, but hardcodes `.eq("teacher_id", <own id>)`
so an org admin silently sees/acts on only their own teacher_id instead of the
whole org. That under-scoping shipped repeatedly (failed-sessions, invites,
registered-count, pending-verifications, pending-grades, grading-audit, groups,
appeals, email-scorecards) — each looked correct in the mocked unit suite because
the caller WAS the only teacher in the test fixture.

Rule: a `@router.*` handler gated by `require_admin` that
  • reads/writes a TENANT_TABLE, AND
  • filters by the caller's own id  (`.eq("teacher_id", …)` together with a
    `teacher["id"]` / `teacher.get("id")` / `tid` self-reference), AND
  • does NOT use the rollup spine
    (resolve_scope / scope_to_teacher_ids / apply_teacher_scope /
     assert_session_accessible)
must EITHER pipe its scope through the spine (so plain teacher → own, admin →
org-wide, superadmin → unfiltered) OR be allow-listed below as a deliberately
own-scoped action.

The product model (chosen 2026-06-11): read-only org-monitoring VIEWS roll up;
per-resource MUTATIONS and the private question-bank library stay owner-only.
The allowlist encodes exactly that boundary — add to it only after confirming the
endpoint is an edit/owner-only action, not a monitoring view.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROUTERS = Path(__file__).resolve().parent.parent / "app" / "routers"

TENANT_TABLES = {
    "exam_sessions", "questions", "answers", "violations", "students",
    "student_invites", "question_bank", "exam_config", "scorecards", "appeals",
    "student_groups", "exam_group_assignments", "subscriptions", "grading_audit",
    "student_group_members",
}
ROLLUP = {
    "resolve_scope", "scope_to_teacher_ids", "apply_teacher_scope",
    "assert_session_accessible",
}
_ATABLE = re.compile(r'_atable\(\s*"([^"]+)"\s*\)|async_table\(\s*"([^"]+)"\s*\)')
_OWNLOCK = re.compile(r'\.eq\(\s*"teacher_id"')
_SELFID = re.compile(r'teacher\[\s*"id"\s*\]|teacher\.get\(\s*"id"|\btid\b')

# (router filename, handler) -> reason. Deliberately OWN-scoped: a per-resource
# mutation or the private question-bank library. NOT a monitoring view. Keep the
# reason current; if an endpoint becomes a rollup view, remove its entry.
ALLOWLIST: dict[tuple[str, str], str] = {
    # ── exam-config / exam-authoring mutations (own exam content) ──
    ("admin.py", "backfill_risk_scores"):        "maintenance recompute over own sessions",
    ("admin_exams.py", "set_phone_camera_config"): "mutates own exam_config",
    ("admin_exams.py", "set_pass_mark"):         "mutates own exam_config pass mark",
    ("admin_exams.py", "archive_exam"):           "archives own exam",
    ("admin_exams.py", "unarchive_exam"):         "unarchives own exam",
    ("admin_exams.py", "delete_exam"):           "deletes own exam + questions",
    ("admin_exams.py", "duplicate_exam"):        "clones own exam + questions",
    ("admin_exams.py", "set_time_extension"):    "mutates own exam's per-student time extension",
    ("admin_invites.py", "create_exam_from_template"): "creates exam from own template",
    ("admin_settings.py", "admin_set_schedule"): "mutates own exam_config schedule",
    ("admin_settings.py", "admin_set_shuffle"):  "mutates own exam_config shuffle",
    ("admin_settings.py", "admin_set_sensitivity"): "mutates own exam_config sensitivity",
    ("admin_settings.py", "admin_set_audio_keywords"): "mutates own exam_config keywords",
    ("question_bank.py", "bank_to_exam"):        "builds own exam from own bank",
    ("question_bank.py", "update_questions"):    "mutates own exam questions",
    # ── group / roster mutations (own roster) ──
    ("admin_exams.py", "rename_group"):          "renames own student_group",
    ("admin_exams.py", "delete_group"):          "deletes own student_group",
    ("admin_exams.py", "add_group_members"):     "mutates own group membership",
    ("admin_exams.py", "remove_group_members"):  "mutates own group membership",
    ("admin_exams.py", "assign_exam_groups"):    "binds own groups to own exam",
    ("admin_exams.py", "unassign_exam_group"):   "unbinds own group from own exam",
    ("admin_students.py", "delete_student_from_roster"): "deletes from own roster",
    ("google_classroom.py", "google_sync_roster"): "syncs own roster from Classroom",
    ("google_classroom.py", "google_link_exam"): "links own exam to Classroom",
    # ── invite mutations (own exam invites) ──
    ("admin_invites.py", "send_invites"):        "sends invites for own exam",
    ("admin_invites.py", "revoke_invite"):       "revokes own exam invite",
    # ── live-session mutations (own sessions) ──
    ("admin_sessions.py", "admin_submit"):       "force-submits own session",
    ("admin_sessions.py", "session_pause"):      "pauses own session",
    ("admin_sessions.py", "session_reset"):      "resets own session",
    ("admin_sessions.py", "session_resume"):     "resumes own session",
    ("admin_sessions.py", "request_recalibration"): "recalibrates own session",
    # ── grading mutations (own answers) ──
    ("grading.py", "grade_suggest"):             "AI-grades own answers",
    ("grading.py", "grade_confirm"):             "confirms grade on own answer",
    ("grading.py", "grade_confirm_bulk"):        "bulk-confirms grades on own answers",
    # ── appeal decision (own appeal) ──
    ("appeals.py", "resolve_appeal"):            "resolves own exam's appeal",
    # ── private question-bank library (own content, not a monitoring view) ──
    ("question_bank.py", "list_bank_questions"): "private question-bank library — own content",
    ("question_bank.py", "update_bank_question"): "mutates own bank question",
    ("question_bank.py", "delete_bank_question"): "deletes own bank question",
    ("question_bank.py", "export_bank_questions"): "exports own bank library",
    ("question_bank.py", "list_question_versions"): "own-scoped version history",
    ("question_bank.py", "restore_question_version"): "own-scoped version restore",
}


def _is_router_decorator(dec: ast.AST) -> bool:
    return (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
            and isinstance(dec.func.value, ast.Name) and dec.func.value.id == "router"
            and dec.func.attr in ("get", "post", "put", "delete", "patch"))


def main() -> int:
    flagged: list[tuple[str, str, list[str]]] = []
    stale_allow: set[tuple[str, str]] = set(ALLOWLIST)

    for p in sorted(ROUTERS.glob("*.py")):
        src = p.read_text()
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not any(_is_router_decorator(d) for d in n.decorator_list):
                continue
            body = ast.get_source_segment(src, n) or ""
            if "require_admin" not in body:
                continue
            tables = {m.group(1) or m.group(2) for m in _ATABLE.finditer(body)} & TENANT_TABLES
            if not tables:
                continue
            own_locked = bool(_OWNLOCK.search(body)) and bool(_SELFID.search(body))
            has_rollup = any(r in body for r in ROLLUP)
            if own_locked and not has_rollup:
                key = (p.name, n.name)
                stale_allow.discard(key)
                if key not in ALLOWLIST:
                    flagged.append((p.name, n.name, sorted(tables)))

    rc = 0
    if flagged:
        rc = 1
        print("✗ admin-rollup: admin endpoint filters a tenant table by the caller's OWN "
              "teacher_id without the rollup scope spine (org admin silently sees only "
              "their own slice):\n")
        for fname, func, tbls in sorted(flagged):
            print(f"    {fname}::{func}  ->  own-locks on {', '.join(tbls)}")
        print("\nPipe the scope through resolve_scope -> scope_to_teacher_ids -> "
              "apply_teacher_scope so admins roll up org-wide — or, if it's a deliberate")
        print("owner-only mutation / private library (NOT a monitoring view), add it to "
              "ALLOWLIST with a reason.")
    if stale_allow:
        rc = 1
        print("\n✗ admin-rollup: stale ALLOWLIST entries (endpoint now rolls up or was "
              "removed — drop them):")
        for fname, func in sorted(stale_allow):
            print(f"    {fname}::{func}")
    if rc == 0:
        print("✓ admin-rollup: every admin endpoint either rolls up org-wide or is "
              f"allow-listed as an owner-only action ({len(ALLOWLIST)} allow-listed)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
