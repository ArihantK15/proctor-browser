import { useState, useEffect, useCallback } from 'react'
import { useAuth } from '../lib/auth'
import TimelineView from '../components/TimelineView'

export default function ResultsPanel({ currentExamId }) {
  const { authFetch } = useAuth()
  const [results, setResults] = useState([])
  const [search, setSearch] = useState('')
  const [riskFilter, setRiskFilter] = useState('all')
  const [sortKey, setSortKey] = useState('submitted_at')
  const [sortAsc, setSortAsc] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [actionError, setActionError] = useState('')
  const [batchSize, setBatchSize] = useState(50)
  const [stats, setStats] = useState({ total: 0, avgScore: 0, avgRisk: 0, highRisk: 0 })
  const [timelineSession, setTimelineSession] = useState(null)

  const loadResults = useCallback(async () => {
    if (!currentExamId) {
      setResults([])
      setLoading(false)
      return
    }
    setLoading(true)
    setError('')
    setActionError('')
    try {
      const r = await authFetch(`/api/v1/results?exam_id=${encodeURIComponent(currentExamId)}`)
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || `Failed to load results (${r.status})`)
      }
      const d = await r.json()
      setResults(d.results || [])
      setBatchSize(50)
    } catch (e) {
      setError(e.message || 'Failed to load results')
    } finally { setLoading(false) }
  }, [currentExamId, authFetch])

  useEffect(() => { loadResults() }, [currentExamId, loadResults])

  const filtered = results
    .filter(r => !search || r.roll_number.toLowerCase().includes(search) || r.full_name.toLowerCase().includes(search) || (r.email || '').toLowerCase().includes(search))
    .filter(r => {
      if (riskFilter === 'all') return true
      const s = r.risk_score
      if (s == null) return riskFilter === 'low'
      if (riskFilter === 'critical') return s > 70
      if (riskFilter === 'high') return s >= 41 && s <= 70
      if (riskFilter === 'moderate') return s >= 16 && s <= 40
      if (riskFilter === 'low') return s <= 15
      return true
    })
    .sort((a, b) => {
      let va = a[sortKey], vb = b[sortKey]
      if (va == null) va = ''
      if (vb == null) vb = ''
      if (typeof va === 'number' && typeof vb === 'number') return sortAsc ? va - vb : vb - va
      return sortAsc ? String(va).localeCompare(String(vb)) : String(vb).localeCompare(String(va))
    })
  const displayed = filtered.slice(0, batchSize)
  const total = results.length
  const avgScore = total ? Math.round(results.reduce((s, r) => s + (r.percentage || 0), 0) / total) : 0
  const scored = results.filter(r => r.risk_score != null)
  const avgRisk = scored.length ? Math.round(scored.reduce((s, r) => s + r.risk_score, 0) / scored.length) : 0
  const highRisk = scored.filter(r => r.risk_score >= 41).length

  const toggleSort = (key) => {
    if (sortKey === key) setSortAsc(!sortAsc)
    else { setSortKey(key); setSortAsc(key === 'roll_number' || key === 'full_name') }
  }

  const loadMore = () => setBatchSize(b => b + 50)
  const hasMore = batchSize < filtered.length
  const downloadPdf = async (sessionId) => {
    setActionError('')
    try {
      const r = await authFetch(`/api/v1/export-pdf/${encodeURIComponent(sessionId)}`)
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || `PDF export failed (${r.status})`)
      }
      const blob = await r.blob()
      const href = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = href
      a.download = `report_${String(sessionId).split('_')[0]}.pdf`
      a.click()
      URL.revokeObjectURL(href)
    } catch (e) {
      setActionError(e.message || 'PDF export failed')
    }
  }

  return (
    <div>
      <div className="stats-bar" id="results-stats">
        {[
          { label: 'Completed', value: total },
          { label: 'Avg Score', value: `${avgScore}%` },
          { label: 'Avg Risk', value: avgRisk },
          { label: 'High Risk', value: `${highRisk} (${total ? Math.round(highRisk / total * 100) : 0}%)` },
        ].map(s => (
          <div className="stat-tile" key={s.label}>
            <div className="stat-tile-label">{s.label}</div>
            <div className="stat-tile-value">{s.value}</div>
          </div>
        ))}
      </div>

      {!currentExamId && <div style={{ textAlign: 'center', padding: 40, color: 'var(--text-muted)' }}>Select an exam to view results.</div>}

      {currentExamId && error && <div className="auth-err" style={{ margin: 20 }}>{error} <button className="btn-link" onClick={loadResults} style={{ marginLeft: 8 }}>Retry</button></div>}
      {currentExamId && actionError && <div className="auth-err" style={{ margin: '0 20px 12px' }}>{actionError}</div>}

      {currentExamId && loading && <div className="loading" style={{ textAlign: 'center', padding: 40 }}>Loading...</div>}

      {currentExamId && !loading && !error && (
        <>
          <div className="table-toolbar">
            <div className="search-wrap">
              <span className="search-icon">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
              </span>
              <input className="search-input" placeholder="Search by name, roll, or email…" value={search} onChange={(e) => { setSearch(e.target.value); setBatchSize(50) }} />
            </div>
            <select className="filter-select" value={riskFilter} onChange={(e) => { setRiskFilter(e.target.value); setBatchSize(50) }} style={{ marginLeft: 8 }}>
              <option value="all">All risk levels</option>
              <option value="critical">Critical (&gt;70)</option>
              <option value="high">High (41–70)</option>
              <option value="moderate">Moderate (16–40)</option>
              <option value="low">Low (≤15)</option>
            </select>
          </div>
          <div className="table-wrap" style={{ overflowY: 'auto', maxHeight: 'calc(100vh - 280px)' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ background: 'var(--surface-1)', borderBottom: '1px solid var(--border-subtle)' }}>
                  {[
                    { key: 'roll_number', label: 'Roll #' },
                    { key: 'full_name', label: 'Name' },
                    { key: 'score', label: 'Score' },
                    { key: 'percentage', label: '%' },
                    { key: 'violation_count', label: 'Violations' },
                    { key: 'risk_score', label: 'Risk Score' },
                    { key: 'time_taken_secs', label: 'Time' },
                    { key: 'submitted_at', label: 'Submitted' },
                  ].map(col => (
                    <th key={col.key} onClick={() => toggleSort(col.key)} style={{
                      padding: '10px 12px', textAlign: 'left', fontWeight: 600, color: 'var(--text-muted)', fontSize: 11, textTransform: 'uppercase', cursor: 'pointer', whiteSpace: 'nowrap',
                      borderRight: '1px solid var(--border-subtle)',
                    }}>
                      {col.label} {sortKey === col.key ? (sortAsc ? '▲' : '▼') : ''}
                    </th>
                  ))}
                  <th style={{ padding: '10px 12px', width: 200 }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {displayed.length === 0 ? (
                  <tr>
                    <td colSpan={9} style={{ padding: '32px 14px', textAlign: 'center', color: 'var(--text-muted)', fontSize: 13 }}>
                      No results yet — completed exam sessions will appear here.
                    </td>
                  </tr>
                ) : displayed.map(r => {
                  const mins = Math.floor((r.time_taken_secs || 0) / 60)
                  const secs = (r.time_taken_secs || 0) % 60
                  const riskColor = r.risk_score == null ? 'var(--muted)' : r.risk_score > 70 ? 'var(--red)' : r.risk_score > 40 ? 'var(--amber)' : r.risk_score > 15 ? '#58a6ff' : 'var(--emerald)'
                  return (
                    <tr key={r.session_id} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                      <td style={{ padding: '10px 12px' }}><strong>{r.roll_number}</strong></td>
                      <td style={{ padding: '10px 12px' }}>{r.full_name}</td>
                      <td style={{ padding: '10px 12px' }}>{r.score}/{r.total}</td>
                      <td style={{ padding: '10px 12px' }}>{r.percentage}%</td>
                      <td style={{ padding: '10px 12px', color: r.violation_count > 5 ? 'var(--red)' : r.violation_count > 0 ? 'var(--amber)' : 'var(--emerald)' }}>{r.violation_count}</td>
                      <td style={{ padding: '10px 12px', color: riskColor, fontWeight: 600 }}>{r.risk_score != null ? `${r.risk_score}/100` : 'N/A'}</td>
                      <td style={{ padding: '10px 12px' }}>{mins}m {secs}s</td>
                      <td style={{ padding: '10px 12px', fontSize: 11, color: 'var(--muted)' }}>{r.submitted_at || '—'}</td>
                      <td style={{ padding: '10px 12px' }}>
                        <button className="btn btn-secondary btn-sm" style={{ padding: '4px 10px', fontSize: 11 }} onClick={() => setTimelineSession(r.session_id)}>Timeline</button>
                        <button className="btn btn-secondary btn-sm" style={{ padding: '4px 10px', fontSize: 11, marginLeft: 4 }} onClick={() => window.open(`/dashboard-react?session=${r.session_id}`, '_blank')}>Detail</button>
                        <button className="btn btn-secondary btn-sm" style={{ padding: '4px 10px', fontSize: 11, marginLeft: 4 }} onClick={() => downloadPdf(r.session_id)}>PDF</button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
            {hasMore && (
              <div style={{ textAlign: 'center', padding: 16 }}>
                <button className="btn btn-secondary btn-sm" onClick={loadMore}>
                  Load more ({filtered.length - batchSize} remaining)
                </button>
              </div>
            )}
          </div>
        </>
      )}
      {timelineSession && <TimelineView sessionId={timelineSession} onClose={() => setTimelineSession(null)} />}
    </div>
  )
}
