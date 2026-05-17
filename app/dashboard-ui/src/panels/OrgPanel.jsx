import { useState, useEffect } from 'react'
import { useAuth } from '../lib/auth'

export default function OrgPanel() {
  const { authFetch } = useAuth()
  const [org, setOrg] = useState(null)
  const [billing, setBilling] = useState(null)
  const [members, setMembers] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const loadAll = async () => {
    setError('')
    try {
      const [orgR, billingR, membersR] = await Promise.all([
        authFetch('/api/v1/org'),
        authFetch('/api/v1/org/billing'),
        authFetch('/api/v1/org/members'),
      ])
      if (!orgR.ok && !billingR.ok) {
        throw new Error(`Failed to load org data (${orgR.status})`)
      }
      if (orgR.ok) setOrg(await orgR.json())
      if (billingR.ok) setBilling(await billingR.json())
      if (membersR.ok) { const d = await membersR.json(); setMembers(d.members || []) }
    } catch (e) {
      setError(e.message || 'Failed to load org data')
    } finally { setLoading(false) }
  }

  useEffect(() => { loadAll() }, [])

  if (loading) return <div className="loading">Loading org data...</div>
  if (error) return <div className="auth-err" style={{ margin: 20 }}>{error} <button className="btn-link" onClick={loadAll} style={{ marginLeft: 8 }}>Retry</button></div>

  const plan = billing?.plan || 'starter'
  const maxStudents = billing?.max_students || 30
  const studentCount = billing?.student_count || 0
  const status = billing?.status || 'unknown'
  const trialEnd = billing?.trial_end
  const remaining = trialEnd ? Math.max(0, Math.ceil((new Date(trialEnd) - new Date()) / 86400000)) : 0

  return (
    <div>
      {/* Stats strip */}
      <div className="stats-bar">
        <div className="stat-tile">
          <div className="stat-tile-label">Organization</div>
          <div className="stat-tile-value accent">{org?.name || '--'}</div>
        </div>
        <div className="stat-tile">
          <div className="stat-tile-label">Plan</div>
          <div className="stat-tile-value accent">{plan}</div>
        </div>
        <div className="stat-tile">
          <div className="stat-tile-label">Students</div>
          <div className="stat-tile-value">{studentCount} / {maxStudents}</div>
        </div>
        <div className="stat-tile">
          <div className="stat-tile-label">Teachers</div>
          <div className="stat-tile-value">{members.length}</div>
        </div>
      </div>

      {/* Trial banner */}
      {status === 'trialing' && trialEnd && (
        <div style={{
          background: remaining <= 1 ? 'rgba(239,68,68,0.1)' : remaining <= 3 ? 'rgba(245,158,11,0.1)' : 'var(--accent-bg)',
          border: `1px solid ${remaining <= 1 ? 'var(--red)' : remaining <= 3 ? 'var(--amber)' : 'var(--accent)'}`,
          borderRadius: 'var(--radius-md)', padding: '14px 18px', marginBottom: 20, fontSize: 13,
          color: remaining <= 1 ? 'var(--red)' : remaining <= 3 ? 'var(--amber)' : 'var(--accent-fg)',
        }}>
          <strong>🔥 Trial period active.</strong> {remaining} day{remaining === 1 ? '' : 's'} remaining.
        </div>
      )}

      {/* Members table */}
      <div style={{ marginTop: 24 }}>
        <h3 style={{ color: 'var(--text-primary)', margin: '0 0 12px' }}>Team Members</h3>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ background: 'var(--surface-1)', borderBottom: '1px solid var(--border-subtle)' }}>
              <th style={{ padding: '10px 14px', textAlign: 'left', fontWeight: 600, color: 'var(--text-muted)', fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.04 }}>Name</th>
              <th style={{ padding: '10px 14px', textAlign: 'left', fontWeight: 600, color: 'var(--text-muted)', fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.04 }}>Email</th>
              <th style={{ padding: '10px 14px', textAlign: 'left', fontWeight: 600, color: 'var(--text-muted)', fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.04 }}>Role</th>
            </tr>
          </thead>
          <tbody>
            {members.map(m => (
              <tr key={m.id} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                <td style={{ padding: '10px 14px' }}>{m.full_name}</td>
                <td style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>{m.email}</td>
                <td style={{ padding: '10px 14px' }}>{m.org_role}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
