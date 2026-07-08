# Installer/Updater Hardening — Design

**Goal:** Three fixes to the Electron desktop app's build/install/update pipeline: (1) make Windows delta updates actually small by decoupling the rarely-changing binary blobs from the diffed installer payload, (2) make the Visual C++ runtime install compulsory with a clear explanation and retry-until-yes instead of a silent best-effort attempt, (3) add the same CI blockmap-presence safety gate to macOS that Windows already has.

**Correction from the scoping conversation:** I originally described the Windows bundle as containing "hundreds of MB" of Python packages inside the diffed installer. That was wrong — verified against the actual code before writing this spec, not re-asserted. The heavy ML packages (`opencv-python`, `onnxruntime`, `insightface`, `vosk`, etc. — the genuinely large dependencies) are **already decoupled**: `ensureVenv()`/pip-install happens at first run (`lib/python-manager.js:473-560`, keyed by a cache marker so it only re-runs when `PIP_PACKAGES` or the app version actually changes), fetched from PyPI over the internet, never bundled in the installer at all. What **is** bundled in `extraResources` (`package.json`) and therefore sits inside the same diffed NSIS payload every release is:
- `python-runtime` / `resources/python` — the base CPython interpreter only (python-build-standalone, `bundle-python.js`), no packages
- `weights/` — **64 MB** of ONNX model files (confirmed via `du -sh weights/`), which change only when a model is retrained/swapped, not on every app-code release

So the real fix is narrower than first described: decouple `weights/` (and the interpreter, while touching this) from the diffed payload — not "the Python runtime" in the large sense I originally implied.

## Global Constraints

- This is exam-proctoring software; a broken update pipeline can strand students on exam day. No change here ships without a real test on a clean VM (no prior install, no cached state) before merging.
- Do not touch `PIP_PACKAGES`/`ensureVenv` — that first-run mechanism already works and is out of scope.
- Windows and macOS both need to keep working after this change; do not regress the existing CI gates (`scripts/verify-macos-build.mjs`, the Windows blockmap check).
- No new external paid infrastructure — reuse GitHub Releases as the asset host (same place installers/blockmaps already live), not a new CDN/bucket.

---

## 1. Decouple `weights/` (and the base interpreter) from the diffed installer

### Current state
`package.json`'s `build.extraResources` bundles `weights` and `python-runtime`/`resources/python` directly into the packaged app via electron-builder, which means they're inside the same NSIS payload that `differentialPackage: true` block-diffs release to release. Compressed-archive block diffing only produces a small delta when unchanged bytes land at the same offset in both versions — repacking 64+ MB of binary model weights (even if none of them changed) shifts every subsequent compressed byte, so the actual delta a student downloads ends up close to the full installer size regardless of how small the real app-code change was.

### Chosen approach: move to a separately-versioned, first-run-fetched cache — reusing the existing setup-window pattern
The app **already has** a first-run setup flow (`setup-preload.js`, `SETUP_WIDTH`/`SETUP_HEIGHT` in `python-manager.js`, the pip-install-with-progress mechanism at `ensureVenv`). Extending that same, already-proven pattern to also fetch `weights/` (and the interpreter) is lower-risk than inventing a new NSIS-scripted download step, because:
- It's plain Node/Electron code (testable, debuggable, real retry/progress/checksum logic) instead of fragile NSIS scripting.
- It reuses a code path that already handles "first run needs internet, show progress, handle failure" for the pip-install case.

**Design:**
- `weights/` and the base interpreter move OUT of `extraResources` and are no longer packaged by electron-builder at all.
- They're instead uploaded as their own versioned asset(s) — e.g. `procta-runtime-assets-v<N>.tar.gz` — attached to a **stable, rarely-updated release** (not re-uploaded on every app version tag; only cut a new `<N>` when weights/interpreter actually change).
- A new small manifest (e.g. `RUNTIME_ASSET_VERSION` in `config.js`) declares which `<N>` the current app version expects.
- On startup, `python-manager.js` checks a version-marker file in a stable, update-independent cache directory (`app.getPath('userData')/../procta-runtime-cache/<N>/` — deliberately OUTSIDE the versioned app install directory, so it survives app updates untouched). If the marker for the expected `<N>` exists, use the cached files (the common case — near-zero cost, most updates don't bump `<N>`). If missing, download+verify+extract before continuing, showing the existing setup-window UI.
- `getBundledPython()`/weight-loading code paths in `python-manager.js`/`proctor.py` change their lookup from `process.resourcesPath` to this new cache directory (falling back to the old bundled path for one transition release, so an in-flight update from an old version doesn't break immediately).
- Integrity: SHA-256 checksum of the downloaded archive, checked against a value baked into `config.js` (same pattern as version-pinning already used for `PIP_PACKAGES`), before extraction.
- Failure handling: if the download fails, retry with backoff — reuse `bundle-python.js`'s existing `download(url, dest, attempts = 4)` pattern (`bundle-python.js:278-285`: 4 attempts, `i * 2` seconds between retries, fresh destination file each attempt) rather than inventing a new retry scheme. If it still fails after all attempts, show a clear blocking error ("Procta couldn't download required components — check your internet connection and restart the app") rather than silently limping forward with a proctor that will crash-loop.

### Impact on delta size
Once `weights/`+interpreter are out of the diffed payload, the NSIS installer only contains app code (main/renderer JS, HTML, CSS, native Electron binaries) — the part that block-diffs well because it's genuinely small and changes are genuinely localized.

## 2. Compulsory VC++ Redistributable install (Windows only)

### Current state
`build/installer.nsh`'s `customInstall` macro runs `vc_redist.x64.exe /quiet /norestart`, captures `$0` (the exit code), logs it, and **does not gate on it** — the comment explicitly says the app must install either way. `vc_redist.x64.exe` carries its own manifest requiring administrator elevation, so Windows shows a UAC prompt regardless of the `/quiet` flag on the redistributable itself; declining that UAC prompt aborts only the redistributable's install, silently, and `onnxruntime` then fails to import at proctor-launch time with a generic, unrelated-looking error.

### Chosen approach: explain-then-elevate, retry-until-real-success, real escape hatch
1. **Before** triggering the elevation-requiring step, show an NSIS `MessageBox` explaining why: *"Procta requires a Microsoft system component (Visual C++ Runtime) to run AI-based exam proctoring — without it, camera/gaze/object detection cannot work. The next step will ask Windows for your approval to install it."* This primes the student so the UAC prompt isn't a confusing surprise (matches the "tell them why" instruction).
2. Run `vc_redist.x64.exe /quiet /norestart`, capture `$0`.
3. **Exit code handling** (Microsoft-documented `vc_redist` codes):
   - `0` — success, proceed.
   - `3010` — success, reboot recommended but not required to proceed now — treat as success.
   - `1638` — a newer version is already installed — treat as success (nothing to do).
   - Anything else (including the UAC-decline case, which surfaces as a non-zero failure code from `ExecWait`) — **do not proceed**. Show a retry dialog: *"Procta cannot verify exams without this component. Click Retry and select 'Yes' when Windows asks for permission, or Cancel to stop installing Procta."* Loop back to step 2 on Retry.
   - On Cancel: abort the **entire** Procta installation (not just the redistributable) — `Abort` or `Quit` in NSIS — so the student doesn't end up with a Procta install that will fail on exam day with a confusing unrelated error. This is the real escape hatch the earlier scoping conversation asked for: cancel must be honest about consequences, not a silent trap.
4. Only after a success/already-present outcome does the installer proceed to finish extracting the app.

### Testing requirement
This must be verified on a real clean Windows VM without any prior VC++ runtime installed, exercising both the "click Yes" and "click No" paths, before merging — NSIS retry-loop behavior and real UAC interaction are not things to trust from code review alone.

## 3. macOS CI blockmap parity gate

### Current state
`.github/workflows/build.yml`'s Windows job has an explicit gate:
```bash
if [ ${#bmap[@]} -eq 0 ]; then echo "✗ no .blockmap produced (differential updates broken)"; exit 1; fi
```
The macOS job's equivalent step only checks that *some* file exists across `dist/*.dmg dist/*.zip dist/*.blockmap` combined — a missing mac `.blockmap` wouldn't fail the build, since dmg+zip alone satisfy that check. Verified directly in `electron-updater`'s `MacUpdater.js`/`AppUpdater.js` source that macOS **does** use the identical blockmap-diffing mechanism as Windows (fetches old+new `.blockmap`, range-requests the diff) — so this is a real, fixable gap, not a moot check for a platform that doesn't support deltas.

### Fix
Add the same explicit per-arch check to the macOS publish step: after `electron-builder --mac` produces its artifacts, verify a `.blockmap` exists for the `.zip` target specifically (not just "some file exists"), fail loud with the same message style as the Windows gate if not.

## Testing

- Task 1 (decouple weights/interpreter): test on a clean VM with no prior install (full first-run fetch path), then simulate an update from an old bundled-weights version to confirm the transition fallback path works, then simulate two consecutive app-code-only updates to confirm the SECOND one skips the runtime-asset fetch entirely (the actual point of this change).
- Task 2 (VC++ compulsory): test on a clean Windows VM with VC++ NOT pre-installed, exercising both the Yes and No path at the UAC prompt.
- Task 3 (mac CI gate): verify it actually fails when a blockmap is deliberately withheld (a quick local dry run of the bash condition), and that it passes on a normal build.
