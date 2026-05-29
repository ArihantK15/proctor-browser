import { useState, useEffect, useCallback } from 'react'
import { useAuth } from '../lib/auth'

const SEV_COLORS = {
  high: 'var(--red)', medium: 'var(--amber)', low: 'var(--emerald)',
}
const SEV_LABELS = {
  high: 'High confidence', medium: 'Medium confidence', low: 'Low confidence',
}
const REASON_MAP = {
  face_missing: 'Camera did not detect a face in frame. Possible causes: student stepped away, camera obstruction.',
  multiple_faces: 'More than one face detected in camera frame.',
  gaze_away: 'Student\'s gaze was directed away from the screen for an extended period.',
  head_turned: 'Student\'s head was turned away from the camera.',
  vpn_detected: 'VPN or proxy connection detected on the device.',
  vm_detected: 'Virtual machine environment detected.',
  remote_desktop_detected: 'Remote desktop software detected.',
  debugger_detected: 'Browser developer tools or debugger detected.',
  phone_consulting: 'Phone or secondary device detected in frame.',
  voice_detected: 'Sustained voice or conversation detected.',
  window_focus_lost: 'Exam window lost focus — student may have switched applications.',
  tab_hidden: 'Browser tab was hidden or backgrounded.',
  time_exceeded: 'Exam was submitted after the allotted time window.',
  multiple_monitors: 'Multiple displays detected during exam.',
  proxy_detected: 'Proxy server detected.',
  calibration_abort: 'Gaze calibration was interrupted or aborted.',
  cheating_device: 'Unauthorized device detected.',
}

function _reasonHint(violationType, details) {
  if (details) return details
  if (REASON_MAP[violationType]) return REASON_MAP[violationType]
  if (violationType) return `Flag type: ${violationType}. Review the evidence screenshot for details.`
  return ''
}

export default function TimelineView({ sessionId, onClose }) {
  const { authFetch } = useAuth()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState(null)
  const [exporting, setExporting] = useState(false)

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

  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps
  if (!sessionId) return null

  const handleExport = async () => {
    setExporting(true)
    try {
      const r = await authFetch(`/api/v1/admin/timeline/${encodeURIComponent(sessionId)}`)
      if (r.ok) {
        const d = await r.json()
        const blob = new Blob([JSON.stringify(d, null, 2)], { type: 'application/json' })
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url; a.download = `audit-${sessionId.slice(0, 12)}.json`; a.click()
        URL.revokeObjectURL(url)
      }
    } catch (_) {} finally { setExporting(false) }
  }

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
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn btn-sm btn-secondary" disabled={exporting} onClick={handleExport}>
              {exporting ? 'Exporting...' : 'Export Audit Packet'}
            </button>
            <button className="btn btn-sm btn-secondary" onClick={onClose}>Close</button>
          </div>
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
              Event timeline &mdash; click any event for evidence screenshot &amp; explainer
            </h4>
            <div style={{ position: 'relative', paddingLeft: 24 }}>
              <div style={{ position: 'absolute', left: 10, top: 0, bottom: 0, width: 2, background: 'var(--border)' }} />
              {(data.timeline || []).map((ev, i) => {
                const isViolation = ev.is_violation || ev.severity === 'high' || ev.severity === 'medium'
                const color = SEV_COLORS[ev.severity] || 'var(--muted)'
                const reason = _reasonHint(ev.type || ev.event_type, ev.details)
                return (
                  <div key={ev.id || i} style={{ position: 'relative', marginBottom: 12, cursor: 'pointer' }}
                    onClick={() => setExpanded(expanded === i ? null : i)}>
                    <div style={{ position: 'absolute', left: -17, top: 4, width: 12, height: 12, borderRadius: '50%', background: color, border: '2px solid var(--surface)' }} />
                    <div className="card" style={{ padding: '10px 14px', borderLeft: `3px solid ${color}`, margin: 0 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
                        <div style={{ flex: 1 }}>
                          <span style={{ fontWeight: 500, fontSize: 13 }}>
                            {isViolation ? '\u26A0\uFE0F ' : ''}{ev.type || ev.event_type || 'event'}
                          </span>
                          {ev.severity && (
                            <span className={`badge ${ev.severity === 'high' ? 'badge-red' : ev.severity === 'medium' ? 'badge-amber' : 'badge-green'}`}
                              style={{ marginLeft: 8, fontSize: 10, verticalAlign: 'middle' }}>
                              {SEV_LABELS[ev.severity] || ev.severity}
                            </span>
                          )}
                          {ev.detection_confidence != null && (
                            <span style={{ marginLeft: 8, fontSize: 10, color: 'var(--muted)', verticalAlign: 'middle' }}>
                              {(ev.detection_confidence * 100).toFixed(0)}% confidence
                            </span>
                          )}
                        </div>
                        <span style={{ fontSize: 11, color: 'var(--muted)', whiteSpace: 'nowrap' }}>
                          {ev.timestamp || ev.raw_ts ? new Date(ev.timestamp || ev.raw_ts).toLocaleTimeString() : ''}
                        </span>
                      </div>

                      {/* Details shown always as the "why" explainer */}
                      {reason && <div style={{ fontSize: 12, color: 'var(--text)', marginTop: 4, lineHeight: 1.4 }}>{reason}</div>}

                      {/* Expanded view: evidence screenshot */}
                      {expanded === i && ev.screenshot && (
                        <div style={{ marginTop: 8 }}>
                          <img src={ev.screenshot} alt="Evidence" style={{ maxWidth: '100%', maxHeight: 240, borderRadius: 8, border: '1px solid var(--border)' }} />
                        </div>
                      )}
                      {expanded === i && !ev.screenshot && (
                        <div style={{ marginTop: 8, padding: 12, background: 'var(--surface)', borderRadius: 8, fontSize: 12, color: 'var(--muted)' }}>
                          No evidence screenshot captured for this event. Violation data was recorded server-side.
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
