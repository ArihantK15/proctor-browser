import { useState, useEffect, useCallback } from 'react'
import { useAuth } from '../lib/auth'
import TimelineView from '../components/TimelineView'

const STATE_COLORS = {
  in_progress: 'var(--emerald)',
  completed: 'var(--text-muted)',
  force_submitted: 'var(--text-muted)',
  terminated: 'var(--red)',
}

export default function SupportConsole() {
  const { authFetch } = useAuth()
  const [sessions, setSessions] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [timelineSession, setTimelineSession] = useState(null)
  const [terminating, setTerminating] = useState(null)
  const [search, setSearch] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await authFetch('/api/v1/admin/live-monitor')
      if (r.ok) {
        const d = await r.json()
        setSessions(d.sessions || [])
        setTotal(d.total || 0)
      } else {
        const d = await r.json().catch(() => ({}))
        setError(d.detail || `HTTP ${r.status}`)
      }
    } catch (e) {
      setError(e.message)
    } finally { setLoading(false) }
  }, [authFetch])

  useEffect(() => { load(); const id = setInterval(load, 30000); return () => clearInterval(id) }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const terminate = async (sessionId) => {
    if (!window.confirm('Force-terminate this session? The student will be disconnected.')) return
    setTerminating(sessionId)
    try {
      await authFetch(`/api/v1/admin/sessions/${encodeURIComponent(sessionId)}/terminate`, { method: 'POST' })
      setTerminating(null)
      load()
    } catch (_) { setTerminating(null) }
  }

  const filtered = sessions.filter(s =>
    !search || s.roll_number?.toLowerCase().includes(search) || s.full_name?.toLowerCase().includes(search) || (s.session_key || '').toLowerCase().includes(search)
  )

  return (
    <div className="panel">
      <div className="panel-header">
        <h2>Support Console</h2>
        <p className="panel-sub">Live monitor of all active exam sessions across the organization.</p>
        <div className="bar-row" style={{ gap: 8, marginTop: 8 }}>
          <input className="input" style={{ flex: 1, maxWidth: 320 }} placeholder="Search by name, roll, or session ID..." value={search} onChange={e => setSearch(e.target.value.toLowerCase())} />
          <span style={{ fontSize: 12, color: 'var(--muted)', whiteSpace: 'nowrap' }}>{total} active sessions</span>
          <button className="btn btn-sm btn-secondary" onClick={load} disabled={loading}>{loading ? 'Loading...' : 'Refresh'}</button>
        </div>
      </div>

      {error && <div style={{ color: 'var(--red)', padding: 16 }}>{error}</div>}

      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        {loading && !sessions.length ? <div className="panel-loading">Loading active sessions...</div> : filtered.length === 0 ? (
          <div className="panel-empty"><p>{search ? 'No sessions match your search.' : 'No active sessions.'}</p></div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table className="dtable">
              <thead>
                <tr>
                  <th>Student</th>
                  <th>Roll</th>
                  <th>Session ID</th>
                  <th>Started</th>
                  <th>Risk</th>
                  <th>Latest Event</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map(s => (
                  <tr key={s.session_key}>
                    <td style={{ maxWidth: 140, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.full_name || '—'}</td>
                    <td style={{ fontFamily: 'monospace', fontSize: 12 }}>{s.roll_number || '—'}</td>
                    <td style={{ fontFamily: 'monospace', fontSize: 11, color: 'var(--muted)' }}>{s.session_key?.slice(0, 20)}</td>
                    <td style={{ whiteSpace: 'nowrap', fontSize: 12 }}>{s.started_at ? new Date(s.started_at).toLocaleString() : '—'}</td>
                    <td style={{ textAlign: 'center' }}>
                      <span className={`badge ${(s.risk_score || 0) > 40 ? 'badge-red' : (s.risk_score || 0) > 15 ? 'badge-amber' : 'badge-green'}`}>
                        {s.risk_score != null ? s.risk_score : '—'}
                      </span>
                    </td>
                    <td style={{ fontSize: 12, color: 'var(--muted)' }}>{s.latest_violation?.replace(/_/g, ' ') || '—'}</td>
                    <td style={{ whiteSpace: 'nowrap' }}>
                      <button className="btn btn-sm btn-secondary" style={{ padding: '4px 8px', fontSize: 11 }} onClick={() => setTimelineSession(s.session_key)}>Timeline</button>
                      <button className="btn btn-sm btn-secondary" style={{ padding: '4px 8px', fontSize: 11, marginLeft: 4, color: 'var(--red)' }}
                        disabled={terminating === s.session_key} onClick={() => terminate(s.session_key)}>
                        {terminating === s.session_key ? '...' : 'Terminate'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {timelineSession && <TimelineView sessionId={timelineSession} onClose={() => setTimelineSession(null)} />}
    </div>
  )
}
