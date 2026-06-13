"""
Tests for LTI 1.3 integration — JWKS, OIDC login, launch validation.

Covers:
  1. JWKS endpoint returns valid key material
  2. Key pair generation and signing
  3. Registration loading from env vars
  4. OIDC login initiation (happy + missing params)
  5. LTI launch validation (happy, bad sig, replayed nonce, wrong aud, expired)
  6. LTI launch user creation (learner, instructor)
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock

import pytest

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.conftest import make_admin_token, shared_supabase_mock

# ── RSA key helpers for test JWT signing ──────────────────────────
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend

_test_priv_key = rsa.generate_private_key(
    public_exponent=65537, key_size=2048, backend=default_backend()
)
_test_pub_key = _test_priv_key.public_key()

TEST_PRIV_PEM = _test_priv_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.TraditionalOpenSSL,
    encryption_algorithm=serialization.NoEncryption(),
).decode()

TEST_PUB_PEM = _test_pub_key.public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
).decode()


def _make_test_id_token(
    claims: dict = None,
    override_priv_pem: str = None,
    override_kid: str = None,
) -> str:
    """Create an RS256-signed JWT for testing LTI launches."""
    import jwt as jose_jwt
    pem = override_priv_pem or TEST_PRIV_PEM
    headers = {"kid": override_kid or "test-key-1", "typ": "JWT"}
    payload = {
        "iss": "https://test.canvas.edu",
        "aud": "test-client-1",
        "sub": "lti-user-abc-123",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        "nonce": "test-nonce-1",
        "https://purl.imsglobal.org/spec/lti/claim/deployment_id": "deployment-1",
        "https://purl.imsglobal.org/spec/lti/claim/roles": [
            "http://purl.imsglobal.org/vocab/lis/v2/membership#Learner"
        ],
        "https://purl.imsglobal.org/spec/lti/claim/context": {
            "id": "course-123",
            "label": "CS101",
            "title": "Intro to CS",
        },
        "https://purl.imsglobal.org/spec/lti/claim/target_link_uri": "https://app.procta.net/lti/launch",
        "name": "Test Student",
        "email": "student@test.edu",
        "given_name": "Test",
        "family_name": "Student",
        **(claims or {}),
    }
    return jose_jwt.encode(payload, pem, algorithm="RS256", headers=headers)


def _static_jwks():
    """Build a JWKS response from the test public key."""
    pub_numbers = _test_pub_key.public_numbers()
    import base64
    n = base64.urlsafe_b64encode(
        pub_numbers.n.to_bytes((pub_numbers.n.bit_length() + 7) // 8, byteorder="big")
    ).rstrip(b"=").decode()
    e = base64.urlsafe_b64encode(
        pub_numbers.e.to_bytes(3, byteorder="big")
    ).rstrip(b"=").decode()
    return {
        "keys": [
            {"kty": "RSA", "alg": "RS256", "use": "sig", "kid": "test-key-1",
             "n": n, "e": e}
        ]
    }


def _mock_jwks_fetch(*args, **kwargs):
    """Return the test JWKS as if fetched from an LMS."""
    return _static_jwks()


# ═══════════════════════════════════════════════════════════════════
#  JWKS Endpoint
# ═══════════════════════════════════════════════════════════════════
class TestJwksEndpoint:
    def test_returns_valid_jwks(self, client):
        resp = client.get("/lti/jwks")
        assert resp.status_code == 200
        data = resp.json()
        assert "keys" in data
        assert len(data["keys"]) == 1
        key = data["keys"][0]
        assert key["kty"] == "RSA"
        assert key["alg"] == "RS256"
        assert key["use"] == "sig"
        assert "kid" in key
        assert "n" in key
        assert "e" in key

    def test_jwks_json_alias(self, client):
        resp = client.get("/lti/jwks.json")
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════
#  Key Management
# ═══════════════════════════════════════════════════════════════════
class TestKeyManagement:
    def test_generates_key_pair(self):
        from app.lti.key import get_key_pair
        priv, pub = get_key_pair()
        assert priv.startswith("-----BEGIN")
        assert pub.startswith("-----BEGIN")
        assert "PRIVATE KEY" in priv
        assert "PUBLIC KEY" in pub

    def test_sign_and_generate_jwks(self):
        from app.lti.key import generate_jwks, get_kid
        jwks = generate_jwks()
        assert len(jwks["keys"]) == 1
        assert jwks["keys"][0]["kid"] == get_kid()


# ═══════════════════════════════════════════════════════════════════
#  Registration Loading
# ═══════════════════════════════════════════════════════════════════
class TestRegistration:
    TEST_REG = json.dumps([{
        "issuer": "https://test.canvas.edu",
        "client_id": "test-client-1",
        "auth_login_url": "https://test.canvas.edu/api/lti/authorize_redirect",
        "auth_token_url": "https://test.canvas.edu/login/oauth2/token",
        "key_set_url": "https://test.canvas.edu/api/lti/security/jwks",
        "deployment_ids": ["deployment-1"],
        "platform_name": "Test Canvas",
    }])

    def test_loads_from_env(self):
        with patch.dict(os.environ, {"LTI_REGISTRATIONS": self.TEST_REG}):
            from app.lti.registration import load_registrations, clear_cache
            clear_cache()
            regs = load_registrations()
            assert len(regs) == 1
            assert regs[0].issuer == "https://test.canvas.edu"
            assert regs[0].platform_name == "Test Canvas"

    def test_find_registration(self):
        with patch.dict(os.environ, {"LTI_REGISTRATIONS": self.TEST_REG}):
            from app.lti.registration import find_registration, clear_cache
            clear_cache()
            reg = find_registration("https://test.canvas.edu", "test-client-1")
            assert reg is not None
            assert reg.issuer == "https://test.canvas.edu"
            reg2 = find_registration("https://unknown.com", "x")
            assert reg2 is None

    def test_authorized_deployment(self):
        from app.lti.registration import is_deployment_authorized
        from app.lti.registration import PlatformRegistration
        reg = PlatformRegistration(
            issuer="test", client_id="test", auth_login_url="",
            auth_token_url="", key_set_url="",
            deployment_ids=["dep-1", "dep-2"],
        )
        assert is_deployment_authorized(reg, "dep-1") is True
        assert is_deployment_authorized(reg, "dep-3") is False
        reg2 = PlatformRegistration(
            issuer="test", client_id="test", auth_login_url="",
            auth_token_url="", key_set_url="",
        )
        assert is_deployment_authorized(reg2, "anything") is True


# ═══════════════════════════════════════════════════════════════════
#  OIDC Login Initiation
# ═══════════════════════════════════════════════════════════════════
class TestOidcLogin:
    TEST_REG_JSON = json.dumps([{
        "issuer": "https://test.canvas.edu",
        "client_id": "test-client-1",
        "auth_login_url": "https://test.canvas.edu/api/lti/authorize_redirect",
        "auth_token_url": "https://test.canvas.edu/login/oauth2/token",
        "key_set_url": "https://test.canvas.edu/api/lti/security/jwks",
        "deployment_ids": ["deployment-1"],
    }])

    def test_missing_params_400(self, client):
        resp = client.get("/lti/login")
        assert resp.status_code == 400

    def test_unknown_registration_400(self, client):
        with patch.dict(os.environ, {"LTI_REGISTRATIONS": self.TEST_REG_JSON}):
            from app.lti.registration import clear_cache
            clear_cache()
            resp = client.get("/lti/login", params={
                "iss": "https://unknown.com",
                "login_hint": "hint-1",
                "target_link_uri": "https://app.procta.net/lti/launch",
                "client_id": "unknown-client",
            })
        assert resp.status_code == 400

    def test_successful_redirect(self, client):
        with patch.dict(os.environ, {"LTI_REGISTRATIONS": self.TEST_REG_JSON}):
            from app.lti.registration import clear_cache
            clear_cache()
            resp = client.get("/lti/login", params={
                "iss": "https://test.canvas.edu",
                "login_hint": "hint-1",
                "target_link_uri": "https://app.procta.net/lti/launch",
                "client_id": "test-client-1",
                "lti_message_hint": "msg-hint-1",
            }, follow_redirects=False)
        assert resp.status_code in (302, 307), resp.text
        location = resp.headers.get("location", "")
        assert "https://test.canvas.edu/api/lti/authorize_redirect" in location
        assert "response_type=id_token" in location
        assert "state=" in location
        assert "nonce=" in location


# ═══════════════════════════════════════════════════════════════════
#  LTI Launch Validation
# ═══════════════════════════════════════════════════════════════════
class TestLtiLaunch:
    TEST_REG_JSON = json.dumps([{
        "issuer": "https://test.canvas.edu",
        "client_id": "test-client-1",
        "auth_login_url": "https://test.canvas.edu/api/lti/authorize_redirect",
        "auth_token_url": "https://test.canvas.edu/login/oauth2/token",
        "key_set_url": "https://test.canvas.edu/api/lti/security/jwks",
        "deployment_ids": ["deployment-1"],
    }])

    @pytest.fixture(autouse=True)
    def _setup_env(self):
        with patch.dict(os.environ, {"LTI_REGISTRATIONS": self.TEST_REG_JSON}):
            from app.lti.registration import clear_cache
            clear_cache()
            # Use in-memory fallback for nonces/states (mock cache is non-functional)
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

    def test_missing_id_token_400(self, client):
        resp = client.post("/lti/launch", data={})
        assert resp.status_code == 400

    def test_happy_path_learner(self, client):
        """Full launch flow with valid id_token."""
        import secrets
        from app.lti.launch import _store_nonce, _store_state
        nonce = secrets.token_urlsafe(32)
        state = secrets.token_urlsafe(32)
        _store_nonce(nonce)
        _store_state(state, {"nonce": nonce, "target_link_uri": "/lti/launch"})

        id_token = _make_test_id_token({"nonce": nonce})

        with patch("app.lti.launch._fetch_platform_jwks", return_value=_static_jwks()), \
             patch("app.lti.launch._atable") as atable:
            atable.return_value.select.return_value.eq.return_value.limit.return_value.execute = AsyncMock(
                return_value=MagicMock(data=[])
            )
            atable.return_value.insert.return_value.execute = AsyncMock()
            resp = self._launch(client, id_token, state)
        # Should redirect to /student
        assert resp.status_code == 302

    def test_happy_path_instructor(self, client):
        """Instructor role redirects to dashboard."""
        import secrets
        from app.lti.launch import _store_nonce, _store_state
        nonce = secrets.token_urlsafe(32)
        state = secrets.token_urlsafe(32)
        _store_nonce(nonce)
        _store_state(state, {"nonce": nonce, "target_link_uri": "/lti/launch"})

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
        assert resp.status_code == 302
        assert "dashboard" in resp.headers.get("location", "")

    def test_invalid_signature_401(self, client):
        """Token signed with a different key should be rejected."""
        import secrets
        from app.lti.launch import _store_nonce, _store_state
        from cryptography.hazmat.primitives.asymmetric import rsa
        wrong_key = rsa.generate_private_key(65537, 2048, default_backend())
        wrong_pem = wrong_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ).decode()

        nonce = secrets.token_urlsafe(32)
        state = secrets.token_urlsafe(32)
        _store_nonce(nonce)
        _store_state(state, {"nonce": nonce, "target_link_uri": "/lti/launch"})

        id_token = _make_test_id_token({"nonce": nonce}, override_priv_pem=wrong_pem)

        with patch("app.lti.launch._fetch_platform_jwks", return_value=_static_jwks()):
            resp = self._launch(client, id_token, state)
        assert resp.status_code == 401

    def test_replayed_nonce_401(self, client):
        """Using the same nonce twice should be rejected."""
        import secrets
        from app.lti.launch import _store_nonce, _store_state, _consume_nonce

        nonce = secrets.token_urlsafe(32)
        state = secrets.token_urlsafe(32)

        id_token = _make_test_id_token({"nonce": nonce})

        # First call — consume the nonce
        _store_nonce(nonce)
        _store_state(state, {"nonce": nonce, "target_link_uri": "/lti/launch"})
        _consume_nonce(nonce)  # simulate replay

        with patch("app.lti.launch._fetch_platform_jwks", return_value=_static_jwks()):
            resp = self._launch(client, id_token, state)
        assert resp.status_code == 401

    def test_expired_state_401(self, client):
        """An expired state should be rejected."""
        import time
        from app.lti.launch import _store_state

        state = "expired-state-1"
        _store_state(state, {"nonce": "x", "target_link_uri": "/lti/launch"})
        # Manually expire the state
        from app.lti import launch as launch_mod
        if launch_mod._cache:
            launch_mod._cache.delete(f"lti_state:{state}")
        else:
            launch_mod._states.pop(state, None)

        id_token = _make_test_id_token({"nonce": "x"})
        resp = self._launch(client, id_token, state)
        assert resp.status_code == 401

    def test_wrong_audience_401(self, client):
        """Token with wrong client_id should be rejected."""
        import secrets
        from app.lti.launch import _store_nonce, _store_state

        nonce = secrets.token_urlsafe(32)
        state = secrets.token_urlsafe(32)
        _store_nonce(nonce)
        _store_state(state, {"nonce": nonce, "target_link_uri": "/lti/launch"})

        id_token = _make_test_id_token({"nonce": nonce, "aud": "wrong-client"})

        with patch("app.lti.launch._fetch_platform_jwks", return_value=_static_jwks()):
            resp = self._launch(client, id_token, state)
        assert resp.status_code == 401

    def test_unknown_deployment_401(self, client):
        """Deployment not in the allow list should be rejected."""
        import secrets
        from app.lti.launch import _store_nonce, _store_state

        nonce = secrets.token_urlsafe(32)
        state = secrets.token_urlsafe(32)
        _store_nonce(nonce)
        _store_state(state, {"nonce": nonce, "target_link_uri": "/lti/launch"})

        id_token = _make_test_id_token({
            "nonce": nonce,
            "https://purl.imsglobal.org/spec/lti/claim/deployment_id": "unauthorized-dep",
        })

        with patch("app.lti.launch._fetch_platform_jwks", return_value=_static_jwks()):
            resp = self._launch(client, id_token, state)
        assert resp.status_code == 401

    def test_config_endpoint(self, client):
        resp = client.get("/lti/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Procta"
        assert "oidc_login_url" in data
        assert "public_jwk_url" in data
        assert "scopes" in data


# ═══════════════════════════════════════════════════════════════════
#  Deep Linking
# ═══════════════════════════════════════════════════════════════════
class TestDeepLinking:
    """Tests for POST /lti/deeplink — deep linking content selection."""

    TEST_REG_JSON = json.dumps([{
        "issuer": "https://test.canvas.edu",
        "client_id": "test-client-1",
        "auth_login_url": "https://test.canvas.edu/api/lti/authorize_redirect",
        "auth_token_url": "https://test.canvas.edu/login/oauth2/token",
        "key_set_url": "https://test.canvas.edu/api/lti/security/jwks",
        "deployment_ids": ["deployment-1"],
        "platform_name": "Test Canvas",
    }])

    @pytest.fixture(autouse=True)
    def _setup_env(self):
        with patch.dict(os.environ, {"LTI_REGISTRATIONS": self.TEST_REG_JSON}):
            from app.lti.registration import clear_cache
            clear_cache()
            with patch("app.lti.launch._cache", None):
                from app.lti import launch as launch_mod
                launch_mod._nonces.clear()
                launch_mod._states.clear()
                launch_mod._lti_contexts.clear()
                yield

    def _build_deeplink_jwt(
        self,
        claims: dict = None,
        override_priv_pem: str = None,
    ) -> str:
        """Build a signed Deep Linking Request JWT for testing."""
        import secrets
        import jwt as jose_jwt
        pem = override_priv_pem or TEST_PRIV_PEM
        headers = {"kid": override_priv_pem and "wrong-key" or "test-key-1", "typ": "JWT"}
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
                "id": "course-123",
                "label": "CS101",
                "title": "Intro to CS",
            },
            "https://purl.imsglobal.org/spec/lti-dl/claim/data": "deep-link-data-123",
            "https://purl.imsglobal.org/spec/lti-dl/claim/return_url": "https://test.canvas.edu/deep_link_return",
            "https://purl.imsglobal.org/spec/lti/claim/target_link_uri": "https://app.procta.net/lti/deeplink",
            "name": "Test Instructor",
            "email": "instructor@test.edu",
            **(claims or {}),
        }
        return jose_jwt.encode(payload, pem, algorithm="RS256", headers=headers)

    def test_missing_jwt_400(self, client):
        resp = client.post("/lti/deeplink", data={})
        assert resp.status_code == 400

    def test_invalid_jwt_401(self, client):
        resp = client.post("/lti/deeplink", data={"JWT": "not-a-valid-jwt"})
        assert resp.status_code == 401

    def test_learner_role_403(self, client):
        """Learners should not be able to use deep linking."""
        jwt_token = self._build_deeplink_jwt({
            "https://purl.imsglobal.org/spec/lti/claim/roles": [
                "http://purl.imsglobal.org/vocab/lis/v2/membership#Learner"
            ],
        })
        with patch("app.lti.deeplink._fetch_platform_jwks", return_value=_static_jwks()):
            resp = client.post("/lti/deeplink", data={"JWT": jwt_token})
        assert resp.status_code == 403

    def test_happy_path_instructor(self, client):
        """Instructor should get an HTML page with auto-submit form."""
        import secrets
        jwt_token = self._build_deeplink_jwt({
            "nonce": secrets.token_urlsafe(32),
        })
        with patch("app.lti.deeplink._fetch_platform_jwks", return_value=_static_jwks()), \
             patch("app.lti.launch._atable") as atable:
            atable.return_value.select.return_value.eq.return_value.limit.return_value.execute = AsyncMock(
                return_value=MagicMock(data=[])
            )
            atable.return_value.insert.return_value.execute = AsyncMock()
            from app.lti.deeplink import get_teacher_exams_as_content_items
            with patch.object(get_teacher_exams_as_content_items, '__wrapped__', create=True):
                pass
            resp = client.post("/lti/deeplink", data={"JWT": jwt_token})
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "").startswith("text/html")
        assert "LMS" in resp.text or "form" in resp.text
        assert "JWT" in resp.text

    def test_deeplink_response_includes_exams(self, client):
        """If the teacher has exams, they should appear as content items."""
        import secrets
        jwt_token = self._build_deeplink_jwt({
            "nonce": secrets.token_urlsafe(32),
        })
        # Real exam_config column is `exam_title` (the code reads it directly;
        # the old PostgREST `title:exam_title` alias never worked on postgres).
        fake_exams = [
            {"exam_id": "exam-1", "exam_title": "Midterm", "duration_minutes": 60},
            {"exam_id": "exam-2", "exam_title": "Final", "duration_minutes": 120},
        ]
        with patch("app.lti.deeplink._fetch_platform_jwks", return_value=_static_jwks()), \
             patch("app.lti.launch._atable") as launch_atable, \
             patch("app.lti.deeplink._atable") as deeplink_atable:
            launch_atable.return_value.select.return_value.eq.return_value.limit.return_value.execute = AsyncMock(
                return_value=MagicMock(data=[])
            )
            launch_atable.return_value.insert.return_value.execute = AsyncMock()
            deeplink_atable.return_value.select.return_value.eq.return_value.order.return_value.execute = AsyncMock(
                return_value=MagicMock(data=fake_exams)
            )
            resp = client.post("/lti/deeplink", data={"JWT": jwt_token})
        assert resp.status_code == 200
        # Extract JWT from the hidden input field
        import re
        match = re.search(r'value="([^"]+)"', resp.text)
        assert match, "JWT not found in response HTML"
        jwt_token_resp = match.group(1)
        import jwt as jose_jwt
        decoded = jose_jwt.decode(jwt_token_resp, options={"verify_signature": False})
        items = decoded.get(
            "https://purl.imsglobal.org/spec/lti-dl/claim/content_items", []
        )
        titles = [i.get("title", "") for i in items]
        assert "Midterm" in titles
        assert "Final" in titles


# ═══════════════════════════════════════════════════════════════════
#  AGS Service (grade passback)
# ═══════════════════════════════════════════════════════════════════
class TestAgsService:
    """Tests for AGS grade passback service functions."""

    def test_build_client_assertion(self):
        from app.lti.ags import _build_client_assertion
        assertion = _build_client_assertion(
            issuer="https://test.canvas.edu",
            client_id="test-client-1",
            auth_token_url="https://test.canvas.edu/login/oauth2/token",
        )
        assert assertion
        assert isinstance(assertion, str)
        assert len(assertion.split(".")) == 3
        # Decode without verification (signed with app's key, not test key)
        import jwt as jose_jwt
        payload = jose_jwt.decode(assertion, options={"verify_signature": False})
        assert payload["iss"] == "https://test.canvas.edu"
        assert payload["sub"] == "test-client-1"
        assert payload["aud"] == "https://test.canvas.edu/login/oauth2/token"

    def test_post_score_success(self):
        from app.lti.ags import post_score
        import asyncio
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.__aenter__.return_value.post.return_value = MagicMock(
                raise_for_status=MagicMock()
            )
            mock_client.return_value = mock_instance
            result = asyncio.run(post_score(
                lineitem_url="https://test.canvas.edu/api/lti/courses/1/line_items/10",
                access_token="test-token",
                user_id="lti-user-abc",
                score_given=85.0,
                score_maximum=100.0,
                timestamp="2025-01-01T00:00:00Z",
            ))
        assert result is True

    def test_post_score_failure(self):
        from app.lti.ags import post_score
        import asyncio
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.__aenter__.return_value.post.side_effect = Exception("LMS unreachable")
            mock_client.return_value = mock_instance
            result = asyncio.run(post_score(
                lineitem_url="https://test.canvas.edu/api/lti/courses/1/line_items/10",
                access_token="test-token",
                user_id="lti-user-abc",
                score_given=85.0,
                score_maximum=100.0,
                timestamp="2025-01-01T00:00:00Z",
            ))
        assert result is False

    def test_get_results(self):
        from app.lti.ags import get_results
        import asyncio
        mock_response = [
            {"userId": "user-1", "resultScore": 85, "resultMaximum": 100},
        ]
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.__aenter__.return_value.get.return_value = MagicMock(
                raise_for_status=MagicMock(),
                json=MagicMock(return_value=mock_response),
            )
            mock_client.return_value = mock_instance
            results = asyncio.run(get_results(
                lineitem_url="https://test.canvas.edu/api/lti/courses/1/line_items/10",
                access_token="test-token",
            ))
        assert len(results) == 1
        assert results[0]["userId"] == "user-1"

    def test_create_line_item(self):
        from app.lti.ags import create_line_item
        import asyncio
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.headers = {"Location": "https://test.canvas.edu/api/lti/courses/1/line_items/20"}
            mock_instance.__aenter__.return_value.post.return_value = resp
            mock_client.return_value = mock_instance
            url = asyncio.run(create_line_item(
                lineitems_url="https://test.canvas.edu/api/lti/courses/1/line_items",
                access_token="test-token",
                label="Midterm Exam",
                score_maximum=100.0,
            ))
        assert url == "https://test.canvas.edu/api/lti/courses/1/line_items/20"


# ═══════════════════════════════════════════════════════════════════
#  NRPS Service (names and roles)
# ═══════════════════════════════════════════════════════════════════
class TestNrpsService:
    """Tests for NRPS membership sync service functions."""

    def test_fetch_membership(self):
        from app.lti.nrps import fetch_membership
        import asyncio
        mock_members = [
            {"user_id": "user-1", "roles": ["Learner"], "name": "Alice", "email": "alice@test.edu"},
            {"user_id": "user-2", "roles": ["Learner"], "name": "Bob", "email": "bob@test.edu"},
        ]
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.__aenter__.return_value.get.return_value = MagicMock(
                raise_for_status=MagicMock(),
                json=MagicMock(return_value={"members": mock_members}),
            )
            mock_client.return_value = mock_instance
            members = asyncio.run(fetch_membership(
                context_memberships_url="https://test.canvas.edu/api/lti/courses/1/memberships",
                access_token="test-token",
            ))
        assert len(members) == 2
        assert members[0]["name"] == "Alice"

    def test_fetch_membership_failure(self):
        from app.lti.nrps import fetch_membership
        import asyncio
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.__aenter__.return_value.get.side_effect = Exception("LMS unreachable")
            mock_client.return_value = mock_instance
            members = asyncio.run(fetch_membership(
                context_memberships_url="https://test.canvas.edu/api/lti/courses/1/memberships",
                access_token="test-token",
            ))
        assert members == []

    def test_sync_learner_roster(self):
        from app.lti.nrps import sync_learner_roster
        import asyncio
        mock_members = [
            {"user_id": "user-1", "name": "Alice", "email": "alice@test.edu", "username": "alice"},
        ]
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.__aenter__.return_value.get.return_value = MagicMock(
                raise_for_status=MagicMock(),
                json=MagicMock(return_value={"members": mock_members}),
            )
            mock_client.return_value = mock_instance
            with patch("app.lti.nrps._atable") as atable:
                # Chain: select().eq().eq().limit().execute()
                # Each .eq() returns self on the real AsyncTable
                _mock_sel = atable.return_value.select.return_value
                _mock_sel.eq.return_value = _mock_sel  # make .eq() return same mock
                _mock_sel.limit.return_value.execute = AsyncMock(
                    return_value=MagicMock(data=[])
                )
                atable.return_value.insert.return_value.execute = AsyncMock()
                result = asyncio.run(sync_learner_roster(
                    context_memberships_url="https://test.canvas.edu/api/lti/courses/1/memberships",
                    access_token="test-token",
                    teacher_id="teacher-1",
                ))
        assert result["created"] == 1
        assert result["existing"] == 0
        assert result["total"] == 1


# ═══════════════════════════════════════════════════════════════════
#  LTI Context Storage (AGS/NRPS)
# ═══════════════════════════════════════════════════════════════════
class TestLtiContextStorage:
    """Tests for storing and retrieving AGS/NRPS context from launch."""

    def test_store_and_get_ags_context(self):
        from app.lti.launch import get_ags_context, _store_ags_nrps_context, _lti_contexts
        import asyncio

        _lti_contexts.clear()
        with patch("app.lti.launch._cache", None):
            claims = {
                "iss": "https://test.canvas.edu",
                "https://purl.imsglobal.org/spec/lti/claim/deployment_id": "deployment-1",
                "https://purl.imsglobal.org/spec/lti-ags/claim/endpoint": {
                    "lineitems": "https://test.canvas.edu/api/lti/courses/1/line_items",
                    "scope": ["https://purl.imsglobal.org/spec/lti-ags/scope/score"],
                },
            }
            asyncio.run(_store_ags_nrps_context(claims))

            ctx = get_ags_context("https://test.canvas.edu", "deployment-1")
            assert ctx is not None
            assert ctx["ags_lineitems"] == "https://test.canvas.edu/api/lti/courses/1/line_items"
            assert "https://purl.imsglobal.org/spec/lti-ags/scope/score" in ctx["ags_scope"]

    def test_store_and_get_nrps_context(self):
        from app.lti.launch import get_nrps_context, _store_ags_nrps_context
        from app.lti.launch import _lti_contexts as ctxs
        import asyncio

        ctxs.clear()
        with patch("app.lti.launch._cache", None):
            claims = {
                "iss": "https://test.canvas.edu",
                "https://purl.imsglobal.org/spec/lti/claim/deployment_id": "deployment-1",
                "https://purl.imsglobal.org/spec/lti-nrps/claim/namesroleservice": {
                    "context_memberships_url": "https://test.canvas.edu/api/lti/courses/1/memberships",
                    "service_versions": ["2.0"],
                },
            }
            asyncio.run(_store_ags_nrps_context(claims))

            ctx = get_nrps_context("https://test.canvas.edu", "deployment-1")
            assert ctx is not None
            assert ctx["nrps_url"] == "https://test.canvas.edu/api/lti/courses/1/memberships"

    def test_get_lti_student_context(self):
        from app.lti.launch import store_lti_student_context, get_lti_student_context
        from app.lti.launch import _lti_contexts as ctxs
        import asyncio

        ctxs.clear()
        with patch("app.lti.launch._cache", None):
            claims = {
                "iss": "https://test.canvas.edu",
                "sub": "student-sub-456",
                "https://purl.imsglobal.org/spec/lti/claim/deployment_id": "deployment-1",
            }
            lti_user_id = "https://test.canvas.edu|student-sub-456"
            asyncio.run(store_lti_student_context(claims, lti_user_id))

            ctx = get_lti_student_context(lti_user_id)
            assert ctx is not None
            assert ctx["iss"] == "https://test.canvas.edu"
            assert ctx["sub"] == "student-sub-456"
            assert ctx["deployment_id"] == "deployment-1"


# ═══════════════════════════════════════════════════════════════════
#  AGS / NRPS HTTP Endpoints
# ═══════════════════════════════════════════════════════════════════
class TestAgsHttpEndpoints:
    """Tests for the HTTP endpoints for AGS grade push and NRPS sync."""

    TEACHER = {"id": "teacher-1", "email": "t@x.com", "org_id": "org-1",
               "org_role": "admin", "full_name": "T", "status": "active"}

    TEST_REG_JSON = json.dumps([{
        "issuer": "https://test.canvas.edu",
        "client_id": "test-client-1",
        "auth_login_url": "https://test.canvas.edu/api/lti/authorize_redirect",
        "auth_token_url": "https://test.canvas.edu/login/oauth2/token",
        "key_set_url": "https://test.canvas.edu/api/lti/security/jwks",
        "deployment_ids": ["deployment-1"],
    }])

    @pytest.fixture(autouse=True)
    def _setup_registration(self):
        # The "no context" tests expect 404 (registration exists, no NRPS
        # context) — that requires the platform registration to be present.
        # Register it explicitly (and clear the module cache) so these tests
        # are self-contained and don't rely on a sibling test running first
        # under a given order. Without this they 400 ("no registration") when
        # shuffled ahead of whatever else used to populate the cache.
        with patch.dict(os.environ, {"LTI_REGISTRATIONS": self.TEST_REG_JSON}):
            from app.lti.registration import clear_cache
            clear_cache()
            yield
            clear_cache()

    def _auth(self):
        return patch("app.auth.admin_auth._get_teacher_by_id", return_value=self.TEACHER)

    def _hdr(self):
        return {"Authorization": f"Bearer {make_admin_token(teacher_id='teacher-1')}"}

    # These push to / pull from the LMS using Procta's OWN tool credentials —
    # they MUST require admin auth (closed an unauthenticated grade-tampering hole).
    def test_ags_push_grades_requires_auth(self, client):
        resp = client.post("/lti/ags/push-grades", json={})
        assert resp.status_code == 401

    def test_nrps_sync_membership_requires_auth(self, client):
        resp = client.post("/lti/ags/sync-membership", json={})
        assert resp.status_code == 401

    def test_ags_push_grades_missing_params(self, client):
        with self._auth():
            resp = client.post("/lti/ags/push-grades", json={}, headers=self._hdr())
        assert resp.status_code == 400

    def test_ags_push_grades_no_context(self, client):
        with self._auth():
            resp = client.post("/lti/ags/push-grades", json={
                "iss": "https://test.canvas.edu",
                "client_id": "test-client-1",
                "deployment_id": "deployment-1",
                "user_id": "user-1",
                "score_given": 85,
            }, headers=self._hdr())
        assert resp.status_code == 404

    def test_nrps_sync_membership_missing_params(self, client):
        with self._auth():
            resp = client.post("/lti/ags/sync-membership", json={}, headers=self._hdr())
        assert resp.status_code == 400

    def test_nrps_sync_membership_no_context(self, client):
        with self._auth():
            resp = client.post("/lti/ags/sync-membership", json={
                "iss": "https://test.canvas.edu",
                "client_id": "test-client-1",
                "deployment_id": "deployment-1",
            }, headers=self._hdr())
        assert resp.status_code == 404

    def test_ags_lineitems_stub(self, client):
        resp = client.get("/lti/ags/lineitems")
        assert resp.status_code == 501

    def test_nrps_membership_stub(self, client):
        resp = client.get("/lti/nrps/membership")
        assert resp.status_code == 501
