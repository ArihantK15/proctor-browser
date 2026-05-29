import React, { useState, useEffect } from 'react'
import ReactDOM from 'react-dom/client'
import useTurnstile from './hooks/useTurnstile'

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

  const loadExams = () => {
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
  }

  useEffect(() => { loadExams() }, [])

  return (
    <div className="app-shell">
      <div className="topbar">
        <div className="topbar-brand">
          <span style={{ fontWeight: 600, fontSize: 14 }}>Procta</span>
        </div>
        <div className="topbar-actions">
          <button className="btn btn-ghost btn-sm" onClick={onLogout}>Logout</button>
        </div>
      </div>
      <div className="container" style={{ padding: '20px 24px' }}>
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
