; ─────────────────────────────────────────────────────────────────────
; Custom NSIS install script for Procta.
;
; Why this exists:
;   The productName field in package.json has drifted twice over the
;   project's life — "Proctor Browser" → "Procta Browser" → "Procta".
;   On top of that, some early oneClick builds installed under the
;   package.json `name` field ("proctor-browser") instead of
;   productName. When a user installs a current build (productName
;   "Procta") on top of any of these legacy installs, NSIS sometimes
;   updates-in-place at the legacy path instead of relocating to
;   $LOCALAPPDATA\Programs\Procta. The legacy app.asar then sits
;   side-by-side with the new main.js, and main.js looks for files
;   the legacy asar doesn't contain (e.g. app/static/student.html,
;   which was only added April 2026). Lobby fails to open with
;   ERR_FILE_NOT_FOUND — exactly the bug reported during demo prep.
;
; The fix: in customInit (runs BEFORE the new files extract), force
; a hard wipe of every known legacy install directory. The user's
; userData ($APPDATA\Procta) is preserved so logins and any local
; autosave aren't blown away. Only the on-disk app payload is reset.
;
; Safe to no-op when the directories don't exist (RMDir /r is silent
; on missing paths).
; ─────────────────────────────────────────────────────────────────────

!macro customInit
  DetailPrint "Procta: cleaning legacy install directories…"

  ; Old install layouts the rename + electron-builder version drift
  ; left behind. We do NOT delete $LOCALAPPDATA\Programs\Procta here
  ; because that's our CURRENT install dir — let NSIS upgrade that
  ; in place normally.
  RMDir /r "$LOCALAPPDATA\Programs\proctor-browser"
  RMDir /r "$LOCALAPPDATA\Programs\Procta Browser"
  RMDir /r "$LOCALAPPDATA\Programs\Proctor Browser"

  ; Stale userData directories from the same rename history. We keep
  ; the current $APPDATA\Procta dir alone (preserves logins / cached
  ; tokens) but wipe the legacy ones so they don't keep firing
  ; auto-update checks or holding orphan caches.
  RMDir /r "$APPDATA\proctor-browser"
  RMDir /r "$APPDATA\Procta Browser"
  RMDir /r "$APPDATA\Proctor Browser"

  ; Stale procta:// URL handler registrations. New install will
  ; re-register correctly; legacy entries can route deeplinks at the
  ; old (now-deleted) binary path and silently fail.
  DeleteRegKey HKCU "Software\Classes\procta"

  ; Stale Start Menu shortcuts pointing at the old install dir.
  Delete "$SMPROGRAMS\proctor-browser.lnk"
  Delete "$SMPROGRAMS\Procta Browser.lnk"
  Delete "$SMPROGRAMS\Proctor Browser.lnk"
  Delete "$DESKTOP\proctor-browser.lnk"
  Delete "$DESKTOP\Procta Browser.lnk"
  Delete "$DESKTOP\Proctor Browser.lnk"
!macroend
