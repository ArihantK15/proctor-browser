import { useState, useEffect, useCallback } from 'react'
import { useAuth } from '../lib/auth'

const PLANS = [
  { id: 'starter', name: 'Starter', price: '₹2,400', students: 30, desc: 'For small classes & tutorials (₹80/student)' },
  { id: 'growth', name: 'Growth', price: '₹12,000', students: 150, desc: 'For departments & mid-size programs (₹80/student)' },
  { id: 'pro', name: 'Pro', price: '₹30,000', students: 500, desc: 'For large universities & institutions (₹80/student)' },
]

// Statuses that mean the org currently holds an entitling plan — mirrors the
// server's ENTITLING_STATUSES (services/billing.py). While entitled, in-place
// plan switching is blocked server-side (create_subscription 409s), so the
// tiles lock to match.
const ENTITLING_STATUSES = new Set(['trialing', 'authenticated', 'active', 'past_due', 'cancelling'])

export default function BillingPanel() {
  const { authFetch } = useAuth()
  const [billing, setBilling] = useState(null)
  const [invoices, setInvoices] = useState([])
  const [usage, setUsage] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [auxError, setAuxError] = useState('')
  const [upgradeStatus, setUpgradeStatus] = useState('')

  const loadAll = useCallback(async () => {
    setError('')
    setAuxError('')
    try {
      const r = await authFetch('/api/v1/org/billing')
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || `Failed to load billing (${r.status})`)
      }
      setBilling(await r.json())
      const [invoiceR, usageR] = await Promise.all([
        authFetch('/api/v1/billing/invoices'),
        authFetch('/api/v1/billing/usage'),
      ])
      if (invoiceR.ok) setInvoices((await invoiceR.json()).invoices || [])
      else setAuxError(`Invoice history failed to load (${invoiceR.status}).`)
      if (usageR.ok) setUsage(await usageR.json())
      else setAuxError(prev => [prev, `Usage failed to load (${usageR.status}).`].filter(Boolean).join(' '))
    } catch (e) {
      setError(e.message || 'Failed to load billing')
    } finally { setLoading(false) }
  }, [authFetch])

  useEffect(() => { loadAll() }, [loadAll])

  // Subscribe — recurring monthly via Razorpay Subscriptions + UPI Autopay.
  // Backend creates the Subscription via Razorpay API and returns a hosted
  // checkout URL (`short_url`). Redirecting there lets Razorpay's own page
  // render the full payment-method matrix including UPI Autopay (NACH-backed
  // auto-debit), which we can't get from the in-modal Razorpay Standard
  // Checkout flow.
  //
  // Pre-req: each tier needs a `RAZORPAY_PLAN_<TIER>` env var on the
  // server pointing at a Razorpay-side plan ID; otherwise the endpoint 503s
  // with a clear "payment credentials not configured" message.
  const subscribe = async (planId) => {
    // Block switching while a plan is active — matches the server's 409 guard,
    // giving instant feedback instead of a round-trip that just errors.
    const status = String(billing?.status || '').toLowerCase()
    if (ENTITLING_STATUSES.has(status)) {
      const cur = String(billing?.plan || '').toLowerCase()
      setUpgradeStatus(
        String(planId).toLowerCase() === cur
          ? 'You’re already on this plan.'
          : 'You already have an active plan. Cancel it first — you keep access until the end of your billing period — to switch plans.',
      )
      return
    }
    setUpgradeStatus('Creating subscription...')
    try {
      const r = await authFetch('/api/v1/billing/create-subscription', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plan_id: planId }),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || `Subscription creation failed (${r.status})`)
      }
      const sub = await r.json()
      if (!sub.short_url) {
        // Sandbox / mis-configured: surface the state but don't crash.
        setUpgradeStatus(
          sub._note ||
            'Subscription created in sandbox mode. Configure RAZORPAY_PLAN_* env vars on the server to enable live checkout.',
        )
        await loadAll()
        return
      }
      setUpgradeStatus('Redirecting to Razorpay checkout for UPI Autopay setup...')
      // Open in the same tab so the redirect-back lands on the dashboard.
      window.location.href = sub.short_url
    } catch (e) {
      setUpgradeStatus(e.message || 'Subscription creation failed.')
    }
  }

  const cancelSubscription = async () => {
    if (!confirm('Cancel this subscription at the end of the current billing period?')) return
    setUpgradeStatus('Cancelling...')
    try {
      const r = await authFetch('/api/v1/billing/cancel', { method: 'POST' })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || `Cancel failed (${r.status})`)
      }
      const d = await r.json()
      setUpgradeStatus(d.message || 'Subscription cancellation scheduled.')
      loadAll()
    } catch (e) {
      setUpgradeStatus(e.message || 'Cancel failed')
    }
  }

  if (loading) return <div className="loading">Loading billing...</div>
  if (error) return <div className="auth-err" style={{ margin: 20 }}>{error} <button className="btn-link" onClick={loadAll} style={{ marginLeft: 8 }}>Retry</button></div>

  const currentPlan = String(billing?.plan || 'starter').toLowerCase()
  const entitled = ENTITLING_STATUSES.has(String(billing?.status || '').toLowerCase())

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
      {billing?.status && !['cancelled', 'expired', 'cancelling'].includes(String(billing.status).toLowerCase()) && (
        <button className="btn btn-secondary btn-sm" onClick={cancelSubscription} style={{ marginBottom: 12 }}>
          Cancel at period end
        </button>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 14, marginTop: 20, marginBottom: 20 }}>
        {PLANS.map(p => {
          // While a plan is active: the current plan shows "Current plan", and
          // every other plan is locked (switching is blocked until they cancel).
          // When not entitled (cancelled/expired/none), all tiles are buyable.
          const isCurrent = entitled && currentPlan === p.id
          const locked = entitled && !isCurrent
          return (
          <div
            key={p.id}
            className="tool-card"
            style={{ textAlign: 'center', borderColor: isCurrent ? 'var(--accent)' : undefined }}
          >
            <div className="tool-card-body">
              <h3>{p.name}</h3>
              <p style={{ fontSize: 20, fontWeight: 700, color: 'var(--text-high)', margin: '4px 0' }}>{p.price}</p>
              <p style={{ fontSize: 11, color: 'var(--text-muted)' }}>{p.students} students</p>
              <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 6, minHeight: 32 }}>{p.desc}</p>
              {/* Single recurring path: Razorpay Subscriptions (UPI Autopay /
                  NACH). Entitlement is granted only when the subscription
                  actually activates (server reconciles from the webhook). */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 10 }}>
                <button
                  className="btn-primary"
                  onClick={() => subscribe(p.id)}
                  disabled={isCurrent || locked || (!!upgradeStatus && upgradeStatus.endsWith('...'))}
                  style={{ fontSize: 12, padding: '6px 10px' }}
                  title={locked ? 'Cancel your current plan to switch plans' : 'Recurring monthly auto-debit via UPI Autopay / NACH'}
                >
                  {isCurrent ? 'Current plan' : 'Subscribe · UPI Autopay'}
                </button>
              </div>
            </div>
          </div>
          )
        })}
      </div>
      {upgradeStatus && <div style={{ fontSize: 13, color: 'var(--emerald)', marginBottom: 12 }}>{upgradeStatus}</div>}
      {auxError && <div className="auth-err" style={{ marginBottom: 12 }}>{auxError} <button className="btn-link" onClick={loadAll} style={{ marginLeft: 8 }}>Retry</button></div>}

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
                <th style={{ padding: '8px 12px', textAlign: 'right', fontWeight: 600, color: 'var(--text-muted)', fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.04 }}>PDF</th>
              </tr>
            </thead>
            <tbody>
              {invoices.map(inv => (
                <tr key={inv.id} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                  <td style={{ padding: '8px 12px' }}>{inv.created_at ? new Date(inv.created_at).toLocaleDateString() : '--'}</td>
                  <td style={{ padding: '8px 12px', fontVariantNumeric: 'tabular-nums' }}>₹{(inv.amount / 100).toFixed(0)}</td>
                  <td style={{ padding: '8px 12px', textAlign: 'center' }}>
                    <span style={{ color: inv.status === 'paid' ? 'var(--emerald)' : 'var(--amber)' }}>{inv.status}</span>
                  </td>
                  <td style={{ padding: '8px 12px', textAlign: 'right' }}>
                    {inv.pdf_url ? <a href={inv.pdf_url} target="_blank" rel="noreferrer">Download</a> : <span style={{ color: 'var(--text-muted)' }}>--</span>}
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
