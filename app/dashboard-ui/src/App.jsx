import { useState, useEffect } from 'react'
import { AuthProvider, useAuth } from './lib/auth'
import { API_BASE } from './config'
import OrgPanel from './panels/OrgPanel'
import BillingPanel from './panels/BillingPanel'
import SecurityPanel from './panels/SecurityPanel'
import MembersPanel from './panels/MembersPanel'
import ResultsPanel from './panels/ResultsPanel'
import AllOrgsPanel from './panels/AllOrgsPanel'
import HistoryPanel from './panels/HistoryPanel'
import OrgSettingsPanel from './panels/OrgSettingsPanel'
import AnalyticsPanel from './panels/AnalyticsPanel'
import ChatPanel from './panels/ChatPanel'
import QuestionsPanel from './panels/QuestionsPanel'
import LiveSessionsPanel from './panels/LiveSessionsPanel'
import ToolsPanel from './panels/ToolsPanel'
import ReviewPanel from './panels/ReviewPanel'

const TABS = [
  { id: 'live', label: 'Live Sessions', roles: ['admin', 'superadmin'] },
  { id: 'tools', label: 'Tools', roles: ['admin', 'superadmin'] },
  { id: 'review', label: 'Review', roles: ['admin', 'superadmin'] },
  { id: 'results', label: 'Results', roles: ['admin', 'superadmin'] },
  { id: 'history', label: 'History', roles: ['admin', 'superadmin'] },
  { id: 'analytics', label: 'Analytics', roles: ['admin', 'superadmin'] },
  { id: 'chat', label: 'Chat', roles: ['admin', 'superadmin'] },
  { id: 'questions', label: 'Questions', roles: ['admin', 'superadmin'] },
  { id: 'org', label: 'Org Overview', roles: ['admin', 'superadmin'] },
  { id: 'org-settings', label: 'Org Settings', roles: ['admin', 'superadmin'] },
  { id: 'members', label: 'Members', roles: ['admin', 'superadmin'] },
  { id: 'billing', label: 'Billing', roles: ['admin', 'superadmin'] },
  { id: 'security', label: 'Security', roles: ['admin', 'superadmin'] },
  { id: 'all-orgs', label: 'All Orgs', roles: ['admin', 'superadmin'] },
]

function LoginForm() {
  const { login } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [unverifiedEmail, setUnverifiedEmail] = useState(null)
  const [resending, setResending] = useState(false)
  const [resendMsg, setResendMsg] = useState('')

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setUnverifiedEmail(null)
    setResendMsg('')
    setLoading(true)
    try {
      await login(email, password)
    } catch (err) {
      if (err.code === 'EMAIL_UNVERIFIED') {
        setUnverifiedEmail(err.email || email)
        setError(err.message || 'Please verify your email.')
      } else {
        setError(err.message || 'Login failed')
      }
    } finally {
      setLoading(false)
    }
  }

  const resendVerification = async () => {
    setResending(true)
    setResendMsg('Sending...')
    try {
      const r = await fetch(`${API_BASE}/auth/resend-verification`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: unverifiedEmail || email }),
      })
      if (!r.ok) throw new Error('Failed')
      setResendMsg('✅ Verification email sent! Check your inbox.')
    } catch (e) {
      setResendMsg('Failed to resend. Try again later.')
    } finally {
      setResending(false)
    }
  }

  return (
    <div className="auth-card" style={{ margin: '100px auto', maxWidth: 380 }}>
      <h2>Procta Dashboard</h2>
      <p>Sign in to your account</p>
      {error && <div className="auth-err">{error}</div>}
      {unverifiedEmail && (
        <div style={{ marginBottom: 12, textAlign: 'center' }}>
          <button className="btn btn-primary btn-sm" onClick={resendVerification} disabled={resending}>
            {resending ? 'Sending...' : 'Resend verification email'}
          </button>
          {resendMsg && <div style={{ fontSize: 11, marginTop: 6, color: resendMsg.includes('✅') ? 'var(--emerald)' : 'var(--text-muted)' }}>{resendMsg}</div>}
        </div>
      )}
      <form onSubmit={handleSubmit}>
        <input
          type="email" placeholder="Email" value={email}
          onChange={(e) => setEmail(e.target.value)}
          required className="input" style={{ width: '100%', boxSizing: 'border-box', marginBottom: 12 }}
        />
        <input
          type="password" placeholder="Password" value={password}
          onChange={(e) => setPassword(e.target.value)}
          required className="input" style={{ width: '100%', boxSizing: 'border-box', marginBottom: 16 }}
        />
        <button type="submit" className="btn btn-primary" style={{ width: '100%' }} disabled={loading}>
          {loading ? 'Signing in...' : 'Sign In'}
        </button>
      </form>
    </div>
  )
}

function DashboardShell() {
  const { user, logout } = useAuth()
  const [activeTab, setActiveTab] = useState('org')
  const [currentExamId, setCurrentExamId] = useState(null)

  const PANELS = {
    live: LiveSessionsPanel,
    tools: ToolsPanel,
    review: ReviewPanel,
    results: ResultsPanel,
    history: HistoryPanel,
    analytics: AnalyticsPanel,
    chat: ChatPanel,
    questions: QuestionsPanel,
    org: OrgPanel,
    members: MembersPanel,
    billing: BillingPanel,
    security: SecurityPanel,
    'all-orgs': AllOrgsPanel,
    'org-settings': OrgSettingsPanel,
  }
  const Panel = PANELS[activeTab]

  useEffect(() => {
    // Load exam list on mount
    document.title = 'Procta Dashboard'
  }, [])

  return (
    <div className="app-shell">
      <div className="topbar">
        <div className="topbar-brand">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" style={{ color: 'var(--accent)' }}>
            <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/>
          </svg>
          <span style={{ fontWeight: 600, fontSize: 14 }}>Procta</span>
        </div>
        <div className="topbar-actions">
          <span className="topbar-teacher">{user?.full_name || user?.email}</span>
          <button className="btn btn-ghost btn-sm topbar-logout" onClick={logout}>Logout</button>
        </div>
      </div>
      <div className="tabs">
        {TABS.map(tab => (
          <button
            key={tab.id}
            className={`tab ${activeTab === tab.id ? 'active' : ''}`}
            onClick={() => setActiveTab(tab.id)}
            style={{ textTransform: 'uppercase', letterSpacing: 0.03 }}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div className="container" style={{ padding: '20px 24px' }}>
        {Panel && <Panel currentExamId={currentExamId} setCurrentExamId={setCurrentExamId} />}
      </div>
    </div>
  )
}

function AppContent() {
  const { user, loading } = useAuth()
  if (loading) return null
  if (!user) return <LoginForm />
  return <DashboardShell />
}

export default function App() {
  return (
    <AuthProvider>
      <AppContent />
    </AuthProvider>
  )
}
