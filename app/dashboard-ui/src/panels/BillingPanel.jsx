import { useState, useEffect } from 'react'
import { useAuth } from '../lib/auth'

const PLANS = [
  { id: 'starter', name: 'Starter', price: '₹2,400', students: 30, desc: 'For small classes & tutorials (₹80/student)' },
  { id: 'growth', name: 'Growth', price: '₹12,000', students: 150, desc: 'For departments & mid-size programs (₹80/student)' },
  { id: 'pro', name: 'Pro', price: '₹30,000', students: 500, desc: 'For large universities & institutions (₹80/student)' },
]

export default function BillingPanel() {
  const { authFetch } = useAuth()
  const [billing, setBilling] = useState(null)
  const [invoices, setInvoices] = useState([])
  const [usage, setUsage] = useState(null)
  const [loading, setLoading] = useState(true)
  const [upgradeStatus, setUpgradeStatus] = useState('')

  useEffect(() => {
    loadBilling()
    loadInvoices()
    loadUsage()
  }, [])

  const loadBilling = async () => {
    try {
      const r = await authFetch('/api/v1/org/billing')
      if (r.ok) setBilling(await r.json())
    } catch (_) {}
    finally { setLoading(false) }
  }

  const loadInvoices = async () => {
    try {
      const r = await authFetch('/api/v1/billing/invoices')
      if (r.ok) {
        const d = await r.json()
        setInvoices(d.invoices || [])
      }
    } catch (_) {}
  }

  const loadUsage = async () => {
    try {
      const r = await authFetch('/api/v1/billing/usage')
      if (r.ok) setUsage(await r.json())
    } catch (_) {}
  }

  const upgrade = async (planId) => {
    setUpgradeStatus('Redirecting to payment...')
    try {
      const r = await authFetch('/api/v1/billing/create-subscription', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plan_id: planId }),
      })
      if (!r.ok) throw new Error('Upgrade failed')
      const d = await r.json()
      if (d.short_url) window.location.href = d.short_url
      else setUpgradeStatus('Subscription created!')
    } catch (e) {
      setUpgradeStatus(e.message)
    }
  }

  if (loading) return <div className="loading">Loading billing...</div>

  const currentPlan = billing?.plan || 'starter'

  return (
    <div>
      <div className="stats-bar">
        <div className="stat-tile">
          <div className="stat-tile-label">Current Plan</div>
          <div className="stat-tile-value accent">{currentPlan}</div>
        </div>
        <div className="stat-tile">
          <div className="stat-tile-label">Status</div>
          <div className="stat-tile-value">{billing?.status || '--'}</div>
        </div>
        <div className="stat-tile">
          <div className="stat-tile-label">Students</div>
          <div className="stat-tile-value">{(billing?.student_count || 0)} / {(billing?.max_students || 30)}</div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 14, marginTop: 20, marginBottom: 20 }}>
        {PLANS.map(p => (
          <div
            key={p.id}
            className="tool-card"
            style={{ cursor: 'pointer', textAlign: 'center', borderColor: currentPlan === p.id ? 'var(--accent)' : undefined }}
            onClick={() => upgrade(p.id)}
          >
            <div className="tool-card-body">
              <h3>{p.name}</h3>
              <p style={{ fontSize: 20, fontWeight: 700, color: 'var(--text-high)', margin: '4px 0' }}>{p.price}</p>
              <p style={{ fontSize: 11, color: 'var(--text-muted)' }}>{p.students} students</p>
              <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 6 }}>{p.desc}</p>
            </div>
          </div>
        ))}
      </div>
      {upgradeStatus && <div style={{ fontSize: 13, color: 'var(--emerald)', marginBottom: 12 }}>{upgradeStatus}</div>}

      {/* Usage */}
      {usage && (
        <div className="card" style={{ padding: 20, marginTop: 20 }}>
          <h3 style={{ margin: '0 0 12px', fontSize: 13, textTransform: 'uppercase', letterSpacing: 0.04 }}>Current Period Usage</h3>
          <div className="stats-row" style={{ marginBottom: 0 }}>
            <div className="stat-card">
              <span className="stat-value">{usage.students_used || 0}<span style={{ fontSize: 12, color: 'var(--muted)', fontWeight: 400 }}> / {usage.plan_limit}</span></span>
              <span className="stat-label">Students</span>
            </div>
            <div className="stat-card">
              <span className="stat-value">{usage.exam_attempts || 0}</span>
              <span className="stat-label">Exam Attempts</span>
            </div>
            <div className="stat-card">
              <span className="stat-value" style={usage.overage > 0 ? { color: 'var(--red)' } : { color: 'var(--emerald)' }}>
                {usage.overage > 0 ? `₹${usage.overage_amount}` : '0'}
              </span>
              <span className="stat-label">Overage (this month)</span>
            </div>
            <div className="stat-card">
              <span className="stat-value" style={{ fontSize: 12 }}>{usage.plan_name}</span>
              <span className="stat-label">Plan</span>
            </div>
          </div>
        </div>
      )}

      {/* Invoices */}
      {invoices.length > 0 && (
        <div style={{ marginTop: 24 }}>
          <h3 style={{ color: 'var(--text-primary)', margin: '0 0 12px', fontSize: 13, textTransform: 'uppercase', letterSpacing: 0.04 }}>Invoice History</h3>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ background: 'var(--surface-1)', borderBottom: '1px solid var(--border-subtle)' }}>
                <th style={{ padding: '8px 12px', textAlign: 'left', fontWeight: 600, color: 'var(--text-muted)', fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.04 }}>Date</th>
                <th style={{ padding: '8px 12px', textAlign: 'left', fontWeight: 600, color: 'var(--text-muted)', fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.04 }}>Amount</th>
                <th style={{ padding: '8px 12px', textAlign: 'center', fontWeight: 600, color: 'var(--text-muted)', fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.04 }}>Status</th>
              </tr>
            </thead>
            <tbody>
              {invoices.map(inv => (
                <tr key={inv.id} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                  <td style={{ padding: '8px 12px' }}>{inv.created_at ? new Date(inv.created_at * 1000).toLocaleDateString() : '--'}</td>
                  <td style={{ padding: '8px 12px', fontVariantNumeric: 'tabular-nums' }}>₹{(inv.amount / 100).toFixed(0)}</td>
                  <td style={{ padding: '8px 12px', textAlign: 'center' }}>
                    <span style={{ color: inv.status === 'paid' ? 'var(--emerald)' : 'var(--amber)' }}>{inv.status}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
