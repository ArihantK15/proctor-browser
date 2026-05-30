import { useState, useEffect, useCallback } from 'react'
import { useAuth } from '../lib/auth'

// Email-OTP 2FA (replaced TOTP/Google Authenticator on 2026-05-23).
// No QR codes, no authenticator app, no backup codes — when 2FA is
// on, the login flow emails a 6-digit code every sign-in.
export default function SecurityPanel() {
  const { authFetch } = useAuth()
  const [tfaStatus, setTfaStatus] = useState(null)
  const [sessions, setSessions] = useState([])
  const [tfaMsg, setTfaMsg] = useState('')
  const [tfaMsgColor, setTfaMsgColor] = useState('var(--text-muted)')
  const [sessionsMsg, setSessionsMsg] = useState('')
  const [loadError, setLoadError] = useState('')

  const loadTfaStatus = useCallback(async () => {
    const r = await authFetch('/api/v1/auth/2fa/status')
    if (!r.ok) {
      const d = await r.json().catch(() => ({}))
      throw new Error(d.detail || `Failed to load 2FA status (${r.status})`)
    }
    setTfaStatus(await r.json())
  }, [authFetch])

  const loadSessions = useCallback(async () => {
    const r = await authFetch('/api/v1/auth/sessions')
    if (!r.ok) {
      const d = await r.json().catch(() => ({}))
      throw new Error(d.detail || `Failed to load active sessions (${r.status})`)
    }
    const d = await r.json()
    setSessions(d.sessions || [])
  }, [authFetch])

  const loadAll = useCallback(async () => {
    setLoadError('')
    try {
      await Promise.all([loadTfaStatus(), loadSessions()])
    } catch (e) {
      setLoadError(e.message || 'Failed to load security settings')
    }
  }, [loadTfaStatus, loadSessions])

  useEffect(() => { loadAll() }, [loadAll])

  // Re-auth helper — exchanges the user's password for a 5-minute
  // reauth_token. Both enable and disable need one.
  const getReauthToken = async (action) => {
    const password = window.prompt(`Enter your password to ${action} two-factor authentication`)
    if (!password) return null
    const rr = await authFetch('/api/v1/auth/reauth', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    })
    const rd = await rr.json().catch(() => ({}))
    if (!rr.ok) throw new Error(rd.detail || 'Password verification failed')
    return rd.reauth_token
  }

  const enable2FA = async () => {
    setTfaMsgColor('var(--text-muted)'); setTfaMsg('')
    try {
      const reauth_token = await getReauthToken('enable')
      if (!reauth_token) return
      setTfaMsg('Enabling...')
      const r = await authFetch('/api/v1/auth/2fa/enable', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reauth_token }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(d.detail || 'Failed to enable 2FA')
      setTfaMsgColor('var(--emerald)')
      setTfaMsg('✅ Enabled — next sign-in will require an email code.')
      await loadTfaStatus()
    } catch (e) {
      setTfaMsgColor('var(--red)')
      setTfaMsg(e.message || 'Failed to enable 2FA')
    }
  }

  const disable2FA = async () => {
    setTfaMsgColor('var(--text-muted)'); setTfaMsg('')
    try {
      const reauth_token = await getReauthToken('disable')
      if (!reauth_token) return
      setTfaMsg('Disabling...')
      const r = await authFetch('/api/v1/auth/2fa/disable', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reauth_token }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(d.detail || 'Failed to disable 2FA')
      setTfaMsgColor('var(--emerald)')
      setTfaMsg('Two-factor authentication disabled.')
      await loadTfaStatus()
    } catch (e) {
      setTfaMsgColor('var(--red)')
      setTfaMsg(e.message || 'Failed to disable 2FA')
    }
  }

  const revokeSession = async (jti) => {
    setSessionsMsg('')
    try {
      const r = await authFetch(`/api/v1/auth/sessions/${jti}/revoke`, { method: 'POST' })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || `Failed to revoke session (${r.status})`)
      }
      setSessionsMsg('Session revoked.')
      loadSessions()
    } catch (err) { setSessionsMsg(err.message || 'Failed to revoke session') }
  }

  const revokeOthers = async () => {
    setSessionsMsg('Revoking...')
    try {
      const r = await authFetch('/api/v1/auth/sessions/revoke-others', { method: 'POST' })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || `Failed to revoke sessions (${r.status})`)
      }
      setSessionsMsg('Other sessions revoked.')
      loadSessions()
    } catch (e) { setSessionsMsg(e.message || 'Failed') }
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
      {loadError && <div className="auth-err" style={{ gridColumn: '1 / -1', marginBottom: 8 }}>{loadError} <button className="btn-link" onClick={loadAll} style={{ marginLeft: 8 }}>Retry</button></div>}
      {/* 2FA card — email-OTP (no QR, no authenticator app) */}
      <div className="tool-card">
        <div className="tool-card-body">
          <h3>Two-Factor Authentication</h3>
          <p style={{ fontSize: 13, color: 'var(--text-muted)', margin: '8px 0' }}>
            When enabled, we'll email you a 6-digit code every time you sign in.
            No app required — works on any device that can read your email.
          </p>
          {tfaStatus && (
            <p style={{ fontSize: 13, color: 'var(--text)', margin: '12px 0' }}>
              {tfaStatus.enabled
                ? <>✅ Email-based 2FA is <strong style={{ color: 'var(--emerald)' }}>enabled</strong>. You'll get a code on every sign-in.</>
                : (tfaStatus.email_verified
                    ? <>ℹ️ Two-factor authentication is <strong style={{ color: 'var(--amber)' }}>not enabled</strong>.</>
                    : <>⚠️ Verify your email address first — we'll send 2FA codes there.</>)}
            </p>
          )}
          {tfaStatus && !tfaStatus.enabled && tfaStatus.email_verified && (
            <button className="btn btn-primary btn-sm" onClick={enable2FA}>Enable Two-Factor Auth</button>
          )}
          {tfaStatus?.enabled && (
            <button className="btn btn-secondary btn-sm" onClick={disable2FA} style={{ color: 'var(--red)' }}>Disable Two-Factor Auth</button>
          )}
          {tfaMsg && <p style={{ fontSize: 12, marginTop: 8, color: tfaMsgColor }}>{tfaMsg}</p>}
        </div>
      </div>

      {/* Sessions card */}
      <div className="tool-card">
        <div className="tool-card-body">
          <h3>Active Sessions</h3>
          <p style={{ fontSize: 13, color: 'var(--text-muted)', margin: '8px 0' }}>Devices logged into your account.</p>
          <div style={{ maxHeight: 300, overflowY: 'auto', marginTop: 10 }}>
            {sessions.length === 0 && <p style={{ color: 'var(--text-muted)', fontSize: 12 }}>No active sessions</p>}
            {sessions.map(s => (
              <div key={s.jti} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 0', borderBottom: '1px solid var(--border-subtle)' }}>
                <div style={{ fontSize: 12, color: 'var(--text)' }}>{s.user_agent || 'Unknown browser'}</div>
                <button className="btn btn-ghost btn-sm" onClick={() => revokeSession(s.jti)} style={{ fontSize: 10, color: 'var(--red)', padding: '2px 6px' }}>Revoke</button>
              </div>
            ))}
          </div>
        </div>
        <div className="tool-card-actions">
          <button className="btn btn-secondary btn-sm" onClick={revokeOthers}>Sign out other devices</button>
          {sessionsMsg && <span style={{ fontSize: 12, color: 'var(--text-muted)', marginLeft: 8 }}>{sessionsMsg}</span>}
        </div>
      </div>
    </div>
  )
}
