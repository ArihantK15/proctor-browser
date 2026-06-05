#!/usr/bin/env python3
"""Cross-platform on-device audio model bootstrap.

Replaces the bash + Node variants of this script for the
production install flow. We use Python (which the Electron setup
window guarantees is installed by the time it calls us) so we
don't depend on bash (missing on Windows) and don't need to
re-enable the Electron `runAsNode` fuse (security-hardened off).

Idempotent + crash-safe: a model is considered present iff the
target path exists AND is non-empty (or, for single files, the
SHA-256 matches the pinned value).

Usage:
    python scripts/download_audio_models.py            # download missing
    python scripts/download_audio_models.py --force    # re-download all
    python scripts/download_audio_models.py --check    # report only
Exit codes:
    0 — all models present + verified
    1 — download or checksum failure
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import time
import urllib.request
import urllib.error
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Windows consoles default to cp1252, which CANNOT encode the ↓ ✓ ✗ → glyphs
# in the progress prints below — print() then raises UnicodeEncodeError and
# crashes the script. At build time CI masks it (best-effort `|| echo`), but a
# real Windows student hits it at first-launch model download → the crash
# silently disables audio proctoring. Force UTF-8 output (errors='replace' so
# even an exotic console can never crash this bootstrap).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
# In a packaged app the script lives under <resources>/scripts, so
# ROOT/weights resolves to <resources>/weights — which is READ-ONLY on a
# signed macOS .app bundle and on Windows Program Files. Extracting a zip
# there raises PermissionError and fails the whole download. Electron
# passes PROCTA_WEIGHTS_DIR pointing at a writable per-user dir
# (app.getPath('userData')/weights); fall back to ROOT/weights only for
# dev runs from a checkout where the tree is writable.
WEIGHTS = Path(os.environ.get("PROCTA_WEIGHTS_DIR") or (ROOT / "weights"))
WEIGHTS.mkdir(parents=True, exist_ok=True)

# Model manifest. SHA-256 hashes are PLACEHOLDERS — empty / "TODO" is
# tolerated (we still verify file integrity by re-running on failure).
# The plan is to mirror these onto a pinned GitHub release on this
# repo so a third-party CDN outage doesn't break paid exams.
MODELS = [
    {
        "name": "vosk-en",
        "url":  "https://alphacephei.com/vosk/models/vosk-model-small-en-in-0.4.zip",
        "dir":  "vosk-model-small-en-in-0.4",
        "sha":  "",
    },
    {
        "name": "vosk-hi",
        "url":  "https://alphacephei.com/vosk/models/vosk-model-small-hi-0.22.zip",
        "dir":  "vosk-model-small-hi-0.22",
        "sha":  "",
    },
    {
        "name": "silero-vad",
        # PINNED to a release tag (was raw/master — a moving branch that could
        # swap the model under us between builds). v6.2.1's file is byte-
        # identical to the master we'd been pulling, so this changes nothing but
        # makes it deterministic; the SHA below now enforces integrity (a
        # mismatch is rejected → falls back to the RMS-only voice path).
        "url":  "https://github.com/snakers4/silero-vad/raw/v6.2.1/src/silero_vad/data/silero_vad.onnx",
        "dir":  "silero_vad.onnx",  # single file
        "sha":  "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3",
    },
]


def _is_present(target: Path) -> bool:
    if not target.exists():
        return False
    if target.is_file():
        return target.stat().st_size > 0
    return any(target.iterdir())


def _verify_sha(path: Path, expected: str) -> bool:
    if not expected or expected.startswith("TODO"):
        return True
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    actual = h.hexdigest()
    if actual != expected:
        print(f"  ✗ SHA256 mismatch on {path}")
        print(f"    expected: {expected}")
        print(f"    actual:   {actual}")
        return False
    return True


def _download(url: str, dest: Path, timeout: int = 600, attempts: int = 3) -> bool:
    """Stream a URL into dest with retry + HTTP-Range resume. Follows
    redirects via urllib's default handler. On a mid-stream drop the next
    attempt resumes from the bytes already on disk (Range: bytes=N-) instead
    of restarting the whole (~40MB) file — important on the slow third-party
    CDNs (alphacephei.com) these models live on."""
    for attempt in range(1, attempts + 1):
        try:
            have = dest.stat().st_size if dest.exists() else 0
            headers = {"User-Agent": "Procta-AudioModels/1.0"}
            if have:
                headers["Range"] = f"bytes={have}-"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                # 206 → server honoured Range (append); 200 → it sent the full
                # body (start over). getattr guards older urllib without .status.
                resume = have > 0 and getattr(r, "status", 200) == 206
                with dest.open("ab" if resume else "wb") as out:
                    shutil.copyfileobj(r, out, length=1 << 20)
            return True
        except (urllib.error.URLError, urllib.error.HTTPError,
                TimeoutError, ConnectionError, OSError) as e:
            # 416 Range Not Satisfiable → our partial is already complete or
            # bogus; drop it so the next attempt fetches fresh.
            if isinstance(e, urllib.error.HTTPError) and e.code == 416:
                try: dest.unlink()
                except OSError: pass
            wait = 2 ** (attempt - 1)
            last = attempt >= attempts
            print(f"  ⚠ download attempt {attempt}/{attempts} failed: {e}"
                  + ("" if last else f" — retrying in {wait}s"))
            if not last:
                time.sleep(wait)
    print(f"  ✗ download failed after {attempts} attempts: {url}")
    return False


def _extract_zip(zip_path: Path, into_dir: Path) -> bool:
    """Pure-Python zipfile extraction — no external deps."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(into_dir)
        return True
    except zipfile.BadZipFile as e:
        print(f"  ✗ zip extraction failed (corrupt archive): {e}")
        return False
    except OSError as e:
        # PermissionError (read-only resources dir), ENOSPC, etc. Catch
        # here so a write failure returns a clean error code instead of
        # an uncaught traceback that the caller can't interpret.
        print(f"  ✗ zip extraction failed (cannot write to {into_dir}): {e}")
        return False


def _install_one(model: dict, *, force: bool, check_only: bool) -> bool:
    target = WEIGHTS / model["dir"]
    if not force and _is_present(target):
        # File-level SHA check; directories don't carry a manifest.
        if target.is_file() and not _verify_sha(target, model["sha"]):
            print(f"  ✗ {model['name']} present but checksum mismatched")
            return False
        print(f"  ✓ {model['name']} (already present)")
        return True
    if check_only:
        print(f"  ✗ {model['name']} (missing — re-run without --check)")
        return False
    print(f"  ↓ {model['name']} from {model['url']}")
    fname = model["url"].rsplit("/", 1)[-1]
    with tempfile.TemporaryDirectory(prefix="procta-audio-") as td:
        tmp = Path(td) / fname
        if not _download(model["url"], tmp):
            return False
        if not _verify_sha(tmp, model["sha"]):
            return False
        if fname.endswith(".zip"):
            if not _extract_zip(tmp, WEIGHTS):
                return False
        else:
            shutil.move(str(tmp), str(target))
        print(f"  ✓ {model['name']} installed → {target}")
        return True


def main(argv: list[str]) -> int:
    force = "--force" in argv
    check_only = "--check" in argv
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0
    print(f"[audio-models] weights dir: {WEIGHTS}")
    if check_only:
        # Cheap local stat() per model — no benefit to threading, and keeps
        # the report ordered.
        ok = all(_install_one(m, force=force, check_only=True) for m in MODELS)
    else:
        # Download the 3 models CONCURRENTLY — they're I/O-bound (~80MB total
        # from slow CDNs) and write to distinct subdirs/files in WEIGHTS, so
        # there's no collision. Cuts a sequential ~10-30min cold fetch to the
        # slowest single model.
        with ThreadPoolExecutor(max_workers=len(MODELS)) as ex:
            results = list(ex.map(
                lambda m: _install_one(m, force=force, check_only=False), MODELS))
        ok = all(results)
    if not ok:
        print("[audio-models] one or more models" + (
            " missing — re-run without --check" if check_only else " FAILED"))
        return 1
    print("[audio-models] all models present")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
