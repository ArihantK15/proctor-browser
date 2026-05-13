import { useState } from 'react'
import { Link } from 'wouter'
import { ArrowLeft, Check } from 'lucide-react'
import { APP_URL } from '../config'

export default function Signup() {
  const [form, setForm] = useState({ name: '', email: '', password: '', org_name: '' })
  const [loading, setLoading] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const [error, setError] = useState('')

  const [demoForm, setDemoForm] = useState({ name: '', email: '', institution: '', role: '', message: '' })
  const [demoLoading, setDemoLoading] = useState(false)
  const [demoSubmitted, setDemoSubmitted] = useState(false)
  const [demoError, setDemoError] = useState('')

  const update = (setter) => (field) => (e) => setter(prev => ({ ...prev, [field]: e.target.value }))
  const updateForm = update(setForm)
  const updateDemo = update(setDemoForm)

  const handleSignup = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const res = await fetch(`${APP_URL}/api/v1/auth/signup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          full_name: form.name,
          email: form.email,
          password: form.password,
          org_name: form.org_name,
        }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || 'Something went wrong. Please try again.')
      }
      const data = await res.json()
      localStorage.setItem('admin_token', data.access_token)
      window.location.href = `${APP_URL}/dashboard`
    } catch (err) {
      setError(err.message || 'Failed to sign up. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  const handleDemo = async (e) => {
    e.preventDefault()
    setDemoError('')
    setDemoLoading(true)
    try {
      const res = await fetch(`${APP_URL}/api/v1/demo-request`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(demoForm),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || 'Something went wrong.')
      }
      setDemoSubmitted(true)
    } catch (err) {
      setDemoError(err.message || 'Failed to submit.')
    } finally {
      setDemoLoading(false)
    }
  }

  if (submitted) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-navy-950 px-6">
        <div className="pointer-events-none fixed inset-0 grain-overlay" />
        <div className="relative w-full max-w-md text-center">
          <div className="relative rounded-2xl border border-white/[0.06] bg-white/[0.02] p-10 overflow-hidden grain-overlay">
            <div className="absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r from-transparent via-accent to-transparent" />
            <div className="mx-auto mb-6 flex h-14 w-14 items-center justify-center rounded-full bg-accent/10 border border-accent/20 accent-glow">
              <Check size={28} className="text-accent" />
            </div>
            <h1 className="text-2xl font-bold text-white font-display">Account Created</h1>
            <p className="mt-3 text-sm text-slate-400">
              Welcome to Procta! Redirecting you to the dashboard...
            </p>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-navy-950 px-6 py-12">
      <div className="pointer-events-none fixed inset-0 grain-overlay" />
      <div className="pointer-events-none fixed top-0 left-1/2 -translate-x-1/2 h-[400px] w-[600px] rounded-full bg-accent/5 blur-[120px]" />

      <div className="relative w-full max-w-lg">
        <Link to="/" className="mb-8 inline-flex items-center gap-2 text-sm text-slate-500 transition-colors hover:text-accent-light no-underline">
          <ArrowLeft size={16} />
          Back to home
        </Link>

        <div className="relative rounded-2xl border border-white/[0.06] bg-white/[0.02] p-8 backdrop-blur-sm overflow-hidden grain-overlay">
          <div className="absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r from-transparent via-accent to-transparent z-10" />

          <div className="mb-8">
            <Link to="/" className="inline-flex items-center gap-2.5 no-underline mb-4">
              <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent accent-glow">
                <svg width="18" height="18" viewBox="0 0 16 16" fill="none">
                  <path d="M4 3h3v1H5v8h2v1H4V3zm5 0h3v10h-3v-1h2V4H9V3z" fill="white"/>
                  <circle cx="8" cy="8" r="1.5" fill="white" opacity="0.8"/>
                </svg>
              </div>
              <span className="font-display text-xl font-bold text-white tracking-tight">Procta</span>
            </Link>
            <h1 className="text-2xl font-bold text-white font-display">Start Your Free Trial</h1>
            <p className="mt-2 text-sm text-slate-400">
              7 days free on Starter plan. No credit card required. Full access, no limits.
            </p>
          </div>

          <form onSubmit={handleSignup} className="space-y-4">
            <div>
              <label className="mb-1.5 block label-mono text-slate-400">Full Name</label>
              <input
                type="text"
                value={form.name}
                onChange={updateForm('name')}
                required
                placeholder="Dr. Jane Doe"
                className="w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-4 py-3 text-sm text-white placeholder-slate-600 outline-none transition-all focus-glow"
              />
            </div>

            <div>
              <label className="mb-1.5 block label-mono text-slate-400">Work Email</label>
              <input
                type="email"
                value={form.email}
                onChange={updateForm('email')}
                required
                placeholder="you@institution.edu"
                className="w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-4 py-3 text-sm text-white placeholder-slate-600 outline-none transition-all focus-glow"
              />
            </div>

            <div>
              <label className="mb-1.5 block label-mono text-slate-400">Organization Name</label>
              <input
                type="text"
                value={form.org_name}
                onChange={updateForm('org_name')}
                required
                placeholder="e.g., IIT Delhi"
                className="w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-4 py-3 text-sm text-white placeholder-slate-600 outline-none transition-all focus-glow"
              />
            </div>

            <div>
              <label className="mb-1.5 block label-mono text-slate-400">Password</label>
              <input
                type="password"
                value={form.password}
                onChange={updateForm('password')}
                required
                minLength={8}
                placeholder="At least 8 characters"
                className="w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-4 py-3 text-sm text-white placeholder-slate-600 outline-none transition-all focus-glow"
              />
            </div>

            {error && (
              <div className="rounded-lg border border-red-500/20 bg-red-500/5 px-4 py-2.5 text-xs text-red-400">
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full rounded-lg bg-accent-dark px-4 py-3 text-sm font-semibold text-white glow-btn disabled:opacity-50 disabled:cursor-not-allowed border-none cursor-pointer"
            >
              {loading ? 'Creating account...' : 'Start Free Trial'}
            </button>
          </form>

          <p className="mt-6 text-center text-sm text-slate-500">
            Already have an account?{' '}
            <a href={`${APP_URL}/dashboard`} className="font-medium text-accent-light hover:text-white transition-colors no-underline">
              Log In
            </a>
          </p>
        </div>

        {/* Enterprise / Demo Request section */}
        <div className="mt-10 rounded-2xl border border-white/[0.06] bg-white/[0.02] p-8 overflow-hidden grain-overlay">
          <div className="absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r from-transparent via-amber/40 to-transparent z-10" />
          <h2 className="text-lg font-bold text-white font-display">Enterprise / Custom Plan</h2>
          <p className="mt-2 text-sm text-slate-400">
            Need more than 500 students? Custom pricing, dedicated support,
            on-premise deployment? Request a personalized demo.
          </p>

          {demoSubmitted ? (
            <div className="mt-6 text-center text-sm text-emerald">
              Demo request received! We'll reach out within 24 hours.
            </div>
          ) : (
            <form onSubmit={handleDemo} className="mt-6 space-y-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <label className="mb-1.5 block label-mono text-slate-400">Full Name</label>
                  <input
                    type="text"
                    value={demoForm.name}
                    onChange={updateDemo('name')}
                    required
                    placeholder="Dr. Jane Doe"
                    className="w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-4 py-3 text-sm text-white placeholder-slate-600 outline-none transition-all focus-glow"
                  />
                </div>
                <div>
                  <label className="mb-1.5 block label-mono text-slate-400">Work Email</label>
                  <input
                    type="email"
                    value={demoForm.email}
                    onChange={updateDemo('email')}
                    required
                    placeholder="you@institution.edu"
                    className="w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-4 py-3 text-sm text-white placeholder-slate-600 outline-none transition-all focus-glow"
                  />
                </div>
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <label className="mb-1.5 block label-mono text-slate-400">Institution</label>
                  <input
                    type="text"
                    value={demoForm.institution}
                    onChange={updateDemo('institution')}
                    required
                    placeholder="University name"
                    className="w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-4 py-3 text-sm text-white placeholder-slate-600 outline-none transition-all focus-glow"
                  />
                </div>
                <div>
                  <label className="mb-1.5 block label-mono text-slate-400">Your Role</label>
                  <select
                    value={demoForm.role}
                    onChange={updateDemo('role')}
                    required
                    className="w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-4 py-3 text-sm text-white outline-none transition-all focus-glow appearance-none"
                  >
                    <option value="" className="bg-navy-900">Select role</option>
                    <option value="faculty" className="bg-navy-900">Faculty / Professor</option>
                    <option value="admin" className="bg-navy-900">Exam Administrator</option>
                    <option value="it" className="bg-navy-900">IT Department</option>
                    <option value="management" className="bg-navy-900">Management</option>
                    <option value="other" className="bg-navy-900">Other</option>
                  </select>
                </div>
              </div>

              {demoError && (
                <div className="rounded-lg border border-red-500/20 bg-red-500/5 px-4 py-2.5 text-xs text-red-400">
                  {demoError}
                </div>
              )}

              <button
                type="submit"
                disabled={demoLoading}
                className="w-full rounded-lg border border-amber/30 bg-amber/5 px-4 py-3 text-sm font-semibold text-amber transition-colors hover:bg-amber/10 disabled:opacity-50 border-none cursor-pointer"
              >
                {demoLoading ? 'Submitting...' : 'Request Demo'}
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  )
}
