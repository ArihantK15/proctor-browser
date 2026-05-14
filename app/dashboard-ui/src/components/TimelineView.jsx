import { useState, useEffect, useCallback } from 'react'
import { useAuth } from '../lib/auth'

const SEV_COLORS = {
  high: 'var(--red)', medium: 'var(--amber)', low: 'var(--emerald)',
}

export default function TimelineView({ sessionId, onClose }) {
  const { authFetch } = useAuth()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await authFetch(`/api/v1/admin/timeline/${encodeURIComponent(sessionId)}`)
      if (r.ok) {
        setData(await r.json())
      } else {
        const d = await r.json().catch(() => ({}))
        setError(d.detail || `HTTP ${r.status}`)
      }
    } catch (e) {
      setError(e.message)
    } finally { setLoading(false) }
  }, [sessionId, authFetch])

  useEffect(() => { load() }, [load])
  if (!sessionId) return null

  return (
    <div className="modal-overlay" style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(0,0,0,.5)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div className="card" style={{ maxWidth: 800, width: '90%', maxHeight: '85vh', overflow: 'auto', padding: 24 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <h3 style={{ margin: 0 }}>
            Evidence Timeline
            {data && <span style={{ fontWeight: 400, fontSize: 13, color: 'var(--muted)', marginLeft: 8 }}>
              {data.roll_number || ''} &middot; {data.full_name || ''}
            </span>}
          </h3>
          <button className="btn btn-sm btn-secondary" onClick={onClose}>Close</button>
        </div>

        {loading && <div className="panel-loading">Loading timeline...</div>}
        {error && <div style={{ color: 'var(--red)', padding: 16 }}>{error}</div>}

        {data && (
          <>
            {/* Summary cards */}
            <div className="stats-row" style={{ marginBottom: 20 }}>
              <div className="stat-card">
                <span className="stat-value" style={data.risk_score > 40 ? { color: 'var(--red)' } : data.risk_score > 15 ? { color: 'var(--amber)' } : { color: 'var(--emerald)' }}>
                  {data.risk_score != null ? data.risk_score : '—'}
                </span>
                <span className="stat-label">Risk Score</span>
              </div>
              <div className="stat-card">
                <span className="stat-value">{data.total_events || 0}</span>
                <span className="stat-label">Events</span>
              </div>
              <div className="stat-card">
                <span className="stat-value">{data.score != null ? data.score + '/' + data.total : '—'}</span>
                <span className="stat-label">Score</span>
              </div>
              <div className="stat-card">
                <span className="stat-value" style={{ fontSize: 12 }}>{data.status || '—'}</span>
                <span className="stat-label">Status</span>
              </div>
            </div>

            {/* Timeline */}
            <h4 style={{ marginBottom: 12, fontSize: 13, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.05em' }}>
              Event timeline
            </h4>
            <div style={{ position: 'relative', paddingLeft: 24 }}>
              <div style={{ position: 'absolute', left: 10, top: 0, bottom: 0, width: 2, background: 'var(--border)' }} />
              {(data.timeline || []).map((ev, i) => {
                const isViolation = ev.is_violation || ev.severity === 'high' || ev.severity === 'medium'
                const color = SEV_COLORS[ev.severity] || 'var(--muted)'
                return (
                  <div key={ev.id || i} style={{ position: 'relative', marginBottom: 12, cursor: 'pointer' }}
                    onClick={() => setExpanded(expanded === i ? null : i)}>
                    <div style={{ position: 'absolute', left: -17, top: 4, width: 12, height: 12, borderRadius: '50%', background: color, border: '2px solid var(--surface)' }} />
                    <div className="card" style={{ padding: '10px 14px', borderLeft: `3px solid ${color}`, margin: 0 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                        <span style={{ fontWeight: 500, fontSize: 13 }}>
                          {isViolation ? '\u26A0\uFE0F ' : ''}{ev.type || ev.event_type || 'event'}
                        </span>
                        <span style={{ fontSize: 11, color: 'var(--muted)', whiteSpace: 'nowrap' }}>
                          {ev.timestamp || ev.raw_ts ? new Date(ev.timestamp || ev.raw_ts).toLocaleTimeString() : ''}
                        </span>
                      </div>
                      {ev.details && <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 4 }}>{ev.details}</div>}
                      {expanded === i && ev.screenshot && (
                        <div style={{ marginTop: 8 }}>
                          <img src={ev.screenshot} alt="Evidence" style={{ maxWidth: '100%', maxHeight: 240, borderRadius: 8, border: '1px solid var(--border)' }} />
                        </div>
                      )}
                    </div>
                  </div>
                )
              })}
              {(!data.timeline || data.timeline.length === 0) && (
                <div style={{ padding: 24, textAlign: 'center', color: 'var(--muted)', fontSize: 13 }}>No events recorded for this session.</div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
