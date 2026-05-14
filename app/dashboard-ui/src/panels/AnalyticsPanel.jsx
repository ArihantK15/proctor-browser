import { useState, useEffect } from 'react'
import { useAuth } from '../lib/auth'

export default function AnalyticsPanel({ currentExamId }) {
  const { authFetch } = useAuth()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => { if (currentExamId) loadAnalytics() }, [currentExamId])

  const loadAnalytics = async () => {
    if (!currentExamId) { setLoading(false); return }
    try {
      const r = await authFetch(`/api/v1/admin/analytics?exam_id=${encodeURIComponent(currentExamId)}`)
      if (r.ok) setData(await r.json())
    } catch (_) {}
    finally { setLoading(false) }
  }

  if (!currentExamId) return <div style={{ textAlign: 'center', padding: 60, color: 'var(--text-muted)' }}>Select an exam to view analytics.</div>
  if (loading) return <div className="loading" style={{ textAlign: 'center', padding: 60 }}>Loading analytics...</div>

  const { overview, score_dist, risk_dist, violations_by_type, per_question } = data || {}
  const maxScoreDist = Math.max(...(score_dist || []).map(b => b.count), 1)
  const maxRiskDist = Math.max(...(risk_dist || []).map(b => b.count), 1)

  return (
    <div>
      {/* Overview chips */}
      <div className="ax-stat-chips">
        {[
          { label: 'Completed', value: overview?.completed || 0 },
          { label: 'Avg Score', value: overview?.avg_score != null ? `${Math.round(overview.avg_score)}%` : '—' },
          { label: 'Avg Risk', value: overview?.avg_risk != null ? Math.round(overview.avg_risk) : '—' },
          { label: 'High Risk', value: overview?.high_risk || 0 },
          { label: 'Total Violations', value: overview?.total_violations || 0 },
          { label: 'Avg Time', value: overview?.avg_time_min != null ? `${Math.round(overview.avg_time_min)}m` : '—' },
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

        {/* Per-question breakdown */}
        <div className="ax-card">
          <div className="ax-card-header">
            <div className="ax-card-title">Per-Question Breakdown</div>
            <div className="ax-card-sub">Average score per question</div>
          </div>
          <div className="ax-card-full" style={{ padding: 12 }}>
            {(per_question || []).slice(0, 15).map(q => (
              <div key={q.question_id} style={{ marginBottom: 10 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, marginBottom: 3 }}>
                  <span style={{ color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '60%' }}>{q.question || `Q${q.question_id}`}</span>
                  <span style={{ color: q.avg_score != null ? 'var(--accent-light)' : 'var(--text-muted)' }}>{q.avg_score != null ? `${Math.round(q.avg_score * 100)}%` : '—'}</span>
                </div>
                {q.avg_score != null && (
                  <div style={{ height: 4, background: 'var(--border)', borderRadius: 2, overflow: 'hidden' }}>
                    <div style={{ height: '100%', width: `${Math.round(q.avg_score * 100)}%`, background: 'var(--accent)', borderRadius: 2, transition: 'width 0.3s' }} />
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
