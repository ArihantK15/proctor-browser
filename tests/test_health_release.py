"""GET /health exposes the deployed release identifier (GIT_SHA / SOURCE_COMMIT
/ APP_VERSION fallback chain), so an external monitor can confirm a deploy
actually took effect by diffing this against the commit it just pushed —
not just that the endpoint returns 200."""
import os

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_includes_release_field():
    r = client.get("/health")
    assert r.status_code in (200, 503)
    assert "release" in r.json()


def test_health_release_prefers_git_sha():
    with patch.dict(os.environ, {"GIT_SHA": "abc123", "APP_VERSION": "v2.5.4"}):
        assert client.get("/health").json()["release"] == "abc123"


def test_health_release_falls_back_to_app_version(monkeypatch):
    monkeypatch.delenv("GIT_SHA", raising=False)
    monkeypatch.delenv("SOURCE_COMMIT", raising=False)
    monkeypatch.setenv("APP_VERSION", "v2.5.4")
    assert client.get("/health").json()["release"] == "v2.5.4"


def test_health_release_none_when_nothing_set(monkeypatch):
    for k in ("GIT_SHA", "SOURCE_COMMIT", "APP_VERSION"):
        monkeypatch.delenv(k, raising=False)
    assert client.get("/health").json()["release"] is None
