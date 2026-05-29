import { useState, useRef, useCallback } from 'react'
import { useAuth } from '../lib/auth'
import { API_BASE } from '../config'

const STEP_UPLOAD = 1
const STEP_PREVIEW = 2
const STEP_RESULT = 3

export default function BulkImportPanel() {
  const { user, authFetch } = useAuth()
  const fileRef = useRef(null)
  const [step, setStep] = useState(STEP_UPLOAD)
  const [file, setFile] = useState(null)
  const [preview, setPreview] = useState(null)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [dragging, setDragging] = useState(false)

  const reset = useCallback(() => {
    setStep(STEP_UPLOAD)
    setFile(null)
    setPreview(null)
    setResult(null)
    setError('')
    setBusy(false)
  }, [])

  const handleFileDrop = useCallback((e) => {
    e.preventDefault()
    setDragging(false)
    const f = e.dataTransfer?.files?.[0] || e.target?.files?.[0]
    if (f && f.name.toLowerCase().endsWith('.csv')) {
      setFile(f)
      setError('')
    } else {
      setError('Please select a .csv file')
    }
  }, [])

  const previewImport = useCallback(async () => {
    if (!file) return
    setBusy(true)
    setError('')
    try {
      const form = new FormData()
      form.append('file', file)
      form.append('dry_run', 'true')
      const r = await authFetch('/api/v1/admin/students/import-csv', {
        method: 'POST',
        body: form,
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || `Upload failed (${r.status})`)
      }
      const data = await r.json()
      setPreview(data)
      setStep(STEP_PREVIEW)
    } catch (e) {
      setError(e.message || 'Preview failed')
    } finally {
      setBusy(false)
    }
  }, [file, authFetch])

  const confirmImport = useCallback(async () => {
    if (!file) return
    setBusy(true)
    setError('')
    try {
      const form = new FormData()
      form.append('file', file)
      form.append('dry_run', 'false')
      const r = await authFetch('/api/v1/admin/students/import-csv', {
        method: 'POST',
        body: form,
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || `Import failed (${r.status})`)
      }
      const data = await r.json()
      setResult(data)
      setStep(STEP_RESULT)
    } catch (e) {
      setError(e.message || 'Import failed')
    } finally {
      setBusy(false)
    }
  }, [file, authFetch])

  const goToMembers = () => {
    window.location.hash = 'members'
  }

  const invalidRows = preview?.invalid || []
  const formatCounts = preview?.format_counts || {}
  const formatEntries = Object.entries(formatCounts)

  return (
    <div>
      <div className="table-toolbar">
        <span style={{ fontSize: 16, fontWeight: 600, color: 'var(--text-primary)' }}>
          Import Students
        </span>
        {step > STEP_UPLOAD && (
          <button className="btn btn-ghost btn-sm" onClick={reset} style={{ marginLeft: 'auto' }}>
            Start Over
          </button>
        )}
      </div>

      {error && (
        <div style={{ fontSize: 12, color: 'var(--red)', marginBottom: 8, padding: '6px 10px', background: 'rgba(239,68,68,.08)', borderRadius: 6 }}>
          {error}
        </div>
      )}

      {step === STEP_UPLOAD && (
        <div>
          <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 12 }}>
            Upload a CSV file with student details. Required columns:{' '}
            <strong>roll_number</strong>, <strong>full_name</strong>,{' '}
            <strong>email</strong>. Optional: <strong>phone</strong>.
          </p>

          <div
            onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
            onDragLeave={() => setDragging(false)}
            onDrop={handleFileDrop}
            onClick={() => fileRef.current?.click()}
            style={{
              border: `2px dashed ${dragging ? 'var(--accent)' : 'var(--border-subtle)'}`,
              borderRadius: 8,
              padding: '40px 20px',
              textAlign: 'center',
              cursor: 'pointer',
              background: dragging ? 'rgba(99,102,241,.04)' : 'transparent',
              transition: 'border-color .15s, background .15s',
            }}
          >
            <input
              ref={fileRef}
              type="file"
              accept=".csv"
              style={{ display: 'none' }}
              onChange={handleFileDrop}
            />
            <div style={{ fontSize: 14, color: 'var(--text-muted)', marginBottom: 6 }}>
              {file ? file.name : 'Drop a CSV here or click to browse'}
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
              {file ? `${(file.size / 1024).toFixed(0)} KB` : 'Max 1 MB, 500 rows'}
            </div>
          </div>

          <div style={{ marginTop: 16, display: 'flex', gap: 8, alignItems: 'center' }}>
            <button
              className="btn btn-primary btn-sm"
              disabled={!file || busy}
              onClick={previewImport}
            >
              {busy ? 'Uploading...' : 'Preview'}
            </button>
            <a
              href={`${API_BASE}/admin/students/csv-template`}
              className="btn btn-ghost btn-sm"
              style={{ textDecoration: 'none' }}
              download
            >
              Download sample CSV
            </a>
          </div>
        </div>
      )}

      {step === STEP_PREVIEW && preview && (
        <div>
          <div className="stats-bar" style={{ marginBottom: 16 }}>
            {preview.would_register !== undefined && (
              <div className="stat-tile">
                <div className="stat-tile-label">Will Register</div>
                <div className="stat-tile-value accent">{preview.would_register}</div>
              </div>
            )}
            {invalidRows.length > 0 && (
              <div className="stat-tile">
                <div className="stat-tile-label">Invalid Rows</div>
                <div className="stat-tile-value" style={{ color: 'var(--red)' }}>{invalidRows.length}</div>
              </div>
            )}
            {preview.dominant_format_label && (
              <div className="stat-tile">
                <div className="stat-tile-label">Format</div>
                <div className="stat-tile-value">{preview.dominant_format_label}</div>
              </div>
            )}
          </div>

          {formatEntries.length > 0 && (
            <div style={{ marginBottom: 12, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {formatEntries.map(([fmt, count]) => (
                <span
                  key={fmt}
                  style={{
                    fontSize: 11,
                    padding: '3px 8px',
                    borderRadius: 4,
                    background: 'var(--surface-2)',
                    color: 'var(--text-secondary)',
                    border: '1px solid var(--border-subtle)',
                  }}
                >
                  {fmt}: {count}
                </span>
              ))}
            </div>
          )}

          {invalidRows.length > 0 && (
            <div style={{ marginBottom: 16 }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--red)', marginBottom: 6 }}>
                {invalidRows.length} row{invalidRows.length > 1 ? 's' : ''} with errors
              </div>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                <thead>
                  <tr style={{ background: 'var(--surface-1)', borderBottom: '1px solid var(--border-subtle)' }}>
                    <th style={{ padding: '6px 10px', textAlign: 'left', fontWeight: 600, color: 'var(--text-muted)' }}>Roll Number</th>
                    <th style={{ padding: '6px 10px', textAlign: 'left', fontWeight: 600, color: 'var(--text-muted)' }}>Errors</th>
                  </tr>
                </thead>
                <tbody>
                  {invalidRows.map((row, i) => (
                    <tr key={i} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                      <td style={{ padding: '6px 10px' }}>{row.roll_number}</td>
                      <td style={{ padding: '6px 10px', color: 'var(--red)' }}>
                        {row.errors?.join(', ')}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <button
              className="btn btn-primary btn-sm"
              disabled={busy || preview.would_register === 0}
              onClick={confirmImport}
            >
              {busy ? 'Importing...' : 'Confirm Import'}
            </button>
            <button className="btn btn-ghost btn-sm" onClick={reset}>
              Choose Different File
            </button>
          </div>
        </div>
      )}

      {step === STEP_RESULT && result && (
        <div>
          <div className="stats-bar" style={{ marginBottom: 16 }}>
            <div className="stat-tile">
              <div className="stat-tile-label">Registered</div>
              <div className="stat-tile-value emerald">{result.registered}</div>
            </div>
            <div className="stat-tile">
              <div className="stat-tile-label">Skipped</div>
              <div className="stat-tile-value" style={{ color: 'var(--amber)' }}>{result.skipped}</div>
            </div>
            <div className="stat-tile">
              <div className="stat-tile-label">Format</div>
              <div className="stat-tile-value">{result.dominant_format_label}</div>
            </div>
          </div>

          {result.invalid && result.invalid.length > 0 && (
            <div style={{ marginBottom: 16, fontSize: 12, color: 'var(--amber)' }}>
              {result.invalid.length} row{result.invalid.length > 1 ? 's' : ''} skipped due to validation errors.
            </div>
          )}

          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <button className="btn btn-primary btn-sm" onClick={goToMembers}>
              View Members
            </button>
            <button className="btn btn-ghost btn-sm" onClick={reset}>
              Import Another File
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
