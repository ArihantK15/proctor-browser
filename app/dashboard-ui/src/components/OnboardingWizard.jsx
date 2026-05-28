import { useState } from 'react'
import { useAuth } from '../lib/auth'

const STEPS = [
  { id: 'welcome', label: 'Welcome' },
  { id: 'exam', label: 'Create Exam' },
  { id: 'import', label: 'Add Students' },
  { id: 'invite', label: 'Send Invites' },
  { id: 'demo', label: 'Test It' },
  { id: 'done', label: 'Go Live' },
]

export default function OnboardingWizard({ onComplete }) {
  const { authFetch } = useAuth()
  const [step, setStep] = useState(0)
  const [examTitle, setExamTitle] = useState('Midterm Exam')
  const [duration, setDuration] = useState(60)
  const [accessCode, setAccessCode] = useState('')
  const [creating, setCreating] = useState(false)
  const [createdExamId, setCreatedExamId] = useState(null)
  const [csvText, setCsvText] = useState('')
  const [importing, setImporting] = useState(false)
  const [importResult, setImportResult] = useState(null)
  const [inviting, setInviting] = useState(false)
  const [inviteResult, setInviteResult] = useState(null)
  const [students, setStudents] = useState([])

  const createExam = async () => {
    setCreating(true)
    try {
      const r = await authFetch('/api/v1/admin/exams', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          exam_title: examTitle,
          duration_minutes: duration,
          phone_camera: false,
        }),
      })
      if (r.ok) {
        const d = await r.json()
        setCreatedExamId(d.exam_id)
        if (accessCode) {
          await authFetch('/api/v1/admin/access-code', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ exam_id: d.exam_id, access_code: accessCode }),
          })
        }
        setStep(2)
      }
    } catch (_) {} finally { setCreating(false) }
  }

  const importStudents = async () => {
    setImporting(true)
    setImportResult(null)
    try {
      const lines = csvText.trim().split('\n').filter(Boolean)
      if (lines.length < 2) {
        setImportResult({ ok: false, msg: 'CSV must have a header row and at least one student.' })
        setImporting(false)
        return
      }
      const headers = lines[0].split(',').map(h => h.trim().toLowerCase())
      const students = lines.slice(1).map(line => {
        const vals = line.split(',').map(v => v.trim())
        const obj = {}
        headers.forEach((h, i) => { obj[h] = vals[i] || '' })
        return obj
      }).filter(s => s.roll_number && s.full_name && s.email)
      if (!students.length) {
        setImportResult({ ok: false, msg: 'No valid rows found. Required columns: roll_number, full_name, email.' })
        setImporting(false)
        return
      }
      const r = await authFetch('/api/v1/admin/register-students-bulk', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ exam_id: createdExamId, students }),
      })
      if (r.ok) {
        const d = await r.json()
        setStudents(students)
        setImportResult({ ok: true, msg: `${d.registered || 0} registered, ${d.skipped || 0} skipped` })
        setTimeout(() => setStep(3), 1500)
      } else {
        const d = await r.json()
        setImportResult({ ok: false, msg: d.detail || 'Import failed' })
      }
    } catch (e) {
      setImportResult({ ok: false, msg: e.message })
    } finally { setImporting(false) }
  }

  const sendInvites = async () => {
    if (!createdExamId || !students.length) {
      setInviteResult({ ok: false, msg: 'Import students first.' })
      return
    }
    setInviting(true)
    setInviteResult(null)
    try {
      const r = await authFetch('/api/v1/admin/invites/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ exam_id: createdExamId, recipients: students }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(d.detail || 'Failed to send invites')
      setInviteResult({ ok: true, msg: `Sent ${d.sent || 0}, skipped ${d.skipped || 0}, failed ${d.failed || 0}` })
      setTimeout(() => setStep(4), 1200)
    } catch (e) {
      setInviteResult({ ok: false, msg: e.message || 'Failed to send invites' })
    } finally {
      setInviting(false)
    }
  }

  const skipInvites = () => setStep(4)

  return (
    <div className="app-shell">
      <div className="topbar">
        <div className="topbar-brand">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" style={{ color: 'var(--accent)' }}>
            <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/>
          </svg>
          <span style={{ fontWeight: 600, fontSize: 14 }}>Get Started</span>
        </div>
        <div className="topbar-actions">
          <button className="btn btn-ghost btn-sm" onClick={() => onComplete()}>Skip — go to dashboard</button>
        </div>
      </div>
      <div className="container" style={{ padding: '40px 24px', display: 'flex', justifyContent: 'center' }}>
        <div className="card" style={{ maxWidth: 560, width: '100%', padding: 32 }}>
          {/* Progress */}
          <div style={{ display: 'flex', gap: 4, marginBottom: 24 }}>
            {STEPS.map((s, i) => (
              <div key={s.id} title={s.label} style={{ flex: 1, height: 3, borderRadius: 2, background: i <= step ? 'var(--accent)' : 'var(--border-subtle)', transition: 'background 0.3s' }} />
            ))}
          </div>

        {/* Step 0: Welcome */}
        {step === 0 && (
          <div>
            <h2 style={{ marginBottom: 8 }}>Welcome to Procta!</h2>
            <p style={{ color: 'var(--text-secondary)', fontSize: 14, marginBottom: 20, lineHeight: 1.6 }}>
              Let's set up your first exam in under 5 minutes. You'll create an exam, add students,
              and send invites. We'll also show you how to test everything before the real thing.
            </p>
            <p style={{ color: 'var(--text-secondary)', fontSize: 13, marginBottom: 24 }}>
              Don't worry — you can always change settings later from the dashboard.
            </p>
            <button className="btn btn-primary" onClick={() => setStep(1)}>Get Started</button>
          </div>
        )}

        {/* Step 1: Create exam */}
        {step === 1 && (
          <div>
            <h2 style={{ marginBottom: 8 }}>Create your first exam</h2>
            <p style={{ color: 'var(--text-secondary)', fontSize: 13, marginBottom: 20 }}>Set the basics — you can always add questions and settings later.</p>
            <div className="fg"><label>Exam title</label><input type="text" value={examTitle} onChange={e => setExamTitle(e.target.value)} style={{ width: '100%' }} /></div>
            <div className="fg"><label>Duration (minutes)</label><input type="number" value={duration} onChange={e => setDuration(Number(e.target.value))} style={{ width: '100%' }} min={5} max={300} /></div>
            <div className="fg"><label>Access code (optional)</label><input type="text" value={accessCode} onChange={e => setAccessCode(e.target.value.toUpperCase())} style={{ width: '100%', textTransform: 'uppercase', fontFamily: 'monospace' }} placeholder="e.g. EXAM2024" /></div>
            <div className="modal-actions" style={{ marginTop: 20 }}>
              <button className="btn btn-secondary" onClick={() => setStep(0)}>Back</button>
              <button className="btn btn-primary" disabled={creating || !examTitle.trim()} onClick={createExam} style={{ flex: 1 }}>
                {creating ? 'Creating...' : 'Create Exam'}
              </button>
            </div>
          </div>
        )}

        {/* Step 2: Import students */}
        {step === 2 && (
          <div>
            <h2 style={{ marginBottom: 8 }}>Add students</h2>
            <p style={{ color: 'var(--text-secondary)', fontSize: 13, marginBottom: 8 }}>Paste CSV with columns: <code>roll_number,full_name,email</code></p>
            <textarea value={csvText} onChange={e => setCsvText(e.target.value)}
              rows={6} style={{ width: '100%', padding: 10, borderRadius: 8, border: '1px solid var(--border-subtle)', fontSize: 13, fontFamily: 'monospace', resize: 'vertical' }}
              placeholder={`roll_number,full_name,email\nSTU001,Alice Johnson,alice@example.edu\nSTU002,Bob Smith,bob@example.edu`} />
            <div style={{ marginTop: 8, fontSize: 12, color: 'var(--text-secondary)' }}>
              <button className="btn btn-ghost btn-sm" onClick={() => setCsvText('roll_number,full_name,email\nSTU001,Alice Johnson,alice@example.edu\nSTU002,Bob Smith,bob@example.edu')} type="button">
                Use sample rows
              </button>
            </div>
            {importResult && (
              <div style={{ marginTop: 8, fontSize: 13, color: importResult.ok ? 'var(--emerald)' : 'var(--red)' }}>{importResult.msg}</div>
            )}
            <div className="modal-actions" style={{ marginTop: 16 }}>
              <button className="btn btn-secondary" onClick={() => setStep(1)}>Back</button>
              <button className="btn btn-primary" disabled={importing || !csvText.trim()} onClick={importStudents} style={{ flex: 1 }}>
                {importing ? 'Importing...' : 'Import Students'}
              </button>
            </div>
          </div>
        )}

        {/* Step 3: Send invites */}
        {step === 3 && (
          <div>
            <h2 style={{ marginBottom: 8 }}>Send invites</h2>
            <p style={{ color: 'var(--text-secondary)', fontSize: 14, marginBottom: 20, lineHeight: 1.6 }}>
              Send email invites to the {students.length} student{students.length === 1 ? '' : 's'} you imported.
              Each invite includes the exam link and desktop app download.
            </p>
            {inviteResult && (
              <div style={{ marginBottom: 12, fontSize: 13, color: inviteResult.ok ? 'var(--emerald)' : 'var(--red)' }}>{inviteResult.msg}</div>
            )}
            <div className="modal-actions" style={{ marginTop: 20 }}>
              <button className="btn btn-secondary" onClick={() => setStep(2)}>Back</button>
              <button className="btn btn-secondary" onClick={skipInvites}>Skip</button>
              <button className="btn btn-primary" disabled={inviting || !students.length} onClick={sendInvites} style={{ flex: 1 }}>
                {inviting ? 'Sending...' : 'Send Invites'}
              </button>
            </div>
          </div>
        )}

        {/* Step 4: Run demo */}
        {step === 4 && (
          <div>
            <h2 style={{ marginBottom: 8 }}>Try it yourself</h2>
            <p style={{ color: 'var(--text-secondary)', fontSize: 14, marginBottom: 20, lineHeight: 1.6 }}>
              Before the real exam, run a practice test to make sure everything works.
              The demo exam uses sample questions so you can verify the full flow.
            </p>
	            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
	              {createdExamId && (
	                <a href={`/dashboard-react?exam=${createdExamId}`} className="btn btn-primary" style={{ textAlign: 'center', textDecoration: 'none' }}>
	                  Add questions to your new exam
	                </a>
	              )}
	              <a href="/download" target="_blank" rel="noreferrer" className="btn btn-secondary" style={{ textAlign: 'center', textDecoration: 'none' }}>
	                Download student app
	              </a>
	              <a href="/student#practice" target="_blank" rel="noreferrer" className="btn btn-secondary" style={{ textAlign: 'center', textDecoration: 'none' }}>
	                Run practice exam
	              </a>
	            </div>
            <button className="btn btn-primary" style={{ marginTop: 16, width: '100%' }} onClick={() => setStep(5)}>I'm done — go to dashboard</button>
          </div>
        )}

        {/* Step 5: Done */}
        {step === 5 && (
          <div style={{ textAlign: 'center' }}>
            <h2 style={{ marginBottom: 8 }}>You're all set!</h2>
            <p style={{ color: 'var(--text-secondary)', fontSize: 14, marginBottom: 24, lineHeight: 1.6 }}>
              Your exam is ready. From the dashboard you can add questions, adjust settings,
              monitor live sessions, and review results.
            </p>
            <div className="modal-actions" style={{ justifyContent: 'center' }}>
              <button className="btn btn-primary" onClick={() => onComplete(createdExamId)}>Open Dashboard</button>
            </div>
          </div>
        )}
      </div>
    </div>
    </div>
  )
}
