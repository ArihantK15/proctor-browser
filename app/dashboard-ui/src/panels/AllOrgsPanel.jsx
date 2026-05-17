import { useState, useEffect } from 'react'
import { useAuth } from '../lib/auth'

export default function AllOrgsPanel() {
  const { authFetch } = useAuth()
  const [orgs, setOrgs] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => { loadOrgs() }, [])

  const loadOrgs = async () => {
    setError('')
    try {
      const r = await authFetch('/api/v1/admin/all-orgs')
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || `Failed to load organizations (${r.status})`)
      }
      const d = await r.json()
      setOrgs(d.orgs || [])
    } catch (e) {
      setError(e.message || 'Failed to load organizations')
    } finally { setLoading(false) }
  }

  if (loading) return <div className="loading">Loading organizations...</div>
  if (error) return <div className="auth-err" style={{ margin: 20 }}>{error} <button className="btn-link" onClick={loadOrgs} style={{ marginLeft: 8 }}>Retry</button></div>

  return (
    <div>
      <div className="stats-bar">
        <div className="stat-tile">
          <div className="stat-tile-label">Organizations</div>
          <div className="stat-tile-value accent">{orgs.length}</div>
        </div>
      </div>
      <div className="table-toolbar">
        <span style={{ fontSize: 16, fontWeight: 600, color: 'var(--text-primary)' }}>All Organizations</span>
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr style={{ background: 'var(--surface-1)', borderBottom: '1px solid var(--border-subtle)' }}>
            {['Name', 'Teachers', 'Students', 'Plan', 'Status', 'Created'].map(h => (
              <th key={h} style={{ padding: '10px 14px', textAlign: 'left', fontWeight: 600, color: 'var(--text-muted)', fontSize: 11, textTransform: 'uppercase' }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {orgs.length === 0 ? (
            <tr>
              <td colSpan={6} style={{ padding: '32px 14px', textAlign: 'center', color: 'var(--text-muted)', fontSize: 13 }}>
                No organizations found.
              </td>
            </tr>
          ) : orgs.map(o => (
            <tr key={o.id} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
              <td style={{ padding: '10px 14px' }}>{o.name}</td>
              <td style={{ padding: '10px 14px' }}>{o.teacher_count || 0}</td>
              <td style={{ padding: '10px 14px' }}>{(o.student_count || 0)} / {(o.max_students || 30)}</td>
              <td style={{ padding: '10px 14px' }}>{o.plan}</td>
              <td style={{ padding: '10px 14px' }}>{o.status}</td>
              <td style={{ padding: '10px 14px', color: 'var(--text-secondary)', fontSize: 12 }}>{o.created_at || ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
