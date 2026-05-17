import { useState, useEffect } from 'react'
import { useAuth } from '../lib/auth'

export default function AnalyticsPanel({ currentExamId }) {
  const { authFetch } = useAuth()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => { if (currentExamId) loadAnalytics() }, [currentExamId])

  const loadAnalytics = async () => {
    if (!currentExamId) { setLoading(false); return }
    setError('')
    try {
      const r = await authFetch(`/api/v1/admin/analytics?exam_id=${encodeURIComponent(currentExamId)}`)
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || `Failed to load analytics (${r.status})`)
      }
      setData(await r.json())
    } catch (e) {
      setError(e.message || 'Failed to load analytics')
    } finally { setLoading(false) }
  }

  if (!currentExamId) return <div style={{ textAlign: 'center', padding: 60, color: 'var(--text-muted)' }}>Select an exam to view analytics.</div>
  if (loading) return <div className="loading" style={{ textAlign: 'center', padding: 60 }}>Loading analytics...</div>
  if (error) return <div className="auth-err" style={{ margin: 20 }}>{error} <button className="btn-link" onClick={loadAnalytics} style={{ marginLeft: 8 }}>Retry</button></div>

  const { exam_overview: overview, score_distribution: score_dist, risk_distribution: risk_dist, violation_summary: violations_by_type, question_analysis: per_question } = data || {}
  const maxScoreDist = Math.max(...(score_dist || []).map(b => b.count), 1)
  const maxRiskDist = Math.max(...(risk_dist || []).map(b => b.count), 1)

  return (
    <div>
      {/* Overview chips */}
      <div className="ax-stat-chips">
        {[
          { label: 'Completed', value: overview?.count || 0 },
          { label: 'Avg Score', value: overview?.avg_percentage != null ? `${Math.round(overview.avg_percentage)}%` : '—' },
          { label: 'Pass Rate', value: overview?.pass_rate != null ? `${overview.pass_rate}%` : '—' },
          { label: 'High Risk', value: risk_dist?.high || 0 },
          { label: 'Total Violations', value: violations_by_type?.total || 0 },
          { label: 'Median Time', value: overview?.median_time_secs != null ? `${Math.round(overview.median_time_secs / 60)}m` : '—' },
        ].map(s => (
          <div className="ax-stat-chip" key={s.label}>
            <div className="ax-stat-chip-val">{s.value}</div>
            <div style={{ fontSize: 10, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 0.05 }}>{s.label}</div>
          </div>
        ))}
      </div>

      <div className="ax-page-grid">
        {/* Score distribution */}
        <div className="ax-card">
          <div className="ax-card-header">
            <div className="ax-card-title">Score Distribution</div>
            <div className="ax-card-sub">How students scored across the exam</div>
          </div>
          <div className="ax-card-full" style={{ height: 160, display: 'flex', alignItems: 'flex-end', gap: 4, padding: '16px 8px' }}>
            {(score_dist || []).map((b, i) => (
              <div key={i} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
                <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>{b.count}</div>
                <div style={{ height: `${(b.count / maxScoreDist) * 120}px`, background: 'linear-gradient(180deg, var(--accent), var(--accent-dark))', borderRadius: '4px 4px 0 0', width: '100%', minHeight: b.count > 0 ? 4 : 0 }} />
                <div style={{ fontSize: 9, color: 'var(--text-muted)', transform: 'rotate(-45deg)', transformOrigin: 'left', whiteSpace: 'nowrap' }}>{b.bucket}</div>
              </div>
            ))}
          </div>
        </div>

        {/* Risk distribution */}
        <div className="ax-card">
          <div className="ax-card-header">
            <div className="ax-card-title">Risk Distribution</div>
            <div className="ax-card-sub">Risk score spread across all students</div>
          </div>
          <div className="ax-card-full" style={{ height: 160, display: 'flex', alignItems: 'flex-end', gap: 4, padding: '16px 8px' }}>
            {(risk_dist || []).map((b, i) => (
              <div key={i} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
                <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>{b.count}</div>
                <div style={{ height: `${(b.count / maxRiskDist) * 120}px`, background: b.label?.includes('High') ? 'var(--sev-error-fg)' : b.label?.includes('Moderate') ? 'var(--sev-warn-fg)' : 'var(--accent)', borderRadius: '4px 4px 0 0', width: '100%', minHeight: b.count > 0 ? 4 : 0 }} />
                <div style={{ fontSize: 9, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>{b.label}</div>
              </div>
            ))}
          </div>
        </div>

        {/* Violations by type */}
        <div className="ax-card">
          <div className="ax-card-header">
            <div className="ax-card-title">Violations by Type</div>
            <div className="ax-card-sub">Most frequent anomalies detected</div>
          </div>
          <div className="ax-card-full" style={{ padding: 12 }}>
            {(violations_by_type || []).slice(0, 10).map(v => (
              <div key={v.type} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '6px 0', borderBottom: '1px solid var(--border-subtle)', fontSize: 12 }}>
                <span style={{ color: 'var(--text)' }}>{v.type?.replace(/_/g, ' ')}</span>
                <span style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>{v.count}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Question difficulty analysis */}
        <div className="ax-card">
          <div className="ax-card-header">
            <div className="ax-card-title">Question Difficulty</div>
            <div className="ax-card-sub">Hardest questions by correct rate (lower = harder). Discrimination measures how well a question separates top vs bottom students.</div>
          </div>
          <div className="ax-card-full" style={{ padding: 0, overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr style={{ background: 'var(--surface-1)', borderBottom: '1px solid var(--border-subtle)' }}>
                  <th style={{ padding: '8px 10px', textAlign: 'left', color: 'var(--text-muted)', fontWeight: 600, fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.04 }}>Question</th>
                  <th style={{ padding: '8px 10px', textAlign: 'center', color: 'var(--text-muted)', fontWeight: 600, fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.04 }}>Correct %</th>
                  <th style={{ padding: '8px 10px', textAlign: 'center', color: 'var(--text-muted)', fontWeight: 600, fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.04 }}>Discrimination</th>
                  <th style={{ padding: '8px 10px', textAlign: 'center', color: 'var(--text-muted)', fontWeight: 600, fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.04 }}>Attempted</th>
                </tr>
              </thead>
              <tbody>
                {(per_question || []).sort((a, b) => (a.difficulty_pct || 0) - (b.difficulty_pct || 0)).slice(0, 20).map(q => (
                  <tr key={q.question_id} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                    <td style={{ padding: '8px 10px', maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{q.question || `Q${q.question_id}`}</td>
                    <td style={{ padding: '8px 10px', textAlign: 'center' }}>
                      <span style={{ color: (q.difficulty_pct || 0) < 40 ? 'var(--red)' : (q.difficulty_pct || 0) < 70 ? 'var(--amber)' : 'var(--emerald)' }}>
                        {q.difficulty_pct != null ? `${q.difficulty_pct}%` : '—'}
                      </span>
                    </td>
                    <td style={{ padding: '8px 10px', textAlign: 'center' }}>
                      {q.discrimination != null ? (
                        <span title=">0.3 = good discriminator">{q.discrimination > 0.3 ? '✓' : q.discrimination > 0.1 ? '~' : '—'}</span>
                      ) : '—'}
                    </td>
                    <td style={{ padding: '8px 10px', textAlign: 'center', color: 'var(--text-muted)', fontFamily: 'monospace' }}>{q.attempted || 0}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  )
}
