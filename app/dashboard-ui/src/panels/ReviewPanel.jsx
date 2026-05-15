import { useState, useEffect, useCallback } from 'react'
import { useAuth } from '../lib/auth'

const LABELS = {
  confirmed: 'Confirmed',
  overridden: 'Overridden',
  bulk_accept: 'Bulk Accepted',
  bulk_reject: 'Bulk Rejected',
}

const ACTION_COLORS = {
  confirmed: 'var(--emerald)',
  overridden: 'var(--amber)',
  bulk_accept: 'var(--emerald)',
  bulk_reject: 'var(--red)',
}

const EVENT_LABELS = {
  face_missing: 'Face missing',
  multiple_faces: 'Multiple faces',
  window_focus_lost: 'Window focus lost',
  tab_hidden: 'Tab hidden',
  phone_detected: 'Phone detected',
  id_verification: 'ID verification',
  exam_started: 'Exam started',
  exam_submitted: 'Exam submitted',
  heartbeat: 'Heartbeat',
}

const answerKey = (answer) => answer.answer_id || answer.id

function confidenceClass(confidence) {
  if (confidence === 'high') return 'badge-green'
  if (confidence === 'medium') return 'badge-amber'
  return 'badge-red'
}

function severityTone(severity) {
  if (severity === 'critical' || severity === 'high') return 'var(--red)'
  if (severity === 'medium') return 'var(--amber)'
  return 'var(--text-secondary)'
}

function formatTime(value) {
  if (!value) return '-'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString()
}

function safeFilename(value) {
  return String(value || 'audit-packet').replace(/[^a-z0-9_-]+/gi, '-').replace(/^-|-$/g, '')
}

function filenameFromDisposition(disposition, fallback) {
  const match = /filename="?([^";]+)"?/i.exec(disposition || '')
  return match?.[1] || fallback
}

export default function ReviewPanel({ currentExamId }) {
  const { authFetch } = useAuth()
  const [mode, setMode] = useState('pending')
  const [answers, setAnswers] = useState([])
  const [audit, setAudit] = useState(null)
  const [appeals, setAppeals] = useState([])
  const [loading, setLoading] = useState(true)
  const [scores, setScores] = useState({})
  const [busy, setBusy] = useState({})
  const [error, setError] = useState('')
  const [selectedAnswer, setSelectedAnswer] = useState(null)
  const [timeline, setTimeline] = useState(null)
  const [timelineLoading, setTimelineLoading] = useState(false)
  const [timelineError, setTimelineError] = useState('')
  const [appealNotes, setAppealNotes] = useState({})

  const load = useCallback(async () => {
    if (!currentExamId) return
    setLoading(true)
    setError('')
    try {
      const r = await authFetch(`/api/v1/admin/pending-grades?exam_id=${encodeURIComponent(currentExamId)}`)
      if (!r.ok) throw new Error('Failed to load pending grades')
      const d = await r.json()
      const pending = d.answers || []
      setAnswers(pending)
      const init = {}
      pending.forEach((a) => { init[answerKey(a)] = a.ai_score ?? '' })
      setScores(init)
    } catch (e) {
      setError(e.message || 'Failed to load pending grades')
    } finally {
      setLoading(false)
    }
  }, [currentExamId, authFetch])

  const loadAudit = useCallback(async () => {
    if (!currentExamId) return
    setLoading(true)
    setError('')
    try {
      const r = await authFetch(`/api/v1/admin/grading-audit?exam_id=${encodeURIComponent(currentExamId)}&limit=200`)
      if (!r.ok) throw new Error('Failed to load audit trail')
      setAudit(await r.json())
    } catch (e) {
      setError(e.message || 'Failed to load audit trail')
    } finally {
      setLoading(false)
    }
  }, [currentExamId, authFetch])

  const loadAppeals = useCallback(async () => {
    if (!currentExamId) return
    setLoading(true)
    setError('')
    try {
      const r = await authFetch(`/api/v1/admin/appeals?exam_id=${encodeURIComponent(currentExamId)}`)
      if (!r.ok) throw new Error('Failed to load appeals')
      const d = await r.json()
      setAppeals(d.appeals || [])
    } catch (e) {
      setError(e.message || 'Failed to load appeals')
    } finally {
      setLoading(false)
    }
  }, [currentExamId, authFetch])

  useEffect(() => {
    if (!currentExamId) return
    if (mode === 'pending') load()
    else if (mode === 'audit') loadAudit()
    else if (mode === 'appeals') loadAppeals()
  }, [currentExamId, mode, load, loadAudit, loadAppeals])

  const openEvidence = async (answer) => {
    if (!answer?.session_key) {
      setTimelineError('This answer has no session evidence attached.')
      return
    }
    setSelectedAnswer(answer)
    setTimeline(null)
    setTimelineError('')
    setTimelineLoading(true)
    setMode('evidence')
    try {
      const r = await authFetch(`/api/v1/admin/timeline/${encodeURIComponent(answer.session_key)}`)
      const d = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(d.detail || 'Failed to load evidence timeline')
      setTimeline(d)
    } catch (e) {
      setTimelineError(e.message || 'Failed to load evidence timeline')
    } finally {
      setTimelineLoading(false)
    }
  }

  const resolveAppeal = async (appealId, status) => {
    setBusy((p) => ({ ...p, [appealId]: true }))
    try {
      const teacherNote = (appealNotes[appealId] || '').trim()
      await authFetch(`/api/v1/admin/appeals/${appealId}/resolve`, {
        method: 'POST',
        body: JSON.stringify({ status, teacher_note: teacherNote }),
        headers: { 'Content-Type': 'application/json' },
      })
      setAppeals((prev) => prev.map((a) => (a.id === appealId ? { ...a, status, teacher_note: teacherNote || a.teacher_note } : a)))
    } finally {
      setBusy((p) => ({ ...p, [appealId]: false }))
    }
  }

  const sorted = [...answers].sort((a, b) => {
    const order = { high: 0, medium: 1, low: 2 }
    return (order[a.ai_confidence] || 1) - (order[b.ai_confidence] || 1)
  })

  const confirmGrade = async (answer) => {
    const id = answerKey(answer)
    setBusy((p) => ({ ...p, [id]: true }))
    setError('')
    try {
      const r = await authFetch('/api/v1/admin/grade-confirm', {
        method: 'POST',
        body: JSON.stringify({ answer_id: id, score: scores[id] }),
        headers: { 'Content-Type': 'application/json' },
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(d.detail || 'Failed to confirm grade')
      setAnswers((prev) => prev.filter((a) => answerKey(a) !== id))
      if (selectedAnswer && answerKey(selectedAnswer) === id) setSelectedAnswer(null)
    } catch (e) {
      setError(e.message || 'Failed to confirm grade')
    } finally {
      setBusy((p) => ({ ...p, [id]: false }))
    }
  }

  const bulkAction = async (action, confidence) => {
    setBusy((p) => ({ ...p, bulk: true }))
    setError('')
    try {
      const body = { exam_id: currentExamId, action }
      if (confidence) body.confidence_filter = confidence
      const r = await authFetch('/api/v1/admin/grade-confirm-bulk', {
        method: 'POST',
        body: JSON.stringify(body),
        headers: { 'Content-Type': 'application/json' },
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(d.detail || 'Bulk action failed')
      await load()
    } catch (e) {
      setError(e.message || 'Bulk action failed')
    } finally {
      setBusy((p) => ({ ...p, bulk: false }))
    }
  }

  const downloadPdfPacket = async (answer) => {
    if (!answer?.session_key) return
    const sid = answer.session_key
    setBusy((p) => ({ ...p, [`pdf:${sid}`]: true }))
    setError('')
    try {
      const r = await authFetch(`/api/v1/export-pdf/${encodeURIComponent(sid)}`)
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || 'Failed to export PDF packet')
      }
      const blob = await r.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = filenameFromDisposition(
        r.headers.get('content-disposition'),
        `${safeFilename(answer.roll_number || sid)}-audit-packet.pdf`,
      )
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch (e) {
      setError(e.message || 'Failed to export PDF packet')
    } finally {
      setBusy((p) => ({ ...p, [`pdf:${sid}`]: false }))
    }
  }

  if (!currentExamId) {
    return <div className="panel-empty"><p>Select an exam to review grades.</p></div>
  }

  const pendingAppeals = appeals.filter((a) => a.status === 'pending').length

  return (
    <div className="panel">
      <div className="panel-header">
        <h2>Evidence Review</h2>
        <p className="panel-sub">
          Confirm scores only after checking the answer, AI rationale, session timeline, and appeal trail.
        </p>
        <div className="bar-row" style={{ gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
          <button className={`btn btn-sm ${mode === 'pending' ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => setMode('pending')}>Pending ({answers.length})</button>
          <button className={`btn btn-sm ${mode === 'audit' ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => setMode('audit')}>Audit Trail</button>
          <button className={`btn btn-sm ${mode === 'appeals' ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => setMode('appeals')}>Appeals ({pendingAppeals})</button>
          {selectedAnswer && (
            <button className={`btn btn-sm ${mode === 'evidence' ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => setMode('evidence')}>Evidence</button>
          )}
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
        {error && <div className="auth-err" style={{ marginTop: 12 }}>{error}</div>}
      </div>

      {mode === 'audit' && <AuditView audit={audit} loading={loading} />}
      {mode === 'evidence' && (
        <EvidenceView
          answer={selectedAnswer}
          timeline={timeline}
          loading={timelineLoading}
          error={timelineError}
          score={selectedAnswer ? scores[answerKey(selectedAnswer)] : ''}
          onScoreChange={(value) => selectedAnswer && setScores((p) => ({ ...p, [answerKey(selectedAnswer)]: value }))}
          onConfirm={() => selectedAnswer && confirmGrade(selectedAnswer)}
          onPdfExport={() => selectedAnswer && downloadPdfPacket(selectedAnswer)}
          busy={selectedAnswer ? busy[answerKey(selectedAnswer)] : false}
          pdfBusy={selectedAnswer ? busy[`pdf:${selectedAnswer.session_key}`] : false}
          onBack={() => setMode('pending')}
        />
      )}
      {mode === 'pending' && (
        <PendingTable
          loading={loading}
          answers={sorted}
          scores={scores}
          busy={busy}
          setScores={setScores}
          onEvidence={openEvidence}
          onConfirm={confirmGrade}
        />
      )}
      {mode === 'appeals' && (
        <AppealsTable
          loading={loading}
          appeals={appeals}
          busy={busy}
          notes={appealNotes}
          setNotes={setAppealNotes}
          onResolve={resolveAppeal}
        />
      )}
    </div>
  )
}

function PendingTable({ loading, answers, scores, busy, setScores, onEvidence, onConfirm }) {
  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      {loading ? <div className="panel-loading">Loading...</div> : answers.length === 0 ? (
        <div className="panel-empty"><p>All caught up - no pending grades.</p></div>
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
              {answers.map((answer) => {
                const id = answerKey(answer)
                return (
                  <tr key={id} style={answer.ai_confidence === 'low' ? { background: 'rgba(239,68,68,.04)' } : {}}>
                    <td style={{ maxWidth: 140, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {answer.roll_number || answer.full_name || answer.student_email || '-'}
                    </td>
                    <td style={{ maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {(answer.question_text || answer.question || '').substring(0, 90)}
                    </td>
                    <td style={{ maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {answer.student_answer || answer.answer || ''}
                    </td>
                    <td style={{ textAlign: 'center' }}>{answer.ai_score != null ? `${answer.ai_score}/${answer.max_score}` : '-'}</td>
                    <td style={{ textAlign: 'center' }}>
                      <span className={`badge ${confidenceClass(answer.ai_confidence)}`}>
                        {answer.ai_confidence || '-'}
                      </span>
                    </td>
                    <td style={{ textAlign: 'center' }}>
                      <input type="number" step="0.5" min="0" max={answer.max_score || 5}
                        value={scores[id] ?? ''}
                        className="score-input"
                        style={{ width: 70 }}
                        onChange={(e) => setScores((p) => ({ ...p, [id]: parseFloat(e.target.value) || 0 }))} />
                    </td>
                    <td>
                      <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                        <button className="btn btn-sm btn-secondary" onClick={() => onEvidence(answer)}>
                          Evidence
                        </button>
                        <button className="btn btn-sm btn-primary" disabled={busy[id]}
                          onClick={() => onConfirm(answer)}>
                          {busy[id] ? '...' : 'Confirm'}
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function EvidenceView({ answer, timeline, loading, error, score, onScoreChange, onConfirm, onPdfExport, busy, pdfBusy, onBack }) {
  if (!answer) return <div className="panel-empty"><p>Select an answer to inspect evidence.</p></div>
  const events = timeline?.timeline || []
  const violations = events.filter((e) => e.is_violation)
  const critical = violations.filter((e) => e.severity === 'critical' || e.severity === 'high').length

  const downloadPacket = () => {
    const packet = {
      generated_at: new Date().toISOString(),
      answer,
      timeline,
      teacher_score: score,
    }
    const blob = new Blob([JSON.stringify(packet, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${safeFilename(answer.roll_number || answer.session_key)}-audit-packet.json`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  }

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <div className="card" style={{ padding: 18 }}>
        <div className="bar-row" style={{ gap: 10, alignItems: 'flex-start' }}>
          <div style={{ flex: 1 }}>
            <h3 style={{ margin: '0 0 6px' }}>Evidence Packet</h3>
            <p style={{ margin: 0, color: 'var(--text-secondary)', fontSize: 13 }}>
              {answer.roll_number || timeline?.roll_number || 'Student'} - {timeline?.full_name || answer.full_name || 'Name unavailable'}
            </p>
          </div>
          <button className="btn btn-sm btn-secondary" onClick={onBack}>Back</button>
          <button className="btn btn-sm btn-secondary" onClick={downloadPacket} disabled={!timeline}>Export Packet</button>
          <button className="btn btn-sm btn-primary" onClick={onPdfExport} disabled={pdfBusy || !answer.session_key}>
            {pdfBusy ? 'Exporting...' : 'Export PDF'}
          </button>
        </div>
      </div>

      {error && <div className="auth-err">{error}</div>}
      {loading ? <div className="panel-loading">Loading evidence...</div> : (
        <>
          <div className="stats-row">
            <div className="stat-card"><span className="stat-value">{timeline?.risk_score ?? '-'}</span><span className="stat-label">Risk Score</span></div>
            <div className="stat-card"><span className="stat-value">{events.length}</span><span className="stat-label">Timeline Events</span></div>
            <div className="stat-card"><span className="stat-value" style={{ color: 'var(--red)' }}>{critical}</span><span className="stat-label">High/Critical</span></div>
            <div className="stat-card"><span className="stat-value">{timeline?.screenshots?.length || 0}</span><span className="stat-label">Evidence Images</span></div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(320px, 100%), 1fr))', gap: 16 }}>
            <div className="card" style={{ padding: 18 }}>
              <h3 style={{ margin: '0 0 12px' }}>Answer Review</h3>
              <EvidenceBlock label="Question" value={answer.question_text || answer.question || '-'} />
              <EvidenceBlock label="Student Answer" value={answer.student_answer || answer.answer || '-'} />
              <EvidenceBlock label="Reference Answer" value={answer.reference || '-'} />
              <EvidenceBlock label="Rubric" value={answer.rubric || '-'} />
              <EvidenceBlock label="AI Rationale" value={answer.ai_feedback || 'No AI feedback recorded.'} />
              <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 14 }}>
                <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>Final score</span>
                <input type="number" step="0.5" min="0" max={answer.max_score || 5}
                  value={score ?? ''}
                  className="score-input"
                  style={{ width: 90 }}
                  onChange={(e) => onScoreChange(parseFloat(e.target.value) || 0)} />
                <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>/ {answer.max_score || 1}</span>
                <button className="btn btn-sm btn-primary" disabled={busy} onClick={onConfirm}>
                  {busy ? 'Confirming...' : 'Confirm Score'}
                </button>
              </div>
            </div>

            <div className="card" style={{ padding: 18 }}>
              <h3 style={{ margin: '0 0 12px' }}>Session Summary</h3>
              <EvidenceBlock label="Session" value={answer.session_key || timeline?.session_id || '-'} compact />
              <EvidenceBlock label="Status" value={timeline?.status || '-'} compact />
              <EvidenceBlock label="Started" value={timeline?.started_at || '-'} compact />
              <EvidenceBlock label="Submitted" value={timeline?.submitted_at || '-'} compact />
              <EvidenceBlock label="Summary" value={timeline?.summary || 'No session summary available.'} />
            </div>
          </div>

          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--border-subtle)' }}>
              <h3 style={{ margin: 0 }}>Violation Timeline</h3>
            </div>
            {events.length === 0 ? (
              <div className="panel-empty"><p>No timeline events recorded.</p></div>
            ) : (
              <div style={{ overflowX: 'auto' }}>
                <table className="dtable">
                  <thead>
                    <tr>
                      <th>Time</th>
                      <th>Reason Code</th>
                      <th>Severity</th>
                      <th>Details</th>
                      <th>Evidence</th>
                    </tr>
                  </thead>
                  <tbody>
                    {events.map((event) => (
                      <tr key={`${event.id}-${event.raw_ts}-${event.type}`}>
                        <td style={{ whiteSpace: 'nowrap' }}>{event.timestamp || formatTime(event.raw_ts)}</td>
                        <td>
                          <span style={{ fontWeight: event.is_violation ? 700 : 500 }}>
                            {EVENT_LABELS[event.type] || event.type}
                          </span>
                          {!event.is_violation && <span style={{ marginLeft: 6, color: 'var(--text-secondary)', fontSize: 11 }}>event</span>}
                        </td>
                        <td style={{ color: severityTone(event.severity), fontWeight: 700 }}>{event.severity || '-'}</td>
                        <td style={{ maxWidth: 360, whiteSpace: 'normal' }}>{event.details || '-'}</td>
                        <td>
                          {event.screenshot ? (
                            <a href={event.screenshot} target="_blank" rel="noreferrer" style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                              <img src={event.screenshot} alt="" style={{ width: 56, height: 36, objectFit: 'cover', borderRadius: 4, border: '1px solid var(--border-subtle)' }} />
                              <span>Open</span>
                            </a>
                          ) : <span style={{ color: 'var(--text-secondary)' }}>-</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}

function EvidenceBlock({ label, value, compact = false }) {
  return (
    <div style={{ marginBottom: compact ? 8 : 12 }}>
      <div style={{ fontSize: 11, color: 'var(--text-secondary)', textTransform: 'uppercase', fontWeight: 700, marginBottom: 3 }}>
        {label}
      </div>
      <div style={{ fontSize: compact ? 13 : 14, lineHeight: 1.5, whiteSpace: 'pre-wrap' }}>{value}</div>
    </div>
  )
}

function AppealsTable({ loading, appeals, busy, notes, setNotes, onResolve }) {
  return (
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
              {appeals.map((appeal) => (
                <tr key={appeal.id}>
                  <td style={{ whiteSpace: 'nowrap' }}>{formatTime(appeal.created_at)}</td>
                  <td>{appeal.roll_number || appeal.student_id || '-'}</td>
                  <td><span className="badge">{appeal.appeal_type}</span></td>
                  <td style={{ maxWidth: 320, whiteSpace: 'normal' }}>{appeal.description || '-'}</td>
                  <td><span className={`badge ${appeal.status === 'pending' ? 'badge-amber' : appeal.status === 'accepted' ? 'badge-green' : 'badge-red'}`}>{appeal.status}</span></td>
                  <td>{appeal.status === 'pending' ? (
                    <div style={{ display: 'grid', gap: 6, minWidth: 220 }}>
                      <textarea
                        className="input"
                        rows={2}
                        placeholder="Reviewer note"
                        value={notes[appeal.id] || ''}
                        onChange={(e) => setNotes((prev) => ({ ...prev, [appeal.id]: e.target.value }))}
                        style={{ width: '100%', resize: 'vertical', fontSize: 12 }}
                      />
                      <div style={{ display: 'flex', gap: 4 }}>
                        <button className="btn btn-sm btn-primary" disabled={busy[appeal.id]}
                          onClick={() => onResolve(appeal.id, 'accepted')}>Accept</button>
                        <button className="btn btn-sm btn-secondary" disabled={busy[appeal.id]}
                          onClick={() => onResolve(appeal.id, 'rejected')}>Reject</button>
                      </div>
                    </div>
                  ) : <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{appeal.teacher_note || 'Resolved'}</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
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
        <div className="stat-card"><span className="stat-value">{s.ai_accept_rate != null ? `${s.ai_accept_rate}%` : '-'}</span><span className="stat-label">AI Accept Rate</span></div>
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
              {(audit.events || []).map((event) => (
                <tr key={event.id}>
                  <td style={{ whiteSpace: 'nowrap' }}>{formatTime(event.created_at)}</td>
                  <td><span style={{ color: ACTION_COLORS[event.action] || 'inherit', fontWeight: 500 }}>{LABELS[event.action] || event.action}</span></td>
                  <td style={{ textAlign: 'center' }}>{event.ai_score != null ? `${event.ai_score}/${event.max_score}` : '-'}</td>
                  <td style={{ textAlign: 'center' }}>{event.teacher_score}/{event.max_score}</td>
                  <td>{event.teacher_name || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  )
}
