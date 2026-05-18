import { useState, useEffect } from 'react'
import { useAuth } from '../lib/auth'

export default function SecurityPanel() {
  const { authFetch } = useAuth()
  const [tfaStatus, setTfaStatus] = useState(null)
  const [sessions, setSessions] = useState([])
  const [enrolling, setEnrolling] = useState(false)
  const [enrollData, setEnrollData] = useState(null)
  const [tfaCode, setTfaCode] = useState('')
  const [tfaMsg, setTfaMsg] = useState('')
  const [sessionsMsg, setSessionsMsg] = useState('')


  const [loadError, setLoadError] = useState('')

  const loadTfaStatus = async () => {
    const r = await authFetch('/api/v1/auth/2fa/status')
    if (!r.ok) {
      const d = await r.json().catch(() => ({}))
      throw new Error(d.detail || `Failed to load 2FA status (${r.status})`)
    }
    setTfaStatus(await r.json())
  }

  const loadSessions = async () => {
    const r = await authFetch('/api/v1/auth/sessions')
    if (!r.ok) {
      const d = await r.json().catch(() => ({}))
      throw new Error(d.detail || `Failed to load active sessions (${r.status})`)
    }
    const d = await r.json()
    setSessions(d.sessions || [])
  }

  const loadAll = async () => {
    setLoadError('')
    try {
      await Promise.all([loadTfaStatus(), loadSessions()])
    } catch (e) {
      setLoadError(e.message || 'Failed to load security settings')
    }
  }

  useEffect(() => { loadAll() }, [])

  const enable2FA = async () => {
    setEnrolling(true)
    setTfaMsg('Generating...')
    try {
      const r = await authFetch('/api/v1/auth/2fa/enroll', { method: 'POST' })
      if (!r.ok) throw new Error('Failed')
      setEnrollData(await r.json())
      setTfaMsg('')
    } catch (e) { setTfaMsg(e.message) }
  }

  const confirm2FA = async () => {
    if (!tfaCode || tfaCode.length !== 6) { setTfaMsg('Enter a 6-digit code'); return }
    setTfaMsg('Verifying...')
    try {
      const r = await authFetch('/api/v1/auth/2fa/confirm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: tfaCode }),
      })
      if (!r.ok) { const d = await r.json(); throw new Error(d.detail || 'Invalid') }
      setTfaMsg('✅ Two-factor authentication enabled!')
      await loadTfaStatus()
    } catch (e) { setTfaMsg(e.message) }
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
      {/* 2FA card */}
      <div className="tool-card">
        <div className="tool-card-body">
          <h3>Two-Factor Authentication</h3>
          <p style={{ fontSize: 13, color: 'var(--text-muted)', margin: '8px 0' }}>
            {tfaStatus?.enabled
              ? '✅ Two-factor authentication is enabled.'
              : '❌ Two-factor authentication is not enabled.'}
          </p>
          {!enrolling && !tfaStatus?.enabled && (
            <button className="btn btn-primary btn-sm" onClick={enable2FA}>Enable Two-Factor Auth</button>
          )}
          {tfaMsg && <p style={{ fontSize: 12, marginTop: 8, color: 'var(--text-muted)' }}>{tfaMsg}</p>}
          {enrollData && (
            <div style={{ marginTop: 12 }}>
              <p style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 8 }}>
                Scan this QR code with your authenticator app, or enter the key manually:
              </p>
              <div style={{ background: '#fff', borderRadius: 8, display: 'inline-block', padding: 8, marginBottom: 8 }}>
                <img src={`https://api.qrserver.com/v1/create-qr-code/?size=160x160&data=${encodeURIComponent(enrollData.otpauth_url)}`} alt="QR" width="160" height="160" />
              </div>
              <p style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'monospace', wordBreak: 'break-all' }}>
                Manual key: {enrollData.secret}
              </p>
              <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                <input
                  type="text" maxLength={6} placeholder="000000"
                  style={{ width: 120, padding: '8px 10px', borderRadius: 6, border: '1px solid var(--border)',
                    background: 'var(--surface-1)', color: 'var(--text)', fontSize: 16, letterSpacing: 4, textAlign: 'center', fontFamily: 'monospace' }}
                  value={tfaCode} onChange={(e) => setTfaCode(e.target.value)}
                />
                <button className="btn btn-primary btn-sm" onClick={confirm2FA}>Confirm & Enable</button>
              </div>
            </div>
          )}
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
