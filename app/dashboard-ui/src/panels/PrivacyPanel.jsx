import { useState } from 'react'
import { useAuth } from '../lib/auth'

// Data-subject rights center (DPDP §11–13 / GDPR Art 15–17).
// Two actions, both backed by /api/v1/privacy/* endpoints. The export
// returns a JSON blob we hand off as a download; delete requires a
// fresh reauth_token (same pattern Security/2FA uses).
export default function PrivacyPanel() {
  const { authFetch } = useAuth()
  const [exportMsg, setExportMsg] = useState('')
  const [exportMsgColor, setExportMsgColor] = useState('var(--text-muted)')
  const [deleteMsg, setDeleteMsg] = useState('')
  const [deleteMsgColor, setDeleteMsgColor] = useState('var(--text-muted)')
  const [exporting, setExporting] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const exportData = async () => {
    setExportMsgColor('var(--text-muted)')
    setExportMsg('Generating export — this may take a few seconds…')
    setExporting(true)
    try {
      const r = await authFetch('/api/v1/privacy/export')
      const d = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(d.detail || `Export failed (${r.status})`)

      // Hand the JSON to the browser as a download. We avoid leaking
      // it into URL history by using a Blob + revokeObjectURL.
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

      setExportMsgColor('var(--emerald)')
      const tables = Object.keys(d).filter(k => Array.isArray(d[k])).length
      setExportMsg(`Export ready — ${tables} categories downloaded.`)
    } catch (e) {
      setExportMsgColor('var(--red)')
      setExportMsg(e.message || 'Export failed')
    } finally {
      setExporting(false)
    }
  }

  const getReauthToken = async () => {
    const password = window.prompt(
      'Enter your password to confirm account deletion.\n\n' +
      'This action is permanent. See docs/PRIVACY.md for what is retained vs deleted.'
    )
    if (!password) return null
    const r = await authFetch('/api/v1/auth/reauth', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    })
    const d = await r.json().catch(() => ({}))
    if (!r.ok) throw new Error(d.detail || 'Password verification failed')
    return d.reauth_token
  }

  const deleteAccount = async () => {
    setDeleteMsgColor('var(--text-muted)')
    setDeleteMsg('')
    // Two-step confirmation: type the word DELETE in addition to the
    // password reauth. Catches misclicks; non-trivial typing cost.
    const typed = window.prompt(
      'Type DELETE (in capitals) to confirm you want to erase your account.'
    )
    if (typed !== 'DELETE') {
      setDeleteMsgColor('var(--text-muted)')
      setDeleteMsg(typed === null ? '' : 'Cancelled — text didn\'t match.')
      return
    }
    setDeleting(true)
    try {
      const reauth_token = await getReauthToken()
      if (!reauth_token) {
        setDeleteMsg('Cancelled.')
        return
      }
      setDeleteMsg('Erasing your account…')
      const r = await authFetch('/api/v1/privacy/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reauth_token }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(d.detail || `Delete failed (${r.status})`)
      setDeleteMsgColor(d.status === 'partial' ? 'var(--amber)' : 'var(--emerald)')
      if (d.status === 'partial') {
        setDeleteMsg(`Account erased with some non-fatal issues (${(d.errors || []).join(', ')}). You will be logged out.`)
      } else {
        setDeleteMsg('Account erased. You will be logged out.')
      }
      // Give the user a moment to read the confirmation, then bounce
      // them to the marketing site — their session is dead anyway.
      setTimeout(() => { window.location.href = 'https://procta.net' }, 4000)
    } catch (e) {
      setDeleteMsgColor('var(--red)')
      setDeleteMsg(e.message || 'Delete failed')
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="panel-content" style={{ maxWidth: 720 }}>
      <h2 style={{ marginBottom: 6 }}>Privacy & Your Data</h2>
      <p style={{ color: 'var(--text-muted)', fontSize: 13, marginTop: 0 }}>
        Manage your data under India's DPDP Act and the EU GDPR.
        See <a href="https://procta.net/legal/privacy" target="_blank" rel="noreferrer">our privacy policy</a> for what we collect and why.
      </p>

      <section style={{ marginTop: 28, padding: 20, border: '1px solid var(--border-subtle)', borderRadius: 12, background: 'var(--surface-1)' }}>
        <h3 style={{ marginTop: 0 }}>Download your data</h3>
        <p style={{ color: 'var(--text-muted)', fontSize: 13 }}>
          Generate a JSON file containing everything we have linked to your account —
          profile, exams, students, sessions, violations, consent records, audit trail.
          Rate-limited to 5 exports per hour.
        </p>
        <button
          className="btn btn-secondary"
          onClick={exportData}
          disabled={exporting}
        >
          {exporting ? 'Exporting…' : 'Export my data'}
        </button>
        {exportMsg && (
          <div style={{ marginTop: 12, color: exportMsgColor, fontSize: 13 }}>{exportMsg}</div>
        )}
      </section>

      <section style={{ marginTop: 24, padding: 20, border: '1px solid var(--red)', borderRadius: 12, background: 'rgba(239,68,68,0.05)' }}>
        <h3 style={{ marginTop: 0, color: 'var(--red)' }}>Delete my account</h3>
        <p style={{ color: 'var(--text-muted)', fontSize: 13 }}>
          Permanently erases your account. Sessions are revoked immediately. Most
          personal identifiers are anonymised; some records are retained for legal
          compliance — see <a href="https://github.com/ArihantK15/proctor-browser/blob/main/docs/PRIVACY.md" target="_blank" rel="noreferrer">our retention policy</a> for the full breakdown.
        </p>
        <p style={{ color: 'var(--text-muted)', fontSize: 13, marginBottom: 16 }}>
          <strong>This cannot be undone.</strong> If you want a copy of your data first,
          export it above.
        </p>
        <button
          className="btn"
          onClick={deleteAccount}
          disabled={deleting}
          style={{
            background: 'var(--red)',
            color: 'white',
            border: '1px solid var(--red)',
          }}
        >
          {deleting ? 'Deleting…' : 'Delete my account'}
        </button>
        {deleteMsg && (
          <div style={{ marginTop: 12, color: deleteMsgColor, fontSize: 13 }}>{deleteMsg}</div>
        )}
      </section>
    </div>
  )
}
