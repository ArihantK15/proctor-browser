import { useState, useEffect, useCallback } from 'react'
import { useAuth } from '../lib/auth'

/**
 * Issues triage inbox — super-admin only.
 *
 * Consumes /api/v1/admin/issues (added in commit 6bc1f97) so this panel
 * is purely a consumer of an existing endpoint. Patches issue status
 * via PATCH /api/v1/admin/issues/{id}.
 *
 * Renders a two-pane layout: a filter row + table on the left, an
 * inline detail view on the right when a row is selected. Mirrors the
 * legacy dashboard's panel-issues layout (dashboard.html:1717) so
 * super admins switching between legacy and React see the same UX.
 */
export default function IssuesPanel() {
  const { authFetch } = useAuth()
  const [issues, setIssues] = useState([])
  const [openCount, setOpenCount] = useState(0)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [statusFilter, setStatusFilter] = useState('open')
  const [categoryFilter, setCategoryFilter] = useState('all')
  const [orgFilter, setOrgFilter] = useState('')
  const [selected, setSelected] = useState(null)
  const [noteDraft, setNoteDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveStatus, setSaveStatus] = useState('')

  const loadIssues = useCallback(async () => {
    setLoading(true)
    setLoadError('')
    try {
      const qs = new URLSearchParams()
      if (statusFilter !== 'all') qs.set('status', statusFilter)
      if (categoryFilter !== 'all') qs.set('category', categoryFilter)
      if (orgFilter) qs.set('org_id', orgFilter)
      const r = await authFetch(`/api/v1/admin/issues?${qs}`)
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || `Failed to load issues (${r.status})`)
      }
      const d = await r.json()
      setIssues(d.issues || [])
      setOpenCount(d.open_count || 0)
    } catch (e) {
      setLoadError(e.message || 'Failed to load issues')
    } finally {
      setLoading(false)
    }
  }, [authFetch, statusFilter, categoryFilter, orgFilter])

  useEffect(() => { loadIssues() }, [loadIssues])

  const onSelect = (issue) => {
    setSelected(issue)
    setNoteDraft(issue.superadmin_note || '')
    setSaveStatus('')
  }

  const updateStatus = async (newStatus) => {
    if (!selected) return
    setSaving(true); setSaveStatus('')
    try {
      const r = await authFetch(`/api/v1/admin/issues/${selected.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: newStatus, superadmin_note: noteDraft }),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || `Update failed (${r.status})`)
      }
      const d = await r.json()
      setSelected(d.issue)
      setSaveStatus('Updated')
      await loadIssues()
    } catch (e) {
      setSaveStatus(e.message || 'Update failed')
    } finally { setSaving(false) }
  }

  const sevColor = (sev) => ({
    high: 'var(--sev-error-fg,#ef4444)',
    normal: 'var(--text-mid,#94a3b8)',
    low: 'var(--text-muted,#64748b)',
  }[sev] || 'var(--text-muted)')

  return (
    <div className="org-wrap">
      <div className="panel-header">
        <div className="panel-glow" aria-hidden="true"></div>
        <div className="panel-header-text">
          <h1 className="panel-title">Issues</h1>
          <p className="panel-lede">
            Teacher-reported bugs and feature requests, across every org.
            {openCount > 0 && <strong style={{ marginLeft: 8 }}>· {openCount} open</strong>}
          </p>
        </div>
      </div>

      <div className="table-toolbar">
        <select className="filter-select" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} title="Filter by status">
          <option value="open">Open</option>
          <option value="triaged">Triaged</option>
          <option value="resolved">Resolved</option>
          <option value="all">All statuses</option>
        </select>
        <select className="filter-select" value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)} title="Filter by category">
          <option value="all">All categories</option>
          <option value="bug">Bug</option>
          <option value="question">Question</option>
          <option value="feature">Feature request</option>
          <option value="session-issue">Session issue</option>
          <option value="other">Other</option>
        </select>
        <input
          type="text" className="search-input" placeholder="Filter by org_id..."
          value={orgFilter} onChange={(e) => setOrgFilter(e.target.value)}
          style={{ maxWidth: 260 }}
        />
        <div className="toolbar-right">
          <button className="btn btn-primary btn-sm" onClick={loadIssues} disabled={loading}>
            {loading ? 'Loading...' : 'Refresh'}
          </button>
        </div>
      </div>

      {loadError && <div style={{ color: 'var(--sev-error-fg)', padding: '12px 0', fontSize: 13 }}>{loadError}</div>}

      <div style={{ display: 'grid', gridTemplateColumns: selected ? '1fr 360px' : '1fr', gap: 16, marginTop: 12 }}>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Status</th>
                <th>Severity</th>
                <th>Org</th>
                <th>Teacher</th>
                <th>Category</th>
                <th>Description</th>
                <th>Submitted</th>
              </tr>
            </thead>
            <tbody>
              {issues.length === 0 && !loading && (
                <tr><td colSpan="7" style={{ textAlign: 'center', padding: 40, color: 'var(--text-muted)' }}>
                  No issues match the current filters.
                </td></tr>
              )}
              {issues.map((iss) => (
                <tr
                  key={iss.id}
                  onClick={() => onSelect(iss)}
                  style={{ cursor: 'pointer', background: selected?.id === iss.id ? 'rgba(91,138,240,0.08)' : undefined }}
                >
                  <td>{iss.status}</td>
                  <td style={{ color: sevColor(iss.severity) }}>{iss.severity}</td>
                  <td>{iss.org_name || iss.org_id || '—'}</td>
                  <td>{iss.teacher_name || iss.teacher_email || '—'}</td>
                  <td>{iss.category}</td>
                  <td style={{ maxWidth: 360, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {iss.description}
                  </td>
                  <td style={{ fontSize: 12, color: 'var(--text-muted)' }}>{iss.created_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {selected && (
          <aside className="org-card" style={{ padding: 16, alignSelf: 'start' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 10 }}>
              <h3 className="org-card-title" style={{ margin: 0 }}>Issue detail</h3>
              <button onClick={() => setSelected(null)} style={{ background: 'transparent', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', fontSize: 18 }}>×</button>
            </div>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>
              {selected.org_name} · {selected.teacher_name} ({selected.teacher_email})
            </div>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 12 }}>
              {selected.category} · {selected.severity} · submitted {selected.created_at}
            </div>
            <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 8, padding: 12, fontSize: 13, lineHeight: 1.5, whiteSpace: 'pre-wrap', marginBottom: 12 }}>
              {selected.description}
            </div>
            {selected.session_id && (
              <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 12 }}>
                Session: <code>{selected.session_id}</code>
                {selected.exam_id && <> · Exam: <code>{selected.exam_id}</code></>}
              </div>
            )}
            <label style={{ display: 'block', fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--text-muted)', marginBottom: 6 }}>
              Super-admin note
            </label>
            <textarea
              value={noteDraft}
              onChange={(e) => setNoteDraft(e.target.value)}
              rows={4}
              style={{ width: '100%', boxSizing: 'border-box', padding: '8px 10px', borderRadius: 6, border: '1px solid rgba(255,255,255,0.10)', background: 'rgba(255,255,255,0.03)', color: 'var(--text-high)', fontSize: 13, fontFamily: 'inherit', resize: 'vertical', marginBottom: 12 }}
            />
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {selected.status !== 'triaged' && (
                <button className="btn btn-secondary btn-sm" onClick={() => updateStatus('triaged')} disabled={saving}>Mark Triaged</button>
              )}
              {selected.status !== 'resolved' && (
                <button className="btn btn-primary btn-sm" onClick={() => updateStatus('resolved')} disabled={saving}>Mark Resolved</button>
              )}
              {selected.status !== 'open' && (
                <button className="btn btn-secondary btn-sm" onClick={() => updateStatus('open')} disabled={saving}>Reopen</button>
              )}
            </div>
            {saveStatus && <div style={{ marginTop: 10, fontSize: 12, color: saveStatus === 'Updated' ? 'var(--sev-success-fg)' : 'var(--sev-error-fg)' }}>{saveStatus}</div>}
          </aside>
        )}
      </div>
    </div>
  )
}
