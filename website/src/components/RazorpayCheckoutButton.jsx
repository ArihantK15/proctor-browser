import { useEffect, useState, useCallback } from 'react'

/**
 * Razorpay Standard Checkout button.
 *
 * Pure UI primitive — drop in anywhere you want a "Pay X" button.
 * Three-step flow:
 *   1. Click → POST /api/v1/checkout/order with {amount, currency, receipt}
 *   2. Backend returns order_id → we open the Razorpay modal via checkout.js
 *   3. On success, modal returns {payment_id, order_id, signature}
 *      → POST /api/v1/checkout/verify → backend HMAC-checks the signature
 *      → onSuccess() callback fires only after server confirms genuine
 *
 * Never trust the modal's "success" state alone — only the verify
 * endpoint return value. A modified frontend could fake a success
 * handler, which is why the backend always re-checks the signature.
 *
 * Props:
 *   amount      — paise (≥100). 100 = ₹1.
 *   currency    — default "INR"
 *   receipt     — optional internal reference (≤40 chars)
 *   prefill     — { name, email, contact } pre-fills the modal
 *   theme       — accent colour string (hex) for the Razorpay UI
 *   onSuccess   — ({ payment_id, order_id }) — called after server verify
 *   onError     — (error) — called on any failure (network, signature, dismiss)
 *   onDismiss   — () — called when user closes modal without paying
 *   children    — button label (defaults to "Pay ₹X")
 *   className   — extra classes for the button
 *   disabled    — disables the button
 */
export default function RazorpayCheckoutButton({
  amount,
  currency = 'INR',
  receipt,
  prefill = {},
  theme = '#5b6df0',
  onSuccess,
  onError,
  onDismiss,
  children,
  className = '',
  disabled = false,
}) {
  const [scriptReady, setScriptReady] = useState(false)
  const [loading, setLoading] = useState(false)

  const apiBase = import.meta.env.VITE_API_BASE || ''
  const publicKeyId = import.meta.env.VITE_RAZORPAY_KEY_ID || ''

  // Lazy-load checkout.js once. Reuses the existing tag if another
  // button on the page already loaded it.
  useEffect(() => {
    if (window.Razorpay) {
      setScriptReady(true)
      return
    }
    const existing = document.querySelector('script[src*="checkout.razorpay.com"]')
    if (existing) {
      existing.addEventListener('load', () => setScriptReady(true), { once: true })
      return
    }
    const s = document.createElement('script')
    s.src = 'https://checkout.razorpay.com/v1/checkout.js'
    s.async = true
    s.onload = () => setScriptReady(true)
    s.onerror = () => onError && onError(new Error('Failed to load Razorpay'))
    document.body.appendChild(s)
    // We intentionally don't remove the script on unmount — other
    // checkout buttons on the page may need it, and the bundle is
    // tiny + cached.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handlePay = useCallback(async () => {
    if (!scriptReady || loading) return
    setLoading(true)
    try {
      // Step 1 — create order on backend
      const orderRes = await fetch(`${apiBase}/api/v1/checkout/order`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ amount, currency, receipt }),
      })
      if (!orderRes.ok) {
        const body = await orderRes.text()
        throw new Error(`Order creation failed (${orderRes.status}): ${body}`)
      }
      const order = await orderRes.json()

      // Step 2 — open the Razorpay modal
      const rzp = new window.Razorpay({
        key: publicKeyId,
        amount: order.amount,
        currency: order.currency,
        name: 'Procta',
        description: receipt || 'Procta payment',
        order_id: order.order_id,
        prefill,
        theme: { color: theme },
        modal: {
          ondismiss: () => {
            setLoading(false)
            onDismiss && onDismiss()
          },
        },
        // Razorpay calls this on successful payment. The signature
        // it gives us is what the backend uses to verify the payment
        // is genuine — we MUST round-trip to /verify before treating
        // this as paid.
        handler: async (resp) => {
          try {
            const verifyRes = await fetch(`${apiBase}/api/v1/checkout/verify`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                razorpay_order_id:   resp.razorpay_order_id,
                razorpay_payment_id: resp.razorpay_payment_id,
                razorpay_signature:  resp.razorpay_signature,
              }),
            })
            if (!verifyRes.ok) {
              const body = await verifyRes.text()
              throw new Error(`Verification failed (${verifyRes.status}): ${body}`)
            }
            const verified = await verifyRes.json()
            if (verified.verified) {
              onSuccess && onSuccess({
                payment_id: resp.razorpay_payment_id,
                order_id:   resp.razorpay_order_id,
              })
            } else {
              throw new Error('Payment signature could not be verified.')
            }
          } catch (err) {
            onError && onError(err)
          } finally {
            setLoading(false)
          }
        },
      })

      // Razorpay's payment.failed event fires for card declines etc.
      // — different from modal dismiss (user-cancel).
      rzp.on('payment.failed', (resp) => {
        setLoading(false)
        onError && onError(new Error(
          resp?.error?.description || 'Payment failed. Please try again.'
        ))
      })

      rzp.open()
    } catch (err) {
      setLoading(false)
      onError && onError(err)
    }
  }, [scriptReady, loading, apiBase, publicKeyId, amount, currency, receipt,
      prefill, theme, onSuccess, onError, onDismiss])

  const label = children || `Pay ₹${(amount / 100).toFixed(2)}`
  const isDisabled = disabled || loading || !scriptReady

  return (
    <button
      type="button"
      onClick={handlePay}
      disabled={isDisabled}
      className={
        className ||
        'rounded-xl bg-accent-dark px-7 py-3.5 text-sm font-semibold text-white ' +
        'glow-btn no-underline disabled:opacity-50 disabled:cursor-not-allowed'
      }
    >
      {loading ? 'Opening payment…' : !scriptReady ? 'Loading…' : label}
    </button>
  )
}
