import { useState, useEffect } from 'react'
import { useAuth } from '../lib/auth'

export default function HistoryPanel({ currentExamId }) {
  const { authFetch } = useAuth()
  const [students, setStudents] = useState([])
  const [search, setSearch] = useState('')
  const [selectedRoll, setSelectedRoll] = useState(null)
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)

  useEffect(() => { if (currentExamId) loadStudents() }, [currentExamId])

  const loadStudents = async () => {
    if (!currentExamId) { setLoading(false); return }
    try {
      const r = await authFetch(`/api/v1/admin/student-history?exam_id=${encodeURIComponent(currentExamId)}`)
      if (r.ok) setStudents((await r.json()).students || [])
    } catch (_) {}
    finally { setLoading(false) }
  }

  const viewDetail = async (roll) => {
    setSelectedRoll(roll)
    setDetailLoading(true)
    try {
      const r = await authFetch(`/api/v1/admin/student-history/${encodeURIComponent(roll)}?exam_id=${encodeURIComponent(currentExamId)}`)
      if (r.ok) setDetail(await r.json())
    } catch (_) {}
    finally { setDetailLoading(false) }
  }

  const filtered = students.filter(s =>
    !search || s.roll_number?.toLowerCase().includes(search) || s.full_name?.toLowerCase().includes(search)
  )

  if (!currentExamId) return <div style={{ textAlign: 'center', padding: 40, color: 'var(--text-muted)' }}>Select an exam to view student history.</div>
  if (loading) return <div className="loading" style={{ textAlign: 'center', padding: 40 }}>Loading...</div>

  return (
    <div>
      {/* Student list */}
      {!selectedRoll && (
        <>
          <div className="table-toolbar">
            <div className="search-wrap">
              <span className="search-icon">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
              </span>
              <input className="search-input" placeholder="Search students…" value={search} onChange={(e) => setSearch(e.target.value.toLowerCase())} />
            </div>
          </div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ background: 'var(--surface-1)', borderBottom: '1px solid var(--border-subtle)' }}>
                <th style={{ padding: '10px 14px', textAlign: 'left', fontWeight: 600, color: 'var(--text-muted)', fontSize: 11, textTransform: 'uppercase' }}>Roll</th>
                <th style={{ padding: '10px 14px', textAlign: 'left', fontWeight: 600, color: 'var(--text-muted)', fontSize: 11, textTransform: 'uppercase' }}>Name</th>
                <th style={{ padding: '10px 14px', textAlign: 'left', fontWeight: 600, color: 'var(--text-muted)', fontSize: 11, textTransform: 'uppercase' }}>Email</th>
                <th style={{ padding: '10px 14px', textAlign: 'center', fontWeight: 600, color: 'var(--text-muted)', fontSize: 11, textTransform: 'uppercase' }}>Exams</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(s => (
                <tr key={s.roll_number} style={{ borderBottom: '1px solid var(--border-subtle)', cursor: 'pointer' }} onClick={() => viewDetail(s.roll_number)}>
                  <td style={{ padding: '10px 14px' }}><strong>{s.roll_number}</strong></td>
                  <td style={{ padding: '10px 14px' }}>{s.full_name}</td>
                  <td style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>{s.email}</td>
                  <td style={{ padding: '10px 14px', textAlign: 'center' }}>{s.exam_count || 0}</td>
                  <td style={{ padding: '10px 14px' }}>
                    <button className="btn btn-primary btn-sm" onClick={(e) => { e.stopPropagation(); viewDetail(s.roll_number) }}>View History</button>
                  </td>
                </tr>
              ))}
              {filtered.length === 0 && (
                <tr><td colSpan="5" style={{ textAlign: 'center', padding: 30, color: 'var(--text-muted)' }}>No students found</td></tr>
              )}
            </tbody>
          </table>
        </>
      )}

      {/* Detail view */}
      {selectedRoll && (
        <div>
          <button className="btn btn-secondary btn-sm" onClick={() => { setSelectedRoll(null); setDetail(null) }} style={{ marginBottom: 16 }}>← Back to student list</button>
          {detailLoading && <div className="loading" style={{ textAlign: 'center', padding: 40 }}>Loading...</div>}
          {detail && (
            <>
              <div className="stats-bar">
                {[
                  { label: 'Student', value: detail.full_name },
                  { label: 'Email', value: detail.email || '—' },
                  { label: 'Exams Taken', value: detail.aggregate?.total_exams || 0 },
                  { label: 'Avg Score', value: detail.aggregate?.avg_percentage != null ? `${detail.aggregate.avg_percentage}%` : '—' },
                  { label: 'Avg Risk', value: detail.aggregate?.avg_risk_score != null ? detail.aggregate.avg_risk_score : '—' },
                  { label: 'Total Violations', value: detail.aggregate?.total_violations || 0 },
                ].map(s => (
                  <div className="stat-tile" key={s.label}>
                    <div className="stat-tile-label">{s.label}</div>
                    <div className="stat-tile-value">{s.value}</div>
                  </div>
                ))}
              </div>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, marginTop: 16 }}>
                <thead>
                  <tr style={{ background: 'var(--surface-1)', borderBottom: '1px solid var(--border-subtle)' }}>
                    {['Date', 'Score', '%', 'Risk', 'Violations', 'Time', 'Status'].map(h => (
                      <th key={h} style={{ padding: '10px 14px', textAlign: 'left', fontWeight: 600, color: 'var(--text-muted)', fontSize: 11, textTransform: 'uppercase' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(detail.history || []).map(h => (
                    <tr key={h.session_id} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                      <td style={{ padding: '10px 14px', fontSize: 12, color: 'var(--text-secondary)' }}>{h.submitted_at || h.created_at || '—'}</td>
                      <td style={{ padding: '10px 14px' }}>{h.score}/{h.total}</td>
                      <td style={{ padding: '10px 14px' }}>{h.percentage != null ? `${h.percentage}%` : '—'}</td>
                      <td style={{ padding: '10px 14px' }}>{h.risk_score != null ? `${h.risk_score}/100` : '—'}</td>
                      <td style={{ padding: '10px 14px' }}>{h.violation_count || 0}</td>
                      <td style={{ padding: '10px 14px' }}>{h.time_taken_secs != null ? `${Math.floor(h.time_taken_secs / 60)}m ${h.time_taken_secs % 60}s` : '—'}</td>
                      <td style={{ padding: '10px 14px' }}>{h.status || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </div>
      )}
    </div>
  )
}
