"""Fixtures for Cloudflare Turnstile verification (services/turnstile.py).

This is an auth bot-gate, so the critical property is FAIL-CLOSED: any
network error, non-200, or non-JSON body must deny (return False), never
let a request through. Sandbox mode (no secret key) intentionally passes
so local dev works. The siteverify HTTP call is stubbed.
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import time
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
import pytest
from fastapi import HTTPException

from app.services import turnstile as ts


class _FakeResp:
    def __init__(self, status=200, json_data=None, raise_json=False):
        self.status_code = status
        self._json = json_data
        self._raise = raise_json

    def json(self):
        if self._raise:
            raise ValueError("not json")
        return self._json


class _FakeClient:
    def __init__(self, resp=None, exc=None):
        self._resp, self._exc = resp, exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, data=None):
        if self._exc:
            raise self._exc
        return self._resp


def _patch_client(monkeypatch, *, resp=None, exc=None):
    monkeypatch.setattr(ts.httpx, "AsyncClient", lambda *a, **k: _FakeClient(resp=resp, exc=exc))


@pytest.mark.asyncio
async def test_sandbox_mode_passes_without_key(monkeypatch):
    monkeypatch.delenv("TURNSTILE_SECRET_KEY", raising=False)
    assert await ts.verify("anything") is True


@pytest.mark.asyncio
async def test_missing_token_denied_when_configured(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "secret")
    assert await ts.verify(None) is False
    assert await ts.verify("") is False


@pytest.mark.asyncio
async def test_valid_token_passes(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "secret")
    _patch_client(monkeypatch, resp=_FakeResp(200, {"success": True}))
    assert await ts.verify("good-token", remote_ip="1.2.3.4") is True


@pytest.mark.asyncio
async def test_rejected_token_denied(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "secret")
    _patch_client(monkeypatch, resp=_FakeResp(200, {"success": False,
                                                    "error-codes": ["invalid-input-response"]}))
    assert await ts.verify("bad-token") is False


@pytest.mark.asyncio
async def test_non_200_fails_closed(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "secret")
    _patch_client(monkeypatch, resp=_FakeResp(503, {"success": True}))
    assert await ts.verify("token") is False


@pytest.mark.asyncio
async def test_non_json_body_fails_closed(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "secret")
    _patch_client(monkeypatch, resp=_FakeResp(200, raise_json=True))
    assert await ts.verify("token") is False


@pytest.mark.asyncio
async def test_network_error_fails_closed(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "secret")
    _patch_client(monkeypatch, exc=httpx.RequestError("connection reset"))
    assert await ts.verify("token") is False


@pytest.mark.asyncio
async def test_non_dict_json_fails_closed(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "secret")
    _patch_client(monkeypatch, resp=_FakeResp(200, ["unexpected", "list"]))
    assert await ts.verify("token") is False


class _Req:
    client = type("c", (), {"host": "9.9.9.9"})()

    def __init__(self, headers=None):
        self.headers = headers or {}


@pytest.mark.asyncio
async def test_verify_or_403_raises_on_failure(monkeypatch):
    async def _deny(token, remote_ip=""):
        return False
    monkeypatch.setattr(ts, "verify", _deny)
    with pytest.raises(HTTPException) as ei:
        await ts.verify_or_403(_Req(), "token")
    assert ei.value.status_code == 403
    assert ei.value.detail["error"] == "BOT_CHECK_FAILED"


@pytest.mark.asyncio
async def test_verify_or_403_passes_on_success(monkeypatch):
    async def _allow(token, remote_ip=""):
        return True
    monkeypatch.setattr(ts, "verify", _allow)
    await ts.verify_or_403(_Req(), "token")  # must not raise


# ── App attestation (desktop-app Turnstile exemption) ──────────────────
# The Electron lobby renders login/signup via the procta-lobby:// custom
# scheme, whose "domain" Cloudflare won't let you allowlist for a Turnstile
# sitekey. Instead the app signs a small payload with the build-time
# KIOSK_ATTESTATION_SECRET and the server accepts a valid signature in
# place of a Turnstile token. These tests exercise the real HMAC path end
# to end — same canonical-JSON + HMAC-SHA256 scheme as lib/attestation.js.


def _sign_att(secret: str, att: dict) -> str:
    canonical = json.dumps(att, sort_keys=True, separators=(",", ":"))
    return hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()


def _attestation_headers(secret: str, ts_value: float | None = None) -> dict:
    att = {"ts": ts_value if ts_value is not None else time.time()}
    sig = _sign_att(secret, att)
    b64 = base64.b64encode(json.dumps(att).encode()).decode()
    return {"x-procta-app-attestation": b64, "x-procta-app-signature": sig}


@pytest.mark.asyncio
async def test_app_attestation_valid_signature_bypasses_turnstile(monkeypatch):
    with patch("app.services.kiosk_attest.KIOSK_ATTESTATION_SECRET", "test-secret"):
        req = _Req(headers=_attestation_headers("test-secret"))

        async def _fail_if_called(token, remote_ip=""):
            raise AssertionError("verify() should not run when app attestation is valid")
        monkeypatch.setattr(ts, "verify", _fail_if_called)
        await ts.verify_or_403(req, None)  # no captcha_token at all — must not raise


@pytest.mark.asyncio
async def test_app_attestation_wrong_secret_falls_back_to_turnstile(monkeypatch):
    with patch("app.services.kiosk_attest.KIOSK_ATTESTATION_SECRET", "real-secret"):
        req = _Req(headers=_attestation_headers("wrong-secret"))

        async def _deny(token, remote_ip=""):
            return False
        monkeypatch.setattr(ts, "verify", _deny)
        with pytest.raises(HTTPException) as ei:
            await ts.verify_or_403(req, None)
        assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_app_attestation_stale_timestamp_falls_back_to_turnstile(monkeypatch):
    with patch("app.services.kiosk_attest.KIOSK_ATTESTATION_SECRET", "test-secret"):
        req = _Req(headers=_attestation_headers("test-secret", ts_value=time.time() - 1000))

        async def _deny(token, remote_ip=""):
            return False
        monkeypatch.setattr(ts, "verify", _deny)
        with pytest.raises(HTTPException):
            await ts.verify_or_403(req, None)


@pytest.mark.asyncio
async def test_missing_attestation_headers_falls_back_to_turnstile(monkeypatch):
    req = _Req(headers={})

    async def _allow(token, remote_ip=""):
        return True
    monkeypatch.setattr(ts, "verify", _allow)
    await ts.verify_or_403(req, "real-turnstile-token")  # normal path still works


@pytest.mark.asyncio
async def test_malformed_attestation_header_falls_back_to_turnstile(monkeypatch):
    req = _Req(headers={
        "x-procta-app-attestation": "not-valid-base64!!!",
        "x-procta-app-signature": "deadbeef",
    })

    async def _deny(token, remote_ip=""):
        return False
    monkeypatch.setattr(ts, "verify", _deny)
    with pytest.raises(HTTPException):
        await ts.verify_or_403(req, None)
