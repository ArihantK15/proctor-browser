"""LTI edge-case tests beyond the existing test_lti.py coverage.

Covers:
  1. Launch with admin role     → still 302 (treated as instructor)
  2. Launch with unknown role   → defaults to learner redirect
  3. Deep linking missing return URL → 400
  4. Deep linking wrong message_type → 401
  5. Launch user creation failure    → 500
  6. OIDC login with extra params still succeeds
  7. Config endpoint includes required LTI 1.3 fields
"""

import json
import os
import sys
import secrets
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")

from tests.conftest import make_admin_token, shared_supabase_mock
from tests.test_lti import _make_test_id_token, _static_jwks, TEST_PRIV_PEM


TEST_REG_JSON = json.dumps([{
    "issuer": "https://test.canvas.edu",
    "client_id": "test-client-1",
    "auth_login_url": "https://test.canvas.edu/api/lti/authorize_redirect",
    "auth_token_url": "https://test.canvas.edu/login/oauth2/token",
    "key_set_url": "https://test.canvas.edu/api/lti/security/jwks",
    "deployment_ids": ["deployment-1"],
    "platform_name": "Test Canvas",
    "org_id": "11111111-1111-1111-1111-111111111111",
}])


# ═══════════════════════════════════════════════════════════════════
#  Launch Role Edge Cases
# ═══════════════════════════════════════════════════════════════════

class TestLtiLaunchRoles:

    @pytest.fixture(autouse=True)
    def _setup(self):
        with patch.dict(os.environ, {"LTI_REGISTRATIONS": TEST_REG_JSON}):
            from app.lti.registration import clear_cache
            clear_cache()
            with patch("app.lti.launch._cache", None):
                from app.lti import launch as launch_mod
                launch_mod._nonces.clear()
                launch_mod._states.clear()
                yield

    def _launch(self, client, id_token, state="test-state-1"):
        return client.post(
            "/lti/launch",
            data={"id_token": id_token, "state": state},
            headers={"content-type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )

    def _setup_nonce_state(self):
        from app.lti.launch import _store_nonce, _store_state
        nonce = secrets.token_urlsafe(32)
        state = secrets.token_urlsafe(32)
        _store_nonce(nonce)
        _store_state(state, {"nonce": nonce, "target_link_uri": "/lti/launch"})
        return nonce, state

    def test_admin_role_redirects_to_dashboard(self, client):
        """Admin LTI role should be treated like instructor → /dashboard."""
        nonce, state = self._setup_nonce_state()
        id_token = _make_test_id_token({
            "nonce": nonce,
            "https://purl.imsglobal.org/spec/lti/claim/roles": [
                "http://purl.imsglobal.org/vocab/lis/v2/membership#Administrator"
            ],
        })
        with patch("app.lti.launch._fetch_platform_jwks", return_value=_static_jwks()), \
             patch("app.lti.launch._atable") as atable:
            atable.return_value.select.return_value.eq.return_value.limit.return_value.execute = AsyncMock(
                return_value=MagicMock(data=[])
            )
            atable.return_value.insert.return_value.execute = AsyncMock()
            resp = self._launch(client, id_token, state)
        assert resp.status_code == 302, resp.text
        assert "dashboard" in resp.headers.get("location", "")

    def test_unknown_role_defaults_to_learner(self, client):
        """Unrecognized LTI role should fall back to learner → /student."""
        nonce, state = self._setup_nonce_state()
        id_token = _make_test_id_token({
            "nonce": nonce,
            "https://purl.imsglobal.org/spec/lti/claim/roles": [
                "http://example.com/role/unknown"
            ],
        })
        with patch("app.lti.launch._fetch_platform_jwks", return_value=_static_jwks()), \
             patch("app.lti.launch._atable") as atable:
            atable.return_value.select.return_value.eq.return_value.limit.return_value.execute = AsyncMock(
                return_value=MagicMock(data=[])
            )
            atable.return_value.insert.return_value.execute = AsyncMock()
            resp = self._launch(client, id_token, state)
        assert resp.status_code == 302, resp.text
        assert "student" in resp.headers.get("location", "").lower()

    def test_learner_redirects_to_student(self, client):
        """Standard learner role → /student."""
        nonce, state = self._setup_nonce_state()
        id_token = _make_test_id_token({"nonce": nonce})
        with patch("app.lti.launch._fetch_platform_jwks", return_value=_static_jwks()), \
             patch("app.lti.launch._atable") as atable:
            atable.return_value.select.return_value.eq.return_value.limit.return_value.execute = AsyncMock(
                return_value=MagicMock(data=[])
            )
            atable.return_value.insert.return_value.execute = AsyncMock()
            resp = self._launch(client, id_token, state)
        assert resp.status_code == 302, resp.text
        assert "student" in resp.headers.get("location", "").lower()

    def test_instructor_redirects_to_dashboard(self, client):
        """Standard instructor role → /dashboard."""
        nonce, state = self._setup_nonce_state()
        id_token = _make_test_id_token({
            "nonce": nonce,
            "https://purl.imsglobal.org/spec/lti/claim/roles": [
                "http://purl.imsglobal.org/vocab/lis/v2/membership#Instructor"
            ],
        })
        with patch("app.lti.launch._fetch_platform_jwks", return_value=_static_jwks()), \
             patch("app.lti.launch._atable") as atable:
            atable.return_value.select.return_value.eq.return_value.limit.return_value.execute = AsyncMock(
                return_value=MagicMock(data=[])
            )
            atable.return_value.insert.return_value.execute = AsyncMock()
            resp = self._launch(client, id_token, state)
        assert resp.status_code == 302, resp.text
        assert "dashboard" in resp.headers.get("location", "")

    def test_launch_user_creation_failure_500(self, client):
        """If user creation fails, endpoint should return 500."""
        nonce, state = self._setup_nonce_state()
        id_token = _make_test_id_token({"nonce": nonce})
        with patch("app.lti.launch._fetch_platform_jwks", return_value=_static_jwks()), \
             patch("app.lti.launch._atable") as atable:
            atable.return_value.select.return_value.eq.return_value.limit.return_value.execute = AsyncMock(
                return_value=MagicMock(data=[])
            )
            atable.return_value.insert.side_effect = Exception("DB unavailable")
            resp = self._launch(client, id_token, state)
        assert resp.status_code == 500, resp.text


# ═══════════════════════════════════════════════════════════════════
#  Deep Linking Edge Cases
# ═══════════════════════════════════════════════════════════════════

class TestDeepLinkingEdgeCases:

    @pytest.fixture(autouse=True)
    def _setup(self):
        with patch.dict(os.environ, {"LTI_REGISTRATIONS": TEST_REG_JSON}):
            from app.lti.registration import clear_cache
            clear_cache()
            with patch("app.lti.launch._cache", None):
                from app.lti import launch as launch_mod
                launch_mod._nonces.clear()
                launch_mod._states.clear()
                launch_mod._lti_contexts.clear()
                yield

    def _build_jwt(self, claims=None):
        import jwt as jose_jwt
        payload = {
            "iss": "https://test.canvas.edu",
            "aud": "test-client-1",
            "sub": "instructor-sub-123",
            "exp": int(time.time()) + 3600,
            "iat": int(time.time()),
            "nonce": secrets.token_urlsafe(32),
            "https://purl.imsglobal.org/spec/lti/claim/message_type": "LtiDeepLinkingRequest",
            "https://purl.imsglobal.org/spec/lti/claim/deployment_id": "deployment-1",
            "https://purl.imsglobal.org/spec/lti/claim/roles": [
                "http://purl.imsglobal.org/vocab/lis/v2/membership#Instructor"
            ],
            "https://purl.imsglobal.org/spec/lti/claim/context": {
                "id": "course-123", "label": "CS101", "title": "Intro to CS",
            },
            "https://purl.imsglobal.org/spec/lti-dl/claim/data": "dl-data-123",
            "https://purl.imsglobal.org/spec/lti-dl/claim/return_url": "https://test.canvas.edu/deep_link_return",
            "https://purl.imsglobal.org/spec/lti/claim/target_link_uri": "https://app.procta.net/lti/deeplink",
            "name": "Instructor",
            "email": "inst@test.edu",
            **(claims or {}),
        }
        return jose_jwt.encode(payload, TEST_PRIV_PEM, algorithm="RS256",
                               headers={"kid": "test-key-1", "typ": "JWT"})

    def test_missing_return_url_400(self, client):
        """Deep linking request without return_url should be rejected."""
        jwt_token = self._build_jwt({
            "https://purl.imsglobal.org/spec/lti-dl/claim/return_url": None,
        })
        with patch("app.lti.deeplink._fetch_platform_jwks", return_value=_static_jwks()):
            resp = client.post("/lti/deeplink", data={"JWT": jwt_token})
        assert resp.status_code == 400, resp.text

    def test_wrong_message_type_401(self, client):
        """Deep linking with wrong message_type should be rejected."""
        jwt_token = self._build_jwt({
            "https://purl.imsglobal.org/spec/lti/claim/message_type": "LtiResourceLinkRequest",
        })
        with patch("app.lti.deeplink._fetch_platform_jwks", return_value=_static_jwks()):
            resp = client.post("/lti/deeplink", data={"JWT": jwt_token})
        assert resp.status_code == 401, resp.text


# ═══════════════════════════════════════════════════════════════════
#  OIDC Login Edge Cases
# ═══════════════════════════════════════════════════════════════════

class TestOidcLoginEdgeCases:

    def test_missing_all_params_400(self, client):
        resp = client.get("/lti/login")
        assert resp.status_code == 400

    def test_unknown_platform_400(self, client):
        with patch.dict(os.environ, {"LTI_REGISTRATIONS": TEST_REG_JSON}):
            from app.lti.registration import clear_cache
            clear_cache()
            resp = client.get("/lti/login", params={
                "iss": "https://unknown.com",
                "login_hint": "hint-1",
                "target_link_uri": "https://app.procta.net/lti/launch",
                "client_id": "unknown",
            })
        assert resp.status_code == 400

    def test_success_with_all_optional_params(self, client):
        """All optional OIDC params should be accepted."""
        with patch.dict(os.environ, {"LTI_REGISTRATIONS": TEST_REG_JSON}):
            from app.lti.registration import clear_cache
            clear_cache()
            resp = client.get("/lti/login", params={
                "iss": "https://test.canvas.edu",
                "login_hint": "hint-1",
                "target_link_uri": "https://app.procta.net/lti/launch",
                "client_id": "test-client-1",
                "lti_message_hint": "msg-hint",
                "lti_deployment_id": "deployment-1",
            }, follow_redirects=False)
        assert resp.status_code in (302, 307)
        location = resp.headers.get("location", "")
        assert "response_type=id_token" in location


# ═══════════════════════════════════════════════════════════════════
#  Config Endpoint
# ═══════════════════════════════════════════════════════════════════

class TestConfigEndpoint:

    def test_config_has_required_fields(self, client):
        resp = client.get("/lti/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Procta"
        assert "oidc_login_url" in data
        assert "public_jwk_url" in data
        assert "scopes" in data
        assert isinstance(data["scopes"], list)
        assert "https://purl.imsglobal.org/spec/lti-ags/scope/lineitem" in data["scopes"]


# ═══════════════════════════════════════════════════════════════════
#  JWKS Edge Cases
# ═══════════════════════════════════════════════════════════════════

class TestJwksEdgeCases:

    def test_jwks_has_valid_rsa_key(self, client):
        resp = client.get("/lti/jwks")
        assert resp.status_code == 200
        keys = resp.json()["keys"]
        assert len(keys) == 1
        key = keys[0]
        assert key["kty"] == "RSA"
        assert key["alg"] == "RS256"
        assert "n" in key
        assert "e" in key
        assert key["use"] == "sig"

    def test_jwks_json_alias(self, client):
        resp = client.get("/lti/jwks.json")
        assert resp.status_code == 200
