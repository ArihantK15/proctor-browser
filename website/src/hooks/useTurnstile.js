/**
 * Cloudflare Turnstile widget hook.
 *
 * Renders an invisible Managed-mode widget that solves itself in the
 * background. Returns the token + a forceRefresh() callback you can
 * call after a form submission to get a fresh token for the next try.
 *
 * Usage:
 *   const { token, ref, refresh } = useTurnstile()
 *   <div ref={ref} />                 ← widget mounts here
 *   <button onClick={() => submit({ captcha_token: token })}>...</button>
 *
 * If VITE_TURNSTILE_SITE_KEY is unset, the hook quietly returns a null
 * token. The backend's sandbox mode (TURNSTILE_SECRET_KEY also unset)
 * will accept the request anyway. In production both halves must be
 * configured.
 */
import { useEffect, useRef, useState, useCallback } from 'react'

const SITE_KEY = import.meta.env.VITE_TURNSTILE_SITE_KEY || ''
const SCRIPT_SRC = 'https://challenges.cloudflare.com/turnstile/v0/api.js'

let _scriptPromise = null
function _loadScript() {
  if (typeof window === 'undefined') return Promise.resolve()
  if (window.turnstile) return Promise.resolve()
  if (_scriptPromise) return _scriptPromise
  _scriptPromise = new Promise((resolve) => {
    const s = document.createElement('script')
    s.src = SCRIPT_SRC
    s.async = true
    s.defer = true
    s.onload = () => resolve()
    s.onerror = () => resolve() // fail-open: sandbox path will handle
    document.head.appendChild(s)
  })
  return _scriptPromise
}

export default function useTurnstile() {
  const ref = useRef(null)
  const widgetIdRef = useRef(null)
  const [token, setToken] = useState(null)
  const [loading, setLoading] = useState(!!SITE_KEY) // true while widget loads (only when key is set)
  const [error, setError] = useState(null)

  const render = useCallback(() => {
    if (!SITE_KEY) {
      setLoading(false)
      return // no site key → backend sandbox mode
    }
    if (!ref.current || !window.turnstile) {
      console.warn('[turnstile] ref.current or window.turnstile not ready', { ref: ref.current, turnstile: !!window.turnstile })
      return
    }
    // Remove any prior widget on this element before re-rendering
    if (widgetIdRef.current != null) {
      try { window.turnstile.remove(widgetIdRef.current) } catch { /* noop */ }
      widgetIdRef.current = null
    }
    try {
      widgetIdRef.current = window.turnstile.render(ref.current, {
        sitekey: SITE_KEY,
        // Managed mode = invisible unless bot signal is high
        appearance: 'interaction-only',
        theme: 'dark',
        callback: (tok) => {
          console.log('[turnstile] callback, token received')
          setToken(tok)
          setLoading(false)
        },
        'expired-callback': () => {
          console.log('[turnstile] expired-callback')
          setToken(null)
          setLoading(false)
        },
        'error-callback': (err) => {
          console.error('[turnstile] error-callback', err)
          setError(err)
          setToken(null)
          setLoading(false)
        },
      })
      console.log('[turnstile] widget rendered, id:', widgetIdRef.current)
    } catch (e) {
      console.error('[turnstile] render failed', e)
      setError(e)
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    _loadScript().then(() => {
      if (cancelled) return
      // Defer to next tick so the ref is attached
      requestAnimationFrame(render)
    })
    return () => {
      cancelled = true
      if (widgetIdRef.current != null && window.turnstile) {
        try { window.turnstile.remove(widgetIdRef.current) } catch { /* noop */ }
      }
    }
  }, [render])

  // After a failed submit, call refresh() to get a fresh token before
  // the next try (Turnstile tokens are single-use).
  const refresh = useCallback(() => {
    setToken(null)
    setLoading(true)
    setError(null)
    if (widgetIdRef.current != null && window.turnstile) {
      try { window.turnstile.reset(widgetIdRef.current) } catch { /* noop */ }
    }
  }, [])

  return { token, ref, refresh, enabled: !!SITE_KEY, loading, error }
}
