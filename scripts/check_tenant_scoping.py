#!/usr/bin/env python3
"""Fail the build if an authenticated endpoint touches a TENANT-owned table
without any tenancy scoping in its body — the cross-tenant-leak class.

For a multi-tenant exam platform, an admin/teacher endpoint that reads or writes
a tenant table (exam_sessions, questions, answers, violations, students, …) but
never scopes the query — by the scope spine, an owner teacher_id/org_id filter,
or a per-resource ownership check — can return or mutate another tenant's data.
The mocked unit suite can't see it; this is a static structural guard.

Recognised scoping (any one in the function is enough — this is a coarse, "is it
scoped AT ALL" gate, not a per-query proof):
  • the scope spine: resolve_scope / assert_session_accessible /
    assert_session_owned / scope_to_teacher_ids / _require_billing_admin /
    _verify_teacher_in_org / _chat_verify_session_owned
  • an owner filter: .eq("teacher_id"/"org_id", …) / .in_("teacher_id", …)
  • the caller identity: teacher["id"] / teacher.get("id") / a `tid` local

Endpoints scoped by a DIFFERENT axis (the student's own account/session, or an
invite token capability, or pure health probes) are allow-listed below with a
reason. Add to ALLOWLIST only after confirming the endpoint really is scoped.
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
TENANCY = {
    "resolve_scope", "assert_session_accessible", "assert_session_owned",
    "scope_to_teacher_ids", "_require_billing_admin", "_verify_teacher_in_org",
    "_chat_verify_session_owned",
}
AUTH = {"require_admin", "require_student_account", "_require_api", "verify_admin_token"}
_EQ_TENANT = re.compile(
    r'\.eq\(\s*"(teacher_id|org_id)"|\.in_\(\s*"teacher_id"|teacher\["id"\]|teacher\.get\(\s*"id"|\btid\b'
)
_ATABLE = re.compile(r'_atable\(\s*"([^"]+)"\s*\)')

# (router filename, handler function name) -> reason. Scoped by a non-teacher
# axis; verified safe. Keep the reason current.
ALLOWLIST: dict[tuple[str, str], str] = {
    ("admin_status.py", "get_status"):
        "health probe — select(id).limit(1) connectivity checks, returns no tenant data",
    ("appeals.py", "submit_appeal"):
        "student-scoped — verifies exam_sessions.student_id + email (403), scopes violation_id to own session",
    ("appeals.py", "list_student_appeals"):
        "student-scoped — .eq('student_id', <own account id>)",
    ("appeals.py", "student_session_evidence"):
        "student-scoped — verifies session student_id + email, 403 otherwise",
    ("auth.py", "student_email_change_confirm"):
        "student-account-scoped — keyed on require_student_account account_id",
    ("public.py", "accept_invite"):
        "invite-token capability — the token encodes the tenant scope",
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
            tables = {m.group(1) for m in _ATABLE.finditer(body)} & TENANT_TABLES
            if not tables:
                continue
            has_auth = any(a in body for a in AUTH)
            has_tenancy = any(t in body for t in TENANCY) or bool(_EQ_TENANT.search(body))
            if has_auth and not has_tenancy:
                key = (p.name, n.name)
                stale_allow.discard(key)
                if key not in ALLOWLIST:
                    flagged.append((p.name, n.name, sorted(tables)))

    rc = 0
    if flagged:
        rc = 1
        print("✗ tenant-scoping: endpoint touches a tenant table with NO tenancy "
              "scoping (possible cross-tenant leak):\n")
        for fname, func, tbls in sorted(flagged):
            print(f"    {fname}::{func}  ->  reads/writes {', '.join(tbls)}")
        print("\nScope it via the scope spine / an owner teacher_id|org_id filter / a")
        print("per-resource ownership check — or, if it's scoped by another axis")
        print("(student account, invite token, health probe), add it to ALLOWLIST with a reason.")
    if stale_allow:
        rc = 1
        print("\n✗ tenant-scoping: stale ALLOWLIST entries (endpoint gained scoping or "
              "was removed — drop them):")
        for fname, func in sorted(stale_allow):
            print(f"    {fname}::{func}")
    if rc == 0:
        print("✓ tenant-scoping: every tenant-table endpoint is scoped "
              f"({len(ALLOWLIST)} allow-listed by a non-teacher axis)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
