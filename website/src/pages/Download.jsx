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

  // Each key maps to BOTH the latest-info JSON key (for the direct
  // signed GitHub URL when available) AND the server-side redirect
  // path used as a fallback when latest-info comes back empty. The
  // backend latest-info handler shares a per-worker GitHub release
  // cache that gets cleared by anonymous GitHub API rate limits;
  // when that happens the JSON returns empty strings but the
  // redirect endpoints still work because they have their own
  // resolution path with a local-file fallback. Falling back here
  // means the Download page never has a dead button.
  const downloads = [
    { label: 'macOS (Apple Silicon)', key: 'mac_arm', fallback: '/download/mac',    icon: '💻' },
    { label: 'macOS (Intel)',         key: 'mac_x64', fallback: '/download/mac-x64', icon: '💻' },
    { label: 'Windows',               key: 'win',     fallback: '/download/win',    icon: '🪟' },
  ]

  return (
    <div className="min-h-screen bg-navy-950 flex items-center justify-center p-6">
      <Helmet>
        <title>Download Procta — Remote exams. Real results.</title>
        <meta name="description" content="Download the AI-proctored exam desktop app for Windows and macOS." />
        <link rel="canonical" href="https://www.procta.net/download" />
        <meta property="og:title" content="Download Procta — Remote exams. Real results." />
        <meta property="og:description" content="Download the AI-proctored exam desktop app for Windows and macOS." />
        <meta property="og:url" content="https://www.procta.net/download" />
        <meta property="og:type" content="website" />
        <meta property="og:image" content="https://www.procta.net/og-image.png" />
        <meta name="twitter:card" content="summary_large_image" />
        <meta name="twitter:image" content="https://www.procta.net/og-image.png" />
      </Helmet>
      <div style={{ maxWidth: 520, width: '100%', textAlign: 'center' }}>
        <h1 style={{ fontSize: 28, fontWeight: 700, color: 'white', margin: '16px 0 8px' }}>Download Procta</h1>
        <p style={{ color: 'var(--muted)', fontSize: 14, lineHeight: 1.6, marginBottom: 32 }}>
          The locked-down exam browser with AI proctoring. Download for your platform, install, and you're ready.
        </p>
        {error && (
          <p style={{ color: 'var(--muted)', fontSize: 13, marginBottom: 16 }}>
            Using fallback download endpoints — release info temporarily unavailable.
          </p>
        )}
        {(
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {downloads.map(dl => (
              <a key={dl.key}
                href={releases?.[dl.key] || dl.fallback}
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
              </a>
            ))}
          </div>
        )}
        <p style={{ color: 'var(--muted)', fontSize: 12, marginTop: 24, lineHeight: 1.6 }}>
          {releases?.tag ? `Version ${releases.tag} · ` : ''}Free 7-day trial · No credit card required
        </p>
      </div>
    </div>
  )
}
