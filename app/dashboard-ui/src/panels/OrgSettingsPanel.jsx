import { useState, useEffect } from 'react'
import { useAuth } from '../lib/auth'

export default function OrgSettingsPanel() {
  const { authFetch } = useAuth()
  const [orgName, setOrgName] = useState('')
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')

  useEffect(() => { loadOrg() }, [])

  const loadOrg = async () => {
    try {
      const r = await authFetch('/api/v1/org')
      if (r.ok) {
        const d = await r.json()
        setOrgName(d.name || '')
      }
    } catch (_) {}
  }

  const save = async () => {
    if (!orgName.trim()) { setMsg('Name is required'); return }
    setSaving(true)
    setMsg('Saving...')
    try {
      const r = await authFetch('/api/v1/org', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: orgName.trim() }),
      })
      if (!r.ok) throw new Error('Failed')
      setMsg('✅ Saved')
    } catch (e) { setMsg(e.message) }
    finally { setSaving(false) }
  }

  return (
    <div style={{ maxWidth: 500, margin: '0 auto' }}>
      <h2 style={{ color: 'var(--text-primary)', margin: '0 0 20px', fontFamily: 'var(--font-display)', fontWeight: 600, fontSize: 20 }}>Org Settings</h2>
      <div className="card" style={{ padding: 24 }}>
        <div style={{ marginBottom: 20 }}>
          <label style={{ display: 'block', fontSize: 'var(--text-sm)', fontWeight: 600, letterSpacing: 0.03, textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 8 }}>Organization Name</label>
          <input className="input" style={{ width: '100%', boxSizing: 'border-box' }} value={orgName} onChange={(e) => setOrgName(e.target.value)} placeholder="Your organization name" />
        </div>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? 'Saving...' : 'Save Changes'}</button>
          {msg && <span style={{ fontSize: 'var(--text-sm)', color: msg.includes('✅') ? 'var(--emerald)' : 'var(--text-muted)' }}>{msg}</span>}
        </div>
      </div>
    </div>
  )
}
