"""Regression: GitHub release asset → download-URL resolution.

The mac matchers silently 404'd /download/mac and /download/mac-x64 across
multiple releases once electron-builder's artifactName gained a "-mac" suffix
(Procta-<ver>-arm64-mac.dmg / -x64-mac.dmg) — _match_mac_arm64 required
"-arm64.dmg" and _match_mac_x64 rejected any "-mac" name. Nothing tested it, so
it shipped broken release after release. These lock the matchers to the real
naming convention and to ignoring the non-installer assets (.zip/.blockmap/.yml).
"""
from app.services.release import _pick_release_assets


def _assets(*names):
    return [{"name": n, "browser_download_url": f"https://example.com/{n}"} for n in names]


# Exactly the asset set a real release publishes (see v2.3.28).
_REAL = _assets(
    "latest-mac.yml", "latest.yml",
    "Procta-2.3.28-arm64-mac.dmg", "Procta-2.3.28-arm64-mac.dmg.blockmap",
    "Procta-2.3.28-arm64-mac.zip", "Procta-2.3.28-arm64-mac.zip.blockmap",
    "Procta-2.3.28-x64-mac.dmg", "Procta-2.3.28-x64-mac.dmg.blockmap",
    "Procta-2.3.28-x64-mac.zip", "Procta-2.3.28-x64-mac.zip.blockmap",
    "Procta-Setup-2.3.28.exe", "Procta-Setup-2.3.28.exe.blockmap",
)


def test_resolves_all_three_platforms_from_real_release_names():
    found = _pick_release_assets(_REAL)
    assert found["mac_arm"].endswith("Procta-2.3.28-arm64-mac.dmg")
    assert found["mac_x64"].endswith("Procta-2.3.28-x64-mac.dmg")
    assert found["win"].endswith("Procta-Setup-2.3.28.exe")


def test_ignores_zip_blockmap_and_yml():
    found = _pick_release_assets(_assets(
        "Procta-2.3.28-arm64-mac.zip", "Procta-2.3.28-arm64-mac.dmg.blockmap",
        "latest.yml", "latest-mac.yml", "Procta-Setup-2.3.28.exe.blockmap",
    ))
    assert found == {"mac_arm": "", "mac_x64": "", "win": ""}


def test_arm64_dmg_not_miscategorised_as_x64():
    # _match_mac_x64 is "any .dmg without arm64" — it must not swallow the arm64 build.
    found = _pick_release_assets(_assets("Procta-9.9.9-arm64-mac.dmg"))
    assert found["mac_arm"].endswith("arm64-mac.dmg")
    assert found["mac_x64"] == ""


def test_skips_assets_with_no_download_url():
    found = _pick_release_assets(
        [{"name": "Procta-Setup-2.3.28.exe", "browser_download_url": ""}])
    assert found["win"] == ""


def test_empty_release_yields_all_empty():
    assert _pick_release_assets([]) == {"mac_arm": "", "mac_x64": "", "win": ""}
