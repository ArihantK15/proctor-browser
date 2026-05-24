import { useState, useEffect } from 'react'
import { useAuth } from '../lib/auth'

const PLANS = [
  { id: 'starter', name: 'Starter', price: '₹2,400', students: 30, desc: 'For small classes & tutorials (₹80/student)' },
  { id: 'growth', name: 'Growth', price: '₹12,000', students: 150, desc: 'For departments & mid-size programs (₹80/student)' },
  { id: 'pro', name: 'Pro', price: '₹30,000', students: 500, desc: 'For large universities & institutions (₹80/student)' },
]

let razorpayCheckoutPromise = null

function loadRazorpayCheckout() {
  if (window.Razorpay) return Promise.resolve()
  if (razorpayCheckoutPromise) return razorpayCheckoutPromise
  razorpayCheckoutPromise = new Promise((resolve, reject) => {
    const existing = document.querySelector('script[src="https://checkout.razorpay.com/v1/checkout.js"]')
    if (existing) {
      existing.addEventListener('load', resolve, { once: true })
      existing.addEventListener('error', () => reject(new Error('Failed to load Razorpay checkout.')), { once: true })
      return
    }
    const s = document.createElement('script')
    s.src = 'https://checkout.razorpay.com/v1/checkout.js'
    s.async = true
    s.onload = resolve
    s.onerror = () => reject(new Error('Failed to load Razorpay checkout.'))
    document.head.appendChild(s)
  })
  return razorpayCheckoutPromise
}

export default function BillingPanel() {
  const { authFetch, user } = useAuth()
  const [billing, setBilling] = useState(null)
  const [invoices, setInvoices] = useState([])
  const [usage, setUsage] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [auxError, setAuxError] = useState('')
  const [upgradeStatus, setUpgradeStatus] = useState('')

  useEffect(() => { loadAll() }, [])

  const loadAll = async () => {
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
  }

  const upgrade = async (planId) => {
    setUpgradeStatus('Opening secure checkout...')
    try {
      await loadRazorpayCheckout()
      const r = await authFetch('/api/v1/billing/checkout/order', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plan_id: planId }),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || `Upgrade failed (${r.status})`)
      }
      const order = await r.json()
      await new Promise((resolve, reject) => {
        if (!window.Razorpay) return reject(new Error('Razorpay checkout did not load.'))
        const rzp = new window.Razorpay({
          key: order.key_id,
          amount: order.amount,
          currency: order.currency || 'INR',
          name: 'Procta',
          description: order.description || `${order.plan_name || 'Procta'} plan`,
          order_id: order.order_id,
          prefill: {
            name: user?.full_name || '',
            email: user?.email || '',
          },
          notes: { plan_id: order.plan_id || '' },
          theme: { color: '#2563eb' },
          modal: {
            confirm_close: true,
            ondismiss: () => {
              setUpgradeStatus('Payment cancelled. No changes were made.')
              resolve()
            },
          },
          handler: async (resp) => {
            setUpgradeStatus('Verifying payment...')
            try {
              const verifyR = await authFetch('/api/v1/billing/checkout/verify', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                  plan_id: order.plan_id,
                  razorpay_order_id: resp.razorpay_order_id,
                  razorpay_payment_id: resp.razorpay_payment_id,
                  razorpay_signature: resp.razorpay_signature,
                }),
              })
              if (!verifyR.ok) {
                const d = await verifyR.json().catch(() => ({}))
                throw new Error(d.detail || `Payment verification failed (${verifyR.status})`)
              }
              setUpgradeStatus('Payment verified. Your plan is active.')
              await loadAll()
              resolve()
            } catch (e) {
              setUpgradeStatus(e.message || 'Payment verification failed.')
              reject(e)
            }
          },
        })
        rzp.on('payment.failed', (response) => {
          setUpgradeStatus(response?.error?.description || 'Payment failed. Please try again.')
        })
        rzp.open()
      })
    } catch (e) {
      setUpgradeStatus(e.message)
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
      {billing?.status && !['cancelled', 'expired', 'cancelling'].includes(String(billing.status).toLowerCase()) && (
        <button className="btn btn-secondary btn-sm" onClick={cancelSubscription} style={{ marginBottom: 12 }}>
          Cancel at period end
        </button>
      )}

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
                  <td style={{ padding: '8px 12px' }}>{inv.created_at ? new Date(inv.created_at * 1000).toLocaleDateString() : '--'}</td>
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
