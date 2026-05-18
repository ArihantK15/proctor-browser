import { useState, useEffect } from 'react'
import { useAuth } from '../lib/auth'

export default function MembersPanel() {
  const { user, authFetch } = useAuth()
  const [members, setMembers] = useState([])
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteName, setInviteName] = useState('')
  const [status, setStatus] = useState('')
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const isAdmin = user?.org_role === 'admin' || user?.org_role === 'superadmin'

  const responseError = async (response, fallback) => {
    const data = await response.json().catch(() => ({}))
    return new Error(data.detail || `${fallback} (${response.status})`)
  }

  useEffect(() => { loadMembers() }, [])

  const loadMembers = async () => {
    setLoadError('')
    try {
      const r = await authFetch('/api/v1/org/members')
      if (!r.ok) {
        throw await responseError(r, 'Failed to load members')
      }
      const d = await r.json()
      setMembers(d.members || [])
    } catch (e) {
      setLoadError(e.message || 'Failed to load members')
    } finally { setLoading(false) }
  }

  const inviteTeacher = async () => {
    if (!inviteEmail || !inviteEmail.includes('@')) { setStatus('Valid email required'); return }
    setStatus('Sending...')
    try {
      const r = await authFetch('/api/v1/org/invite', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: inviteEmail, full_name: inviteName || '' }),
      })
      if (!r.ok) throw await responseError(r, 'Failed to send invite')
      setStatus('✅ Invite sent!')
      setInviteEmail('')
      setInviteName('')
      loadMembers()
    } catch (e) { setStatus(e.message) }
  }

  const removeMember = async (memberId) => {
    if (!confirm('Remove this member?')) return
    try {
      const r = await authFetch(`/api/v1/org/members/${memberId}`, { method: 'DELETE' })
      if (!r.ok) throw await responseError(r, 'Failed to remove member')
      setStatus('✅ Member removed.')
      loadMembers()
    } catch (err) { setStatus(err.message || 'Failed to remove member') }
  }

  const changeRole = async (memberId, newRole) => {
    try {
      const r = await authFetch(`/api/v1/org/members/${memberId}/role`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role: newRole }),
      })
      if (!r.ok) throw await responseError(r, 'Failed to change role')
      setStatus('✅ Role updated.')
      loadMembers()
    } catch (err) { setStatus(err.message || 'Failed to change role') }
  }

  if (loading) return <div className="loading">Loading members...</div>
  if (loadError) return <div className="auth-err" style={{ margin: 20 }}>{loadError} <button className="btn-link" onClick={loadMembers} style={{ marginLeft: 8 }}>Retry</button></div>

  return (
    <div>
      <div className="stats-bar">
        <div className="stat-tile">
          <div className="stat-tile-label">Members</div>
          <div className="stat-tile-value accent">{members.length}</div>
        </div>
      </div>

      <div className="table-toolbar">
        <span style={{ fontSize: 16, fontWeight: 600, color: 'var(--text-primary)' }}>Team</span>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
          <input
            type="email" placeholder="Email" value={inviteEmail}
            onChange={(e) => setInviteEmail(e.target.value)}
            className="input" style={{ width: 200, padding: '6px 10px', fontSize: 12 }}
          />
          <input
            type="text" placeholder="Name (optional)" value={inviteName}
            onChange={(e) => setInviteName(e.target.value)}
            className="input" style={{ width: 180, padding: '6px 10px', fontSize: 12 }}
          />
          <button className="btn btn-primary btn-sm" onClick={inviteTeacher}>Invite</button>
        </div>
      </div>
      {status && <div style={{ fontSize: 12, color: status.includes('✅') ? 'var(--emerald)' : 'var(--red)', marginBottom: 8 }}>{status}</div>}

      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr style={{ background: 'var(--surface-1)', borderBottom: '1px solid var(--border-subtle)' }}>
            <th style={{ padding: '10px 14px', textAlign: 'left', fontWeight: 600, color: 'var(--text-muted)', fontSize: 11, textTransform: 'uppercase' }}>Name</th>
            <th style={{ padding: '10px 14px', textAlign: 'left', fontWeight: 600, color: 'var(--text-muted)', fontSize: 11, textTransform: 'uppercase' }}>Email</th>
            <th style={{ padding: '10px 14px', textAlign: 'left', fontWeight: 600, color: 'var(--text-muted)', fontSize: 11, textTransform: 'uppercase' }}>Role</th>
            <th style={{ padding: '10px 14px' }}></th>
          </tr>
        </thead>
        <tbody>
          {members.length === 0 ? (
            <tr>
              <td colSpan={4} style={{ padding: '32px 14px', textAlign: 'center', color: 'var(--text-muted)', fontSize: 13 }}>
                No team members yet — invite a colleague using the form above.
              </td>
            </tr>
          ) : members.map(m => (
            <tr key={m.id} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
              <td style={{ padding: '10px 14px' }}>{m.full_name}</td>
              <td style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>{m.email}</td>
              <td style={{ padding: '10px 14px' }}>
                {isAdmin && m.id !== user?.id ? (
                  <select value={m.org_role} onChange={e => changeRole(m.id, e.target.value)}
                    className="input" style={{ padding: '4px 6px', fontSize: 12, width: 'auto' }}>
                    <option value="admin">admin</option>
                    <option value="teacher">teacher</option>
                    <option value="viewer">viewer</option>
                  </select>
                ) : (
                  <span>{m.org_role}</span>
                )}
              </td>
              <td style={{ padding: '10px 14px' }}>
                {isAdmin && m.id !== user?.id && (
                  <button className="btn btn-ghost btn-sm" onClick={() => removeMember(m.id)} style={{ color: 'var(--red)', fontSize: 11, padding: '4px 8px' }}>Remove</button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
