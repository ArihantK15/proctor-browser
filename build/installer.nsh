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
  RMDir /r "$LOCALAPPDATA\Programs\proctor-browser"
  RMDir /r "$LOCALAPPDATA\Programs\Procta Browser"
  RMDir /r "$LOCALAPPDATA\Programs\Proctor Browser"

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
