import { useState, useEffect } from 'react'
import { fetchWithTimeout } from '../lib/auth'

export default function DownloadPage() {
  const [releases, setReleases] = useState(null)

  useEffect(() => {
    fetchWithTimeout('/download/latest-info')
      .then(r => r.ok ? r.json() : null)
      .then(d => setReleases(d))
      .catch(() => {})
  }, [])

  return (
    <div style={{ minHeight: '100vh', background: 'var(--surface)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }}>
      <div style={{ maxWidth: 480, width: '100%', textAlign: 'center' }}>
        <h1 style={{ fontSize: 26, fontWeight: 700, marginBottom: 8 }}>Download Procta</h1>
        <p style={{ color: 'var(--text-secondary)', fontSize: 14, marginBottom: 28, lineHeight: 1.6 }}>
          The locked-down exam browser with AI proctoring.
        </p>
        {[
          { label: 'macOS (Apple Silicon)', key: 'mac_arm', icon: '💻' },
          { label: 'macOS (Intel)', key: 'mac_x64', icon: '💻' },
          { label: 'Windows', key: 'win', icon: '🪟' },
          { label: 'Linux (AppImage)', key: 'linux', icon: '🐧' },
        ].map(dl => (
          <a key={dl.key} href={releases?.[dl.key] || '#'}
            style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10, padding: '14px 20px', marginBottom: 10, borderRadius: 10, background: 'var(--surface-1)', border: '1px solid var(--border)', color: 'var(--text)', textDecoration: 'none', fontSize: 14, fontWeight: 500 }}>
            <span>{dl.icon}</span>
            <span>{dl.label}</span>
          </a>
        ))}
        <p style={{ color: 'var(--text-muted)', fontSize: 12, marginTop: 20 }}>Version {releases?.tag || '...'}</p>
      </div>
    </div>
  )
}
