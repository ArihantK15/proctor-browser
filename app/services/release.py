import asyncio
import logging
import os
import time

import httpx

from ..constants import RELEASE_REPO, RELEASE_TTL_SEC, GITHUB_TOKEN

logger = logging.getLogger(__name__)

_RELEASE_CACHE: dict = {"mac_arm": "", "mac_x64": "", "win": "", "tag": ""}
_RELEASE_CACHE_EXPIRES: float = 0.0
_RELEASE_CACHE_LOCK = asyncio.Lock()


def _match_mac_arm64(name: str) -> bool:
    return name.lower().endswith("-arm64.dmg")


def _match_mac_x64(name: str) -> bool:
    n = name.lower()
    return n.endswith(".dmg") and "-arm64" not in n and "-mac" not in n.replace("-macos", "")


def _match_win(name: str) -> bool:
    n = name.lower()
    return n.endswith(".exe") and "setup" in n


async def _refresh_release_cache() -> None:
    global _RELEASE_CACHE, _RELEASE_CACHE_EXPIRES
    url = f"https://api.github.com/repos/{RELEASE_REPO}/releases/latest"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "procta-backend"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as c:
            r = await c.get(url, headers=headers)
            if r.status_code != 200:
                logger.warning("[Release] GitHub API returned %s: %s", r.status_code, r.text[:200])
                _RELEASE_CACHE_EXPIRES = time.time() + 60
                return
            data = r.json()
    except Exception as e:
        logger.error("[Release] Fetch failed: %s", e)
        _RELEASE_CACHE_EXPIRES = time.time() + 60
        return
    assets = data.get("assets", []) or []
    tag = data.get("tag_name", "")
    found = {"mac_arm": "", "mac_x64": "", "win": ""}
    for a in assets:
        name = a.get("name", "") or ""
        url_ = a.get("browser_download_url", "") or ""
        if not url_:
            continue
        if not found["mac_arm"] and _match_mac_arm64(name):
            found["mac_arm"] = url_
        elif not found["mac_x64"] and _match_mac_x64(name):
            found["mac_x64"] = url_
        elif not found["win"] and _match_win(name):
            found["win"] = url_
    _RELEASE_CACHE = {**found, "tag": tag}
    _RELEASE_CACHE_EXPIRES = time.time() + RELEASE_TTL_SEC
    logger.info("[Release] Auto-discovered %s: mac_arm=%s mac_x64=%s win=%s", tag, '✓' if found['mac_arm'] else '✗', '✓' if found['mac_x64'] else '✗', '✓' if found['win'] else '✗')


async def _resolve_release_asset(key: str) -> str:
    if time.time() >= _RELEASE_CACHE_EXPIRES:
        async with _RELEASE_CACHE_LOCK:
            if time.time() >= _RELEASE_CACHE_EXPIRES:
                await _refresh_release_cache()
    return _RELEASE_CACHE.get(key, "") or ""


async def _download_redirect(env_url: str, release_key: str, fallback_path: str, fallback_name: str):
    if env_url:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=env_url)
    auto = await _resolve_release_asset(release_key)
    if auto:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=auto)
    if os.path.exists(fallback_path):
        from fastapi.responses import FileResponse
        return FileResponse(fallback_path, filename=fallback_name, media_type="application/octet-stream")
    from fastapi import HTTPException
    raise HTTPException(status_code=404, detail="Installer not available")
