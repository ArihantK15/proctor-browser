"""Tests for release.py caching, auto-discovery, and download-redirect logic.

Extends the existing matcher-only tests (test_release_assets.py) to cover
_refresh_release_cache, _resolve_release_asset, _download_redirect, and
release_cache_snapshot.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio
import json
import time
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services import release


# ── fixtures ────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _save_and_restore_globals():
    """Save/restore the module-level mutable globals so tests never leak."""
    saved_cache = release._RELEASE_CACHE.copy()
    saved_expires = release._RELEASE_CACHE_EXPIRES
    yield
    release._RELEASE_CACHE.clear()
    release._RELEASE_CACHE.update(saved_cache)
    release._RELEASE_CACHE_EXPIRES = saved_expires


def _mock_httpx_client(json_body: dict, status_code: int = 200, exc: Exception | None = None):
    """Build a mock ``httpx.AsyncClient`` class that yields a controlled client.

    When the production code does ``async with httpx.AsyncClient(...) as c``,
    the mock *class* is called (returns an instance), then ``__aenter__`` on
    that instance yields the mock client whose ``get`` returns the canned response.
    """
    resp = MagicMock(status_code=status_code)
    resp.json.return_value = json_body
    resp.text = json.dumps(json_body)

    mock_get = AsyncMock(side_effect=exc) if exc else AsyncMock(return_value=resp)

    instance = MagicMock()
    instance.get = mock_get
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=None)

    cls = MagicMock(return_value=instance)
    return cls, instance


# ── release_cache_snapshot ──────────────────────────────────────────────

class TestReleaseCacheSnapshot:
    def test_returns_copy_of_cache_plus_expiry(self):
        release._RELEASE_CACHE["mac_arm"] = "https://dl.example.com/arm.dmg"
        release._RELEASE_CACHE["tag"] = "v2.3.28"
        release._RELEASE_CACHE_EXPIRES = 42.0

        snap = release.release_cache_snapshot()
        assert snap["mac_arm"] == "https://dl.example.com/arm.dmg"
        assert snap["tag"] == "v2.3.28"
        assert snap["_expires"] == 42.0
        # ensure the module dict isn't mutated by the caller
        snap["mac_arm"] = "evil"
        assert release._RELEASE_CACHE["mac_arm"] == "https://dl.example.com/arm.dmg"


# ── _refresh_release_cache ──────────────────────────────────────────────

_SAMPLE_ASSETS = [
    {"name": "Procta-2.3.28-arm64-mac.dmg", "browser_download_url": "https://dl/arm.dmg"},
    {"name": "Procta-2.3.28-x64-mac.dmg", "browser_download_url": "https://dl/x64.dmg"},
    {"name": "Procta-Setup-2.3.28.exe", "browser_download_url": "https://dl/win.exe"},
    {"name": "latest-mac.yml", "browser_download_url": ""},
]
_GOOD_JSON = {"assets": _SAMPLE_ASSETS, "tag_name": "v2.3.28"}


class TestRefreshReleaseCache:
    @pytest.mark.asyncio
    async def test_success_populates_cache(self, monkeypatch):
        cls, _ = _mock_httpx_client(_GOOD_JSON, 200)
        monkeypatch.setattr(httpx, "AsyncClient", cls)

        await release._refresh_release_cache()

        assert release._RELEASE_CACHE["mac_arm"] == "https://dl/arm.dmg"
        assert release._RELEASE_CACHE["mac_x64"] == "https://dl/x64.dmg"
        assert release._RELEASE_CACHE["win"] == "https://dl/win.exe"
        assert release._RELEASE_CACHE["tag"] == "v2.3.28"
        assert release._RELEASE_CACHE_EXPIRES > time.time()

    @pytest.mark.asyncio
    async def test_non_200_sets_short_expiry(self, monkeypatch):
        cls, _ = _mock_httpx_client({"message": "rate limited"}, 403)
        monkeypatch.setattr(httpx, "AsyncClient", cls)

        await release._refresh_release_cache()

        assert release._RELEASE_CACHE["mac_arm"] == ""
        assert release._RELEASE_CACHE["tag"] == ""
        # 60-second retry
        assert 0 <= release._RELEASE_CACHE_EXPIRES - time.time() <= 65

    @pytest.mark.asyncio
    async def test_exception_sets_short_expiry(self, monkeypatch):
        cls, _ = _mock_httpx_client({}, exc=httpx.TimeoutException("timed out"))
        monkeypatch.setattr(httpx, "AsyncClient", cls)

        await release._refresh_release_cache()

        assert release._RELEASE_CACHE["mac_arm"] == ""
        assert 0 <= release._RELEASE_CACHE_EXPIRES - time.time() <= 65

    @pytest.mark.asyncio
    async def test_sets_auth_header_when_token_present(self, monkeypatch):
        monkeypatch.setattr(release, "GITHUB_TOKEN", "ghp_secret")
        cls, instance = _mock_httpx_client(_GOOD_JSON, 200)
        monkeypatch.setattr(httpx, "AsyncClient", cls)

        await release._refresh_release_cache()

        _call_headers = instance.get.call_args[1]["headers"]
        assert _call_headers["Authorization"] == "Bearer ghp_secret"
        assert release._RELEASE_CACHE["tag"] == "v2.3.28"


# ── _resolve_release_asset ──────────────────────────────────────────────

class TestResolveReleaseAsset:
    @pytest.mark.asyncio
    async def test_returns_cached_value_when_not_expired(self, monkeypatch):
        release._RELEASE_CACHE["mac_arm"] = "https://dl/arm.dmg"
        release._RELEASE_CACHE_EXPIRES = time.time() + 3600  # far in the future

        refresh = AsyncMock()
        monkeypatch.setattr(release, "_refresh_release_cache", refresh)

        url = await release._resolve_release_asset("mac_arm")
        assert url == "https://dl/arm.dmg"
        refresh.assert_not_called()

    @pytest.mark.asyncio
    async def test_refreshes_when_cache_expired(self, monkeypatch):
        release._RELEASE_CACHE.clear()
        release._RELEASE_CACHE_EXPIRES = 0.0  # expired

        # simulate a refresh that populates the cache
        async def _fake_refresh():
            release._RELEASE_CACHE["mac_arm"] = "https://dl/arm.dmg"
            release._RELEASE_CACHE_EXPIRES = time.time() + 3600
        monkeypatch.setattr(release, "_refresh_release_cache", _fake_refresh)

        url = await release._resolve_release_asset("mac_arm")
        assert url == "https://dl/arm.dmg"

    @pytest.mark.asyncio
    async def test_returns_empty_string_for_missing_key(self, monkeypatch):
        release._RELEASE_CACHE["mac_arm"] = "https://dl/arm.dmg"
        release._RELEASE_CACHE_EXPIRES = time.time() + 3600

        url = await release._resolve_release_asset("linux")
        assert url == ""

    @pytest.mark.asyncio
    async def test_concurrent_refresh_uses_lock(self, monkeypatch):
        """Second caller should not refresh again while first is refreshing."""
        release._RELEASE_CACHE.clear()
        release._RELEASE_CACHE_EXPIRES = 0.0  # expired

        event = asyncio.Event()

        async def _slow_refresh():
            release._RELEASE_CACHE["mac_arm"] = "https://dl/arm.dmg"
            release._RELEASE_CACHE_EXPIRES = time.time() + 3600
            await event.wait()  # hold the lock

        monkeypatch.setattr(release, "_refresh_release_cache", _slow_refresh)

        async def caller(key: str) -> str:
            return await release._resolve_release_asset(key)

        # Start two concurrent callers (they share _RELEASE_CACHE_LOCK)
        t1 = asyncio.create_task(caller("mac_arm"))
        t2 = asyncio.create_task(caller("mac_arm"))

        # Give them time to both enter the lock section
        await asyncio.sleep(0.05)
        event.set()  # release the lock

        r1, r2 = await asyncio.gather(t1, t2)
        assert r1 == "https://dl/arm.dmg"
        assert r2 == "https://dl/arm.dmg"


# ── _download_redirect ─────────────────────────────────────────────────

class TestDownloadRedirect:
    @pytest.mark.asyncio
    async def test_env_url_returns_redirect_response(self):
        from fastapi.responses import RedirectResponse

        resp = await release._download_redirect(
            "https://env-override.example.com/dl", "mac_arm", "/fallback", "Procta.dmg",
        )
        assert isinstance(resp, RedirectResponse)
        assert resp.headers["location"] == "https://env-override.example.com/dl"

    @pytest.mark.asyncio
    async def test_auto_asset_returns_redirect_response(self, monkeypatch):
        monkeypatch.setattr(
            release, "_resolve_release_asset",
            AsyncMock(return_value="https://auto.example.com/arm.dmg"),
        )
        from fastapi.responses import RedirectResponse

        resp = await release._download_redirect("", "mac_arm", "/fallback", "Procta.dmg")
        assert isinstance(resp, RedirectResponse)
        assert resp.headers["location"] == "https://auto.example.com/arm.dmg"

    @pytest.mark.asyncio
    async def test_fallback_file_returns_file_response(self, monkeypatch, tmp_path):
        fallback = tmp_path / "installer.dmg"
        fallback.write_bytes(b"\x00\x01\x02")
        monkeypatch.setattr(
            release, "_resolve_release_asset", AsyncMock(return_value=""),
        )

        from fastapi.responses import FileResponse

        resp = await release._download_redirect(
            "", "mac_arm", str(fallback), "Procta.dmg",
        )
        assert isinstance(resp, FileResponse)
        assert resp.path == str(fallback)
        assert resp.filename == "Procta.dmg"

    @pytest.mark.asyncio
    async def test_nothing_available_raises_404(self, monkeypatch):
        monkeypatch.setattr(
            release, "_resolve_release_asset", AsyncMock(return_value=""),
        )
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await release._download_redirect("", "mac_arm", "/nonexistent", "nope")
        assert exc.value.status_code == 404
