import { createContext, useContext, useState, useEffect, useCallback } from 'react'
import { API_BASE } from '../config'

const AuthContext = createContext(null)

function getCsrfToken(token) {
  try {
    const [, payload] = String(token || '').split('.')
    if (!payload) return ''
    const normalized = payload.replace(/-/g, '+').replace(/_/g, '/')
    const data = JSON.parse(atob(normalized))
    return data.csrf || data.jti || ''
  } catch (_) {
    return ''
  }
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [org, setOrg] = useState(null)
  const [billing, setBilling] = useState(null)

  const checkAuth = useCallback(async () => {
    const token = localStorage.getItem('procta_token')
    if (!token) {
      setLoading(false)
      return
    }
    try {
      const r = await fetch(`${API_BASE}/auth/me`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!r.ok) throw new Error('Auth failed')
      const data = await r.json()
      setUser(data)
      // Fetch org and billing info
      try {
        const orgR = await fetch(`${API_BASE}/org`, {
          headers: { Authorization: `Bearer ${token}` },
        })
        if (orgR.ok) setOrg(await orgR.json())
      } catch (_) {}
      try {
        const billR = await fetch(`${API_BASE}/org/billing`, {
          headers: { Authorization: `Bearer ${token}` },
        })
        if (billR.ok) setBilling(await billR.json())
      } catch (_) {}
    } catch (e) {
      localStorage.removeItem('procta_token')
      localStorage.removeItem('procta_refresh')
      sessionStorage.removeItem('procta_current_exam_id')
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { checkAuth() }, [checkAuth])

  const login = async (email, password, emailOtpCode = null) => {
    const body = { email, password }
    if (emailOtpCode) body.email_otp_code = emailOtpCode
    const r = await fetch(`${API_BASE}/auth/login`, {
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
    setUser(d.teacher)
    return d
  }

  const logout = async () => {
    try {
      const token = localStorage.getItem('procta_token')
      const csrf = getCsrfToken(token)
      const headers = { Authorization: `Bearer ${token}` }
      if (csrf) headers['X-CSRF-Token'] = csrf
      await fetch(`${API_BASE}/auth/logout`, { method: 'POST', headers })
    } catch (_) {}
    localStorage.removeItem('procta_token')
    localStorage.removeItem('procta_refresh')
    // Clear session-scoped state so a new login doesn't inherit previous user's context (L-3)
    sessionStorage.removeItem('procta_current_exam_id')
    setUser(null)
    window.location.href = '/dashboard'
  }

  const authFetch = async (url, opts = {}) => {
    const token = localStorage.getItem('procta_token')
    const method = (opts.method || 'GET').toUpperCase()
    const csrf = getCsrfToken(token)
    const headers = { ...opts.headers, Authorization: `Bearer ${token}` }
    if (csrf && !['GET', 'HEAD', 'OPTIONS'].includes(method)) {
      headers['X-CSRF-Token'] = csrf
    }
    const requestOpts = { ...opts, method, headers }
    const r = await fetch(url, requestOpts)
    if (r.status === 401) {
      const refresh = localStorage.getItem('procta_refresh')
      if (refresh) {
        try {
          const rr = await fetch(`${API_BASE}/auth/refresh`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ refresh_token: refresh }),
          })
          if (rr.ok) {
            const rd = await rr.json()
            localStorage.setItem('procta_token', rd.access_token)
            const retryHeaders = { ...headers, Authorization: `Bearer ${rd.access_token}` }
            const retryCsrf = getCsrfToken(rd.access_token)
            if (retryCsrf && !['GET', 'HEAD', 'OPTIONS'].includes(method)) {
              retryHeaders['X-CSRF-Token'] = retryCsrf
            }
            return fetch(url, { ...requestOpts, headers: retryHeaders })
          }
        } catch (_) {}
      }
      logout()
    }
    return r
  }

  return (
    <AuthContext.Provider value={{ user, loading, error, org, billing, login, logout, authFetch, checkAuth }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
