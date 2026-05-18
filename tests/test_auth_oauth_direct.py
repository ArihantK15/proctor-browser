from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlparse
from unittest.mock import AsyncMock


def test_state_token_round_trips_provider():
    from app.services import auth_oauth

    token = auth_oauth.issue_state_token(
        intent="teacher",
        return_to="https://procta.net/dashboard",
        provider="google",
    )
    claims = auth_oauth.verify_state_token(token)

    assert claims["intent"] == "teacher"
    assert claims["return_to"] == "https://procta.net/dashboard"
    assert claims["provider"] == "google"


def test_local_google_authorize_url_uses_direct_provider(monkeypatch):
    from app.services import auth_oauth

    monkeypatch.setenv("AUTH_PROVIDER", "local")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "google-client")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "google-secret")

    url = auth_oauth.build_authorize_url(provider="google", state="state-123")
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    assert parsed.netloc == "accounts.google.com"
    assert query["client_id"] == ["google-client"]
    assert query["redirect_uri"][0].endswith("/api/v1/auth/oauth/callback")
    assert query["response_type"] == ["code"]
    assert query["state"] == ["state-123"]
    assert "openid email profile" in query["scope"]


def test_hybrid_google_authorize_url_uses_direct_provider(monkeypatch):
    from app.services import auth_oauth

    monkeypatch.setenv("AUTH_PROVIDER", "hybrid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "google-client")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "google-secret")

    url = auth_oauth.build_authorize_url(provider="google", state="state-123")
    parsed = urlparse(url)

    assert parsed.netloc == "accounts.google.com"


class _FakeResponse:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data

    def json(self):
        return self._data


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        self.posts = []
        self.gets = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, data=None, headers=None):
        self.posts.append((url, data, headers))
        return _FakeResponse(200, {"access_token": "access-1"})

    async def get(self, url, headers=None):
        self.gets.append((url, headers))
        return _FakeResponse(200, {
            "sub": "google-sub-1",
            "email": "USER@Example.COM",
            "email_verified": True,
            "name": "User Example",
            "picture": "https://example.com/avatar.png",
        })


def test_direct_code_exchange_normalizes_google_profile(monkeypatch):
    from app.services import auth_oauth

    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "google-client")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "google-secret")
    monkeypatch.setattr(auth_oauth.httpx, "AsyncClient", _FakeAsyncClient)

    user = asyncio.run(auth_oauth.exchange_direct_code_for_user("code-1", provider="google"))

    assert user == {
        "id": "google:google-sub-1",
        "email": "user@example.com",
        "full_name": "User Example",
        "avatar_url": "https://example.com/avatar.png",
        "auth_provider": "google",
    }


def test_oauth_start_returns_503_when_local_provider_missing_credentials(client, monkeypatch):
    monkeypatch.setenv("AUTH_PROVIDER", "local")
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)

    resp = client.get("/api/v1/auth/oauth/start?provider=google&intent=teacher&return_to=/dashboard")

    assert resp.status_code == 503
    assert resp.json()["detail"] == "OAuth provider is not configured"


def test_oauth_callback_maps_unverified_duplicate_email_to_conflict(client, monkeypatch):
    from app.services import auth_oauth
    from app.routers import auth as auth_router

    state = auth_oauth.issue_state_token(intent="teacher", return_to="/dashboard", provider="google")
    exchange = AsyncMock(return_value={
        "id": "google:sub-1",
        "email": "existing@example.com",
        "full_name": "Existing User",
        "auth_provider": "google",
    })
    bind = AsyncMock(side_effect=ValueError("email already exists but is not verified"))
    monkeypatch.setattr(auth_oauth, "exchange_code_for_user", exchange)
    monkeypatch.setattr(auth_oauth, "bind_or_create_teacher", bind)
    monkeypatch.setattr(auth_router, "record_auth_event", AsyncMock())

    resp = client.get(f"/api/v1/auth/oauth/callback?code=code-1&state={state}", follow_redirects=False)

    assert resp.status_code == 409
    assert resp.json()["detail"] == "email already exists but is not verified"
    exchange.assert_awaited_once_with("code-1", provider="google")


def test_oauth_callback_success_redirects_with_token(client, monkeypatch):
    from app.services import auth_oauth
    from app.routers import auth as auth_router

    state = auth_oauth.issue_state_token(intent="teacher", return_to="/dashboard", provider="google")
    exchange = AsyncMock(return_value={
        "id": "google:sub-1",
        "email": "new@example.com",
        "full_name": "New User",
        "auth_provider": "google",
    })
    bind = AsyncMock(return_value={
        "id": "teacher-1",
        "email": "new@example.com",
        "full_name": "New User",
    })
    monkeypatch.setattr(auth_oauth, "exchange_code_for_user", exchange)
    monkeypatch.setattr(auth_oauth, "bind_or_create_teacher", bind)
    monkeypatch.setattr(auth_router, "issue_admin_token", lambda teacher: "jwt-token")
    monkeypatch.setattr(auth_router, "record_auth_event", AsyncMock())

    resp = client.get(f"/api/v1/auth/oauth/callback?code=code-1&state={state}", follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == "/dashboard#access_token=jwt-token&token_type=Bearer"
    assert resp.headers["Referrer-Policy"] == "no-referrer"
    exchange.assert_awaited_once_with("code-1", provider="google")
