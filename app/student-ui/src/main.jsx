import React, { useState, useEffect, useCallback } from 'react'
import ReactDOM from 'react-dom/client'
import * as Sentry from '@sentry/react'
import useTurnstile from './hooks/useTurnstile'

// Sentry — gated on VITE_SENTRY_DSN set at build time. No-op without it.
// This UI is what students see during exams, so error visibility here is
// where most user-facing bugs surface. Replays are off because the UI
// surfaces answers + camera state.
const SENTRY_DSN = import.meta.env.VITE_SENTRY_DSN
if (SENTRY_DSN) {
  Sentry.init({
    dsn: SENTRY_DSN,
    environment: import.meta.env.VITE_SENTRY_ENVIRONMENT || 'production',
    release: import.meta.env.VITE_RELEASE || undefined,
    tracesSampleRate: 0.1,
    replaysSessionSampleRate: 0.0,
    replaysOnErrorSampleRate: 0.0,
  })
}

const API_BASE = '/api/v1'

function fetchWithTimeout(url, opts = {}, timeoutMs = 30000) {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeoutMs)
  return fetch(url, { ...opts, signal: opts.signal || ctrl.signal }).finally(() => clearTimeout(timer))
}

function useAuth() {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetchWithTimeout(`${API_BASE}/student/auth/me`, { credentials: 'include' })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setUser(d) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const login = async (email, password, captchaToken = null) => {
    const body = { email, password }
    if (captchaToken) body.captcha_token = captchaToken
    const r = await fetchWithTimeout(`${API_BASE}/student/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify(body),
    })
    if (!r.ok) throw new Error((await r.json()).detail || 'Login failed')
    const d = await r.json()
    setUser(d.account || d.student || d.user)
    return d
  }

  const logout = async () => {
    try {
      await fetchWithTimeout(`${API_BASE}/student/auth/logout`, {
        method: 'POST',
        credentials: 'include',
      })
    } catch (_) {}
    setUser(null)
  }

  return { user, loading, login, logout }
}

function LoginForm({ onLogin }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const turnstile = useTurnstile()

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      await onLogin(email, password, turnstile.token)
    } catch (e) {
      turnstile.refresh()
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <h1>Procta</h1>
        <p className="sub">Student exam dashboard</p>
        <form onSubmit={handleSubmit}>
          <div className="fg"><label>Email</label><input type="email" value={email} onChange={e => setEmail(e.target.value)} required autoFocus /></div>
          <div className="fg"><label>Password</label><input type="password" value={password} onChange={e => setPassword(e.target.value)} required /></div>
          {error && <div className="err">{error}</div>}
          <div ref={turnstile.ref} style={{ marginTop: 8 }} />
          <button className="btn btn-primary" type="submit" disabled={busy} style={{ width: '100%', marginTop: 12 }}>{busy ? 'Logging in...' : 'Log in'}</button>
        </form>
        <p className="sub" style={{ marginTop: 16 }}>Don't have an account? <a href="/register">Register here</a></p>
      </div>
    </div>
  )
}

let _studentRefreshPromise = null

function StudentDashboard({ onLogout }) {
  const [exams, setExams] = useState([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(null)
  // 'exams' | 'privacy' — toggle between the default exam list and the
  // DPDP/GDPR data-subject-rights view. Students reach the privacy
  // view via the topbar link.
  const [view, setView] = useState('exams')

  const authFetch = async (url, opts = {}) => {
    const r = await fetchWithTimeout(url, { ...opts, credentials: 'include' })
    if (r.status === 401) {
      try {
        if (!_studentRefreshPromise) {
          _studentRefreshPromise = fetchWithTimeout(`${API_BASE}/student/auth/refresh`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({}),
          }).finally(() => { _studentRefreshPromise = null })
        }
        const rr = await _studentRefreshPromise
        if (rr.ok) return fetchWithTimeout(url, { ...opts, credentials: 'include' })
      } catch (_) {}
      onLogout()
      return r
    }
    return r
  }

  const loadExams = useCallback(() => {
    setLoading(true)
    setLoadError(null)
    authFetch('/api/student/exams')
      .then(async (r) => {
        if (!r.ok) {
          let detail = `Could not load your exams (${r.status})`
          try {
            const b = await r.json()
            detail = b.detail || b.message || detail
          } catch (_) {}
          const requestId = r.headers.get('X-Request-ID') || null
          throw Object.assign(new Error(detail), { requestId })
        }
        return r.json()
      })
      .then(d => setExams(d.exams || d.active || []))
      .catch(err => setLoadError({ message: err.message, requestId: err.requestId }))
      .finally(() => setLoading(false))
    // authFetch is a fresh closure each render but we don't want
    // re-running this loader to refetch on every parent re-render —
    // the mount-only behaviour is intentional. eslint-disable to
    // pin that intent rather than carry authFetch in the deps.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => { loadExams() }, [loadExams])

  return (
    <div className="app-shell">
      <div className="topbar">
        <div className="topbar-brand">
          <span style={{ fontWeight: 600, fontSize: 14 }}>Procta</span>
        </div>
        <div className="topbar-actions" style={{ display: 'flex', gap: 8 }}>
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => setView(view === 'privacy' ? 'exams' : 'privacy')}
          >
            {view === 'privacy' ? 'My Exams' : 'Privacy'}
          </button>
          <button className="btn btn-ghost btn-sm" onClick={onLogout}>Logout</button>
        </div>
      </div>
      <div className="container" style={{ padding: '20px 24px' }}>
        {view === 'privacy' ? (
          <StudentPrivacyView authFetch={authFetch} onLoggedOut={onLogout} />
        ) : (
          <>
        <h2 style={{ marginBottom: 16 }}>My Exams</h2>
        {loading ? (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--muted)' }}>Loading...</div>
        ) : loadError ? (
          <div className="card" style={{ padding: 24, textAlign: 'center', border: '1px solid rgba(239,68,68,0.30)', background: 'rgba(239,68,68,0.05)' }}>
            <p style={{ color: 'var(--red, #ef4444)', fontWeight: 600, marginBottom: 6 }}>Could not load your exams</p>
            <p style={{ color: 'var(--muted)', fontSize: 13, marginBottom: 12 }}>{loadError.message}</p>
            {loadError.requestId && (
              <p style={{ color: 'var(--muted)', fontSize: 11, fontFamily: 'monospace', marginBottom: 12 }}>request_id: {loadError.requestId}</p>
            )}
            <button className="btn btn-secondary btn-sm" onClick={loadExams}>Retry</button>
          </div>
        ) : exams.length === 0 ? (
          <div className="card" style={{ padding: 40, textAlign: 'center' }}>
            <p style={{ color: 'var(--muted)' }}>No upcoming exams. Your teacher will invite you when an exam is scheduled.</p>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {exams.map(exam => {
              const examId = exam.exam_id || exam.id;
              const title = exam.exam_title || exam.title || 'Exam';
              const duration = exam.duration_minutes || exam.duration || 60;
              const handleStart = () => {
                if (window.procta_native && window.procta_native.launchExam) {
                  window.procta_native.launchExam({
                    rollNumber: '',
                    accessCode: '',
                    examTitle: title,
                    teacherId: exam.teacher_id || '',
                    examId,
                  });
                } else {
                  window.open(`/exam?exam_id=${encodeURIComponent(examId)}`, '_blank');
                }
              };
              return (
              <div key={examId} className="card" style={{ padding: 20 }}>
                <h3 style={{ marginBottom: 4 }}>{title}</h3>
                <p style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 12 }}>{exam.teacher_name || ''}</p>
                <div style={{ display: 'flex', gap: 16, fontSize: 12, color: 'var(--muted)', marginBottom: 16 }}>
                  <span>Starts: {exam.starts_at ? new Date(exam.starts_at).toLocaleString() : '—'}</span>
                  <span>Duration: {duration} min</span>
                </div>
                <button className="btn btn-primary btn-sm" onClick={handleStart}>Start Exam</button>
              </div>
              );
            })}
          </div>
        )}
          </>
        )}
      </div>
    </div>
  )
}

// DPDP §11–13 / GDPR Art 15–17 data-subject view. Two actions:
//   - Export: GET /api/v1/privacy/export → JSON download.
//   - Delete: POST /api/v1/privacy/delete with reauth_token from
//             /api/v1/student/auth/reauth.
function StudentPrivacyView({ authFetch, onLoggedOut }) {
  const [exportMsg, setExportMsg] = useState('')
  const [exportColor, setExportColor] = useState('var(--muted)')
  const [deleteMsg, setDeleteMsg] = useState('')
  const [deleteColor, setDeleteColor] = useState('var(--muted)')
  const [exporting, setExporting] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const exportData = async () => {
    setExportColor('var(--muted)')
    setExportMsg('Generating export — this may take a few seconds…')
    setExporting(true)
    try {
      const r = await authFetch(`${API_BASE}/privacy/export`)
      const d = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(d.detail || `Export failed (${r.status})`)
      const blob = new Blob([JSON.stringify(d, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      const ts = new Date().toISOString().replace(/[:.]/g, '-')
      a.href = url
      a.download = `procta-data-export-${ts}.json`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
      const tables = Object.keys(d).filter(k => Array.isArray(d[k])).length
      setExportColor('var(--emerald, #10b981)')
      setExportMsg(`Export ready — ${tables} categories downloaded.`)
    } catch (e) {
      setExportColor('var(--red, #ef4444)')
      setExportMsg(e.message || 'Export failed')
    } finally {
      setExporting(false)
    }
  }

  const deleteAccount = async () => {
    const typed = window.prompt(
      'Type DELETE (in capitals) to confirm you want to erase your account.\n\n' +
      'This is permanent. If you want a copy of your data, export it first.'
    )
    if (typed !== 'DELETE') {
      setDeleteColor('var(--muted)')
      setDeleteMsg(typed === null ? '' : 'Cancelled — text didn\'t match.')
      return
    }
    const password = window.prompt('Enter your password to confirm.')
    if (!password) { setDeleteMsg('Cancelled.'); return }
    setDeleting(true)
    setDeleteColor('var(--muted)')
    setDeleteMsg('Verifying password…')
    try {
      const rr = await authFetch(`${API_BASE}/student/auth/reauth`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      })
      const rd = await rr.json().catch(() => ({}))
      if (!rr.ok) throw new Error(rd.detail || 'Password verification failed')
      const reauth_token = rd.reauth_token
      setDeleteMsg('Erasing your account…')
      const r = await authFetch(`${API_BASE}/privacy/delete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reauth_token }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(d.detail || `Delete failed (${r.status})`)
      setDeleteColor(d.status === 'partial' ? 'var(--amber, #f59e0b)' : 'var(--emerald, #10b981)')
      setDeleteMsg(d.status === 'partial'
        ? `Erased with some non-fatal issues (${(d.errors || []).join(', ')}). Logging you out…`
        : 'Account erased. Logging you out…')
      setTimeout(() => { onLoggedOut() }, 3500)
    } catch (e) {
      setDeleteColor('var(--red, #ef4444)')
      setDeleteMsg(e.message || 'Delete failed')
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div style={{ maxWidth: 640 }}>
      <h2 style={{ marginBottom: 6 }}>Privacy & Your Data</h2>
      <p style={{ color: 'var(--muted)', fontSize: 13, marginTop: 0 }}>
        Manage your data under India's DPDP Act and the EU GDPR.
      </p>

      <div className="card" style={{ padding: 20, marginTop: 24 }}>
        <h3 style={{ marginTop: 0 }}>Download your data</h3>
        <p style={{ color: 'var(--muted)', fontSize: 13 }}>
          Generate a JSON file containing everything we have linked to your
          account — profile, exam sessions, violations, answers, consent
          records, login history.
        </p>
        <button className="btn btn-secondary btn-sm" onClick={exportData} disabled={exporting}>
          {exporting ? 'Exporting…' : 'Export my data'}
        </button>
        {exportMsg && <div style={{ marginTop: 12, color: exportColor, fontSize: 13 }}>{exportMsg}</div>}
      </div>

      <div className="card" style={{ padding: 20, marginTop: 16, border: '1px solid rgba(239,68,68,0.30)', background: 'rgba(239,68,68,0.05)' }}>
        <h3 style={{ marginTop: 0, color: 'var(--red, #ef4444)' }}>Delete my account</h3>
        <p style={{ color: 'var(--muted)', fontSize: 13 }}>
          Permanently erases your account. Sessions are revoked immediately.
          Some records (consent proofs, anonymised exam history needed by
          your teacher) are retained per the privacy policy.
        </p>
        <p style={{ color: 'var(--muted)', fontSize: 13, marginBottom: 16 }}>
          <strong>This cannot be undone.</strong>
        </p>
        <button
          className="btn btn-sm"
          onClick={deleteAccount}
          disabled={deleting}
          style={{ background: 'var(--red, #ef4444)', color: 'white', border: '1px solid var(--red, #ef4444)' }}
        >
          {deleting ? 'Deleting…' : 'Delete my account'}
        </button>
        {deleteMsg && <div style={{ marginTop: 12, color: deleteColor, fontSize: 13 }}>{deleteMsg}</div>}
      </div>
    </div>
  )
}

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }
  static getDerivedStateFromError(error) { return { error } }
  componentDidCatch(error, info) {
    // Forward to Sentry when initialized — keeps the existing reload
    // fallback UI but ensures the error reaches the central tracker.
    try { Sentry.captureException(error, { extra: { componentStack: info?.componentStack } }) } catch { /* no-op */ }
  }
  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 40, textAlign: 'center', maxWidth: 480, margin: '80px auto' }}>
          <h2>Something went wrong</h2>
          <p style={{ color: 'var(--muted)', marginTop: 12, fontSize: 13 }}>{this.state.error.message}</p>
          <button className="btn btn-secondary btn-sm" style={{ marginTop: 16 }} onClick={() => { this.setState({ error: null }); window.location.reload() }}>
            Reload
          </button>
        </div>
      )
    }
    return this.props.children
  }
}

function App() {
  const { user, loading, login, logout } = useAuth()
  if (loading) return null
  if (!user) return <LoginForm onLogin={login} />
  return <StudentDashboard onLogout={logout} />
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <ErrorBoundary>
    <App />
  </ErrorBoundary>
)
