import { createContext, useContext, useState, useEffect, useCallback } from 'react'
import { API_BASE } from '../config'

const AuthContext = createContext(null)

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
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { checkAuth() }, [checkAuth])

  const login = async (email, password) => {
    const r = await fetch(`${API_BASE}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    })
    if (!r.ok) {
      const d = await r.json().catch(() => ({}))
      throw new Error(d.detail || 'Login failed')
    }
    const d = await r.json()
    localStorage.setItem('procta_token', d.access_token)
    if (d.refresh_token) localStorage.setItem('procta_refresh', d.refresh_token)
    setUser(d.teacher)
    return d
  }

  const logout = () => {
    localStorage.removeItem('procta_token')
    localStorage.removeItem('procta_refresh')
    setUser(null)
    window.location.href = '/login'
  }

  const authFetch = async (url, opts = {}) => {
    const token = localStorage.getItem('procta_token')
    opts.headers = { ...opts.headers, Authorization: `Bearer ${token}` }
    const r = await fetch(url, opts)
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
            opts.headers.Authorization = `Bearer ${rd.access_token}`
            return fetch(url, opts)
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
