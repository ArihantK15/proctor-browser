import { useState, useEffect } from 'react'
import { useAuth } from '../lib/auth'

export default function ToolsPanel({ currentExamId }) {
  const { authFetch } = useAuth()
  const [accessCode, setAccessCode] = useState('')
  const [scheduleStart, setScheduleStart] = useState('')
  const [scheduleEnd, setScheduleEnd] = useState('')
  const [shuffleQ, setShuffleQ] = useState(true)
  const [shuffleO, setShuffleO] = useState(true)
  const [inviteText, setInviteText] = useState('')
  const [inviteResult, setInviteResult] = useState('')
  const [accessResult, setAccessResult] = useState('')
  const [sensitivity, setSensitivity] = useState('balanced')
  const [sensitivityLoaded, setSensitivityLoaded] = useState(false)
  const [sensitivityResult, setSensitivityResult] = useState('')
  const [scheduleResult, setScheduleResult] = useState('')
  const [shuffleResult, setShuffleResult] = useState('')
  const [copyResult, setCopyResult] = useState('')
  const [backfillResult, setBackfillResult] = useState('')
  const [clearSessionsResult, setClearSessionsResult] = useState('')

  const [loadError, setLoadError] = useState('')

  const responseError = async (response, fallback) => {
    const data = await response.json().catch(() => ({}))
    return new Error(data.detail || `${fallback} (${response.status})`)
  }

  const loadAll = async () => {
    if (!currentExamId) return
    setLoadError('')
    try {
      const [configR, schedR, accessR, sensR] = await Promise.all([
        authFetch(`/api/v1/admin/shuffle-config?exam_id=${encodeURIComponent(currentExamId)}`),
        authFetch(`/api/v1/admin/exam-schedule?exam_id=${encodeURIComponent(currentExamId)}`),
        authFetch(`/api/v1/admin/access-code?exam_id=${encodeURIComponent(currentExamId)}`),
        authFetch(`/api/v1/admin/proctoring-sensitivity?exam_id=${encodeURIComponent(currentExamId)}`),
      ])
      const failures = []
      if (configR.ok) { const d = await configR.json(); setShuffleQ(!!d.shuffle_questions); setShuffleO(!!d.shuffle_options) }
      else failures.push(`shuffle ${configR.status}`)
      if (schedR.ok) { const d = await schedR.json(); setScheduleStart(d.starts_at || ''); setScheduleEnd(d.ends_at || '') }
      else failures.push(`schedule ${schedR.status}`)
      if (accessR.ok) { const d = await accessR.json(); setAccessCode(d.access_code || '') }
      else failures.push(`access code ${accessR.status}`)
      if (sensR.ok) { const d = await sensR.json(); setSensitivity(d.proctoring_sensitivity || 'balanced'); setSensitivityLoaded(true) }
      else failures.push(`sensitivity ${sensR.status}`)
      if (failures.length) setLoadError(`Some exam settings could not load: ${failures.join(', ')}`)
    } catch (e) {
      setLoadError(e.message || 'Failed to load exam settings')
    }
  }

  useEffect(() => { loadAll() }, [currentExamId])

  const saveShuffle = async () => {
    try {
      const r = await authFetch('/api/v1/admin/shuffle-config', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ exam_id: currentExamId, shuffle_questions: shuffleQ, shuffle_options: shuffleO }),
      })
      if (!r.ok) throw await responseError(r, 'Failed to save randomization')
      setShuffleResult('✅ Saved')
    } catch (err) { setShuffleResult(err.message || 'Failed') }
  }
  const saveAccess = async () => {
    try {
      const r = await authFetch('/api/v1/admin/access-code', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ exam_id: currentExamId, access_code: accessCode }),
      })
      if (!r.ok) throw await responseError(r, 'Failed to save access code')
      setAccessResult('✅ Saved')
    } catch (err) { setAccessResult(err.message || 'Failed') }
  }
  const generateAccess = async () => {
    try {
      const r = await authFetch('/api/v1/admin/access-code', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ exam_id: currentExamId, access_code: '' }),
      })
      if (!r.ok) throw await responseError(r, 'Failed to generate access code')
      const d = await r.json(); setAccessCode(d.access_code || ''); setAccessResult('✅ Generated')
    } catch (err) { setAccessResult(err.message || 'Failed') }
  }
  const clearAccess = async () => {
    try {
      const r = await authFetch('/api/v1/admin/access-code/clear', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ exam_id: currentExamId }) })
      if (!r.ok) throw await responseError(r, 'Failed to clear access code')
      setAccessCode(''); setAccessResult('✅ Cleared')
    } catch (err) { setAccessResult(err.message || 'Failed') }
  }
  const saveSchedule = async () => {
    try {
      const r = await authFetch('/api/v1/admin/exam-schedule', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ exam_id: currentExamId, starts_at: scheduleStart, ends_at: scheduleEnd }),
      })
      if (!r.ok) throw await responseError(r, 'Failed to save schedule')
      setScheduleResult('✅ Saved')
    } catch (err) { setScheduleResult(err.message || 'Failed') }
  }
  const sendInvites = async () => {
    if (!inviteText.trim()) { setInviteResult('Enter recipients'); return }
    setInviteResult('Sending...')
    try {
      const lines = inviteText.trim().split('\n').filter(Boolean).map(l => {
        const parts = l.split(',').map(s => s.trim())
        return parts.length >= 2 ? { email: parts[0], full_name: parts[1], roll_number: parts[2] || parts[0].split('@')[0] } : null
      }).filter(Boolean)
      const r = await authFetch('/api/v1/admin/invites/send', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ exam_id: currentExamId, recipients: lines }),
      })
      if (!r.ok) { const d = await r.json(); throw new Error(d.detail || 'Failed') }
      const d = await r.json()
      setInviteResult(`✅ Sent: ${d.sent || 0}, Skipped: ${d.skipped || 0}, Failed: ${d.failed || 0}`)
      setInviteText('')
    } catch (e) { setInviteResult(`❌ ${e.message}`) }
  }
  const doBackfill = async () => {
    setBackfillResult('Running...')
    try {
      const r = await authFetch(`/api/v1/admin/backfill-risk-scores?exam_id=${encodeURIComponent(currentExamId)}`, { method: 'POST' })
      if (!r.ok) throw await responseError(r, 'Backfill failed')
      const d = await r.json()
      setBackfillResult(`✅ Backfilled ${d.backfilled || 0} sessions`)
      window.parent.postMessage({ type: 'backfill_done', backfilled: d.backfilled }, window.location.origin) // nosemgrep: javascript.browser.security.wildcard-postmessage-configuration.wildcard-postmessage-configuration
    } catch (err) { setBackfillResult(err.message || 'Backfill failed') }
  }

  const copyLink = (url) => {
    navigator.clipboard.writeText(url)
      .then(() => setCopyResult('Copied.'))
      .catch(() => setCopyResult('Copy failed. Select the link and copy manually.'))
  }

  if (!currentExamId) return <div style={{ textAlign: 'center', padding: 60, color: 'var(--text-muted)' }}>Select an exam to access tools.</div>

  const appUrl = import.meta.env.VITE_APP_URL || 'https://app.procta.net'
  const shareUrl = `${appUrl}/register?teacher_id=`
  const downloadUrl = `${appUrl}/download`

  return (
    <div className="tools-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 16 }}>
      {loadError && <div className="auth-err" style={{ gridColumn: '1 / -1', marginBottom: 8 }}>{loadError} <button className="btn-link" onClick={loadAll} style={{ marginLeft: 8 }}>Retry</button></div>}
      {copyResult && <div className="auth-ok" style={{ gridColumn: '1 / -1', marginBottom: 8 }}>{copyResult}</div>}
      {/* Share links */}
      <ToolCard title="Share With Students" desc="Registration and download links for students.">
        <div style={{ marginBottom: 8 }}>
          <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 4, fontWeight: 600, textTransform: 'uppercase' }}>Register Link</label>
          <div style={{ display: 'flex', gap: 6 }}>
            <input className="input" style={{ flex: 1, fontSize: 11 }} value={shareUrl} readOnly />
            <button className="btn btn-primary btn-sm" onClick={() => copyLink(shareUrl)}>Copy</button>
          </div>
        </div>
        <div>
          <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 4, fontWeight: 600, textTransform: 'uppercase' }}>Download Link</label>
          <div style={{ display: 'flex', gap: 6 }}>
            <input className="input" style={{ flex: 1, fontSize: 11 }} value={downloadUrl} readOnly />
            <button className="btn btn-primary btn-sm" onClick={() => copyLink(downloadUrl)}>Copy</button>
          </div>
        </div>
      </ToolCard>

      {/* Email invites */}
      <ToolCard title="Email Invites" desc="Bulk-invite students via email.">
        <textarea className="input" style={{ width: '100%', minHeight: 80, resize: 'vertical', marginBottom: 8, fontSize: 12, fontFamily: 'monospace' }} placeholder="name, email, roll_number (one per line)" value={inviteText} onChange={(e) => setInviteText(e.target.value)} />
        {inviteResult && <div style={{ fontSize: 12, marginBottom: 6 }}>{inviteResult}</div>}
        <button className="btn btn-primary btn-sm" onClick={sendInvites}>Send Invites</button>
      </ToolCard>

      {/* Per-student randomization */}
      <ToolCard title="Per-Student Randomization" desc="Randomize question/option order per student.">
        <div className="shuffle-toggle">
          <input type="checkbox" checked={shuffleQ} onChange={() => setShuffleQ(!shuffleQ)} />
          <span>Shuffle question order</span>
        </div>
        <div className="shuffle-toggle">
          <input type="checkbox" checked={shuffleO} onChange={() => setShuffleO(!shuffleO)} />
          <span>Shuffle option order</span>
        </div>
        {shuffleResult && <div style={{ fontSize: 12, marginTop: 6 }}>{shuffleResult}</div>}
        <button className="btn btn-primary btn-sm" onClick={saveShuffle} style={{ marginTop: 8 }}>Save</button>
      </ToolCard>

      {/* Proctoring sensitivity */}
      <ToolCard title="Detection Sensitivity" desc="Controls how strictly the AI flags potential violations. Stricter = more flags, lenient = fewer false positives.">
        {sensitivityLoaded ? (
          <div>
            <select className="input" style={{ width: '100%', marginBottom: 8 }} value={sensitivity} onChange={(e) => setSensitivity(e.target.value)}>
              <option value="strict">Strict — flag every possible violation</option>
              <option value="balanced">Balanced — recommended default</option>
              <option value="lenient">Lenient — fewer flags, more false negatives</option>
            </select>
            {sensitivityResult && <div style={{ fontSize: 12, marginBottom: 6 }}>{sensitivityResult}</div>}
            <button className="btn btn-primary btn-sm" onClick={async () => {
              try {
                const r = await authFetch('/api/v1/admin/proctoring-sensitivity', {
                  method: 'POST', headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ exam_id: currentExamId, proctoring_sensitivity: sensitivity }),
                })
                if (!r.ok) throw await responseError(r, 'Failed to save sensitivity')
                setSensitivityResult('✅ Sensitivity saved')
              } catch (err) {
                setSensitivityResult(err.message || 'Failed')
              }
            }}>Save Sensitivity</button>
          </div>
        ) : <div style={{ fontSize: 12, color: 'var(--muted)' }}>Loading...</div>}
      </ToolCard>

      {/* Access code */}
      <ToolCard title="Exam Access Code" desc="Set a shared access code students enter to start the exam.">
        <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
          <input className="input" style={{ flex: 1, fontSize: 16, fontWeight: 700, letterSpacing: 2, textTransform: 'uppercase', fontFamily: 'monospace' }} value={accessCode} onChange={(e) => setAccessCode(e.target.value.toUpperCase())} placeholder="e.g. ABC123" />
        </div>
        {accessResult && <div style={{ fontSize: 12, marginBottom: 6 }}>{accessResult}</div>}
        <div style={{ display: 'flex', gap: 6 }}>
          <button className="btn btn-primary btn-sm" onClick={saveAccess}>Save</button>
          <button className="btn btn-secondary btn-sm" onClick={generateAccess}>Generate</button>
          <button className="btn btn-secondary btn-sm" onClick={clearAccess} style={{ color: 'var(--red)' }}>Clear</button>
        </div>
      </ToolCard>

      {/* Schedule */}
      <ToolCard title="Schedule Exam" desc="Set start/end times. Students can't begin outside this window.">
        <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
          <div style={{ flex: 1 }}>
            <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>Start</label>
            <input type="datetime-local" className="input" style={{ width: '100%' }} value={scheduleStart} onChange={(e) => setScheduleStart(e.target.value)} />
          </div>
          <div style={{ flex: 1 }}>
            <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>End</label>
            <input type="datetime-local" className="input" style={{ width: '100%' }} value={scheduleEnd} onChange={(e) => setScheduleEnd(e.target.value)} />
          </div>
        </div>
        {scheduleResult && <div style={{ fontSize: 12, marginBottom: 6 }}>{scheduleResult}</div>}
        <div style={{ display: 'flex', gap: 6 }}>
          <button className="btn btn-primary btn-sm" onClick={saveSchedule}>Save</button>
        </div>
      </ToolCard>

      {/* Backfill */}
      <ToolCard title="Backfill Risk Scores" desc="Recompute risk scores for all completed sessions.">
        {backfillResult && <div style={{ fontSize: 12, marginBottom: 6 }}>{backfillResult}</div>}
        <button className="btn btn-primary btn-sm" onClick={doBackfill}>Run Backfill</button>
      </ToolCard>

      {/* Clear sessions */}
      <ToolCard title="Clear Live Sessions" desc="Erase stale/active test sessions from the dashboard.">
        {clearSessionsResult && <div style={{ fontSize: 12, marginBottom: 6 }}>{clearSessionsResult}</div>}
        <button className="btn btn-secondary btn-sm" style={{ color: 'var(--red)', borderColor: 'rgba(239,68,68,0.35)' }} onClick={() => {
          if (!confirm('This will clear all live sessions. Continue?')) return
          setClearSessionsResult('Clearing...')
          authFetch(`/api/v1/admin/sessions/clear-live`, { method: 'POST' }).then(async r => {
            if (!r.ok) throw await responseError(r, 'Failed to clear sessions')
            setClearSessionsResult('✅ Sessions cleared')
            window.parent.postMessage({ type: 'sessions_cleared' }, window.location.origin) // nosemgrep: javascript.browser.security.wildcard-postmessage-configuration.wildcard-postmessage-configuration
          }).catch(err => setClearSessionsResult(err.message || 'Failed to clear sessions'))
        }}>Clear Sessions</button>
      </ToolCard>
    </div>
  )
}

function ToolCard({ title, desc, children }) {
  return (
    <div className="tool-card">
      <div className="tool-card-body">
        <h3 style={{ margin: '0 0 4px' }}>{title}</h3>
        <p style={{ fontSize: 12, color: 'var(--text-secondary)', margin: '0 0 12px' }}>{desc}</p>
        {children}
      </div>
    </div>
  )
}
