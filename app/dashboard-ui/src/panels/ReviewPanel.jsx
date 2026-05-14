import { useState, useEffect, useCallback } from 'react'
import { useAuth } from '../lib/auth'

const LABELS = {
  accepted: 'Accepted', overridden: 'Overridden', rejected: 'Rejected',
  bulk_accept: 'Bulk Accepted', bulk_reject: 'Bulk Rejected',
}
const ACTION_COLORS = {
  confirmed: 'var(--emerald)', overridden: 'var(--amber)',
  bulk_accept: 'var(--emerald)', bulk_reject: 'var(--red)',
}

export default function ReviewPanel({ currentExamId }) {
  const { authFetch } = useAuth()
  const [mode, setMode] = useState('pending') // pending | audit | appeals
  const [answers, setAnswers] = useState([])
  const [audit, setAudit] = useState(null)
  const [appeals, setAppeals] = useState([])
  const [loading, setLoading] = useState(true)
  const [scores, setScores] = useState({})
  const [busy, setBusy] = useState({})

  const load = useCallback(async () => {
    if (!currentExamId) return
    setLoading(true)
    try {
      const r = await authFetch(`/api/v1/admin/pending-grades?exam_id=${encodeURIComponent(currentExamId)}`)
      if (r.ok) {
        const d = await r.json()
        setAnswers(d.answers || [])
        const init = {}
        ;(d.answers || []).forEach(a => { init[a.id] = a.ai_score })
        setScores(init)
      }
    } catch (_) {} finally { setLoading(false) }
  }, [currentExamId, authFetch])

  const loadAudit = useCallback(async () => {
    if (!currentExamId) return
    setLoading(true)
    try {
      const r = await authFetch(`/api/v1/admin/grading-audit?exam_id=${encodeURIComponent(currentExamId)}&limit=200`)
      if (r.ok) setAudit(await r.json())
    } catch (_) {} finally { setLoading(false) }
  }, [currentExamId, authFetch])

  const loadAppeals = useCallback(async () => {
    if (!currentExamId) return
    setLoading(true)
    try {
      const r = await authFetch(`/api/v1/admin/appeals?exam_id=${encodeURIComponent(currentExamId)}`)
      if (r.ok) {
        const d = await r.json()
        setAppeals(d.appeals || [])
      }
    } catch (_) {} finally { setLoading(false) }
  }, [currentExamId, authFetch])

  useEffect(() => {
    if (!currentExamId) return
    if (mode === 'pending') load()
    else if (mode === 'audit') loadAudit()
    else if (mode === 'appeals') loadAppeals()
  }, [currentExamId, mode, load, loadAudit, loadAppeals])

  const resolveAppeal = async (appealId, status) => {
    setBusy(p => ({ ...p, [appealId]: true }))
    try {
      await authFetch(`/api/v1/admin/appeals/${appealId}/resolve`, {
        method: 'POST', body: JSON.stringify({ status, teacher_note: '' }),
        headers: { 'Content-Type': 'application/json' },
      })
      setAppeals(prev => prev.map(a => a.id === appealId ? { ...a, status } : a))
    } catch (_) {} finally { setBusy(p => ({ ...p, [appealId]: false })) }
  }

  const sorted = [...answers].sort((a, b) => {
    const order = { high: 0, medium: 1, low: 2 }
    return (order[a.ai_confidence] || 1) - (order[b.ai_confidence] || 1)
  })

  const confirmGrade = async (answerId) => {
    setBusy(p => ({ ...p, [answerId]: true }))
    try {
      await authFetch('/api/v1/admin/grade-confirm', {
        method: 'POST', body: JSON.stringify({ answer_id: answerId, score: scores[answerId] }),
        headers: { 'Content-Type': 'application/json' },
      })
      setAnswers(prev => prev.filter(a => a.id !== answerId))
    } catch (_) {} finally { setBusy(p => ({ ...p, [answerId]: false })) }
  }

  const bulkAction = async (action, confidence) => {
    setBusy(p => ({ ...p, bulk: true }))
    try {
      const body = { exam_id: currentExamId, action }
      if (confidence) body.confidence_filter = confidence
      await authFetch('/api/v1/admin/grade-confirm-bulk', {
        method: 'POST', body: JSON.stringify(body),
        headers: { 'Content-Type': 'application/json' },
      })
      await load()
    } catch (_) {} finally { setBusy(p => ({ ...p, bulk: false })) }
  }

  if (!currentExamId) {
    return <div className="panel-empty"><p>Select an exam to review grades.</p></div>
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <h2>Grade Review</h2>
        <p className="panel-sub">
          AI suggestions are recommendations, not verdicts. Review before confirming.
        </p>
        <div className="bar-row" style={{ gap: 8, marginTop: 8 }}>
          <button className={`btn btn-sm ${mode === 'pending' ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => setMode('pending')}>Pending ({answers.length})</button>
          <button className={`btn btn-sm ${mode === 'audit' ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => setMode('audit')}>Audit Trail</button>
          <button className={`btn btn-sm ${mode === 'appeals' ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => setMode('appeals')}>Appeals ({appeals.filter(a => a.status === 'pending').length})</button>
          {mode === 'pending' && answers.length > 0 && (
            <>
              <span style={{ flex: 1 }} />
              <button className="btn btn-sm btn-secondary" disabled={busy.bulk}
                onClick={() => bulkAction('accept', 'high')}>
                Accept All High-Confidence
              </button>
              <button className="btn btn-sm btn-secondary" disabled={busy.bulk}
                onClick={() => bulkAction('reject')}>
                Reject All Unconfirmed
              </button>
            </>
          )}
        </div>
      </div>

      {mode === 'audit' && <AuditView audit={audit} loading={loading} />}
      {mode === 'pending' && (
        <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
          {loading ? <div className="panel-loading">Loading...</div> : sorted.length === 0 ? (
            <div className="panel-empty"><p>All caught up — no pending grades.</p></div>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table className="dtable">
                <thead>
                  <tr>
                    <th>Student</th>
                    <th>Question</th>
                    <th>Answer</th>
                    <th>AI Score</th>
                    <th>Confidence</th>
                    <th>Teacher Score</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {sorted.map(a => (
                    <tr key={a.id} style={a.ai_confidence === 'low' ? { background: 'rgba(239,68,68,.04)' } : {}}>
                      <td style={{ maxWidth: 140, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {a.roll_number || a.full_name || a.student_email || '—'}
                      </td>
                      <td style={{ maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {(a.question_text || a.question || '').substring(0, 80)}
                      </td>
                      <td style={{ maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {a.student_answer || a.answer || ''}
                      </td>
                      <td style={{ textAlign: 'center' }}>{a.ai_score != null ? a.ai_score + '/' + a.max_score : '—'}</td>
                      <td style={{ textAlign: 'center' }}>
                        <span className={`badge ${a.ai_confidence === 'high' ? 'badge-green' : a.ai_confidence === 'medium' ? 'badge-amber' : 'badge-red'}`}>
                          {a.ai_confidence || '—'}
                        </span>
                      </td>
                      <td style={{ textAlign: 'center' }}>
                        <input type="number" step="0.5" min="0" max={a.max_score || 5}
                          value={scores[a.id] ?? ''}
                          className="score-input"
                          style={{ width: 70 }}
                          onChange={e => setScores(p => ({ ...p, [a.id]: parseFloat(e.target.value) || 0 }))} />
                      </td>
                      <td>
                        <button className="btn btn-sm btn-primary" disabled={busy[a.id]}
                          onClick={() => confirmGrade(a.id)}>
                          {busy[a.id] ? '...' : 'Confirm'}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {mode === 'appeals' && (
        <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
          {loading ? <div className="panel-loading">Loading...</div> : appeals.length === 0 ? (
            <div className="panel-empty"><p>No appeals from students.</p></div>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table className="dtable">
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Student</th>
                    <th>Type</th>
                    <th>Description</th>
                    <th>Status</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {appeals.map(a => (
                    <tr key={a.id}>
                      <td style={{ whiteSpace: 'nowrap' }}>{new Date(a.created_at).toLocaleString()}</td>
                      <td>{a.roll_number || a.student_id || '—'}</td>
                      <td><span className="badge">{a.appeal_type}</span></td>
                      <td style={{ maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.description || '—'}</td>
                      <td><span className={`badge ${a.status === 'pending' ? 'badge-amber' : a.status === 'accepted' ? 'badge-green' : 'badge-red'}`}>{a.status}</span></td>
                      <td>{a.status === 'pending' ? (
                        <div style={{ display: 'flex', gap: 4 }}>
                          <button className="btn btn-sm btn-primary" disabled={busy[a.id]}
                            onClick={() => resolveAppeal(a.id, 'accepted')}>Accept</button>
                          <button className="btn btn-sm btn-secondary" disabled={busy[a.id]}
                            onClick={() => resolveAppeal(a.id, 'rejected')}>Reject</button>
                        </div>
                      ) : <span style={{ fontSize: 12, color: 'var(--muted)' }}>{a.teacher_note || 'Resolved'}</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function AuditView({ audit, loading }) {
  if (loading) return <div className="panel-loading">Loading...</div>
  if (!audit) return <div className="panel-empty"><p>No audit data.</p></div>
  const s = audit.stats || {}
  return (
    <>
      <div className="stats-row" style={{ marginBottom: 16 }}>
        <div className="stat-card"><span className="stat-value">{s.total || 0}</span><span className="stat-label">Total Graded</span></div>
        <div className="stat-card"><span className="stat-value">{s.ai_accept_rate != null ? s.ai_accept_rate + '%' : '—'}</span><span className="stat-label">AI Accept Rate</span></div>
        <div className="stat-card"><span className="stat-value" style={{ color: 'var(--emerald)' }}>{s.accepted || 0}</span><span className="stat-label">Accepted</span></div>
        <div className="stat-card"><span className="stat-value" style={{ color: 'var(--amber)' }}>{s.overridden || 0}</span><span className="stat-label">Overridden</span></div>
        <div className="stat-card"><span className="stat-value" style={{ color: 'var(--red)' }}>{s.rejected || 0}</span><span className="stat-label">Rejected</span></div>
      </div>
      <div className="card" style={{ padding: 0 }}>
        <div style={{ overflowX: 'auto' }}>
          <table className="dtable">
            <thead>
              <tr>
                <th>Time</th>
                <th>Action</th>
                <th>AI Score</th>
                <th>Teacher Score</th>
                <th>Teacher</th>
              </tr>
            </thead>
            <tbody>
              {(audit.events || []).map(e => (
                <tr key={e.id}>
                  <td style={{ whiteSpace: 'nowrap' }}>{new Date(e.created_at).toLocaleString()}</td>
                  <td><span style={{ color: ACTION_COLORS[e.action] || 'inherit', fontWeight: 500 }}>{LABELS[e.action] || e.action}</span></td>
                  <td style={{ textAlign: 'center' }}>{e.ai_score != null ? e.ai_score + '/' + e.max_score : '—'}</td>
                  <td style={{ textAlign: 'center' }}>{e.teacher_score + '/' + e.max_score}</td>
                  <td>{e.teacher_name || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  )
}
