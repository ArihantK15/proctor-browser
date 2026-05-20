#!/usr/bin/env python3
"""Create a teacher account for load testing — no CAPTCHA, no email verification.

Runs inside the API container so it uses the same Postgres pool, JWT secret,
and password hasher as production. Idempotent: if the email already exists,
it prints the existing teacher_id + a freshly issued JWT.

Usage (from repo root on the KVM):

    docker compose run --rm --no-deps --entrypoint python api \
      scripts/create_loadtest_teacher.py \
      --email loadtest@procta.net \
      --password 'LoadTest!2026' \
      --full-name 'Load Test Teacher' \
      --org-name 'Procta Load Test Org'

Output (stdout):

    teacher_id:     <uuid>
    org_id:         <uuid>
    default_exam:   <uuid>
    access_token:   <JWT for /api/v1/admin/* calls>

The JWT has the standard 12 h TTL. Re-run the script (or call
/api/v1/auth/login) to mint a fresh one.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import uuid

# Make `app.*` imports work when run as a script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower().strip())
    return s.strip("-") or "org"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--email", required=True, help="Teacher email (lowercased)")
    p.add_argument("--password", required=True, help="Password (min 8 chars for load testing — production rules are stricter)")
    p.add_argument("--full-name", default="Load Test Teacher", help="Display name")
    p.add_argument("--org-name", default="Procta Load Test Org", help="Organization name")
    p.add_argument("--mark-verified", action="store_true", default=True,
                   help="Mark email as verified (default: yes — load test accounts shouldn't need an inbox)")
    return p.parse_args()


async def _existing_teacher(email: str) -> dict | None:
    """Fetch teacher row including org_id + org_role — these are required
    by issue_admin_token() so the JWT carries the right org claims for
    /api/v1/admin/* endpoints. Without org_role='admin' the load test
    can't call /admin/exams or /admin/register-students-bulk.
    """
    from app.database import async_table as _atable
    rows = await _atable("teachers").select(
        "id,email,full_name,org_id,org_role,supabase_uid"
    ).eq("email", email).limit(1).execute()
    return rows.data[0] if rows.data else None


async def _create_teacher(email: str, password: str, full_name: str, org_name: str) -> dict:
    """Create org + trial subscription + teacher + default exam in one tx."""
    from app.services.local_auth import hash_password
    from app.routers.auth import _create_teacher_signup_postgres_tx

    password_hash = await hash_password(password)
    supabase_uid = str(uuid.uuid4())  # synthetic — not a real Supabase user, but the column requires a value
    slug = _slugify(org_name)

    teacher, org_id, default_exam_id = await _create_teacher_signup_postgres_tx(
        email=email,
        name=full_name,
        org_name=org_name,
        slug=slug,
        supabase_uid=supabase_uid,
        password_hash=password_hash,
    )
    return {
        "teacher_id":   teacher["id"],
        "org_id":       org_id,
        "default_exam": default_exam_id,
    }


async def _mark_email_verified(teacher_id: str) -> None:
    """Set email_verified_at so login won't 403 with EMAIL_UNVERIFIED."""
    from app.database import async_table as _atable
    from app.utils import now_ist
    await _atable("teachers").update({
        "email_verified_at": now_ist().isoformat(),
    }).eq("id", str(teacher_id)).execute()


def _issue_jwt(teacher: dict) -> str:
    """Mint a 12 h admin JWT — same shape as /api/v1/auth/login returns.

    issue_admin_token() takes the full teacher dict so it can embed
    org_id + org_role claims; without those the JWT can't reach the
    /api/v1/admin/* endpoints that the load test relies on.
    """
    from app.auth import issue_admin_token
    return issue_admin_token(teacher)


async def main_async(args: argparse.Namespace) -> int:
    email = args.email.strip().lower()

    existing = await _existing_teacher(email)
    if existing:
        teacher_dict = existing
        teacher_id = existing["id"]
        org_id = existing.get("org_id") or ""
        default_exam_id = ""  # not returned by the existing-teacher query
        print(f"# Teacher with email {email!r} already exists — issuing fresh JWT")
    else:
        result = await _create_teacher(email, args.password, args.full_name, args.org_name)
        teacher_id = result["teacher_id"]
        org_id = result["org_id"]
        default_exam_id = result["default_exam"]
        # Build the dict shape issue_admin_token expects. The signup tx
        # always sets org_role='admin' so the first user can manage the org.
        teacher_dict = {
            "id":       teacher_id,
            "email":    email,
            "org_id":   org_id,
            "org_role": "admin",
        }
        print(f"# Created new teacher + org for {email!r}")

    if args.mark_verified:
        try:
            await _mark_email_verified(teacher_id)
        except Exception as e:
            print(f"# WARN: failed to mark email verified: {e}", file=sys.stderr)

    token = _issue_jwt(teacher_dict)

    print(f"teacher_id:     {teacher_id}")
    print(f"org_id:         {org_id}")
    print(f"org_role:       {teacher_dict.get('org_role', '')}")
    if default_exam_id:
        print(f"default_exam:   {default_exam_id}")
    print(f"email:          {email}")
    print(f"password:       {args.password}")
    print(f"access_token:   {token}")
    print("")
    print("# Quick sanity check (org should return 200):")
    print(f"#   curl -H 'Authorization: Bearer {token}' https://app.procta.net/api/v1/org")
    return 0


def main() -> int:
    args = _parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
