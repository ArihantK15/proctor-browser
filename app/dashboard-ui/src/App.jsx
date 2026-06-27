import { lazy, Suspense, useState, useEffect, useMemo } from 'react'
import { AuthProvider, fetchWithTimeout, useAuth } from './lib/auth'
import { API_BASE } from './config'
import useTurnstile from './hooks/useTurnstile'

const OrgPanel = lazy(() => import('./panels/OrgPanel'))
const BillingPanel = lazy(() => import('./panels/BillingPanel'))
const SecurityPanel = lazy(() => import('./panels/SecurityPanel'))
const MembersPanel = lazy(() => import('./panels/MembersPanel'))
const BulkImportPanel = lazy(() => import('./panels/BulkImportPanel'))
const ResultsPanel = lazy(() => import('./panels/ResultsPanel'))
const AllOrgsPanel = lazy(() => import('./panels/AllOrgsPanel'))
const HistoryPanel = lazy(() => import('./panels/HistoryPanel'))
const OrgSettingsPanel = lazy(() => import('./panels/OrgSettingsPanel'))
const AnalyticsPanel = lazy(() => import('./panels/AnalyticsPanel'))
const ChatPanel = lazy(() => import('./panels/ChatPanel'))
const QuestionsPanel = lazy(() => import('./panels/QuestionsPanel'))
const LiveSessionsPanel = lazy(() => import('./panels/LiveSessionsPanel'))
const ToolsPanel = lazy(() => import('./panels/ToolsPanel'))
const ReviewPanel = lazy(() => import('./panels/ReviewPanel'))
const OpsPanel = lazy(() => import('./panels/OpsPanel'))
const SupportConsole = lazy(() => import('./panels/SupportConsole'))
const IssuesPanel = lazy(() => import('./panels/IssuesPanel'))
const PrivacyPanel = lazy(() => import('./panels/PrivacyPanel'))
const OnboardingWizard = lazy(() => import('./components/OnboardingWizard'))

// Role matrix mirrors the legacy dashboard's data-roles attributes
// (commit c1d75e4 + earlier role-reshape plan). Teacher gets exam
// tools, admin loses Questions/Chat/Tools/Review (delegated to teachers
// in the org), super admin is maintenance-only — no exam tools at all.
const TABS = [
  // Teacher + admin operational tabs
  { id: 'live',         label: 'Live Sessions',  roles: ['teacher', 'admin'] },
  { id: 'results',      label: 'Results',        roles: ['teacher', 'admin'] },
  { id: 'history',      label: 'History',        roles: ['teacher', 'admin'] },
  { id: 'analytics',    label: 'Analytics',      roles: ['teacher', 'admin'] },
  // Teacher-only exam ops
  { id: 'questions',    label: 'Questions',      roles: ['teacher'] },
  { id: 'chat',         label: 'Chat',           roles: ['teacher'] },
  { id: 'tools',        label: 'Tools',          roles: ['teacher'] },
  { id: 'review',       label: 'Review',         roles: ['teacher'] },
  // Admin + super-admin org management
  { id: 'org',          label: 'Org Overview',   roles: ['admin', 'superadmin'] },
  { id: 'members',      label: 'Members',        roles: ['admin', 'superadmin'] },
  { id: 'bulk-import',  label: 'Import Students', roles: ['admin', 'superadmin'] },
  { id: 'billing',      label: 'Billing',        roles: ['admin', 'superadmin'] },
  { id: 'security',     label: 'Security',       roles: ['admin', 'superadmin'] },
  { id: 'privacy',      label: 'Privacy',        roles: ['teacher', 'admin', 'superadmin'] },
  { id: 'org-settings', label: 'Org Settings',   roles: ['admin', 'superadmin'] },
  { id: 'support',      label: 'Support',        roles: ['admin', 'superadmin'] },
  // Super-admin only (maintenance)
  { id: 'all-orgs',     label: 'All Orgs',       roles: ['superadmin'] },
  { id: 'issues',       label: 'Issues',         roles: ['superadmin'] },
]

function getUserRole(user) {
  return user?.org_role || user?.role || 'teacher'
}

function canSeeTab(tab, user) {
  const role = getUserRole(user)
  return !tab.roles || tab.roles.includes(role)
}

function LoginForm() {
  const { login } = useAuth()
  // 'login' | 'reset' — mirrors the legacy dashboard's toggleAuthForm so
  // the split-screen auth shell can swap the right-hand card between the
  // sign-in form and the password-reset form without leaving the page.
  const [mode, setMode] = useState('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [unverifiedEmail, setUnverifiedEmail] = useState(null)
  const [resending, setResending] = useState(false)
  const [resendMsg, setResendMsg] = useState('')
  // Password-reset form state (mode === 'reset')
  const [resetEmail, setResetEmail] = useState('')
  const [resetErr, setResetErr] = useState('')
  const [resetSent, setResetSent] = useState(false)
  const [resetLoading, setResetLoading] = useState(false)
  // Email-OTP 2FA challenge state — set when server returns
  // EMAIL_2FA_REQUIRED. While `awaiting2FA` is true we surface a
  // 6-digit input and resubmit login() with the code populated.
  const [awaiting2FA, setAwaiting2FA] = useState(false)
  const [otpCode, setOtpCode] = useState('')
  // P1.1: Cloudflare Turnstile token. Managed mode renders invisibly
  // 99% of the time. When TURNSTILE_SECRET_KEY is unset (dev), `token`
  // stays null and login() proceeds via the backend's sandbox path.
  const turnstile = useTurnstile()

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setUnverifiedEmail(null)
    setResendMsg('')
    setLoading(true)
    try {
      // If the 2FA input is showing, send the code along with credentials.
      await login(email, password, awaiting2FA ? otpCode : null, turnstile.token)
      // Success → useEffect in useAuth picks up the new user.
    } catch (err) {
      // Turnstile tokens are single-use; refresh on every failure so
      // the next attempt has a valid token (same pattern as
      // website/Signup.jsx).
      turnstile.refresh()
      if (err.code === 'EMAIL_UNVERIFIED') {
        setUnverifiedEmail(err.email || email)
        setError(err.message || 'Please verify your email.')
      } else if (err.code === 'EMAIL_2FA_REQUIRED') {
        // Server has just emailed a code. Show the OTP input.
        setAwaiting2FA(true)
        setOtpCode('')
        setError(err.message || 'We sent a 6-digit code to your email.')
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
      const body = { email: unverifiedEmail || email }
      // Backend's /resend-verification also runs verify_or_403 in
      // production. Pass the same Turnstile token used on login.
      if (turnstile.token) body.captcha_token = turnstile.token
      const r = await fetchWithTimeout(`${API_BASE}/auth/resend-verification`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!r.ok) throw new Error('Failed')
      setResendMsg('✅ Verification email sent! Check your inbox.')
    } catch (e) {
      setResendMsg('Failed to resend. Try again later.')
    } finally {
      turnstile.refresh()
      setResending(false)
    }
  }

  // Password-reset submit — mirrors the legacy dashboard's
  // doPasswordReset(): POST /auth/password-reset {email, captcha_token?}.
  // The endpoint always returns 200 (no account enumeration) so we show
  // the success state on any non-error response.
  const handleReset = async (e) => {
    e.preventDefault()
    setResetErr('')
    setResetLoading(true)
    try {
      const body = { email: resetEmail }
      if (turnstile.token) body.captcha_token = turnstile.token
      const r = await fetchWithTimeout(`${API_BASE}/auth/password-reset`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!r.ok) throw new Error('Failed')
      setResetSent(true)
    } catch (err) {
      setResetErr('Could not send reset link. Try again later.')
    } finally {
      turnstile.refresh()
      setResetLoading(false)
    }
  }

  return (
    <div id="auth-overlay">
      {/* Decorative accent glow behind the card — matches the marketing
          site's Hero treatment. Pure CSS, no JS hook. */}
      <div className="auth-glow" aria-hidden="true"></div>
      <div className="auth-shell">
        {/* Left: value-prop panel (hidden on mobile via .auth-aside CSS). */}
        <aside className="auth-aside">
          <a href="https://procta.net" className="auth-aside-back">← Back to procta.net</a>
          <h1 className="auth-aside-h1">
            Remote exams.<br />
            <span className="auth-aside-h1-accent">Real results.</span>
          </h1>
          <p className="auth-aside-lede">
            AI proctoring that catches cheating without making honest
            students nervous. Sign in to manage your exams, monitor
            live sessions, and review flagged candidates.
          </p>
          <ul className="auth-aside-list">
            <li><span className="auth-aside-bullet"></span>Real-time face, gaze, and audio monitoring</li>
            <li><span className="auth-aside-bullet"></span>Risk-scored sessions with evidence trails</li>
            <li><span className="auth-aside-bullet"></span>3,000 concurrent students on a ₹699/mo box</li>
          </ul>
        </aside>

        {/* Right: login / reset card */}
        <div className="auth-card">
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10, marginBottom: 20 }}>
            <div style={{ width: 40, height: 40, borderRadius: 10, background: 'var(--accent)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <svg width="20" height="20" viewBox="0 0 16 16" fill="none">
                <path d="M4 3h3v1H5v8h2v1H4V3zm5 0h3v10h-3v-1h2V4H9V3z" fill="white" />
                <circle cx="8" cy="8" r="1.5" fill="white" opacity="0.8" />
              </svg>
            </div>
            <span style={{ fontFamily: 'var(--font-display)', fontSize: 22, fontWeight: 700, color: 'white' }}>Procta</span>
          </div>

          {mode === 'login' ? (
            <div>
              <h2>Welcome Back</h2>
              <p>Sign in to your teacher dashboard</p>
              {unverifiedEmail && (
                <div style={{ marginBottom: 12, textAlign: 'center' }}>
                  <button className="btn btn-primary btn-sm" onClick={resendVerification} disabled={resending}>
                    {resending ? 'Sending...' : 'Resend verification email'}
                  </button>
                  {resendMsg && <div style={{ fontSize: 11, marginTop: 6, color: resendMsg.includes('✅') ? 'var(--emerald)' : 'var(--muted)' }}>{resendMsg}</div>}
                </div>
              )}
              <form onSubmit={handleSubmit} autoComplete="on">
                <input
                  type="email" placeholder="Email address" value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  autoComplete="email" required
                />
                <input
                  type="password" placeholder="Password" value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="current-password" required
                />
                {awaiting2FA && (
                  <div style={{ marginTop: 8 }}>
                    <p style={{ fontSize: 12, color: 'var(--muted)', margin: '4px 0 6px' }}>
                      We sent a 6-digit code to your email. Enter it to finish signing in.
                    </p>
                    <input
                      type="text" inputMode="numeric" pattern="[0-9]{6}" maxLength={6}
                      placeholder="6-digit code" value={otpCode}
                      onChange={(e) => setOtpCode(e.target.value)}
                      autoComplete="one-time-code" required autoFocus
                      style={{ letterSpacing: 6, textAlign: 'center', fontFamily: 'monospace' }}
                    />
                  </div>
                )}
                {/* Cloudflare Turnstile (Managed mode) — invisible 99% of
                    the time. ref=null in dev makes this a harmless no-op. */}
                <div ref={turnstile.ref} style={{ margin: '4px 0' }} />
                {/* Cloudflare Turnstile (Managed mode) — invisible 99% of
                    the time. ref=null in dev makes this a harmless no-op. */}
                <div ref={turnstile.ref} style={{ margin: '4px 0' }} />
                <button type="submit" id="login-btn" disabled={loading || turnstile.loading}>
                  {loading ? 'Signing in...' : turnstile.loading ? 'Verifying...' : (awaiting2FA ? 'Verify & Log In' : 'Log In')}
                </button>
              </form>
              {error && <div className="auth-err">{error}</div>}
              <p style={{ color: 'var(--muted)', fontSize: 12, marginTop: 12 }}>
                <a
                  href="#"
                  onClick={(e) => { e.preventDefault(); setError(''); setMode('reset') }}
                  style={{ color: 'var(--muted)', textDecoration: 'none', fontSize: 12 }}
                >
                  Forgot password?
                </a>
              </p>
              <p style={{ color: 'var(--muted)', fontSize: 12, marginTop: 8 }}>
                {"Don't have an account? "}
                <a href="https://procta.net/signup" style={{ color: 'var(--accent-light)', textDecoration: 'none', fontWeight: 600 }}>
                  Start free trial
                </a>
              </p>
            </div>
          ) : (
            <div>
              <h2>Reset Password</h2>
              <p>{"Enter your email and we'll send a reset link"}</p>
              <form onSubmit={handleReset} autoComplete="on">
                <input
                  type="email" placeholder="Email address" value={resetEmail}
                  onChange={(e) => setResetEmail(e.target.value)}
                  autoComplete="email" required
                />
                <button type="submit" id="reset-btn" disabled={resetLoading || turnstile.loading}>
                  {resetLoading ? 'Sending...' : turnstile.loading ? 'Verifying...' : 'Send Reset Link'}
                </button>
              </form>
              {resetErr && <div className="auth-err">{resetErr}</div>}
              {resetSent && (
                <div style={{ color: 'var(--emerald)', fontSize: 13, marginTop: 12 }}>
                  Reset link sent! Check your email inbox.
                </div>
              )}
              <p style={{ color: 'var(--muted)', fontSize: 12, marginTop: 16 }}>
                <a
                  href="#"
                  onClick={(e) => { e.preventDefault(); setResetErr(''); setMode('login') }}
                  style={{ color: 'var(--accent-light)', textDecoration: 'none', fontWeight: 600 }}
                >
                  Back to login
                </a>
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function DashboardShell() {
  const { user, logout, authFetch } = useAuth()
  const [activeTab, setActiveTab] = useState(() => window.location.hash.replace(/^#/, '') || 'org')
  const [currentExamId, setCurrentExamId] = useState(() => {
    // Restore the last-selected exam from sessionStorage so a refresh
    // doesn't drop the user back to the first exam in the list.
    const saved = sessionStorage.getItem('procta_current_exam_id')
    return saved || null
  })
  const [showOnboarding, setShowOnboarding] = useState(() => {
    // Show onboarding for new users (not yet completed or skipped)
    const done = localStorage.getItem('procta_onboarding_done')
    if (done) return false
    // If we've never checked, show a loading state while we verify
    return null // null = checking, false = don't show, true = show
  })
  const [showDemoCta, setShowDemoCta] = useState(() => !localStorage.getItem('procta_demo_cta_done'))
  const visibleTabs = useMemo(() => TABS.filter(tab => canSeeTab(tab, user)), [user])

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
    'bulk-import': BulkImportPanel,
    billing: BillingPanel,
    security: SecurityPanel,
    privacy: PrivacyPanel,
    'all-orgs': AllOrgsPanel,
    'org-settings': OrgSettingsPanel,
    issues: IssuesPanel,
  }
  const Panel = PANELS[activeTab]

  useEffect(() => {
    if (visibleTabs.length && !visibleTabs.some(tab => tab.id === activeTab)) {
      // Role-aware default landing tab. Mirrors the legacy dashboard
      // default-routing (commit c1d75e4 in dashboard-app.js): teachers
      // land on live, admins on live (operational visibility daily),
      // superadmin on all-orgs (maintenance overview). Falls back to
      // whatever's first visible if a preferred tab isn't actually in
      // the role's matrix (defensive).
      const role = getUserRole(user)
      const prefs = role === 'superadmin'
        ? ['all-orgs', 'issues', 'org']
        : role === 'admin'
          ? ['live', 'org']
          : ['live']
      const pick = prefs.find(p => visibleTabs.some(tab => tab.id === p))
      const fallback = pick || visibleTabs[0].id
      setActiveTab(fallback)
      window.history.replaceState(null, '', `#${fallback}`)
    }
  }, [activeTab, visibleTabs, user])

  useEffect(() => {
    const onHashChange = () => {
      const next = window.location.hash.replace(/^#/, '')
      if (next && visibleTabs.some(tab => tab.id === next)) setActiveTab(next)
    }
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [visibleTabs])

  const selectTab = (tabId) => {
    setActiveTab(tabId)
    window.location.hash = tabId
  }

  // Keep sessionStorage in sync with currentExamId so a page refresh
  // restores the user to the same exam they were working on.
  useEffect(() => {
    if (currentExamId) {
      sessionStorage.setItem('procta_current_exam_id', currentExamId)
    } else {
      sessionStorage.removeItem('procta_current_exam_id')
    }
  }, [currentExamId])

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
    return (
      <Suspense fallback={<div style={{ padding: 40, color: 'var(--text-muted)' }}>Loading...</div>}>
        <OnboardingWizard onComplete={(examId) => {
          if (examId) setCurrentExamId(examId)
          selectTab(examId ? 'questions' : 'org')
          setShowOnboarding(false)
          localStorage.setItem('procta_onboarding_done', '1')
        }} />
      </Suspense>
    )
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
        {visibleTabs.map(tab => (
          <button
            key={tab.id}
            className={`tab ${activeTab === tab.id ? 'active' : ''}`}
            onClick={() => selectTab(tab.id)}
            style={{ textTransform: 'uppercase', letterSpacing: 0.03 }}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div className="container dashboard-react-container">
        {showDemoCta && (
          <ActivationBanner
            onDismiss={() => {
              localStorage.setItem('procta_demo_cta_done', '1')
              setShowDemoCta(false)
            }}
            onQuestions={() => selectTab('questions')}
          />
        )}
        <Suspense fallback={<div style={{ padding: 24, color: 'var(--text-muted)' }}>Loading panel...</div>}>
          {Panel && <Panel currentExamId={currentExamId} setCurrentExamId={setCurrentExamId} />}
        </Suspense>
      </div>
    </div>
  )
}

function ActivationBanner({ onDismiss, onQuestions }) {
  const openPractice = () => {
    window.open('/student#practice', '_blank', 'noopener,noreferrer')
  }

  return (
    <div className="card activation-banner" style={{ padding: 18, marginBottom: 18, borderColor: 'rgba(217,119,6,.45)', background: 'linear-gradient(180deg, rgba(217,119,6,.08), rgba(15,23,42,.02))' }}>
      <div style={{ display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ flex: '1 1 260px', minWidth: 0 }}>
          <div style={{ fontSize: 11, color: 'var(--amber)', textTransform: 'uppercase', fontWeight: 800, marginBottom: 4 }}>Practice Exam</div>
          <h3 style={{ margin: '0 0 4px', fontSize: 18 }}>Run a setup test before your first live exam</h3>
          <p style={{ margin: 0, color: 'var(--text-secondary)', fontSize: 13, lineHeight: 1.45 }}>
            Validate the student app, camera checks, lockdown flow, and submit path with the practice sandbox.
          </p>
        </div>
        <div className="activation-banner-actions" style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
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
