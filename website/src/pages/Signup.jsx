import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft, Check } from 'lucide-react'
import { APP_URL } from '../config'

export default function Signup() {
  const [form, setForm] = useState({ name: '', email: '', institution: '', role: '', message: '' })
  const [loading, setLoading] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const [error, setError] = useState('')

  const update = (field) => (e) => setForm({ ...form, [field]: e.target.value })

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const res = await fetch(`${APP_URL}/api/demo-request`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || 'Something went wrong. Please try again.')
      }
      setSubmitted(true)
    } catch (err) {
      setError(err.message || 'Failed to submit. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  if (submitted) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-navy-950 px-6">
        <div className="pointer-events-none fixed inset-0 opacity-[0.02]"
          style={{
            backgroundImage: 'linear-gradient(rgba(255,255,255,0.1) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.1) 1px, transparent 1px)',
            backgroundSize: '64px 64px'
          }}
        />
        <div className="relative w-full max-w-md text-center">
          <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-10">
            <div className="mx-auto mb-6 flex h-14 w-14 items-center justify-center rounded-full bg-emerald/10">
              <Check size={28} className="text-emerald" />
            </div>
            <h1 className="text-2xl font-bold text-white font-display">Request Received</h1>
            <p className="mt-3 text-sm text-slate-400">
              We'll reach out to <span className="text-white">{form.email}</span> within 24 hours to
              schedule your personalized demo.
            </p>
            <Link
              to="/"
              className="mt-8 inline-flex items-center gap-2 text-sm font-medium text-accent-light hover:text-white transition-colors no-underline"
            >
              <ArrowLeft size={16} />
              Back to home
            </Link>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-navy-950 px-6 py-12">
      <div className="pointer-events-none fixed inset-0 opacity-[0.02]"
        style={{
          backgroundImage: 'linear-gradient(rgba(255,255,255,0.1) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.1) 1px, transparent 1px)',
          backgroundSize: '64px 64px'
        }}
      />
      <div className="pointer-events-none fixed top-0 left-1/2 -translate-x-1/2 h-[400px] w-[600px] rounded-full bg-accent/5 blur-[120px]" />

      <div className="relative w-full max-w-lg">
        <Link to="/" className="mb-8 inline-flex items-center gap-2 text-sm text-slate-500 transition-colors hover:text-white no-underline">
          <ArrowLeft size={16} />
          Back to home
        </Link>

        <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-8 backdrop-blur-sm">
          <div className="mb-8">
            <Link to="/" className="inline-flex items-center gap-2.5 no-underline mb-4">
              <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent">
                <svg width="18" height="18" viewBox="0 0 16 16" fill="none">
                  <path d="M4 3h3v1H5v8h2v1H4V3zm5 0h3v10h-3v-1h2V4H9V3z" fill="white"/>
                  <circle cx="8" cy="8" r="1.5" fill="white" opacity="0.8"/>
                </svg>
              </div>
              <span className="font-display text-xl font-bold text-white tracking-tight">Procta</span>
            </Link>
            <h1 className="text-2xl font-bold text-white font-display">Request a Demo</h1>
            <p className="mt-2 text-sm text-slate-400">
              Tell us about your institution and we'll set up a personalized walkthrough.
            </p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-400">Full Name</label>
                <input
                  type="text"
                  value={form.name}
                  onChange={update('name')}
                  required
                  placeholder="Dr. Jane Doe"
                  className="w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-4 py-3 text-sm text-white placeholder-slate-600 outline-none transition-colors focus:border-accent/40 focus:bg-white/[0.05]"
                />
              </div>
              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-400">Work Email</label>
                <input
                  type="email"
                  value={form.email}
                  onChange={update('email')}
                  required
                  placeholder="you@institution.edu"
                  className="w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-4 py-3 text-sm text-white placeholder-slate-600 outline-none transition-colors focus:border-accent/40 focus:bg-white/[0.05]"
                />
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-400">Institution</label>
                <input
                  type="text"
                  value={form.institution}
                  onChange={update('institution')}
                  required
                  placeholder="University name"
                  className="w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-4 py-3 text-sm text-white placeholder-slate-600 outline-none transition-colors focus:border-accent/40 focus:bg-white/[0.05]"
                />
              </div>
              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-400">Your Role</label>
                <select
                  value={form.role}
                  onChange={update('role')}
                  required
                  className="w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-4 py-3 text-sm text-white outline-none transition-colors focus:border-accent/40 focus:bg-white/[0.05] appearance-none"
                >
                  <option value="" className="bg-navy-900">Select role</option>
                  <option value="faculty" className="bg-navy-900">Faculty / Professor</option>
                  <option value="admin" className="bg-navy-900">Exam Administrator</option>
                  <option value="it" className="bg-navy-900">IT Department</option>
                  <option value="management" className="bg-navy-900">Management</option>
                  <option value="hr" className="bg-navy-900">HR / Recruitment</option>
                  <option value="other" className="bg-navy-900">Other</option>
                </select>
              </div>
            </div>

            <div>
              <label className="mb-1.5 block text-xs font-medium text-slate-400">
                What are you looking to solve? <span className="text-slate-600">(optional)</span>
              </label>
              <textarea
                value={form.message}
                onChange={update('message')}
                rows={3}
                placeholder="e.g., We need to proctor 500 students for semester exams..."
                className="w-full resize-none rounded-lg border border-white/[0.08] bg-white/[0.03] px-4 py-3 text-sm text-white placeholder-slate-600 outline-none transition-colors focus:border-accent/40 focus:bg-white/[0.05]"
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
              className="w-full rounded-lg bg-accent px-4 py-3 text-sm font-semibold text-white transition-all hover:bg-accent-light disabled:opacity-50 disabled:cursor-not-allowed border-none cursor-pointer"
            >
              {loading ? 'Submitting...' : 'Request Demo'}
            </button>
          </form>

          <p className="mt-6 text-center text-sm text-slate-500">
            Already have an account?{' '}
            <a href={`${APP_URL}/dashboard`} className="font-medium text-accent-light hover:text-white transition-colors no-underline">
              Log In
            </a>
          </p>
        </div>
      </div>
    </div>
  )
}
