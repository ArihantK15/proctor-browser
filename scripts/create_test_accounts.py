#!/usr/bin/env python3
"""Create the three test accounts needed to exercise every role view.

Idempotent — re-running prints existing IDs + fresh JWTs without
creating duplicates. Safe to run on production (creates a dedicated
test org named "Procta UI Test Org" so it can't accidentally pollute
a real customer org's member list).

Accounts created
----------------
1. admin@procta.test    — org_role='admin'      (sees ALL tabs)
2. teacher@procta.test  — org_role='teacher'    (sees only teacher tabs)
3. super@procta.test    — org_role='superadmin' IF you ALSO set the
                          env var SUPER_ADMIN_EMAIL=super@procta.test
                          on the KVM. issue_admin_token() promotes
                          based on email match (admin_auth.py:119), so
                          just creating the row isn't enough.

All three share password 'TestPass!2026'. Emails are marked verified
so login works without an inbox round-trip.

Usage (inside the API container, same as create_loadtest_teacher.py)
-------------------------------------------------------------------
    docker compose run --rm --no-deps --entrypoint python api \
      scripts/create_test_accounts.py

After it runs you'll see:

    admin@procta.test     / TestPass!2026   → Admin role
    teacher@procta.test   / TestPass!2026   → Teacher role
    super@procta.test     / TestPass!2026   → Super Admin
        (requires SUPER_ADMIN_EMAIL=super@procta.test in api .env)

For local dev, run from the project root:

    python scripts/create_test_accounts.py
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEST_PASSWORD = "TestPass!2026"
TEST_ORG_NAME = "Procta UI Test Org"

ACCOUNTS = [
    {"email": "admin@procta.test",   "name": "Test Admin",       "role": "admin"},
    {"email": "teacher@procta.test", "name": "Test Teacher",     "role": "teacher"},
    {"email": "super@procta.test",   "name": "Test Super Admin", "role": "admin"},  # promoted via SUPER_ADMIN_EMAIL
]


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower().strip())
    return s.strip("-") or "org"


async def _existing_teacher(email: str) -> dict | None:
    from app.database import async_table as _atable
    rows = await _atable("teachers").select(
        "id,email,full_name,org_id,org_role,supabase_uid,email_verified_at"
    ).eq("email", email).limit(1).execute()
    return rows.data[0] if rows.data else None


async def _existing_org_by_slug(slug: str) -> dict | None:
    from app.database import async_table as _atable
    rows = await _atable("organizations").select("id,name,slug").eq("slug", slug).limit(1).execute()
    return rows.data[0] if rows.data else None


async def _create_first_admin(email: str, name: str) -> dict:
    """Create the test org + the admin row in one signup tx. Mirrors
    create_loadtest_teacher.py — uses the real signup helper so the
    org/subscription/exam plumbing is identical to production."""
    from app.services.local_auth import hash_password
    from app.routers.auth import _create_teacher_signup_postgres_tx

    password_hash = await hash_password(TEST_PASSWORD)
    teacher, org_id, _exam = await _create_teacher_signup_postgres_tx(
        email=email,
        name=name,
        org_name=TEST_ORG_NAME,
        slug=_slugify(TEST_ORG_NAME),
        supabase_uid=str(uuid.uuid4()),
        password_hash=password_hash,
    )
    return {"teacher_id": teacher["id"], "org_id": org_id}


async def _create_secondary_user(email: str, name: str, role: str, org_id: str) -> dict:
    """Create a non-first user in an existing org. Bypasses the signup
    tx because that would create a fresh org. We insert into teachers
    directly with the desired org_role."""
    from app.database import async_table as _atable
    from app.services.local_auth import hash_password
    from app.utils import now_ist

    password_hash = await hash_password(TEST_PASSWORD)
    teacher_id = str(uuid.uuid4())
    await _atable("teachers").insert({
        "id": teacher_id,
        "email": email,
        "full_name": name,
        "password_hash": password_hash,
        "auth_provider": "local",
        "password_changed_at": now_ist().isoformat(),
        "supabase_uid": str(uuid.uuid4()),
        "org_id": org_id,
        "org_role": role,
        "email_verified_at": now_ist().isoformat(),
        "status": "active",
    }).execute()
    return {"teacher_id": teacher_id, "org_id": org_id}


async def _mark_email_verified(teacher_id: str) -> None:
    from app.database import async_table as _atable
    from app.utils import now_ist
    await _atable("teachers").update({
        "email_verified_at": now_ist().isoformat(),
    }).eq("id", str(teacher_id)).execute()


async def _set_org_role(teacher_id: str, role: str) -> None:
    from app.database import async_table as _atable
    await _atable("teachers").update({"org_role": role}).eq("id", str(teacher_id)).execute()


def _issue_jwt(teacher: dict) -> str:
    from app.auth import issue_admin_token
    return issue_admin_token(teacher)


async def _ensure_account(spec: dict, shared_org_id: str | None) -> tuple[dict, str | None]:
    email = spec["email"]
    existing = await _existing_teacher(email)
    if existing:
        # Bring its role in line with the spec — handles re-runs where
        # someone might have manually flipped a role.
        if existing.get("org_role") != spec["role"]:
            await _set_org_role(existing["id"], spec["role"])
            existing["org_role"] = spec["role"]
        if not existing.get("email_verified_at"):
            await _mark_email_verified(existing["id"])
        return existing, None  # second arg is "did we create a new org?"

    if shared_org_id is None:
        # First time through — bootstrap the test org via the admin.
        # Subsequent accounts attach to this org_id.
        result = await _create_first_admin(email, spec["name"])
        teacher_dict = {
            "id": result["teacher_id"],
            "email": email,
            "org_id": result["org_id"],
            "org_role": "admin",
        }
        return teacher_dict, result["org_id"]

    # Secondary account in the existing test org.
    result = await _create_secondary_user(email, spec["name"], spec["role"], shared_org_id)
    teacher_dict = {
        "id": result["teacher_id"],
        "email": email,
        "org_id": result["org_id"],
        "org_role": spec["role"],
    }
    return teacher_dict, None


async def main_async() -> int:
    print("Creating test accounts (idempotent — safe to re-run)...\n")
    org_id: str | None = None

    # If the test org already exists, pre-seed org_id so the first
    # spec doesn't try to re-create it.
    pre = await _existing_org_by_slug(_slugify(TEST_ORG_NAME))
    if pre:
        org_id = pre["id"]

    rows: list[dict] = []
    for spec in ACCOUNTS:
        teacher, new_org = await _ensure_account(spec, org_id)
        if new_org:
            org_id = new_org  # latch the org we just created
        if not teacher.get("org_id") and org_id:
            # The existing-teacher row might be in the org already; if
            # somehow it lacks the link (older signup?), patch it.
            from app.database import async_table as _atable
            await _atable("teachers").update({"org_id": org_id, "org_role": spec["role"]}).eq("id", teacher["id"]).execute()
            teacher["org_id"] = org_id
            teacher["org_role"] = spec["role"]
        token = _issue_jwt(teacher)
        rows.append({"spec": spec, "teacher": teacher, "token": token})

    # Pretty-print summary
    super_email = os.environ.get("SUPER_ADMIN_EMAIL", "").strip().lower()
    print(f"Org ID: {org_id}\n")
    for row in rows:
        spec, teacher, token = row["spec"], row["teacher"], row["token"]
        label = {"admin": "Admin", "teacher": "Teacher"}.get(spec["role"], spec["role"])
        if spec["email"] == "super@procta.test":
            promoted = (super_email == spec["email"])
            label = "Super Admin" + ("" if promoted else "  (NOT promoted — see note below)")
        print(f"  {spec['email']:24s} / {TEST_PASSWORD}   →  {label}")
        print(f"    teacher_id: {teacher['id']}")
        print(f"    jwt:        {token[:48]}...")
        print()

    if super_email != "super@procta.test":
        print("NOTE on Super Admin:")
        print("  issue_admin_token() promotes a teacher's JWT to org_role='superadmin'")
        print("  ONLY when their email matches the SUPER_ADMIN_EMAIL env var.")
        print(f"  Current SUPER_ADMIN_EMAIL = {super_email or '(unset)'!r}.")
        print("  To activate super@procta.test as superadmin, set on the KVM:")
        print("    SUPER_ADMIN_EMAIL=super@procta.test")
        print("  Then `docker compose up -d api` and log in again.")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
