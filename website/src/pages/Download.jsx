import { Helmet } from 'react-helmet-async'
import { useState, useEffect } from 'react'

export default function Download() {
  const [releases, setReleases] = useState(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    fetch('/download/latest-info')
      .then(r => r.ok ? r.json() : Promise.reject(new Error('Server returned error')))
      .then(d => setReleases(d))
      .catch(() => setError(true))
  }, [])

  const downloads = [
    { label: 'macOS (Apple Silicon)', key: 'mac_arm', icon: '💻' },
    { label: 'macOS (Intel)', key: 'mac_x64', icon: '💻' },
    { label: 'Windows', key: 'win', icon: '🪟' },
  ]

  return (
    <div className="min-h-screen bg-navy-950 flex items-center justify-center p-6">
      <Helmet>
        <title>Download Procta — Remote exams. Real results.</title>
        <meta name="description" content="Download the AI-proctored exam desktop app for Windows and macOS." />
        <link rel="canonical" href="https://app.procta.net/download" />
      </Helmet>
      <div style={{ maxWidth: 520, width: '100%', textAlign: 'center' }}>
        <h1 style={{ fontSize: 28, fontWeight: 700, color: 'white', margin: '16px 0 8px' }}>Download Procta</h1>
        <p style={{ color: 'var(--muted)', fontSize: 14, lineHeight: 1.6, marginBottom: 32 }}>
          The locked-down exam browser with AI proctoring. Download for your platform, install, and you're ready.
        </p>
        {error ? (
          <p style={{ color: 'var(--danger, #ef4444)', fontSize: 14, marginBottom: 16 }}>
            Could not load downloads. Please try reloading the page or visit{' '}
            <a href="/dashboard" style={{ color: 'var(--accent, #6366f1)', textDecoration: 'underline' }}>the dashboard</a>.
          </p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {downloads.map(dl => (
              <a key={dl.key}
                href={releases?.[dl.key] || '#'}
                style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10,
                  padding: '14px 24px', borderRadius: 12, background: 'rgba(99,102,241,.1)',
                  border: '1px solid rgba(99,102,241,.25)', color: 'white', textDecoration: 'none',
                  fontSize: 15, fontWeight: 500, transition: 'background .15s',
                }}
                onMouseOver={e => e.currentTarget.style.background = 'rgba(99,102,241,.2)'}
                onMouseOut={e => e.currentTarget.style.background = 'rgba(99,102,241,.1)'}
              >
                <span>{dl.icon}</span>
                <span>{dl.label}</span>
                {releases?.[dl.key] ? '' : <span style={{ fontSize: 12, color: 'var(--muted)' }}>—</span>}
              </a>
            ))}
          </div>
        )}
        <p style={{ color: 'var(--muted)', fontSize: 12, marginTop: 24, lineHeight: 1.6 }}>
          Version {releases?.tag || '...'} &middot; Free 14-day trial &middot; No credit card required
        </p>
      </div>
    </div>
  )
}
