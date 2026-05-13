"""LTI 1.3 Deep Linking — receive content requests and respond with available exams.

Deep Linking allows an LMS to request our tool to return one or more
content items (exams/resources) that the instructor can link into their course.
"""

import json
import logging
import secrets
import time
from datetime import datetime, timezone
from typing import Optional

from .key import sign_jwt_payload, get_kid
from .launch import _fetch_platform_jwks, find_registration
from ..database import async_table as _atable

logger = logging.getLogger(__name__)


async def validate_deep_linking_request(id_token: str) -> dict:
    """Validate the Deep Linking request JWT from the LMS.

    Follows the same verification as launch validation but checks
    message_type == LtiDeepLinkingRequest.

    Returns the validated claims dict on success.
    Raises ValueError with a description on failure.
    """
    from .jwk_utils import jwk_to_public_key
    import jwt as _jwt
    import base64 as b64
    import json as _json

    try:
        header_b64 = id_token.split(".")[0]
        header_padded = header_b64 + "=" * (4 - len(header_b64) % 4)
        header_bytes = b64.urlsafe_b64decode(header_padded)
        header = _json.loads(header_bytes)
    except Exception as e:
        raise ValueError(f"Failed to decode JWT header: {e}")

    kid = header.get("kid", "")
    alg = header.get("alg", "RS256")

    try:
        payload_b64 = id_token.split(".")[1]
        payload_padded = payload_b64 + "=" * (4 - len(payload_b64) % 4)
        payload_bytes = b64.urlsafe_b64decode(payload_padded)
        claims = _json.loads(payload_bytes)
    except Exception as e:
        raise ValueError(f"Failed to decode JWT payload: {e}")

    msg_type = claims.get(
        "https://purl.imsglobal.org/spec/lti/claim/message_type", ""
    )
    if msg_type != "LtiDeepLinkingRequest":
        raise ValueError(
            f"Expected LtiDeepLinkingRequest, got {msg_type}"
        )

    iss = claims.get("iss", "")
    aud_raw = claims.get("aud", "")
    client_id = aud_raw if isinstance(aud_raw, str) else (aud_raw[0] if isinstance(aud_raw, list) else "")

    if not iss or not client_id:
        raise ValueError("Missing iss or aud in deep linking request")

    registration = find_registration(iss, client_id)
    if not registration:
        raise ValueError(f"No registration found for issuer={iss} client_id={client_id}")

    try:
        jwks_data = await _fetch_platform_jwks(registration.key_set_url)
    except Exception:
        raise ValueError("Failed to fetch platform JWKS")

    if not jwks_data:
        raise ValueError("Failed to fetch platform JWKS")

    matching_key = None
    for key in jwks_data.get("keys", []):
        if key.get("kid") == kid:
            matching_key = key
            break
    if not matching_key:
        raise ValueError(f"No matching key found for kid={kid}")

    try:
        public_key = jwk_to_public_key(matching_key)
        verified = _jwt.decode(
            id_token,
            public_key,
            audience=client_id,
            issuer=iss,
            algorithms=[alg],
            options={
                "verify_signature": True,
                "verify_aud": True,
                "verify_exp": True,
                "verify_iat": True,
                "verify_iss": True,
            },
        )
    except Exception as e:
        raise ValueError(f"JWT verification failed: {e}")

    return verified


def build_deep_linking_response(
    claims: dict,
    content_items: list[dict],
) -> str:
    """Build and sign a Deep Linking Response JWT.

    Returns a signed JWT string that should be POSTed back to the LMS
    at the deep_link_return_url.

    Args:
        claims: The validated deep linking request claims.
        content_items: List of LTI content item dicts (typically ltiResourceLink).

    Returns:
        Signed JWT string ready for form POST.
    """
    iss = claims.get("iss", "")
    aud_raw = claims.get("aud", "")
    aud = aud_raw if isinstance(aud_raw, list) else [aud_raw]
    deployment_id = claims.get(
        "https://purl.imsglobal.org/spec/lti/claim/deployment_id", ""
    )
    data = claims.get(
        "https://purl.imsglobal.org/spec/lti-dl/claim/data", ""
    )

    now = datetime.now(timezone.utc)
    response_payload = {
        "iss": "https://app.procta.net",
        "aud": aud,
        "exp": int(now.timestamp()) + 3600,
        "iat": int(now.timestamp()),
        "nonce": secrets.token_urlsafe(32),
        "https://purl.imsglobal.org/spec/lti/claim/message_type": "LtiDeepLinkingResponse",
        "https://purl.imsglobal.org/spec/lti/claim/deployment_id": deployment_id,
        "https://purl.imsglobal.org/spec/lti-dl/claim/content_items": content_items,
        "https://purl.imsglobal.org/spec/lti-dl/claim/data": data,
    }

    return sign_jwt_payload(response_payload)


async def get_teacher_exams_as_content_items(teacher_id: str) -> list[dict]:
    """Fetch the teacher's exams and format them as LTI content items.

    Returns a list of ltiResourceLink content items.
    """
    exams = (await _atable("exam_config")
        .select("exam_id,title,description,duration_minutes")
        .eq("teacher_id", teacher_id)
        .order("created_at", desc=True)
        .execute()).data or []

    items = []
    for exam in exams:
        eid = exam.get("exam_id", "")
        title = exam.get("title", "Untitled Exam")
        desc = exam.get("description") or f"Duration: {exam.get('duration_minutes', 0)} min"

        items.append({
            "type": "ltiResourceLink",
            "title": title,
            "text": desc,
            "url": "https://app.procta.net/lti/launch",
            "custom": {
                "exam_id": eid,
            },
            "icon": {
                "url": "https://app.procta.net/static/logo.svg",
                "width": 64,
                "height": 64,
            },
            "window": {
                "targetName": "procta-exam",
                "windowFeatures": "width=1024,height=768",
            },
        })

    return items


def build_auto_submit_html(
    return_url: str,
    jwt_response: str,
) -> str:
    """Build an HTML page that auto-submits the Deep Linking response to the LMS.

    The LMS expects a form POST to the deep_link_return_url with the
    JWT in a form field called 'JWT'.
    """
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Redirecting to LMS...</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
           display: flex; justify-content: center; align-items: center;
           height: 100vh; margin: 0; background: #f5f5f5; color: #333; }}
    .card {{ background: white; padding: 40px; border-radius: 12px;
             box-shadow: 0 2px 12px rgba(0,0,0,0.1); text-align: center; }}
    .spinner {{ border: 3px solid #e0e0e0; border-top: 3px solid #4f46e5;
                border-radius: 50%; width: 32px; height: 32px;
                animation: spin 0.8s linear infinite; margin: 20px auto; }}
    @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
  </style>
</head>
<body>
  <div class="card">
    <h2>Content selection complete</h2>
    <div class="spinner"></div>
    <p>Returning to your LMS...</p>
  </div>
  <form id="dlform" method="post" action="{return_url}">
    <input type="hidden" name="JWT" value="{jwt_response}">
  </form>
  <script>document.getElementById('dlform').submit();</script>
</body>
</html>"""
