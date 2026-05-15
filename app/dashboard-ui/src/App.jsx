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
import OpsPanel from './panels/OpsPanel'
import SupportConsole from './panels/SupportConsole'
import OnboardingWizard from './components/OnboardingWizard'

const TABS = [
  { id: 'live', label: 'Live Sessions', roles: ['admin', 'superadmin'] },
  { id: 'tools', label: 'Tools', roles: ['admin', 'superadmin'] },
  { id: 'support', label: 'Support', roles: ['admin', 'superadmin'] },
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
  const { user, logout, authFetch } = useAuth()
  const [activeTab, setActiveTab] = useState('org')
  const [currentExamId, setCurrentExamId] = useState(null)
  const [showOnboarding, setShowOnboarding] = useState(() => {
    // Show onboarding for new users (not yet completed or skipped)
    const done = localStorage.getItem('procta_onboarding_done')
    if (done) return false
    // If we've never checked, show a loading state while we verify
    return null // null = checking, false = don't show, true = show
  })
  const [showDemoCta, setShowDemoCta] = useState(() => !localStorage.getItem('procta_demo_cta_done'))

  const PANELS = {
    live: LiveSessionsPanel,
    ops: OpsPanel,
    support: SupportConsole,
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
    document.title = 'Procta Dashboard'
    // If user has already completed onboarding, skip the check entirely
    if (showOnboarding !== null) return
    // Check exam count — new users (0-1 exams) get the wizard
    const checkOnboarding = async () => {
      try {
        const r = await authFetch('/api/v1/admin/exams')
        if (r.ok) {
          const data = await r.json()
          const exams = Array.isArray(data) ? data : (data.exams || [])
          setShowOnboarding(Array.isArray(exams) && exams.length <= 1)
        } else {
          setShowOnboarding(false)
        }
      } catch (_) { setShowOnboarding(false) }
    }
    checkOnboarding()
  }, [authFetch, user, showOnboarding])

  if (showOnboarding === null) return <div style={{ padding: 40, color: 'var(--text-muted)' }}>Loading...</div>
  if (showOnboarding) {
    return <OnboardingWizard onComplete={(examId) => {
      if (examId) setCurrentExamId(examId)
      setActiveTab(examId ? 'questions' : 'org')
      setShowOnboarding(false)
      localStorage.setItem('procta_onboarding_done', '1')
    }} />
  }

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
        {showDemoCta && (
          <ActivationBanner
            onDismiss={() => {
              localStorage.setItem('procta_demo_cta_done', '1')
              setShowDemoCta(false)
            }}
            onQuestions={() => setActiveTab('questions')}
          />
        )}
        {Panel && <Panel currentExamId={currentExamId} setCurrentExamId={setCurrentExamId} />}
      </div>
    </div>
  )
}

function ActivationBanner({ onDismiss, onQuestions }) {
  const openPractice = () => {
    window.open('/student#practice', '_blank', 'noopener,noreferrer')
  }

  return (
    <div className="card" style={{ padding: 18, marginBottom: 18, borderColor: 'rgba(217,119,6,.45)', background: 'linear-gradient(180deg, rgba(217,119,6,.08), rgba(15,23,42,.02))' }}>
      <div style={{ display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ flex: '1 1 260px', minWidth: 0 }}>
          <div style={{ fontSize: 11, color: 'var(--amber)', textTransform: 'uppercase', fontWeight: 800, marginBottom: 4 }}>Practice Exam</div>
          <h3 style={{ margin: '0 0 4px', fontSize: 18 }}>Run a setup test before your first live exam</h3>
          <p style={{ margin: 0, color: 'var(--text-secondary)', fontSize: 13, lineHeight: 1.45 }}>
            Validate the student app, camera checks, lockdown flow, and submit path with the practice sandbox.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <a className="btn btn-secondary btn-sm" href="/download" target="_blank" rel="noreferrer" style={{ textDecoration: 'none' }}>Download App</a>
          <button className="btn btn-primary btn-sm" onClick={openPractice}>Run Practice</button>
          <button className="btn btn-secondary btn-sm" onClick={onQuestions}>Add Questions</button>
          <button className="btn btn-ghost btn-sm" onClick={onDismiss}>Dismiss</button>
        </div>
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
