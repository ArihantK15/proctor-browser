"""LTI 1.3 Assignment and Grade Services (AGS) — grade passback to LMS.

This module provides:
  1. OAuth2 client_credentials token acquisition from the LMS
  2. Line item lookup and creation
  3. Score posting (grade passback)

The AGS endpoint URLs and scopes are provided by the LMS during the
LTI launch in the ``https://purl.imsglobal.org/spec/lti-ags/claim/endpoint``
claim.
"""

from ..log_safe import safe
import json
import logging
import time
import uuid
from typing import Any, Optional

import httpx

from .key import sign_jwt_payload, get_private_key, get_kid
from .registration import find_registration
logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = 15


def _build_client_assertion(
    issuer: str,
    client_id: str,
    auth_token_url: str,
) -> str:
    """Build a JWT client_assertion for OAuth2 client_credentials grant.

    The assertion is signed with our private key and used to obtain
    an access token from the LMS token endpoint.
    """
    now = int(time.time())
    # The previous jti was f"{issuer}-{client_id}-{now}" — two
    # assertions built in the same second for the same (issuer,
    # client_id) collided. LMSes that track jti for replay protection
    # (Canvas does) would reject the second one. Appending a random
    # uuid4 component makes collisions impossible.
    payload = {
        "iss": issuer,
        "sub": client_id,
        "aud": auth_token_url,
        "iat": now,
        "exp": now + 3600,
        "jti": f"{issuer}-{client_id}-{now}-{uuid.uuid4().hex}",
    }
    return sign_jwt_payload(payload)


async def get_access_token(
    issuer: str,
    client_id: str,
    auth_token_url: str,
    scopes: list[str],
) -> str | None:
    """Obtain an OAuth2 access token from the LMS for AGS/NRPS calls.

    Uses the client_credentials grant type with a JWT client_assertion
    signed by our private key.

    Returns the access token string, or None on failure.
    """
    client_assertion = _build_client_assertion(
        issuer, client_id, auth_token_url
    )

    data = {
        "grant_type": "client_credentials",
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": client_assertion,
        "scope": " ".join(scopes),
    }

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(
                auth_token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            result = resp.json()
            return result.get("access_token")
    except Exception as e:
        logger.warning("Failed to obtain AGS access token from %s: %s", auth_token_url, e)  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
        return None


async def post_score(
    lineitem_url: str,
    access_token: str,
    user_id: str,
    score_given: float,
    score_maximum: float,
    timestamp: str,
    activity_progress: str = "Completed",
    grading_progress: str = "FullyGraded",
    comment: str = "",
) -> bool:
    """Post a score for a user to an AGS line item.

    Args:
        lineitem_url: The full URL of the line item (from the LMS).
        access_token: OAuth2 bearer token for the LMS AGS API.
        user_id: The LTI user ID (sub claim from the launch id_token).
        score_given: The score the student received.
        score_maximum: The maximum possible score.
        timestamp: ISO 8601 timestamp of the score.
        activity_progress: One of Initialized, Started, InProgress, Submitted, Completed.
        grading_progress: One of FullyGraded, Pending, PendingManual, Failed, NotReady.
        comment: Optional instructor comment.

    Returns:
        True if the score was posted successfully.
    """
    body = {
        "userId": user_id,
        "scoreGiven": score_given,
        "scoreMaximum": score_maximum,
        "timestamp": timestamp,
        "activityProgress": activity_progress,
        "gradingProgress": grading_progress,
    }
    if comment:
        body["comment"] = comment

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(
                lineitem_url.rstrip("/") + "/scores",
                json=body,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/vnd.ims.lis.v2.score+json",
                },
            )
            resp.raise_for_status()
            logger.info(
                "AGS score posted: userId=%s scoreGiven=%s scoreMaximum=%s",
                safe(user_id), score_given, score_maximum,
            )
            return True
    except Exception as e:
        logger.warning("Failed to post AGS score to %s: %s", lineitem_url, e)
        return False


async def get_results(
    lineitem_url: str,
    access_token: str,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    """Read results for a line item from the LMS.

    Args:
        lineitem_url: The full URL of the line item.
        access_token: OAuth2 bearer token.
        user_id: Optional — if provided, only results for this user.

    Returns:
        List of result dicts, or empty list on failure.
    """
    url = lineitem_url.rstrip("/") + "/results"
    if user_id:
        url += f"?user_id={user_id}"

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(
                url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.ims.lis.v2.resultcontainer+json",
                },
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.warning("Failed to get AGS results from %s: %s", url, e)
        return []


async def create_line_item(
    lineitems_url: str,
    access_token: str,
    label: str,
    score_maximum: float,
    resource_id: str = "",
    tag: str = "",
) -> str | None:
    """Create a new line item in the LMS.

    Args:
        lineitems_url: The base URL for line items collection.
        access_token: OAuth2 bearer token.
        label: Human-readable label for the line item.
        score_maximum: Maximum score.
        resource_id: Optional resource ID linking to our tool.
        tag: Optional tag for grouping.

    Returns:
        The URL of the created line item, or None on failure.
    """
    body = {
        "label": label,
        "scoreMaximum": score_maximum,
        "resourceId": resource_id or label.lower().replace(" ", "-"),
    }
    if tag:
        body["tag"] = tag

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(
                lineitems_url,
                json=body,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/vnd.ims.lis.v2.lineitem+json",
                },
            )
            resp.raise_for_status()
            location = resp.headers.get("Location", "")
            if location:
                return location
            result = resp.json()
            return result.get("id", "")
    except Exception as e:
        logger.warning("Failed to create line item at %s: %s", lineitems_url, e)
        return None
