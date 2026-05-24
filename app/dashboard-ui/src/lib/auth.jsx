import { createContext, useContext, useState, useEffect, useCallback } from 'react'
import { API_BASE } from '../config'

const AuthContext = createContext(null)

export async function fetchWithTimeout(url, opts = {}, timeoutMs = 30000) {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeoutMs)
  try {
    return await fetch(url, { ...opts, signal: opts.signal || ctrl.signal })
  } finally {
    clearTimeout(timer)
  }
}

function getCsrfToken() {
  return sessionStorage.getItem('procta_csrf') || localStorage.getItem('procta_csrf') || ''
}

function clearCsrfToken() {
  sessionStorage.removeItem('procta_csrf')
  localStorage.removeItem('procta_csrf')
}

async function ensureCsrfToken(token, force = false) {
  if (!token) return ''
  const existing = getCsrfToken()
  if (existing && !force) return existing
  const r = await fetchWithTimeout(`${API_BASE}/auth/csrf`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!r.ok) return ''
  const d = await r.json()
  const csrf = d.csrf_token || ''
  if (csrf) {
    sessionStorage.setItem('procta_csrf', csrf)
    localStorage.setItem('procta_csrf', csrf)
  }
  return csrf
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [org, setOrg] = useState(null)
  const [billing, setBilling] = useState(null)
  // P2.4: per-panel error state with request_id. Previously the
  // org/billing fetches silently swallowed errors → user saw empty
  // panels with no way to tell "no data" from "API broken". Now
  // surfaces a retry-able banner in the affected panel.
  const [orgError, setOrgError] = useState(null)
  const [billingError, setBillingError] = useState(null)

  const _captureError = async (response, fallbackMsg) => {
    const requestId = response.headers.get('X-Request-ID') || null
    let detail = fallbackMsg
    try {
      const body = await response.json()
      detail = body.detail || body.message || fallbackMsg
    } catch (_) { /* keep fallbackMsg */ }
    return { message: `${detail} (${response.status})`, requestId }
  }

  const loadOrg = useCallback(async (token) => {
    setOrgError(null)
    try {
      const orgR = await fetchWithTimeout(`${API_BASE}/org`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (orgR.ok) {
        setOrg(await orgR.json())
      } else {
        setOrgError(await _captureError(orgR, 'Could not load organisation'))
      }
    } catch (e) {
      setOrgError({ message: e.message || 'Could not load organisation', requestId: null })
    }
  }, [])

  const loadBilling = useCallback(async (token) => {
    setBillingError(null)
    try {
      const billR = await fetchWithTimeout(`${API_BASE}/org/billing`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (billR.ok) {
        setBilling(await billR.json())
      } else {
        setBillingError(await _captureError(billR, 'Could not load billing'))
      }
    } catch (e) {
      setBillingError({ message: e.message || 'Could not load billing', requestId: null })
    }
  }, [])

  const checkAuth = useCallback(async () => {
    const token = localStorage.getItem('procta_token')
    if (!token) {
      setLoading(false)
      return
    }
    try {
      const r = await fetchWithTimeout(`${API_BASE}/auth/me`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!r.ok) throw new Error('Auth failed')
      const data = await r.json()
      await ensureCsrfToken(token, true)
      setUser(data)
      // Org and billing are non-fatal — track their errors separately
      // so the dashboard still mounts (auth succeeded). loadOrg/Billing
      // are exposed in context so individual panels can call them as
      // retry handlers from a banner click.
      await Promise.all([loadOrg(token), loadBilling(token)])
    } catch (e) {
      localStorage.removeItem('procta_token')
      localStorage.removeItem('procta_refresh')
      clearCsrfToken()
      sessionStorage.removeItem('procta_current_exam_id')
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [loadOrg, loadBilling])

  const retryOrg = useCallback(() => {
    const token = localStorage.getItem('procta_token')
    if (token) loadOrg(token)
  }, [loadOrg])
  const retryBilling = useCallback(() => {
    const token = localStorage.getItem('procta_token')
    if (token) loadBilling(token)
  }, [loadBilling])

  useEffect(() => { checkAuth() }, [checkAuth])

  const login = async (email, password, emailOtpCode = null, captchaToken = null) => {
    // P1.1: backend's /auth/login calls verify_or_403 (auth.py:699)
    // when TURNSTILE_SECRET_KEY is set in production. Without
    // captcha_token here, login 403s on the KVM but worked in dev
    // (where the var was unset). LoginForm passes the token from
    // useTurnstile() — if the hook didn't load a site key (dev/
    // sandbox), captchaToken is null and the backend's matching
    // sandbox path lets it through.
    const body = { email, password }
    if (emailOtpCode) body.email_otp_code = emailOtpCode
    if (captchaToken) body.captcha_token = captchaToken
    const r = await fetchWithTimeout(`${API_BASE}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!r.ok) {
      const d = await r.json().catch(() => ({}))
      // Handle EMAIL_UNVERIFIED specifically
      if (d.error === 'EMAIL_UNVERIFIED') {
        throw { code: 'EMAIL_UNVERIFIED', message: d.message || 'Please verify your email.', email }
      }
      // Email-OTP 2FA — server has emailed a 6-digit code and is asking
      // the caller to retry with `emailOtpCode`. The caller (LoginPage)
      // catches this code and surfaces a code-input UI.
      if (d.error === 'EMAIL_2FA_REQUIRED') {
        throw { code: 'EMAIL_2FA_REQUIRED', message: d.message || 'We sent a 6-digit code to your email.', email }
      }
      throw new Error(d.detail || d.message || 'Login failed')
    }
    const d = await r.json()
    localStorage.setItem('procta_token', d.access_token)
    if (d.refresh_token) localStorage.setItem('procta_refresh', d.refresh_token)
    await ensureCsrfToken(d.access_token, true)
    setUser(d.teacher)
    return d
  }

  const logout = async () => {
    try {
      const token = localStorage.getItem('procta_token')
      const csrf = getCsrfToken()
      const headers = { Authorization: `Bearer ${token}` }
      if (csrf) headers['X-CSRF-Token'] = csrf
      await fetchWithTimeout(`${API_BASE}/auth/logout`, { method: 'POST', headers })
    } catch (_) {}
    localStorage.removeItem('procta_token')
    localStorage.removeItem('procta_refresh')
    clearCsrfToken()
    // Clear session-scoped state so a new login doesn't inherit previous user's context (L-3)
    sessionStorage.removeItem('procta_current_exam_id')
    setUser(null)
    window.location.href = '/dashboard'
  }

  const authFetch = async (url, opts = {}) => {
    const token = localStorage.getItem('procta_token')
    const method = (opts.method || 'GET').toUpperCase()
    const headers = { ...opts.headers, Authorization: `Bearer ${token}` }
    const needsCsrf = !['GET', 'HEAD', 'OPTIONS'].includes(method)
    const csrf = needsCsrf ? await ensureCsrfToken(token) : ''
    if (csrf && needsCsrf) {
      headers['X-CSRF-Token'] = csrf
    }
    const requestOpts = { ...opts, method, headers }
    const r = await fetchWithTimeout(url, requestOpts)
    if (r.status === 401) {
      const refresh = localStorage.getItem('procta_refresh')
      if (refresh) {
        try {
          const rr = await fetchWithTimeout(`${API_BASE}/auth/refresh`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ refresh_token: refresh }),
          })
          if (rr.ok) {
            const rd = await rr.json()
            localStorage.setItem('procta_token', rd.access_token)
            const retryHeaders = { ...headers, Authorization: `Bearer ${rd.access_token}` }
            const retryCsrf = needsCsrf ? await ensureCsrfToken(rd.access_token, true) : ''
            if (retryCsrf && needsCsrf) {
              retryHeaders['X-CSRF-Token'] = retryCsrf
            }
            return fetchWithTimeout(url, { ...requestOpts, headers: retryHeaders })
          }
        } catch (_) {}
      }
      logout()
    }
    return r
  }

  return (
    <AuthContext.Provider value={{ user, loading, error, org, billing, orgError, billingError, retryOrg, retryBilling, login, logout, authFetch, checkAuth }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
