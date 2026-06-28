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

console.log('[turnstile] init - SITE_KEY:', SITE_KEY ? 'SET (' + SITE_KEY.slice(0, 10) + '...)' : 'NOT SET')

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
    s.onload = () => {
      console.log('[turnstile] script loaded')
      resolve()
    }
    s.onerror = () => {
      console.error('[turnstile] script load failed')
      resolve() // fail-open: sandbox path will handle
    }
    document.head.appendChild(s)
  })
  return _scriptPromise
}

export default function useTurnstile() {
  const ref = useRef(null)
  const widgetIdRef = useRef(null)
  const [token, setToken] = useState(null)
  const [loading, setLoading] = useState(!!SITE_KEY)
  const [error, setError] = useState(null)

  const render = useCallback(() => {
    console.log('[turnstile] render called - SITE_KEY:', SITE_KEY ? 'SET' : 'NOT SET', 'ref:', ref.current, 'turnstile:', !!window.turnstile)
    if (!SITE_KEY) {
      console.log('[turnstile] no site key, disabling')
      setLoading(false)
      return
    }
    if (!ref.current) {
      console.warn('[turnstile] ref.current is null - element not mounted yet')
      return
    }
    if (!window.turnstile) {
      console.warn('[turnstile] window.turnstile not available yet')
      return
    }
    if (widgetIdRef.current != null) {
      try { window.turnstile.remove(widgetIdRef.current) } catch { /* noop */ }
      widgetIdRef.current = null
    }
    try {
      widgetIdRef.current = window.turnstile.render(ref.current, {
        sitekey: SITE_KEY,
        appearance: 'always',
        theme: 'dark',
        callback: (tok) => {
          console.log('[turnstile] callback fired, token:', tok ? tok.slice(0, 20) + '...' : 'empty')
          setToken(tok)
          setLoading(false)
        },
        'expired-callback': () => {
          console.log('[turnstile] expired-callback')
          setToken(null)
          setLoading(false)
        },
        'error-callback': (err) => {
          console.error('[turnstile] error-callback:', err)
          setError(err)
          setToken(null)
          setLoading(false)
        },
      })
      console.log('[turnstile] widget rendered successfully, id:', widgetIdRef.current)
    } catch (e) {
      console.error('[turnstile] render failed:', e)
      setError(e)
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    console.log('[turnstile] useEffect - loading script')
    let cancelled = false
    _loadScript().then(() => {
      if (cancelled) return
      console.log('[turnstile] script ready, scheduling render')
      requestAnimationFrame(render)
    })
    return () => {
      cancelled = true
      if (widgetIdRef.current != null && window.turnstile) {
        try { window.turnstile.remove(widgetIdRef.current) } catch { /* noop */ }
      }
    }
  }, [render])

  const refresh = useCallback(() => {
    console.log('[turnstile] refresh called')
    setToken(null)
    setLoading(true)
    setError(null)
    if (widgetIdRef.current != null && window.turnstile) {
      try { window.turnstile.reset(widgetIdRef.current) } catch { /* noop */ }
    }
  }, [])

  return { token, ref, refresh, enabled: !!SITE_KEY, loading, error }
}
