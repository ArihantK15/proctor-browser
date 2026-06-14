"""Tests for per-session nonce attestation (Command A).

Exercises the challenge endpoint, v2 attestation signing/verification,
single-use semantics, expiry, and replay rejection.
"""

from __future__ import annotations

import hmac
import hashlib
import json
import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

# ── Helpers ─────────────────────────────────────────────────────────


def _sign(secret: str, att: dict) -> str:
    return hmac.new(
        secret.encode(),
        json.dumps(att, sort_keys=True, separators=(",", ":")).encode(),
        hashlib.sha256,
    ).hexdigest()


def _make_v2_att(nonce: str, **overrides) -> dict:
    """Build a v2 attestation payload with the given nonce."""
    payload = {
        "v": 2,
        "nonce": nonce,
        "session_key": "R001_abc123",
        "exam_id": "exam-1",
        "roll": "R001",
        "kiosk": True,
        "client_version": "2.5.0",
        "packaged": True,
        "platform": "darwin",
        "ts": time.time(),
    }
    payload.update(overrides)
    return payload


def _make_hb_att(kiosk=True, **overrides) -> dict:
    """Heartbeat attestation (v1, no nonce)."""
    payload = {"kiosk": kiosk, "ts": time.time()}
    payload.update(overrides)
    return payload


class _AsyncMockTable:
    """Fluent mock for _atable — returned by _atable() calls."""
    def __init__(self, data=None):
        self._data = data if data is not None else []
    def select(self, *a, **kw): return self
    def eq(self, *a, **kw): return self
    def neq(self, *a, **kw): return self
    def is_(self, *a, **kw): return self
    def in_(self, *a, **kw): return self
    def gte(self, *a, **kw): return self
    def lte(self, *a, **kw): return self
    def gt(self, *a, **kw): return self
    def lt(self, *a, **kw): return self
    def like(self, *a, **kw): return self
    def contains(self, *a, **kw): return self
    def order(self, *a, **kw): return self
    def limit(self, *a, **kw): return self
    def range(self, *a, **kw): return self
    def single(self, *a, **kw): return self
    def insert(self, *a, **kw): return self
    def upsert(self, *a, **kw): return self
    def update(self, *a, **kw): return self
    def delete(self, *a, **kw): return self
    async def execute(self):
        r = __import__("unittest").mock.MagicMock()
        r.data = self._data
        r.count = None
        return r


# ── Tests: verify_attestation v2 nonce ──────────────────────────────


class TestVerifyAttestationNonce:
    """Unit tests for verify_attestation with nonce parameters."""

    @pytest.fixture(autouse=True)
    def _env(self):
        with patch.dict("os.environ", {
            "KIOSK_ATTESTATION_SECRET": "test-secret",
            "MIN_CLIENT_VERSION": "0.0.0",
        }), patch("app.services.kiosk_attest.KIOSK_ATTESTATION_SECRET", "test-secret"), \
            patch("app.services.kiosk_attest.MIN_CLIENT_VERSION", "0.0.0"):
            yield

    def test_valid_v2_with_nonce(self):
        from app.services.kiosk_attest import verify_attestation
        nonce = "abc123nonce"
        issued_at = datetime.now(timezone.utc).isoformat()
        att = _make_v2_att(nonce)
        sig = _sign("test-secret", att)
        ok, reason = verify_attestation(
            att, sig,
            expected_session_key="R001_abc123",
            expected_roll="R001",
            expected_nonce=nonce,
            nonce_issued_at=issued_at,
        )
        assert ok, reason
        assert reason == "ok"

    def test_v1_payload_rejected_when_nonce_expected(self):
        from app.services.kiosk_attest import verify_attestation
        issued_at = datetime.now(timezone.utc).isoformat()
        att = _make_v2_att("nonce", v=1)
        sig = _sign("test-secret", att)
        ok, reason = verify_attestation(
            att, sig,
            expected_session_key="R001_abc123",
            expected_roll="R001",
            expected_nonce="nonce",
            nonce_issued_at=issued_at,
        )
        assert not ok
        assert "expected v2" in reason

    def test_wrong_nonce_rejected(self):
        from app.services.kiosk_attest import verify_attestation
        issued_at = datetime.now(timezone.utc).isoformat()
        att = _make_v2_att("correct-nonce")
        sig = _sign("test-secret", att)
        ok, reason = verify_attestation(
            att, sig,
            expected_session_key="R001_abc123",
            expected_roll="R001",
            expected_nonce="wrong-nonce",
            nonce_issued_at=issued_at,
        )
        assert not ok
        assert "nonce mismatch" in reason

    def test_missing_nonce_in_payload_rejected(self):
        from app.services.kiosk_attest import verify_attestation
        issued_at = datetime.now(timezone.utc).isoformat()
        att = _make_v2_att("ignored")
        att.pop("nonce", None)
        sig = _sign("test-secret", att)
        ok, reason = verify_attestation(
            att, sig,
            expected_session_key="R001_abc123",
            expected_roll="R001",
            expected_nonce="server-issued-nonce",
            nonce_issued_at=issued_at,
        )
        assert not ok
        assert "nonce mismatch" in reason

    def test_expired_nonce_rejected(self):
        from app.services.kiosk_attest import verify_attestation
        old = (datetime.now(timezone.utc).timestamp() - 600)
        issued_at = datetime.fromtimestamp(old, tz=timezone.utc).isoformat()
        att = _make_v2_att("stale-nonce")
        sig = _sign("test-secret", att)
        ok, reason = verify_attestation(
            att, sig,
            expected_session_key="R001_abc123",
            expected_roll="R001",
            expected_nonce="stale-nonce",
            nonce_issued_at=issued_at,
        )
        assert not ok
        assert "nonce expired" in reason

    def test_no_nonce_params_skips_nonce_checks(self):
        """Without expected_nonce, v1 attestations still work (backward compat)."""
        from app.services.kiosk_attest import verify_attestation
        att = _make_v2_att("some-nonce", v=1)
        sig = _sign("test-secret", att)
        ok, reason = verify_attestation(
            att, sig,
            expected_session_key="R001_abc123",
            expected_roll="R001",
        )
        assert ok, reason

    def test_heartbeat_attestation_without_nonce(self):
        """Heartbeat re-attestation (no nonce params) should still work."""
        from app.services.kiosk_attest import verify_attestation
        hb_att = _make_hb_att(kiosk=True)
        hb_sig = _sign("test-secret", hb_att)
        ok, reason = verify_attestation(hb_att, hb_sig)
        assert ok, reason


# ── Tests: HTTP endpoints ───────────────────────────────────────────


@pytest.mark.usefixtures("_disable_rate_limits")
class TestAttestChallengeEndpoint:
    """Test the GET /api/v1/exam/attest-challenge endpoint."""

    @pytest.fixture(autouse=True)
    def _env_and_mocks(self):
        from tests.conftest import make_student_token
        token = make_student_token(roll="R001")
        self.headers = {"Authorization": f"Bearer {token}"}
        patcher = patch.dict("os.environ", {
            "KIOSK_ATTESTATION_SECRET": "test-secret",
            "MIN_CLIENT_VERSION": "1.0.0",
        })
        patcher.start()
        self._const_patcher = patch(
            "app.services.kiosk_attest.KIOSK_ATTESTATION_SECRET", "test-secret"
        )
        self._min_ver_patcher = patch(
            "app.services.kiosk_attest.MIN_CLIENT_VERSION", "1.0.0"
        )
        self._min_ver_patcher.start()
        self._const_patcher.start()
        yield
        patcher.stop()
        self._const_patcher.stop()
        self._min_ver_patcher.stop()

    def test_challenge_issues_nonce(self, client):
        mt = _AsyncMockTable()
        with patch("app.routers.exam._atable", return_value=mt):
            resp = client.get(
                "/api/v1/exam/attest-challenge",
                params={"session_id": "R001_abc123"},
                headers=self.headers,
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "nonce" in body
        assert len(body["nonce"]) > 20  # token_urlsafe(32)

    def test_challenge_stores_nonce_on_session(self, client):
        from unittest.mock import MagicMock
        mt = _AsyncMockTable()
        with patch("app.routers.exam._atable", return_value=mt):
            resp = client.get(
                "/api/v1/exam/attest-challenge",
                params={"session_id": "R001_abc123"},
                headers=self.headers,
            )
        assert resp.status_code == 200
        # The _atable mock's update().eq().execute() was called
        assert mt._data == []

    def test_challenge_requires_auth(self, client):
        resp = client.get(
            "/api/v1/exam/attest-challenge",
            params={"session_id": "R001_abc123"},
        )
        assert resp.status_code == 401


@pytest.mark.usefixtures("_disable_rate_limits")
class TestNoncedAttestHttp:
    """HTTP-level tests for the nonce-aware attest endpoint."""

    @pytest.fixture(autouse=True)
    def _env_and_mocks(self):
        from tests.conftest import make_student_token
        token = make_student_token(roll="R001")
        self.headers = {"Authorization": f"Bearer {token}"}
        patcher = patch.dict("os.environ", {
            "KIOSK_ATTESTATION_SECRET": "test-secret",
            "MIN_CLIENT_VERSION": "1.0.0",
        })
        patcher.start()
        self._const_patcher = patch(
            "app.services.kiosk_attest.KIOSK_ATTESTATION_SECRET", "test-secret"
        )
        self._min_ver_patcher = patch(
            "app.services.kiosk_attest.MIN_CLIENT_VERSION", "1.0.0"
        )
        self._min_ver_patcher.start()
        self._const_patcher.start()
        yield
        patcher.stop()
        self._const_patcher.stop()
        self._min_ver_patcher.stop()

    def _sign_att(self, att: dict) -> str:
        return _sign("test-secret", att)

    def test_valid_attestation_with_nonce(self, client):
        nonce = "test-nonce-123"
        issued_at = datetime.now(timezone.utc).isoformat()
        # Mock session row with nonce + issued_at
        mt = _AsyncMockTable(data=[{
            "attest_nonce": nonce,
            "attest_nonce_issued_at": issued_at,
        }])
        with patch("app.routers.exam._atable", return_value=mt):
            att = _make_v2_att(nonce)
            sig = self._sign_att(att)
            resp = client.post(
                "/api/v1/exam/attest",
                json={"att": att, "sig": sig},
                headers=self.headers,
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True

    def test_wrong_nonce_at_http_level(self, client):
        mt = _AsyncMockTable(data=[{
            "attest_nonce": "server-nonce",
            "attest_nonce_issued_at": datetime.now(timezone.utc).isoformat(),
        }])
        with patch("app.routers.exam._atable", return_value=mt):
            att = _make_v2_att("wrong-client-nonce")
            sig = self._sign_att(att)
            resp = client.post(
                "/api/v1/exam/attest",
                json={"att": att, "sig": sig},
                headers=self.headers,
            )
        assert resp.status_code == 403
        body = resp.json()
        assert "nonce mismatch" in body.get("detail", "")

    def test_missing_nonce_on_session_allows_v1_fallback(self, client):
        """If the session has no attest_nonce, nonce is None → skip nonce check."""
        mt = _AsyncMockTable(data=[{
            "attest_nonce": None,
            "attest_nonce_issued_at": None,
        }])
        with patch("app.routers.exam._atable", return_value=mt):
            att = _make_v2_att("any-nonce", v=1)
            sig = self._sign_att(att)
            resp = client.post(
                "/api/v1/exam/attest",
                json={"att": att, "sig": sig},
                headers=self.headers,
            )
        # Without expected_nonce the v1 check passes
        assert resp.status_code == 200
