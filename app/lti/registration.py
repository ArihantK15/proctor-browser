"""LTI 1.3 platform registration management.

Supports multiple LMS platform registrations (Canvas, Moodle, Google Classroom, etc.)
via the LTI_REGISTRATIONS env var (JSON array) or DB-backed storage.

Each registration represents one LMS platform that has installed our LTI tool.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class PlatformRegistration:
    issuer: str
    client_id: str
    auth_login_url: str
    auth_token_url: str
    key_set_url: str
    deployment_ids: list[str] = field(default_factory=list)
    platform_name: Optional[str] = None
    # Tenant binding: which Procta organization owns this LMS integration.
    # Every user provisioned from a launch on this platform is stamped with
    # this org_id so LTI identities fit the tenant-isolation (RLS) model.
    # A registration with no org_id is rejected at launch (fail-closed) —
    # see find_or_create_lti_user.
    org_id: Optional[str] = None


_registrations_cache: list[PlatformRegistration] | None = None


def _load_from_env() -> list[PlatformRegistration]:
    raw = os.environ.get("LTI_REGISTRATIONS", "")
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
        regs = []
        for item in data:
            regs.append(PlatformRegistration(
                issuer=item.get("issuer", ""),
                client_id=item.get("client_id", ""),
                auth_login_url=item.get("auth_login_url", ""),
                auth_token_url=item.get("auth_token_url", ""),
                key_set_url=item.get("key_set_url", ""),
                deployment_ids=item.get("deployment_ids", []),
                platform_name=item.get("platform_name"),
                org_id=item.get("org_id") or None,
            ))
        return regs
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("Failed to parse LTI_REGISTRATIONS env var: %s", e)
        return []


def _load_legacy_single() -> list[PlatformRegistration]:
    issuer = os.environ.get("LTI_ISSUER", "")
    client_id = os.environ.get("LTI_CLIENT_ID", "")
    if not issuer and not client_id:
        return []
    return [PlatformRegistration(
        issuer=issuer,
        client_id=client_id,
        auth_login_url=os.environ.get("LTI_AUTH_LOGIN_URL", ""),
        auth_token_url=os.environ.get("LTI_AUTH_TOKEN_URL", ""),
        key_set_url=os.environ.get("LTI_KEY_SET_URL", ""),
        deployment_ids=[
            s.strip() for s in os.environ.get("LTI_DEPLOYMENT_IDS", "").split(",") if s.strip()
        ],
        org_id=os.environ.get("LTI_ORG_ID", "").strip() or None,
    )]


def load_registrations() -> list[PlatformRegistration]:
    global _registrations_cache
    if _registrations_cache is not None:
        return _registrations_cache

    regs = _load_from_env()
    if not regs:
        regs = _load_legacy_single()
    _registrations_cache = regs
    return regs


def _norm_iss(s: str) -> str:
    """Normalise an issuer for comparison.

    The LTI spec treats the issuer as an exact opaque string, but the
    single most common real-world misconfig is a trailing-slash
    mismatch (platform sends ``https://x.org`` while the registration is
    stored as ``https://x.org/`` or vice-versa). We tolerate only that
    difference — strip surrounding whitespace and a trailing slash —
    while keeping the comparison otherwise exact (case-sensitive, no
    scheme/host munging) so we don't accidentally match distinct
    platforms.
    """
    return (s or "").strip().rstrip("/")


def find_registration(issuer: str, client_id: str) -> Optional[PlatformRegistration]:
    want_iss = _norm_iss(issuer)
    want_cid = (client_id or "").strip()
    for r in load_registrations():
        if _norm_iss(r.issuer) == want_iss and (r.client_id or "").strip() == want_cid:
            return r
    return None


def is_deployment_authorized(registration: PlatformRegistration, deployment_id: str) -> bool:
    if not registration.deployment_ids:
        return True
    return deployment_id in registration.deployment_ids


def clear_cache():
    global _registrations_cache
    _registrations_cache = None
