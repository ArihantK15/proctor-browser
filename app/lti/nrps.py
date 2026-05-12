"""LTI 1.3 Names and Roles Provisioning Service (NRPS) — course roster sync.

This module provides:
  1. Fetching the course membership roster from the LMS
  2. Filtering by role (learners, instructors)
  3. Syncing membership data to local student accounts

The NRPS endpoint URL is provided by the LMS during the LTI launch
in the ``https://purl.imsglobal.org/spec/lti-nrps/claim/namesroleservice``
claim.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from .ags import get_access_token
from ..database import async_table as _atable

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = 15


async def fetch_membership(
    context_memberships_url: str,
    access_token: str,
    role_filter: Optional[str] = None,
) -> list[dict]:
    """Fetch the course membership roster from the LMS via NRPS.

    Args:
        context_memberships_url: The NRPS endpoint URL from the launch claim.
        access_token: OAuth2 bearer token (obtained via client_credentials grant).
        role_filter: Optional — filter by role, e.g. 'http://purl.imsglobal.org/vocab/lis/v2/membership#Learner'.

    Returns:
        List of membership dicts, each containing 'user_id', 'roles', 'name', 'email', etc.
        Returns empty list on failure.
    """
    url = context_memberships_url
    if role_filter:
        url += f"?role={role_filter}"

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(
                url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.ims.lis.v2.membershipcontainer+json",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("members", [])
    except Exception as e:
        logger.warning("Failed to fetch NRPS membership from %s: %s", url, e)
        return []


async def sync_learner_roster(
    context_memberships_url: str,
    access_token: str,
    teacher_id: str,
) -> dict:
    """Fetch learner membership from the LMS and create local student accounts.

    This is typically called after an LTI launch by an instructor to
    pre-provision student accounts for the course.

    Args:
        context_memberships_url: The NRPS endpoint URL.
        access_token: OAuth2 bearer token.
        teacher_id: The local teacher ID to associate students with.

    Returns:
        Summary dict with counts of created/existing/failed students.
    """
    members = await fetch_membership(
        context_memberships_url,
        access_token,
        role_filter="http://purl.imsglobal.org/vocab/lis/v2/membership#Learner",
    )

    if not members:
        return {"created": 0, "existing": 0, "failed": 0, "total": 0}

    created = 0
    existing = 0
    failed = 0

    for member in members:
        user_id = member.get("user_id", "")
        name = member.get("name", "")
        email = member.get("email", "") or f"lti_{user_id[:8]}@lti.procta.net"
        username = member.get("username", "")

        if not user_id:
            failed += 1
            continue

        lti_user_id = f"{user_id}"

        try:
            result = (await _atable("students")
                .select("id")
                .eq("lti_user_id", lti_user_id)
                .limit(1)
                .execute()).data

            if result:
                existing += 1
                continue

            roll = f"NRPS_{user_id[:12].upper()}"
            student = {
                "roll_number": roll,
                "full_name": name or username or f"Student {user_id[:8]}",
                "email": email,
                "lti_user_id": lti_user_id,
                "teacher_id": teacher_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            await _atable("students").insert(student).execute()
            created += 1
        except Exception as e:
            logger.warning("Failed to create NRPS student %s: %s", user_id, e)
            failed += 1

    return {
        "created": created,
        "existing": existing,
        "failed": failed,
        "total": len(members),
    }
