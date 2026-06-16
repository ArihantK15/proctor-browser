"""LTI configuration helper — auto-config endpoint + setup guide."""

import os
from fastapi import APIRouter

router = APIRouter(prefix="/lti", tags=["lti"])


def _base_url() -> str:
    return os.environ.get("LTI_BASE_URL", os.environ.get("PUBLIC_URL", "https://app.procta.net")).rstrip("/")


@router.get("/auto-config")
def lti_auto_config():
    """LTI 1.3 Auto-configuration JSON (IMS Standard).

    LMS platforms can use this URL to auto-configure the Procta tool
    without manual entry of individual endpoints.
    """
    base = _base_url()
    return {
        "title": "Procta",
        "description": "AI-powered exam proctoring for educational institutions.",
        "oidc_login_initiation_url": f"{base}/lti/login",
        "target_link_uri": f"{base}/lti/launch",
        "public_jwk_url": f"{base}/lti/jwks",
        "scopes": [
            "https://purl.imsglobal.org/spec/lti-ags/scope/lineitem",
            "https://purl.imsglobal.org/spec/lti-ags/scope/result.readonly",
            "https://purl.imsglobal.org/spec/lti-nrps/scope/membership.readonly",
        ],
        "custom_parameters": {},
        "placements": [
            {
                "placement": "course_navigation",
                "target_link_uri": f"{base}/lti/launch",
                "text": "Procta Exams",
                "default": "disabled",
                "message_type": "LtiResourceLinkRequest",
            },
            {
                "placement": "assignment_selection",
                "target_link_uri": f"{base}/lti/launch",
                "text": "Procta Exam",
                "default": "disabled",
                "message_type": "LtiDeepLinkingRequest",
            },
        ],
    }
