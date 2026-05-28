import { useState, useEffect, useRef } from 'react'
import { fetchWithTimeout, useAuth } from '../lib/auth'
import { API_BASE } from '../config'

const PAGE_SIZE = 50

// Notifications API helper for tab-hidden violation alerts.
// We rate-limit to one notification per session per 60s so a noisy
// session can't spam the OS notification center.
const _lastNotifyAt = new Map()
function _maybeNotifyTabHiddenViolation(evt) {
  if (typeof window === 'undefined' || !('Notification' in window)) return
  if (Notification.permission !== 'granted') return
  const sid = evt?.session_id
  if (!sid) return
  const now = Date.now()
  const last = _lastNotifyAt.get(sid) || 0
  if (now - last < 60_000) return
  _lastNotifyAt.set(sid, now)
  // Trim caches with a hard cap so a long session can't accumulate
  // thousands of session_id keys in the map.
  if (_lastNotifyAt.size > 500) {
    const cutoff = now - 5 * 60_000
    for (const [k, t] of _lastNotifyAt) {
      if (t < cutoff) _lastNotifyAt.delete(k)
    }
  }
  try {
    const title = `Procta — violation flagged`
    const body = [
      evt.event_type ? `Type: ${evt.event_type}` : null,
      evt.full_name || evt.roll_number ? `Student: ${evt.full_name || evt.roll_number}` : null,
      evt.risk_score != null ? `Risk: ${evt.risk_score}` : null,
    ].filter(Boolean).join(' · ')
    const n = new Notification(title, {
      body: body || 'Open the Live tab for details.',
      icon: '/favicon-48.png',
      tag: `procta-violation-${sid}`,  // collapses repeats per session
      renotify: false,
      silent: false,
    })
    n.onclick = () => {
      try { window.focus() } catch (_) { /* noop */ }
      n.close()
    }
  } catch (_) {
    // Notification ctor can throw on Safari mobile / locked-down kiosks.
    // Silent fall-through is fine — feature is opt-in enhancement.
  }
}

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
  // Cam-pop-in optimisation (audit #9): when a high-severity violation
  // arrives, we eagerly fetch the cached low-rate frame and stash it as
  // an inline thumbnail keyed by session_id. The thumbnail shows in the
  // row, and clicking the row opens the modal with the frame ALREADY
  // rendered (no 1-second poll wait). Brings cam-pop-in from ~3 s to
  // under 1 s when a frame is cached.
  const [thumbnails, setThumbnails] = useState({})           // { sid: dataUrl }
  const prewarmedSids = useRef(new Set())                    // dedupe per session
  const sseRef = useRef(null)
  // Live-view modal polling: holds the setInterval id + the most-recent
  // blob: URL so the next tick can revoke the prior frame's URL before
  // creating a new one. Without this the poll leaked one blob: URL
  // every 1.5 s (3500-session-scale memory ramp = serious).
  // Also avoids the previous `window._liveViewInterval` antipattern
  // which made the panel unsafe to mount twice.
  const livePollRef = useRef(null)
  const liveBlobUrlRef = useRef(null)

  useEffect(() => {
    connectSSE()
    // Best-effort Notifications API prompt for tab-hidden violation
    // alerts. Browsers only show the prompt if permission state is
    // 'default' (never asked before); subsequent visits are silent.
    // Some browsers (Safari, Firefox in some configs) require a user
    // gesture and will silently no-op the request here — that's fine,
    // the user can still grant via the site-settings menu later.
    if (typeof window !== 'undefined' && 'Notification' in window &&
        Notification.permission === 'default') {
      try { Notification.requestPermission().catch(() => {}) } catch (_) { /* noop */ }
    }
    return () => {
      if (sseRef.current) sseRef.current.close()
      // Tear down the live-view modal poll if the panel unmounts while
      // a session is open (teacher navigates away mid-watch). Without
      // this the setInterval keeps firing forever, leaking both the
      // interval and a fresh blob URL every 1.5 s.
      if (livePollRef.current) { clearInterval(livePollRef.current); livePollRef.current = null }
      if (liveBlobUrlRef.current && liveBlobUrlRef.current.startsWith('blob:')) {
        try { URL.revokeObjectURL(liveBlobUrlRef.current) } catch (_) { /* noop */ }
        liveBlobUrlRef.current = null
      }
      // Revoke any pre-warmed blob: URLs to avoid leaking memory when
      // the user navigates away from the Live tab.
      setThumbnails(prev => {
        for (const url of Object.values(prev)) {
          if (typeof url === 'string' && url.startsWith('blob:')) {
            try { URL.revokeObjectURL(url) } catch (_) { /* noop */ }
          }
        }
        return {}
      })
    }
  }, [])

  const connectSSE = async () => {
    try {
      const ctr = await authFetch(`${API_BASE}/sse/connect-token`, { method: 'POST' })
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
          // Tab-closed alerting: when the dashboard tab is hidden and a
          // violation arrives, fire a Desktop Notification so the teacher
          // sees it on the OS-level even if their browser is in a different
          // tab or behind another window. No bell sound (browsers throttle
          // notification audio anyway). Permission is requested on first
          // user gesture in the panel; we no-op silently if denied.
          if (d.kind === 'violation' && typeof document !== 'undefined' && document.hidden) {
            _maybeNotifyTabHiddenViolation(d)
          }
          // Cam-pop-in (audit #9): on any violation, eagerly pull the
          // cached frame so a teacher's first click on the session
          // row renders instantly instead of waiting on the 1 s poll
          // cycle. Gated on (sid not already pre-warmed) to avoid
          // hammering the live-frame endpoint when a single session
          // floods events.
          if (d.kind === 'violation' && d.session_id) {
            prewarmThumbnail(d.session_id, d.risk_score)
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
      // Backend exposes /api/v1/admin/sessions (admin_sessions.py:36);
      // /api/v1/sessions was a stale path from before the admin/ prefix
      // landed. Pass page_size=200 so org admins viewing the whole org
      // don't get truncated at the server-side default of 50.
      const qs = new URLSearchParams()
      if (currentExamId) qs.set('exam_id', currentExamId)
      qs.set('page_size', '200')
      const r = await authFetch(`${API_BASE}/admin/sessions?${qs}`)
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || `Failed to load sessions (${r.status})`)
      }
      setSessions((await r.json()).sessions || [])
    } catch (e) {
      setError(e.message || 'Failed to load sessions')
    } finally { setLoading(false) }
  }

  // Pre-warm thumbnail for a session that just flagged a violation.
  // Idempotent + rate-limited per-sid via prewarmedSids set.
  // No await on the response in callers — fire-and-forget.
  const prewarmThumbnail = async (sid, riskScore) => {
    if (!sid || prewarmedSids.current.has(sid)) return
    prewarmedSids.current.add(sid)
    // Cap memory: if we've pre-warmed > 500 sids in one session, drop
    // the oldest half. Stops a multi-hour heavy session leaking refs.
    if (prewarmedSids.current.size > 500) {
      const arr = Array.from(prewarmedSids.current).slice(-250)
      prewarmedSids.current = new Set(arr)
    }
    try {
      // Hit the existing live-frame endpoint — it returns the cached
      // low-rate frame even when no live-view is active (10 s TTL).
      // If the cache is empty we get 204 and silently skip.
      const r = await fetchWithTimeout(
        `${API_BASE}/admin/sessions/${encodeURIComponent(sid)}/live-frame?t=${Date.now()}`,
        { credentials: 'include' },
      )
      if (!r.ok || r.status === 204) return
      const blob = await r.blob()
      if (!blob || blob.size < 256) return  // empty / corrupt
      const url = URL.createObjectURL(blob)
      setThumbnails(prev => {
        // Revoke any prior URL for this sid to avoid leaking blob handles.
        const prior = prev[sid]
        if (prior && prior.startsWith('blob:')) {
          try { URL.revokeObjectURL(prior) } catch (_) { /* noop */ }
        }
        return { ...prev, [sid]: url }
      })
      // Auto-pre-warm live-view-start for HIGH-risk violations only
      // (score > 60). Pushes the student client into high-rate uploads
      // BEFORE the teacher clicks, so the first frame in the modal is
      // already current when they open it. Gated by score so a noisy
      // low-risk session doesn't trigger the entire org's clients into
      // high-rate mode.
      if (typeof riskScore === 'number' && riskScore > 60) {
        try {
          await authFetch(
            `${API_BASE}/admin/sessions/${encodeURIComponent(sid)}/live-view/start`,
            { method: 'POST' },
          )
        } catch (_) { /* best-effort prewarm; ignore */ }
      }
    } catch (_) { /* network blip; the next click will refetch */ }
  }

  const openLiveView = async (sid) => {
    setLiveViewSid(sid)
    // If we pre-warmed a thumbnail, show it instantly while the
    // full-rate poll spins up. Otherwise fall back to the "Connecting"
    // state until the first frame arrives.
    const cached = thumbnails[sid]
    setLiveViewFrame(cached || null)
    setLiveViewStatus(cached ? 'Live (pre-warmed)' : 'Connecting')
    try {
      await authFetch(`${API_BASE}/admin/sessions/${encodeURIComponent(sid)}/live-view/start`, { method: 'POST' })
      setLiveViewStatus('Live')
      // Clear any prior interval before starting a new one (e.g. teacher
      // clicks "Camera" on session A, then on session B without closing
      // A's modal). Without this we'd leak intervals.
      if (livePollRef.current) clearInterval(livePollRef.current)
      const poll = setInterval(async () => {
        try {
          const r = await fetchWithTimeout(`${API_BASE}/admin/sessions/${encodeURIComponent(sid)}/live-frame?t=${Date.now()}`, {
            credentials: 'include',
          })
          if (r.ok) {
            const blob = await r.blob()
            const newUrl = URL.createObjectURL(blob)
            // Revoke the prior tick's URL before swapping in the new
            // one. The leak fix: at 1.5 s/tick over a 90 min exam we
            // were piling up ~3,600 dangling blob handles per teacher
            // before this commit.
            const prior = liveBlobUrlRef.current
            liveBlobUrlRef.current = newUrl
            setLiveViewFrame(newUrl)
            setLiveViewStatus('Live')
            if (prior && prior.startsWith('blob:')) {
              try { URL.revokeObjectURL(prior) } catch (_) { /* noop */ }
            }
          }
        } catch (_) { setLiveViewStatus('Offline') }
      }, 1500)
      livePollRef.current = poll
    } catch (_) { setLiveViewStatus('Failed') }
  }

  const forceSubmitSession = async (sid) => {
    if (!confirm(`Force-submit session ${sid.substring(0, 20)}…? This will end the student's exam.`)) return
    const password = prompt('Enter your password to confirm force-submit:')
    if (!password) return
    try {
      const reauthR = await authFetch(`${API_BASE}/auth/reauth`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      })
      if (!reauthR.ok) { alert('Reauthentication failed — wrong password?'); return }
      const { reauth_token } = await reauthR.json()
      const r = await authFetch(`${API_BASE}/admin-submit/${encodeURIComponent(sid)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reauth_token }),
      })
      if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.detail || `HTTP ${r.status}`) }
      const d = await r.json()
      alert(`Force-submitted! Score: ${d.score}/${d.total}, Risk: ${d.risk_score}/100`)
      loadSessions()
    } catch (e) { alert(`Force submit failed: ${e.message}`) }
  }

  const closeLiveView = () => {
    if (livePollRef.current) { clearInterval(livePollRef.current); livePollRef.current = null }
    // Revoke the last frame URL on close to release the final blob.
    if (liveBlobUrlRef.current && liveBlobUrlRef.current.startsWith('blob:')) {
      try { URL.revokeObjectURL(liveBlobUrlRef.current) } catch (_) { /* noop */ }
      liveBlobUrlRef.current = null
    }
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
                    <td style={{ padding: '10px 12px' }}>
                      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                        {/* Pre-warmed thumbnail (audit #9). When a violation
                            fires, we pre-fetch the cached frame and render
                            it here so the teacher gets an at-a-glance view
                            of what was happening WITHOUT having to click
                            "Camera". Click the thumbnail to pop into the
                            full live view in <1 s (frame already cached
                            and modal opens instant). */}
                        {thumbnails[sid] && (
                          <img
                            src={thumbnails[sid]}
                            alt={`Live preview of ${roll}`}
                            onClick={() => openLiveView(sid)}
                            style={{
                              width: 56, height: 42, objectFit: 'cover',
                              borderRadius: 4, cursor: 'pointer',
                              border: '1px solid var(--border-subtle)',
                              flexShrink: 0,
                            }}
                            title="Click to open full live view"
                          />
                        )}
                        <div>
                          <strong>{roll}</strong>
                          <br />
                          <span style={{ fontSize: 10, color: 'var(--muted)' }}>{sid.substring(0, 36)}</span>
                        </div>
                      </div>
                    </td>
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
                      {s.live_state === 'stale' && (
                        <button className="btn btn-ghost btn-sm" style={{ padding: '4px 8px', fontSize: 10, color: 'var(--red)' }} onClick={() => forceSubmitSession(sid)}>Force Submit</button>
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
