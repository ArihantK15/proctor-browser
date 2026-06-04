; ─────────────────────────────────────────────────────────────────────
; Custom NSIS install script for Procta.
;
; v2.3.3 attempted: wipe legacy install dirs before extraction.
; v2.3.4 hardens that with two additions discovered after v2.3.3
; shipped and the user's machine STILL re-installed back into
; $LOCALAPPDATA\Programs\proctor-browser\:
;
;   1. Force INSTDIR to the canonical Procta path in customInit.
;      NSIS oneClick installers read InstallLocation from the
;      registry uninstall entry of any prior install — if that
;      points to a legacy dir like proctor-browser\, the new
;      installer extracts THERE instead of relocating. Setting
;      $INSTDIR explicitly overrides that.
;   2. Delete the legacy Uninstall registry entries so NSIS can't
;      resurrect them on subsequent installs.
;
; Preserves $APPDATA\Procta (canonical userData — logins / cached
; tokens). Wipes legacy userData dirs from the same rename history.
; ─────────────────────────────────────────────────────────────────────

!macro customInit
  DetailPrint "Procta: cleaning legacy install directories…"

  ; ── 1. Force canonical install dir ─────────────────────────────
  ; This is the critical line. Without it, NSIS reads the existing
  ; UninstallString from registry and re-installs to whatever path
  ; the old uninstaller was registered at (proctor-browser\ for
  ; any user whose first install predates the productName rename).
  StrCpy $INSTDIR "$LOCALAPPDATA\Programs\Procta"

  ; ── 2. Wipe legacy install directories ─────────────────────────
  ; AND the canonical Procta\ directory. v2.3.4 left Procta\ alone on
  ; the theory of "let NSIS upgrade in-place"; in practice that left a
  ; partial / stale asar from an earlier interrupted install at the
  ; canonical path, causing the lobby to still fail with
  ; ERR_FILE_NOT_FOUND despite the install going to the right dir.
  ; Wiping Procta\ here too forces a clean extraction. The dir is
  ; immediately re-created by NSIS during file extraction. userData
  ; ($APPDATA\Procta) is NOT touched — logins survive.
  RMDir /r "$LOCALAPPDATA\Programs\proctor-browser"
  RMDir /r "$LOCALAPPDATA\Programs\Procta Browser"
  RMDir /r "$LOCALAPPDATA\Programs\Proctor Browser"
  RMDir /r "$LOCALAPPDATA\Programs\Procta"

  ; ── 3. Wipe legacy userData dirs (keep canonical $APPDATA\Procta) ─
  RMDir /r "$APPDATA\proctor-browser"
  RMDir /r "$APPDATA\Procta Browser"
  RMDir /r "$APPDATA\Proctor Browser"

  ; ── 4. Wipe legacy Uninstall registry entries so NSIS can't
  ;       resurrect the legacy install dir on the next reinstall.
  ;       electron-builder writes these under:
  ;         HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\<appId>
  ;       Old appId was com.proctor.browser, new is net.procta.browser.
  ;       We also try by display name patterns and the package.name
  ;       fallback (proctor-browser) some early builds used as the key.
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\com.proctor.browser"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Proctor Browser"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Procta Browser"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\proctor-browser"
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\com.proctor.browser"
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Proctor Browser"
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Procta Browser"
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\proctor-browser"

  ; ── 5. Stale procta:// URL handler. New install re-registers ────
  DeleteRegKey HKCU "Software\Classes\procta"

  ; ── 6. Stale Start Menu / Desktop shortcuts pointing at the old
  ;       binary that no longer exists after step 2.
  Delete "$SMPROGRAMS\proctor-browser.lnk"
  Delete "$SMPROGRAMS\Procta Browser.lnk"
  Delete "$SMPROGRAMS\Proctor Browser.lnk"
  Delete "$DESKTOP\proctor-browser.lnk"
  Delete "$DESKTOP\Procta Browser.lnk"
  Delete "$DESKTOP\Proctor Browser.lnk"
!macroend

; ─────────────────────────────────────────────────────────────────────
; Visual C++ runtime — REQUIRED by onnxruntime (the face / gaze / object
; detection models all load through it). Without msvcp140.dll /
; vcruntime140_1.dll, the proctor's `import onnxruntime` fails with
;   ImportError: DLL load failed … the specified module could not be found
; which kills ALL AI proctoring and strands the student on gaze
; calibration with only "camera may be unavailable". End-user Windows
; machines very often lack this runtime, so we bundle the official
; redistributable into Setup.exe and install it silently here.
;
; vc_redist is idempotent: it no-ops (returns 1638 / 3010) when an
; equal-or-newer runtime is already present, so running it on every
; install is safe. We deliberately do NOT gate the app install on its
; exit code — a student who already has the runtime, or whose machine
; blocks the redist, must still get Procta.
;
; CI (.github/workflows/build.yml) downloads vc_redist.x64.exe into the
; build/ resources dir before electron-builder packages. The /FileExists
; guard keeps local dev builds (which don't fetch it) compiling — they
; simply ship without the embedded runtime.
; ─────────────────────────────────────────────────────────────────────
!macro customInstall
  !if /FileExists "${BUILD_RESOURCES_DIR}\vc_redist.x64.exe"
    SetOutPath "$PLUGINSDIR"
    File "${BUILD_RESOURCES_DIR}\vc_redist.x64.exe"
    DetailPrint "Installing Microsoft Visual C++ runtime (required for AI proctoring)…"
    ExecWait '"$PLUGINSDIR\vc_redist.x64.exe" /quiet /norestart' $0
    DetailPrint "Visual C++ runtime installer exit code: $0"
    SetOutPath "$INSTDIR"
  !else
    DetailPrint "vc_redist.x64.exe not bundled — skipping VC++ runtime install"
  !endif
!macroend
