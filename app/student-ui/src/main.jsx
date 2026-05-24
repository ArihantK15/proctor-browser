import { useState, useEffect } from 'react'
import ReactDOM from 'react-dom/client'

const API_BASE = '/api/v1'

function fetchWithTimeout(url, opts = {}, timeoutMs = 30000) {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeoutMs)
  return fetch(url, { ...opts, signal: opts.signal || ctrl.signal }).finally(() => clearTimeout(timer))
}

function useAuth() {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)
  const token = localStorage.getItem('procta_student_token') || ''

  useEffect(() => {
    if (!token) { setLoading(false); return }
    fetchWithTimeout(`${API_BASE}/student/auth/me`, { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setUser(d) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [token])

  const login = async (email, password) => {
    const r = await fetchWithTimeout(`${API_BASE}/student/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    })
    if (!r.ok) throw new Error((await r.json()).detail || 'Login failed')
    const d = await r.json()
    localStorage.setItem('procta_student_token', d.access_token)
    setUser(d.student || d.user)
    return d
  }

  const logout = async () => {
    try {
      const currentToken = localStorage.getItem('procta_student_token')
      if (currentToken) {
        await fetchWithTimeout(`${API_BASE}/student/auth/logout`, {
          method: 'POST',
          headers: { Authorization: `Bearer ${currentToken}` },
        })
      }
    } catch (_) {}
    localStorage.removeItem('procta_student_token')
    setUser(null)
  }

  return { user, loading, login, logout, token }
}

function LoginForm({ onLogin }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError(''); setBusy(true)
    try {
      await onLogin(email, password)
    } catch (e) { setError(e.message) }
    finally { setBusy(false) }
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
          <button className="btn btn-primary" type="submit" disabled={busy} style={{ width: '100%', marginTop: 12 }}>{busy ? 'Logging in...' : 'Log in'}</button>
        </form>
        <p className="sub" style={{ marginTop: 16 }}>Don't have an account? <a href="/register">Register here</a></p>
      </div>
    </div>
  )
}

function StudentDashboard({ token, onLogout }) {
  const [exams, setExams] = useState([])
  const [loading, setLoading] = useState(true)

  const authFetch = (url, opts = {}) => fetchWithTimeout(url, { ...opts, headers: { ...opts.headers, Authorization: `Bearer ${token}` } })

  useEffect(() => {
    authFetch('/api/student/exams')
      .then(r => r.ok ? r.json() : [])
      .then(d => setExams(d.exams || d.active || []))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [token])

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
        {loading ? <div style={{ padding: 40, textAlign: 'center', color: 'var(--muted)' }}>Loading...</div> : exams.length === 0 ? (
          <div className="card" style={{ padding: 40, textAlign: 'center' }}>
            <p style={{ color: 'var(--muted)' }}>No upcoming exams. Your teacher will invite you when an exam is scheduled.</p>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {exams.map(exam => (
              <div key={exam.id} className="card" style={{ padding: 20 }}>
                <h3 style={{ marginBottom: 4 }}>{exam.title || 'Exam'}</h3>
                <p style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 12 }}>{exam.teacher_name || ''}</p>
                <div style={{ display: 'flex', gap: 16, fontSize: 12, color: 'var(--muted)', marginBottom: 16 }}>
                  <span>Starts: {exam.starts_at ? new Date(exam.starts_at).toLocaleString() : '—'}</span>
                  <span>Duration: {exam.duration || 60} min</span>
                </div>
                <button className="btn btn-primary btn-sm" onClick={() => window.open('/student', '_blank')}>Start Exam</button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function App() {
  const { user, loading, login, logout } = useAuth()
  if (loading) return null
  if (!user) return <LoginForm onLogin={login} />
  return <StudentDashboard token={localStorage.getItem('procta_student_token')} onLogout={logout} />
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />)
