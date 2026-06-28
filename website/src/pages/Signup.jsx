import { useState } from 'react'
import { Link } from 'wouter'
import { Helmet } from 'react-helmet-async'
import { motion, useReducedMotion } from 'framer-motion'
import { ArrowLeft, Check } from 'lucide-react'
import { APP_URL } from '../config'
import { fadeUp, scaleIn, stagger, pick } from '../lib/motion'
import useTurnstile from '../hooks/useTurnstile'
import { isPasswordPwned } from '../lib/hibp'

export default function Signup() {
  const [form, setForm] = useState({ name: '', email: '', password: '', passwordConfirm: '', org_name: '' })
  const [loading, setLoading] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const [error, setError] = useState('')
  const [fieldErrors, setFieldErrors] = useState({})
  const [accountType, setAccountType] = useState('solo')

  const [demoForm, setDemoForm] = useState({ name: '', email: '', institution: '', role: '', message: '' })
  const [demoLoading, setDemoLoading] = useState(false)
  const [demoSubmitted, setDemoSubmitted] = useState(false)
  const [demoError, setDemoError] = useState('')

  const update = (setter) => (field) => (e) => setter(prev => ({ ...prev, [field]: e.target.value }))
  const updateForm = update(setForm)
  const updateDemo = update(setDemoForm)

  const turnstile = useTurnstile()

  // ── Motion vocabulary (shared, Emil-Kowalski-grounded; reduced-motion safe) ──
  const reduced = useReducedMotion()
  const child = pick(reduced, fadeUp)
  const card = pick(reduced, scaleIn)
  const hoverLift = reduced ? undefined : { y: -3 }
  const tap = reduced ? undefined : { scale: 0.985 }
  const checkPop = reduced
    ? { initial: { opacity: 0 }, animate: { opacity: 1 } }
    : { initial: { scale: 0, rotate: -25 }, animate: { scale: 1, rotate: 0 },
        transition: { type: 'spring', stiffness: 600, damping: 18 } }

  const validateSignupFields = () => {
    const nextErrors = {}
    const pw = form.password
    if (!form.name.trim()) nextErrors.name = 'Enter your full name.'
    if (!form.email.trim()) nextErrors.email = 'Enter your work email.'
    if (!form.org_name.trim()) nextErrors.org_name = 'Enter your organization name.'
    if (pw.length < 10) nextErrors.password = 'Password must be at least 10 characters.'
    else if (!/[A-Z]/.test(pw)) nextErrors.password = 'Add at least one uppercase letter.'
    else if (!/[a-z]/.test(pw)) nextErrors.password = 'Add at least one lowercase letter.'
    else if (!/[0-9]/.test(pw)) nextErrors.password = 'Add at least one number.'
    else if (!/[^A-Za-z0-9]/.test(pw)) nextErrors.password = 'Add at least one special character.'
    if (!form.passwordConfirm) nextErrors.passwordConfirm = 'Confirm your password.'
    else if (form.passwordConfirm !== pw) nextErrors.passwordConfirm = 'Passwords do not match.'
    return nextErrors
  }

  const handleSignup = async (e) => {
    e.preventDefault()
    setError('')
    setFieldErrors({})
    setLoading(true)
    try {
      const nextErrors = validateSignupFields()
      if (Object.keys(nextErrors).length) {
        setFieldErrors(nextErrors)
        throw new Error('Please fix the highlighted fields.')
      }

      // Client-side HIBP check — refuse passwords known to be in
      // public breach corpora. Fails open if HIBP is unreachable;
      // server-side validate_password still catches weak passwords.
      if (await isPasswordPwned(form.password)) {
        throw new Error(
          "This password has appeared in a known data breach. " +
          "Please choose a different password."
        )
      }

      const res = await fetch(`${APP_URL}/api/v1/auth/signup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          full_name: form.name,
          email: form.email,
          password: form.password,
          org_name: form.org_name,
          account_type: accountType,
          captcha_token: turnstile.token,
        }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        // Refresh the Turnstile widget on any failure — the token is
        // single-use, so a retry needs a fresh one.
        turnstile.refresh()
        throw new Error(typeof data.detail === 'string' ? data.detail : (data.detail?.message || 'Something went wrong. Please try again.'))
      }
      setSubmitted(true)  // Show "Check your inbox" instead of redirecting
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
        body: JSON.stringify({ ...demoForm, captcha_token: turnstile.token }),
      })
      if (!res.ok) {
        // P2.7: Turnstile tokens are single-use. Both signup and demo
        // share one widget on this page; without refresh-on-failure,
        // a user who fails one form then submits the other gets a
        // bogus "captcha required" 403 from the reused stale token.
        turnstile.refresh()
        const data = await res.json().catch(() => ({}))
        throw new Error(typeof data.detail === 'string' ? data.detail : (data.detail?.message || 'Something went wrong.'))
      }
      setDemoSubmitted(true)
    } catch (err) {
      setDemoError(err.message || 'Failed to submit.')
    } finally {
      setDemoLoading(false)
    }
  }

  // Ambient drifting orbs — give the dark backdrop depth + slow life without
  // distracting from the form. `hero-aurora` is GPU-only drift and collapses to
  // static under prefers-reduced-motion (see index.css).
  const Ambience = () => (
    <>
      <div className="pointer-events-none fixed inset-0 grain-overlay" />
      <div className="hero-aurora pointer-events-none fixed -top-32 left-1/2 -translate-x-1/2 h-[520px] w-[720px] rounded-full bg-accent/10 blur-[140px]" />
      <div className="hero-aurora pointer-events-none fixed bottom-[-12rem] right-[-8rem] h-[420px] w-[560px] rounded-full bg-amber/[0.07] blur-[150px]" style={{ animationDelay: '-7s' }} />
    </>
  )

  if (submitted) {
    return (
      <>
      <Helmet>
        <title>Sign Up — Procta Browser</title>
        <meta name="description" content="Create your Procta account and start running AI-proctored exams with instant setup, free trial, and no credit card required." />
        <meta property="og:title" content="Sign Up — Procta Browser" />
        <meta property="og:description" content="Create your Procta account and start running AI-proctored exams with instant setup." />
        <meta property="og:type" content="website" />
        <link rel="canonical" href="https://www.procta.net/signup" />
      </Helmet>
      <div className="flex min-h-screen items-center justify-center bg-navy-950 px-6">
        <Ambience />
        <motion.div
          className="relative w-full max-w-md text-center"
          initial="hidden" animate="show" variants={card}
        >
          <div className="relative rounded-2xl border border-white/[0.06] bg-white/[0.02] p-10 overflow-hidden grain-overlay">
            <div className="absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r from-transparent via-accent to-transparent" />
            <motion.div
              className="mx-auto mb-6 flex h-14 w-14 items-center justify-center rounded-full bg-accent/10 border border-accent/20 accent-glow"
              {...checkPop}
            >
              <Check size={28} className="text-accent" />
            </motion.div>
            <h1 className="text-2xl font-bold text-white font-display">Check Your Inbox</h1>
            <p className="mt-3 text-sm text-slate-400">
              We sent a verification link to <strong className="text-white">{form.email}</strong>.
              Click the link to activate your account, then log in.
            </p>
            <p className="mt-4 text-xs text-slate-500">The link expires in 24 hours. Check your spam folder if you don't see it.</p>
          </div>
        </motion.div>
      </div>
      </>
    )
  }

  return (
    <>
      <Helmet>
        <title>Sign Up — Procta Browser</title>
        <meta name="description" content="Create your Procta account and start running AI-proctored exams with instant setup, free trial, and no credit card required." />
        <meta property="og:title" content="Sign Up — Procta Browser" />
        <meta property="og:description" content="Create your Procta account and start running AI-proctored exams with instant setup." />
        <meta property="og:type" content="website" />
        <link rel="canonical" href="https://www.procta.net/signup" />
      </Helmet>
    <div className="flex min-h-screen items-center justify-center bg-navy-950 px-6 py-12">
      <Ambience />

      {/* One orchestrated page-load: back-link, then the two panels rise in,
          then the signup panel's sections cascade. */}
      <motion.div
        className="relative w-full max-w-lg lg:max-w-6xl"
        initial="hidden" animate="show" variants={stagger(0.1)}
      >
        <motion.div variants={child}>
          <Link to="/" className="mb-8 inline-flex items-center gap-2 text-sm text-slate-500 transition-colors hover:text-accent-light no-underline group">
            <ArrowLeft size={16} className="transition-transform duration-200 group-hover:-translate-x-1" />
            Back to home
          </Link>
        </motion.div>

        <div className="grid gap-8 lg:grid-cols-2 lg:items-start">
        <motion.div
          variants={card}
          className="relative rounded-2xl border border-white/[0.06] bg-white/[0.02] p-8 backdrop-blur-sm overflow-hidden grain-overlay"
        >
          <div className="absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r from-transparent via-accent to-transparent z-10" />

          {/* Inner cascade — sections fade up in sequence once the panel lands. */}
          <motion.div initial="hidden" animate="show" variants={stagger(0.07, 0.15)}>
          <motion.div className="mb-8" variants={child}>
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
              14 days free on Starter plan. No credit card required. Full access, no limits.
            </p>
          </motion.div>

          <motion.div className="mb-6" variants={child}>
            <label className="mb-3 block label-mono text-slate-400">Account Type</label>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <motion.button
                type="button"
                onClick={() => setAccountType('solo')}
                whileHover={hoverLift}
                whileTap={tap}
                className={`relative rounded-xl border p-3.5 text-left transition-colors ${
                  accountType === 'solo'
                    ? 'border-accent/40 bg-accent/[0.03]'
                    : 'border-white/[0.06] bg-white/[0.02] hover:border-white/[0.12]'
                }`}
              >
                {accountType === 'solo' && (
                  <div className="absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r from-transparent via-accent to-transparent rounded-t-xl" />
                )}
                <div className="flex items-start gap-3">
                  <div className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border-2 transition-colors ${
                    accountType === 'solo' ? 'border-accent bg-accent' : 'border-slate-600'
                  }`}>
                    {accountType === 'solo' && (
                      <motion.span {...checkPop}><Check size={12} className="text-white" /></motion.span>
                    )}
                  </div>
                  <div className="min-w-0">
                    <p className="text-sm font-semibold text-white">Solo teacher</p>
                    <p className="mt-1 text-xs text-slate-400 leading-relaxed">
                      Run your own exams and students; manage your own billing.
                    </p>
                  </div>
                </div>
              </motion.button>
              <motion.button
                type="button"
                onClick={() => setAccountType('org')}
                whileHover={hoverLift}
                whileTap={tap}
                className={`relative rounded-xl border p-3.5 text-left transition-colors ${
                  accountType === 'org'
                    ? 'border-accent/40 bg-accent/[0.03]'
                    : 'border-white/[0.06] bg-white/[0.02] hover:border-white/[0.12]'
                }`}
              >
                {accountType === 'org' && (
                  <div className="absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r from-transparent via-accent to-transparent rounded-t-xl" />
                )}
                <div className="flex items-start gap-3">
                  <div className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border-2 transition-colors ${
                    accountType === 'org' ? 'border-accent bg-accent' : 'border-slate-600'
                  }`}>
                    {accountType === 'org' && (
                      <motion.span {...checkPop}><Check size={12} className="text-white" /></motion.span>
                    )}
                  </div>
                  <div className="min-w-0">
                    <p className="text-sm font-semibold text-white">Organization</p>
                    <p className="mt-1 text-xs text-slate-400 leading-relaxed">
                      Manage teachers, billing & oversight for an institution. Admins don't run exams — you invite teachers for that.
                    </p>
                  </div>
                </div>
              </motion.button>
            </div>
          </motion.div>

          <motion.form onSubmit={handleSignup} className="space-y-4" variants={child}>
            <div>
              <label className="mb-1.5 block label-mono text-slate-400">Full Name</label>
              <input
                type="text"
                value={form.name}
                onChange={updateForm('name')}
                required
                autoComplete="name"
                aria-invalid={Boolean(fieldErrors.name)}
                placeholder="Dr. Jane Doe"
                className="w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-4 py-3 text-sm text-white placeholder-slate-600 outline-none transition-all focus-glow"
              />
              {fieldErrors.name && <p className="mt-1 text-xs text-red-400">{fieldErrors.name}</p>}
            </div>

            <div>
              <label className="mb-1.5 block label-mono text-slate-400">Work Email</label>
              <input
                type="email"
                value={form.email}
                onChange={updateForm('email')}
                required
                autoComplete="email"
                aria-invalid={Boolean(fieldErrors.email)}
                placeholder="you@institution.edu"
                className="w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-4 py-3 text-sm text-white placeholder-slate-600 outline-none transition-all focus-glow"
              />
              {fieldErrors.email && <p className="mt-1 text-xs text-red-400">{fieldErrors.email}</p>}
            </div>

            <div>
              <label className="mb-1.5 block label-mono text-slate-400">
                {accountType === 'org' ? 'Institution Name' : 'Your Name or Class'}
              </label>
              <input
                type="text"
                value={form.org_name}
                onChange={updateForm('org_name')}
                required
                autoComplete="organization"
                aria-invalid={Boolean(fieldErrors.org_name)}
                placeholder={accountType === 'org' ? 'e.g., IIT Delhi' : "e.g., Ms. Sharma's Class"}
                className="w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-4 py-3 text-sm text-white placeholder-slate-600 outline-none transition-all focus-glow"
              />
              {fieldErrors.org_name && <p className="mt-1 text-xs text-red-400">{fieldErrors.org_name}</p>}
            </div>

            <div>
              <label className="mb-1.5 block label-mono text-slate-400">Password</label>
              <input
                type="password"
                value={form.password}
                onChange={updateForm('password')}
                required
                minLength={10}
                autoComplete="new-password"
                aria-invalid={Boolean(fieldErrors.password)}
                placeholder="At least 10 characters (uppercase, number, symbol)"
                className="w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-4 py-3 text-sm text-white placeholder-slate-600 outline-none transition-all focus-glow"
              />
              {fieldErrors.password && <p className="mt-1 text-xs text-red-400">{fieldErrors.password}</p>}
            </div>

            <div>
              <label className="mb-1.5 block label-mono text-slate-400">Confirm Password</label>
              <input
                type="password"
                value={form.passwordConfirm}
                onChange={updateForm('passwordConfirm')}
                required
                minLength={10}
                autoComplete="new-password"
                aria-invalid={Boolean(fieldErrors.passwordConfirm)}
                placeholder="Re-enter your password"
                className="w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-4 py-3 text-sm text-white placeholder-slate-600 outline-none transition-all focus-glow"
              />
              {fieldErrors.passwordConfirm && <p className="mt-1 text-xs text-red-400">{fieldErrors.passwordConfirm}</p>}
            </div>

            {error && (
              <div className="rounded-lg border border-red-500/20 bg-red-500/5 px-4 py-2.5 text-xs text-red-400">
                {error}
              </div>
            )}

            {/* Cloudflare Turnstile — invisible Managed mode. Renders
                nothing visible 99% of the time; shows a challenge only
                when bot signal is high. */}
            <div ref={turnstile.ref} />

            <motion.button
              type="submit"
              disabled={loading || turnstile.loading || (turnstile.enabled && !turnstile.token)}
              whileHover={(loading || turnstile.loading) ? undefined : hoverLift}
              whileTap={(loading || turnstile.loading) ? undefined : tap}
              className="w-full rounded-lg bg-accent-dark px-4 py-3 text-sm font-semibold text-white glow-btn disabled:opacity-50 disabled:cursor-not-allowed border-none cursor-pointer"
            >
              {loading ? 'Creating account...' : turnstile.loading ? 'Verifying...' : 'Start Free Trial'}
            </motion.button>
          </motion.form>

          <motion.p className="mt-6 text-center text-sm text-slate-500" variants={child}>
            Already have an account?{' '}
            <a href={`${APP_URL}/dashboard`} className="font-medium text-accent-light hover:text-white transition-colors no-underline">
              Log In
            </a>
          </motion.p>
          </motion.div>
        </motion.div>

        {/* Right column: a value panel (balances the tall signup form and
            sells while they sign up) stacked above the enterprise CTA, so the
            two columns read as symmetric rather than tall-form-beside-stub. */}
        <div className="flex flex-col gap-8">
        <motion.div
          variants={card}
          className="relative rounded-2xl border border-white/[0.06] bg-white/[0.02] p-8 overflow-hidden grain-overlay"
        >
          <div className="absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r from-transparent via-accent to-transparent z-10" />
          <h2 className="font-display text-2xl font-bold text-white tracking-tight">Everything in the free trial.</h2>
          <p className="mt-2 text-sm text-slate-400">
            Full proctoring, no feature gates, no card. Here's what runs from day one:
          </p>
          <ul className="mt-6 space-y-3.5">
            {[
              ['On-device AI proctoring', 'Gaze, face, phone & object detection run on the student device — raw video never leaves it.'],
              ['Phone-camera room monitoring', 'A second angle from the student’s phone, included on every plan.'],
              ['Real-time teacher dashboard', 'Live sessions, a violation timeline with evidence, and one-click interventions.'],
              ['Built for India', 'INR + GST billing, DPDP-aligned, data hosted in-country.'],
            ].map(([t, d]) => (
              <li key={t} className="flex gap-3">
                <span className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-accent accent-glow" />
                <span>
                  <span className="block text-sm font-semibold text-white">{t}</span>
                  <span className="mt-0.5 block text-xs leading-relaxed text-slate-400">{d}</span>
                </span>
              </li>
            ))}
          </ul>
        </motion.div>

        {/* Enterprise / Demo Request section — sits beside signup on
            desktop, stacks below on mobile (handled by parent grid). */}
        <motion.div
          variants={card}
          className="relative rounded-2xl border border-white/[0.06] bg-white/[0.02] p-8 overflow-hidden grain-overlay"
        >
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

              <motion.button
                type="submit"
                disabled={demoLoading || turnstile.loading || (turnstile.enabled && !turnstile.token)}
                whileHover={(demoLoading || turnstile.loading) ? undefined : hoverLift}
                whileTap={(demoLoading || turnstile.loading) ? undefined : tap}
                className="w-full rounded-lg border border-amber/30 bg-amber/5 px-4 py-3 text-sm font-semibold text-amber transition-colors hover:bg-amber/10 disabled:opacity-50 cursor-pointer"
              >
                {demoLoading ? 'Verifying...' : 'Request Demo'}
              </motion.button>
            </form>
          )}
        </motion.div>
        </div>
        </div>
      </motion.div>
    </div>
    </>
  )
}
