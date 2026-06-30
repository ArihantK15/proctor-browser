"""Fixtures for LTI auto-config (routers/lti_config.py).

LMS platforms consume /lti/auto-config to wire up the tool, so the
endpoint URLs must be built from the correct base (env precedence:
LTI_BASE_URL > PUBLIC_URL > default) with no trailing-slash doubling.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


from app.routers.lti_config import _base_url, lti_auto_config


def test_base_url_prefers_lti_base_url(monkeypatch):
    monkeypatch.setenv("LTI_BASE_URL", "https://lti.example.edu/")
    monkeypatch.setenv("PUBLIC_URL", "https://public.example.edu")
    assert _base_url() == "https://lti.example.edu"  # trailing slash stripped


def test_base_url_falls_back_to_public_url(monkeypatch):
    monkeypatch.delenv("LTI_BASE_URL", raising=False)
    monkeypatch.setenv("PUBLIC_URL", "https://public.example.edu/")
    assert _base_url() == "https://public.example.edu"


def test_base_url_default(monkeypatch):
    monkeypatch.delenv("LTI_BASE_URL", raising=False)
    monkeypatch.delenv("PUBLIC_URL", raising=False)
    assert _base_url() == "https://app.procta.net"


def test_auto_config_builds_urls_from_base(monkeypatch):
    monkeypatch.setenv("LTI_BASE_URL", "https://lti.example.edu")
    cfg = lti_auto_config()
    assert cfg["oidc_login_initiation_url"] == "https://lti.example.edu/lti/login"
    assert cfg["target_link_uri"] == "https://lti.example.edu/lti/launch"
    assert cfg["public_jwk_url"] == "https://lti.example.edu/lti/jwks"
    # IMS scopes + both placements present
    assert len(cfg["scopes"]) == 3
    placements = {p["placement"] for p in cfg["placements"]}
    assert placements == {"course_navigation", "assignment_selection"}
    # deep-linking placement carries the right message type
    dl = next(p for p in cfg["placements"] if p["placement"] == "assignment_selection")
    assert dl["message_type"] == "LtiDeepLinkingRequest"
