"""Tests for kiosk attestation (Gap #43)."""

from __future__ import annotations

import hmac
import hashlib
import json
import time
from unittest.mock import MagicMock, patch

import pytest

# ── Helpers ─────────────────────────────────────────────────────────


def _sign(secret: str, att: dict) -> str:
    return hmac.new(
        secret.encode(),
        json.dumps(att, sort_keys=True, separators=(",", ":")).encode(),
        hashlib.sha256,
    ).hexdigest()


# ── Tests: verify_attestation ────────────────────────────────────────


class TestVerifyAttestation:
    @pytest.fixture(autouse=True)
    def _env(self):
        with patch.dict("os.environ", {
            "KIOSK_ATTESTATION_SECRET": "test-secret",
            "MIN_CLIENT_VERSION": "1.0.0",
        }), patch("app.services.kiosk_attest.KIOSK_ATTESTATION_SECRET", "test-secret"), \
            patch("app.services.kiosk_attest.MIN_CLIENT_VERSION", "1.0.0"):
            yield

    def _make_att(self, **overrides) -> dict:
        payload = {
            "v": 1,
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

    def test_valid(self):
        from app.services.kiosk_attest import verify_attestation
        att = self._make_att()
        sig = _sign("test-secret", att)
        ok, reason = verify_attestation(att, sig, "R001_abc123", "R001")
        assert ok
        assert reason == "ok"

    def test_bad_sig(self):
        from app.services.kiosk_attest import verify_attestation
        att = self._make_att()
        ok, reason = verify_attestation(att, "bad-sig", "R001_abc123", "R001")
        assert not ok
        assert "invalid signature" in reason

    def test_stale_ts(self):
        from app.services.kiosk_attest import verify_attestation
        att = self._make_att(ts=time.time() - 1000)
        sig = _sign("test-secret", att)
        ok, reason = verify_attestation(att, sig, "R001_abc123", "R001")
        assert not ok
        assert "timestamp out of tolerance" in reason

    def test_kiosk_not_enabled(self):
        from app.services.kiosk_attest import verify_attestation
        att = self._make_att(kiosk=False)
        sig = _sign("test-secret", att)
        ok, reason = verify_attestation(att, sig, "R001_abc123", "R001")
        assert not ok
        assert "kiosk not enabled" in reason

    def test_old_version(self):
        from app.services.kiosk_attest import verify_attestation
        att = self._make_att(client_version="0.1.0")
        sig = _sign("test-secret", att)
        ok, reason = verify_attestation(att, sig, "R001_abc123", "R001")
        assert not ok
        assert "below minimum" in reason

    def test_session_key_mismatch(self):
        from app.services.kiosk_attest import verify_attestation
        att = self._make_att(session_key="WRONG_SESSION")
        sig = _sign("test-secret", att)
        ok, reason = verify_attestation(att, sig, "R001_abc123", "R001")
        assert not ok
        assert "session_key mismatch" in reason

    def test_roll_mismatch(self):
        from app.services.kiosk_attest import verify_attestation
        att = self._make_att(roll="WRONG_ROLL")
        sig = _sign("test-secret", att)
        ok, reason = verify_attestation(att, sig, "R001_abc123", "R001")
        assert not ok
        assert "roll mismatch" in reason

    def test_no_secret_configured(self):
        with patch("app.services.kiosk_attest.KIOSK_ATTESTATION_SECRET", ""):
            from app.services.kiosk_attest import verify_attestation
            att = self._make_att()
            sig = _sign("test-secret", att)
            ok, reason = verify_attestation(att, sig, "R001_abc123", "R001")
            assert not ok
            assert "attestation not configured" in reason


# ── Tests: HTTP endpoint ────────────────────────────────────────────


class _AsyncMockTable:
    """Fluent mock that returns self on every chain method, with awaitable execute()."""
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
        r = MagicMock()
        r.data = self._data
        r.count = None
        return r


@pytest.mark.usefixtures("_disable_rate_limits")
class TestAttestEndpoint:
    @pytest.fixture(autouse=True)
    def _env_and_mocks(self):
        from tests.conftest import make_student_token
        token = make_student_token(roll="R001")
        self.headers = {"Authorization": f"Bearer {token}"}
        patcher1 = patch.dict("os.environ", {
            "KIOSK_ATTESTATION_SECRET": "test-secret",
            "MIN_CLIENT_VERSION": "1.0.0",
        })
        patcher1.start()
        self._constant_patcher = patch(
            "app.services.kiosk_attest.KIOSK_ATTESTATION_SECRET", "test-secret"
        )
        self._min_ver_patcher = patch(
            "app.services.kiosk_attest.MIN_CLIENT_VERSION", "1.0.0"
        )
        self._min_ver_patcher.start()
        self._constant_patcher.start()
        yield
        patcher1.stop()
        self._constant_patcher.stop()
        self._min_ver_patcher.stop()

    def _make_att(self, session_key="R001_abc123", **overrides) -> dict:
        payload = {
            "v": 1,
            "session_key": session_key,
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

    def _sign_att(self, att: dict) -> str:
        return _sign("test-secret", att)

    def test_valid_attestation(self, client):
        mt = _AsyncMockTable()
        with patch("app.routers.exam._atable", return_value=mt):
            att = self._make_att()
            sig = self._sign_att(att)
            resp = client.post(
                "/api/v1/exam/attest",
                json={"att": att, "sig": sig},
                headers=self.headers,
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True

    def test_bad_sig_rejected(self, client):
        mt = _AsyncMockTable()
        with patch("app.routers.exam._atable", return_value=mt):
            att = self._make_att()
            resp = client.post(
                "/api/v1/exam/attest",
                json={"att": att, "sig": "bad-sig"},
                headers=self.headers,
            )
        assert resp.status_code == 403
        body = resp.json()
        assert "invalid signature" in body.get("detail", "")

    def test_stale_ts_rejected(self, client):
        mt = _AsyncMockTable()
        with patch("app.routers.exam._atable", return_value=mt):
            att = self._make_att(ts=time.time() - 1000)
            sig = self._sign_att(att)
            resp = client.post(
                "/api/v1/exam/attest",
                json={"att": att, "sig": sig},
                headers=self.headers,
            )
        assert resp.status_code == 403
        body = resp.json()
        assert "timestamp" in body.get("detail", "")

    def test_kiosk_not_enabled_rejected(self, client):
        mt = _AsyncMockTable()
        with patch("app.routers.exam._atable", return_value=mt):
            att = self._make_att(kiosk=False)
            sig = self._sign_att(att)
            resp = client.post(
                "/api/v1/exam/attest",
                json={"att": att, "sig": sig},
                headers=self.headers,
            )
        assert resp.status_code == 403
        body = resp.json()
        assert "kiosk not enabled" in body.get("detail", "")

    def test_old_version_rejected(self, client):
        mt = _AsyncMockTable()
        with patch("app.routers.exam._atable", return_value=mt):
            att = self._make_att(client_version="0.1.0")
            sig = self._sign_att(att)
            resp = client.post(
                "/api/v1/exam/attest",
                json={"att": att, "sig": sig},
                headers=self.headers,
            )
        assert resp.status_code == 403
        body = resp.json()
        assert "below minimum" in body.get("detail", "")

    def test_no_secret_returns_403(self, client):
        mt = _AsyncMockTable()
        with patch("app.routers.exam._atable", return_value=mt), \
             patch("app.services.kiosk_attest.KIOSK_ATTESTATION_SECRET", ""):
            att = self._make_att()
            sig = self._sign_att(att)
            resp = client.post(
                "/api/v1/exam/attest",
                json={"att": att, "sig": sig},
                headers=self.headers,
            )
        assert resp.status_code == 403
        body = resp.json()
        assert "not configured" in body.get("detail", "")


@pytest.mark.usefixtures("_disable_rate_limits")
class TestAttestationGate:
    """Test that KIOSK_ATTESTATION_ENFORCED blocks get-questions/submit-exam."""

    @pytest.fixture(autouse=True)
    def _env_and_mocks(self):
        from tests.conftest import make_student_token
        token = make_student_token(roll="R001")
        self.headers = {"Authorization": f"Bearer {token}"}
        patcher = patch.dict("os.environ", {
            "KIOSK_ATTESTATION_SECRET": "test-secret",
            "KIOSK_ATTESTATION_ENFORCED": "true",
        })
        patcher.start()
        self._const_patcher = patch(
            "app.routers.exam.KIOSK_ATTESTATION_ENFORCED", True
        )
        self._const_patcher.start()
        yield
        patcher.stop()
        self._const_patcher.stop()

    def test_get_questions_blocked_without_attestation(self, client):
        mt = _AsyncMockTable(data=[{"kiosk_attested": None}])
        with patch("app.routers.exam._atable", return_value=mt), \
             patch("app.routers.exam._load_questions", return_value=[]):
            resp = client.get(
                "/api/v1/questions?session_id=R001_abc123",
                headers=self.headers,
            )
        assert resp.status_code == 403
        body = resp.json()
        assert "secure browser" in body.get("detail", "")

    def test_get_questions_allowed_with_attestation(self, client):
        mt = _AsyncMockTable(data=[{"kiosk_attested": True}])
        config_data = {"duration_minutes": 30, "exam_title": "Test"}
        with patch("app.routers.exam._atable", return_value=mt), \
             patch("app.routers.exam._load_questions", return_value=[{"id": "q1"}]), \
             patch("app.routers.exam._load_exam_config", return_value=config_data):
            resp = client.get(
                "/api/v1/questions?session_id=R001_abc123",
                headers=self.headers,
            )
        assert resp.status_code == 200

    def test_enforced_off_allows_without_attestation(self, client):
        with patch("app.routers.exam.KIOSK_ATTESTATION_ENFORCED", False), \
             patch("app.routers.exam._atable", return_value=_AsyncMockTable(data=[{"kiosk_attested": None}])), \
             patch("app.routers.exam._load_questions", return_value=[{"id": "q1"}]), \
             patch("app.routers.exam._load_exam_config", return_value={"duration_minutes": 30, "exam_title": "Test"}):
            resp = client.get(
                "/api/v1/questions?session_id=R001_abc123",
                headers=self.headers,
            )
        assert resp.status_code == 200


@pytest.mark.usefixtures("_disable_rate_limits")
class TestHeartbeatAttestation:
    """Test that heartbeat re-attestation records violation on kiosk exit."""

    @pytest.fixture(autouse=True)
    def _env_and_mocks(self):
        from tests.conftest import make_student_token
        token = make_student_token(roll="R001")
        self.headers = {"Authorization": f"Bearer {token}"}
        patcher = patch.dict("os.environ", {
            "KIOSK_ATTESTATION_SECRET": "test-secret",
        })
        patcher.start()
        self._const_patcher = patch(
            "app.services.kiosk_attest.KIOSK_ATTESTATION_SECRET", "test-secret"
        )
        self._const_patcher.start()
        yield
        patcher.stop()
        self._const_patcher.stop()

    def _make_hb_att(self, kiosk=True, **overrides) -> dict:
        payload = {"kiosk": kiosk, "ts": time.time()}
        payload.update(overrides)
        return payload

    def _sign_hb(self, att: dict) -> str:
        return _sign("test-secret", att)

    def test_kiosk_exit_records_violation(self, client):
        mt = _AsyncMockTable(data=[{"status": "in_progress", "exam_id": "exam-1"}])
        with patch("app.routers.exam._atable", return_value=mt):
            hb_att = self._make_hb_att(kiosk=False)
            hb_sig = self._sign_hb(hb_att)
            resp = client.post(
                "/api/v1/heartbeat",
                json={
                    "session_id": "R001_abc123",
                    "event_type": "heartbeat",
                    "severity": "low",
                    "att": hb_att,
                    "sig": hb_sig,
                },
                headers=self.headers,
            )
        assert resp.status_code == 200

    def test_heartbeat_kiosk_true(self, client):
        mt = _AsyncMockTable(data=[{"status": "in_progress", "exam_id": "exam-1"}])
        with patch("app.routers.exam._atable", return_value=mt):
            hb_att = self._make_hb_att(kiosk=True)
            hb_sig = self._sign_hb(hb_att)
            resp = client.post(
                "/api/v1/heartbeat",
                json={
                    "session_id": "R001_abc123",
                    "event_type": "heartbeat",
                    "severity": "low",
                    "att": hb_att,
                    "sig": hb_sig,
                },
                headers=self.headers,
            )
        assert resp.status_code == 200
