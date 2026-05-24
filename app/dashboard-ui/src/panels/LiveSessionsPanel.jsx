import { useState, useEffect, useRef } from 'react'
import { fetchWithTimeout, useAuth } from '../lib/auth'
import { API_BASE } from '../config'

const PAGE_SIZE = 50

export default function LiveSessionsPanel({ currentExamId }) {
  const { authFetch } = useAuth()
  const [sessions, setSessions] = useState([])
  const [search, setSearch] = useState('')
  const [sevFilter, setSevFilter] = useState('all')
  const [sortKey, setSortKey] = useState('risk_score')
  const [sortAsc, setSortAsc] = useState(false)
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState(1)
  const [liveViewSid, setLiveViewSid] = useState(null)
  const [liveViewFrame, setLiveViewFrame] = useState(null)
  const [liveViewStatus, setLiveViewStatus] = useState('Connecting')
  const [streamStatus, setStreamStatus] = useState('')
  const sseRef = useRef(null)

  useEffect(() => {
    connectSSE()
    return () => { if (sseRef.current) sseRef.current.close() }
  }, [])

  const connectSSE = async () => {
    const token = localStorage.getItem('procta_token')
    if (!token) return
    try {
      const ctr = await fetchWithTimeout(`${API_BASE}/sse/connect-token`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!ctr.ok) {
        setStreamStatus(`Live updates unavailable (${ctr.status}). Use Refresh for current data.`)
        return
      }
      const { connect_token } = await ctr.json()
      const es = new EventSource(`${API_BASE}/sse/sessions?token=${encodeURIComponent(connect_token)}`)
      es.addEventListener('init', (e) => {
        try {
          const d = JSON.parse(e.data)
          setSessions(d.sessions || [])
        } catch (_) { setStreamStatus('Live update payload was unreadable. Use Refresh for current data.') }
      })
      es.addEventListener('update', (e) => {
        try {
          const d = JSON.parse(e.data)
          if (d.session_id) {
            setSessions(prev => prev.map(s => s.session_id === d.session_id ? { ...s, ...d } : s))
          }
        } catch (_) { setStreamStatus('Live update payload was unreadable. Use Refresh for current data.') }
      })
      es.addEventListener('refresh', () => { loadSessions() })
      es.onerror = () => { setStreamStatus('Live updates disconnected. Use Refresh while reconnecting.') }
      sseRef.current = es
    } catch (_) { setStreamStatus('Live updates unavailable. Use Refresh for current data.') }
  }

  const [error, setError] = useState('')

  const loadSessions = async () => {
    setError('')
    try {
      const r = await authFetch(`${API_BASE}/sessions${currentExamId ? `?exam_id=${encodeURIComponent(currentExamId)}` : ''}`)
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || `Failed to load sessions (${r.status})`)
      }
      setSessions((await r.json()).sessions || [])
    } catch (e) {
      setError(e.message || 'Failed to load sessions')
    } finally { setLoading(false) }
  }

  const openLiveView = async (sid) => {
    setLiveViewSid(sid)
    setLiveViewFrame(null)
    setLiveViewStatus('Connecting')
    try {
      await authFetch(`${API_BASE}/admin/sessions/${encodeURIComponent(sid)}/live-view/start`, { method: 'POST' })
      setLiveViewStatus('Live')
      const poll = setInterval(async () => {
        try {
          const r = await fetchWithTimeout(`${API_BASE}/admin/sessions/${encodeURIComponent(sid)}/live-frame?t=${Date.now()}`, {
            headers: { Authorization: `Bearer ${localStorage.getItem('procta_token')}` },
          })
          if (r.ok) {
            const blob = await r.blob()
            setLiveViewFrame(URL.createObjectURL(blob))
            setLiveViewStatus('Live')
          }
        } catch (_) { setLiveViewStatus('Offline') }
      }, 1500)
      // Store interval for cleanup
      window._liveViewInterval = poll
    } catch (_) { setLiveViewStatus('Failed') }
  }

  const closeLiveView = () => {
    if (window._liveViewInterval) { clearInterval(window._liveViewInterval); window._liveViewInterval = null }
    if (liveViewSid) {
      authFetch(`${API_BASE}/admin/sessions/${encodeURIComponent(liveViewSid)}/live-view/stop`, { method: 'POST' })
        .catch(() => setStreamStatus('Live view stopped locally, but the server stop request failed.'))
    }
    setLiveViewSid(null)
    setLiveViewFrame(null)
  }

  const filtered = sessions
    .filter(s => !search || s.session_id?.toLowerCase().includes(search) || s.last_event?.toLowerCase().includes(search))
    .filter(s => sevFilter === 'all' || (s.last_severity || '').toLowerCase() === sevFilter)
    .sort((a, b) => {
      let va = a[sortKey], vb = b[sortKey]
      if (va == null) va = ''
      if (vb == null) vb = ''
      return sortAsc ? va - vb : vb - va
    })

  const displayed = filtered.slice(0, page * PAGE_SIZE)
  const hasMore = displayed.length < filtered.length

  const sevColor = (sev) => sev === 'high' ? 'var(--red)' : sev === 'medium' ? 'var(--amber)' : 'var(--muted)'

  if (loading) return <div className="loading" style={{ textAlign: 'center', padding: 40 }}>Loading sessions...</div>

  return (
    <div className="live-layout" style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 200px)' }}>
      {error && <div className="auth-err" style={{ marginBottom: 12 }}>{error} <button className="btn-link" onClick={loadSessions} style={{ marginLeft: 8 }}>Retry</button></div>}
      {streamStatus && <div className="auth-err" style={{ marginBottom: 12 }}>{streamStatus}</div>}
      {/* Stats bar */}
      <div className="stats-bar" style={{ marginBottom: 14 }}>
        {[
          { label: 'Active', value: sessions.filter(s => s.live_state === 'live').length },
          { label: 'High Risk', value: sessions.filter(s => s.risk_score > 70).length },
          { label: 'Total', value: sessions.length },
        ].map(s => (
          <div className="stat-tile" key={s.label}>
            <div className="stat-tile-label">{s.label}</div>
            <div className="stat-tile-value">{s.value}</div>
          </div>
        ))}
      </div>

      {/* Toolbar */}
      <div className="table-toolbar" style={{ marginBottom: 10 }}>
        <div className="search-wrap">
          <span className="search-icon">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
          </span>
          <input className="search-input" placeholder="Search sessions…" value={search} onChange={(e) => setSearch(e.target.value.toLowerCase())} />
        </div>
        <select className="filter-select" value={sevFilter} onChange={(e) => setSevFilter(e.target.value)} style={{ marginLeft: 8 }}>
          <option value="all">All severity</option>
          <option value="critical">Critical</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </select>
        <button className="btn btn-secondary btn-sm" onClick={loadSessions} style={{ marginLeft: 8 }}>Refresh</button>
      </div>

      {loading && <div className="loading" style={{ textAlign: 'center', padding: 40 }}>Loading...</div>}

      {/* Sessions table */}
      {!loading && (
        <div className="table-wrap" style={{ flex: 1, overflow: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr style={{ background: 'var(--surface-1)', borderBottom: '1px solid var(--border-subtle)' }}>
                {['Session', 'Event', 'Severity', 'Risk', 'Cal', 'Last Seen', 'Status', 'Actions'].map(h => (
                  <th key={h} style={{ padding: '10px 12px', textAlign: 'left', fontWeight: 600, color: 'var(--text-muted)', fontSize: 11, textTransform: 'uppercase', whiteSpace: 'nowrap', position: 'sticky', top: 0, background: 'var(--surface-1)', zIndex: 2 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {displayed.map(s => {
                const sid = s.session_id || ''
                const roll = sid.split('_')[0]
                return (
                  <tr key={sid} style={{
                    borderBottom: '1px solid var(--border-subtle)',
                    boxShadow: s.live_state === 'live' && s.last_severity === 'critical' ? 'inset 3px 0 0 var(--sev-critical-fg)' :
                               s.live_state === 'live' && s.last_severity === 'high' ? 'inset 3px 0 0 var(--sev-error-fg)' :
                               s.live_state === 'live' && s.last_severity === 'medium' ? 'inset 3px 0 0 var(--sev-warn-fg)' : undefined,
                  }}>
                    <td style={{ padding: '10px 12px' }}><strong>{roll}</strong><br /><span style={{ fontSize: 10, color: 'var(--muted)' }}>{sid.substring(0, 36)}</span></td>
                    <td style={{ padding: '10px 12px' }}>{s.last_event?.replace(/_/g, ' ')}</td>
                    <td style={{ padding: '10px 12px', color: sevColor(s.last_severity), fontWeight: 600 }}>{s.last_severity?.toUpperCase()}</td>
                    <td style={{ padding: '10px 12px', color: s.risk_score > 70 ? 'var(--red)' : s.risk_score > 40 ? 'var(--amber)' : s.risk_score > 15 ? '#58a6ff' : 'var(--emerald)', fontWeight: 600 }}>{s.risk_score != null ? `${s.risk_score}/100` : '—'}</td>
                    <td style={{ padding: '10px 12px' }}>{s.calibration?.tier === 'tight' ? '⚠️ TIGHT' : s.calibration?.tier === 'loose' ? 'LOOSE' : '—'}</td>
                    <td style={{ padding: '10px 12px', fontSize: 12 }}>{s.last_seen}</td>
                    <td style={{ padding: '10px 12px' }}>
                      <span className={`status-badge ${s.live_state === 'live' ? 'status-live' : s.live_state === 'stale' ? 'status-stale' : ''}`}>
                        {s.live_state === 'live' ? 'Live' : s.live_state === 'stale' ? 'Stale' : s.live_state === 'submitted' ? 'Submitted' : '—'}
                      </span>
                    </td>
                    <td style={{ padding: '10px 12px' }}>
                      {s.live_state === 'live' && (
                        <button className="btn btn-secondary btn-sm" style={{ padding: '4px 8px', fontSize: 10 }} onClick={() => openLiveView(sid)}>Camera</button>
                      )}
                    </td>
                  </tr>
                )
              })}
              {filtered.length === 0 && (
                <tr><td colSpan="8" style={{ textAlign: 'center', padding: 40, color: 'var(--text-muted)' }}>
                  {search || sevFilter !== 'all'
                    ? 'No live sessions matching filters.'
                    : <span>No live sessions yet. <a href="#tools" style={{ color: 'var(--blue)', cursor: 'pointer' }} onClick={e => { e.preventDefault(); window.location.hash = 'tools'; }}>Share your exam link</a> to get students started.</span>}
                </td></tr>
              )}
            </tbody>
          </table>
        {hasMore && (
          <div style={{ textAlign: 'center', padding: 12 }}>
            <button className="btn btn-secondary btn-sm" onClick={() => setPage(p => p + 1)}>
              Load more ({filtered.length - displayed.length} remaining)
            </button>
          </div>
        )}
        </div>
      )}

      {/* Live view modal */}
      {liveViewSid && (
        <div className="modal-bg" style={{ position: 'fixed', inset: 0, zIndex: 100, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.7)' }} onClick={(e) => { if (e.target === e.currentTarget) closeLiveView() }}>
          <div className="modal" style={{ maxWidth: 560, width: '90%' }}>
            <button className="close" onClick={closeLiveView}>&times;</button>
            <h3 style={{ marginBottom: 4 }}>Live Camera</h3>
            <div style={{ fontSize: 11, color: 'var(--muted)', fontFamily: 'var(--font-mono)', marginBottom: 14 }}>{liveViewSid}</div>
            <div style={{ background: '#000', borderRadius: 8, overflow: 'hidden', aspectRatio: '4/3', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              {liveViewFrame ? (
                <img src={liveViewFrame} alt="Live feed" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
              ) : (
                <div style={{ color: 'var(--muted)', fontSize: 13, textAlign: 'center', padding: 20 }}>
                  {liveViewStatus === 'Live' ? 'Loading frame...' : `${liveViewStatus}...`}
                </div>
              )}
            </div>
            <div style={{ marginTop: 10, display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 11, color: 'var(--muted)' }}>
              <span>● {liveViewStatus}</span>
            </div>
            <div style={{ marginTop: 14, display: 'flex', gap: 10 }}>
              <button className="btn btn-secondary btn-sm" onClick={closeLiveView} style={{ flex: 1 }}>Stop & Close</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
