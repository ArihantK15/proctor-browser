"""Guard: no NEW migration may add an RLS policy on the retired auth.uid() model.

phase124 moved tenancy to the app.* session-context helpers (app.teacher_id(),
app.org_id(), app.visible_teacher_ids(), app.is_privileged()). The old Supabase
auth.uid() model is dead on the live DB. A new migration that CREATEs a POLICY
referencing auth.uid() would be a straggler that DENIES ALL rows the moment the
procta_app cutover (RLS_SESSION_CONTEXT=1) flips — because auth.uid() is NULL
under the restricted non-owner role. That is exactly the bug class that left
invite_send_counters policy-less (fixed in phase137).

Two pre-phase124 files legitimately still contain auth.uid() CREATE POLICY
statements; their policies were superseded/dropped by phase124+ and are inert
history, so they're allowlisted. Anything else is a regression and fails CI.

(This is the static "new tables can't regress" backstop. The complementary
LIVE check — RLS-enabled tables with no app.* policy at all — is an operational
pg_policies query run before any cutover, not something a static scan can prove.)
"""
import glob
import os
import re

_MIG_DIR = os.path.join(os.path.dirname(__file__), "..", "migrations")

# Files that pre-date the app.* model. Their auth.uid() policies are historical
# and already replaced on the live DB.
_LEGACY_ALLOWLIST = {
    "rls_policies.sql",
    "phase20_organizations.sql",
}

_CREATE_POLICY = re.compile(r"CREATE\s+POLICY\b.*?;", re.IGNORECASE | re.DOTALL)


def test_no_new_migration_uses_auth_uid_policy():
    offenders = []
    for path in sorted(glob.glob(os.path.join(_MIG_DIR, "*.sql"))):
        name = os.path.basename(path)
        if name in _LEGACY_ALLOWLIST:
            continue
        sql = open(path, encoding="utf-8").read()
        for stmt in _CREATE_POLICY.findall(sql):
            if "auth.uid" in stmt.lower():
                offenders.append(name)
                break
    assert not offenders, (
        "These migrations create RLS policies on the retired auth.uid() model — "
        "use the phase124 app.* helpers (app.is_privileged() / app.teacher_id() / "
        f"app.visible_teacher_ids()) instead: {offenders}"
    )


def test_legacy_allowlist_still_accurate():
    """Keep the allowlist honest: every allowlisted file must still actually
    contain an auth.uid() policy (else remove it from the list)."""
    for name in _LEGACY_ALLOWLIST:
        path = os.path.join(_MIG_DIR, name)
        assert os.path.exists(path), f"allowlisted migration missing: {name}"
        sql = open(path, encoding="utf-8").read()
        assert any("auth.uid" in s.lower() for s in _CREATE_POLICY.findall(sql)), \
            f"{name} no longer has an auth.uid() policy — drop it from _LEGACY_ALLOWLIST"
