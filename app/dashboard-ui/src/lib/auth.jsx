import { createContext, useContext, useState, useEffect, useCallback } from 'react'
import { API_BASE } from '../config'

const AuthContext = createContext(null)
let csrfMemory = ''

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
  return csrfMemory
}

function clearCsrfToken() {
  csrfMemory = ''
}

async function ensureCsrfToken(force = false) {
  const existing = getCsrfToken()
  if (existing && !force) return existing
  const r = await fetchWithTimeout(`${API_BASE}/auth/csrf`, {
    credentials: 'include',
  })
  if (!r.ok) return ''
  const d = await r.json()
  const csrf = d.csrf_token || ''
  if (csrf) csrfMemory = csrf
  return csrf
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [org, setOrg] = useState(null)
  const [billing, setBilling] = useState(null)
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

  const loadOrg = useCallback(async () => {
    setOrgError(null)
    try {
      const orgR = await fetchWithTimeout(`${API_BASE}/org`, {
        credentials: 'include',
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

  const loadBilling = useCallback(async () => {
    setBillingError(null)
    try {
      const billR = await fetchWithTimeout(`${API_BASE}/org/billing`, {
        credentials: 'include',
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
    try {
      const r = await fetchWithTimeout(`${API_BASE}/auth/me`, {
        credentials: 'include',
      })
      if (!r.ok) throw new Error('Auth failed')
      const data = await r.json()
      await ensureCsrfToken(true)
      setUser(data)
      await Promise.all([loadOrg(), loadBilling()])
    } catch (e) {
      clearCsrfToken()
      sessionStorage.removeItem('procta_current_exam_id')
      setUser(null)
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [loadOrg, loadBilling])

  const retryOrg = useCallback(() => { loadOrg() }, [loadOrg])
  const retryBilling = useCallback(() => { loadBilling() }, [loadBilling])

  useEffect(() => { checkAuth() }, [checkAuth])

  const login = async (email, password, emailOtpCode = null, captchaToken = null) => {
    const body = { email, password }
    if (emailOtpCode) body.email_otp_code = emailOtpCode
    if (captchaToken) body.captcha_token = captchaToken
    const r = await fetchWithTimeout(`${API_BASE}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify(body),
    })
    if (!r.ok) {
      const d = await r.json().catch(() => ({}))
      if (d.error === 'EMAIL_UNVERIFIED') {
        throw { code: 'EMAIL_UNVERIFIED', message: d.message || 'Please verify your email.', email }
      }
      if (d.error === 'EMAIL_2FA_REQUIRED') {
        throw { code: 'EMAIL_2FA_REQUIRED', message: d.message || 'We sent a 6-digit code to your email.', email }
      }
      throw new Error(d.detail || d.message || 'Login failed')
    }
    const d = await r.json()
    await ensureCsrfToken(true)
    setUser(d.teacher)
    await Promise.all([loadOrg(), loadBilling()])
    return d
  }

  const logout = async () => {
    try {
      const csrf = getCsrfToken() || await ensureCsrfToken()
      const headers = {}
      if (csrf) headers['X-CSRF-Token'] = csrf
      await fetchWithTimeout(`${API_BASE}/auth/logout`, {
        method: 'POST',
        headers,
        credentials: 'include',
      })
    } catch (_) {}
    clearCsrfToken()
    sessionStorage.removeItem('procta_current_exam_id')
    setUser(null)
    window.location.href = '/dashboard'
  }

  const authFetch = async (url, opts = {}) => {
    const method = (opts.method || 'GET').toUpperCase()
    const headers = { ...opts.headers }
    const needsCsrf = !['GET', 'HEAD', 'OPTIONS'].includes(method)
    const csrf = needsCsrf ? await ensureCsrfToken() : ''
    if (csrf && needsCsrf) headers['X-CSRF-Token'] = csrf

    const requestOpts = { ...opts, method, headers, credentials: 'include' }
    const r = await fetchWithTimeout(url, requestOpts)
    if (r.status === 401) {
      try {
        const rr = await fetchWithTimeout(`${API_BASE}/auth/refresh`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({}),
        })
        if (rr.ok) {
          if (needsCsrf) {
            const retryCsrf = await ensureCsrfToken(true)
            if (retryCsrf) headers['X-CSRF-Token'] = retryCsrf
          }
          return fetchWithTimeout(url, { ...requestOpts, headers })
        }
      } catch (_) {}
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
