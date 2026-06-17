import { Helmet } from 'react-helmet-async'
import { Link } from 'wouter'
import { ArrowRight, ExternalLink, CheckCircle, Copy, Check } from 'lucide-react'
import Navbar from '../components/Navbar'
import Footer from '../components/Footer'
import { useState } from 'react'
import { motion, useReducedMotion } from 'framer-motion'
import { fadeUp, stagger, inViewProps, pick } from '../lib/motion'

const APP = 'https://app.procta.net'
const BASE = import.meta.env.VITE_PROCTA_URL || APP

const platforms = [
  {
    id: 'canvas',
    name: 'Canvas',
    logo: '📚',
    steps: [
      { id: 'apps', label: 'Navigate to Admin → Settings → Apps', done: false },
      { id: 'add', label: 'Click + App, select By Client ID', done: false },
      { id: 'paste', label: 'Paste the Auto-config URL below', done: false },
      { id: 'placements', label: 'Add Course Navigation + Assignment Selection placements', done: false },
      { id: 'verify', label: 'Open a course and verify the tool loads', done: false },
    ],
    configUrl: `${BASE}/lti/auto-config`,
    loginUrl: `${BASE}/lti/login`,
    launchUrl: `${BASE}/lti/launch`,
    jwksUrl: `${BASE}/lti/jwks`,
    tip: 'For Client ID setup, use the auto-config URL below. Canvas will fetch configuration automatically.',
  },
  {
    id: 'moodle',
    name: 'Moodle',
    logo: '🎓',
    steps: [
      { id: 'tools', label: 'Site Administration → Plugins → External tool → Manage tools', done: false },
      { id: 'install', label: 'Click Install LTI Advantage Tool', done: false },
      { id: 'url', label: 'Enter the Auto-config URL below, click Add Legacy LTI', done: false },
      { id: 'save', label: 'Set Default launch container to Existing window, click Save', done: false },
      { id: 'activate', label: 'Click gear icon → Activate to enable across courses', done: false },
    ],
    configUrl: `${BASE}/lti/auto-config`,
    loginUrl: `${BASE}/lti/login`,
    launchUrl: `${BASE}/lti/launch`,
    jwksUrl: `${BASE}/lti/jwks`,
    tip: 'Ensure your Moodle server can reach app.procta.net. If behind a firewall, whitelist the domain.',
  },
  {
    id: 'blackboard',
    name: 'Blackboard',
    logo: '🖥️',
    steps: [
      { id: 'admin', label: 'Admin Panel → LTI Tool Providers → Register LTI 1.3/Advantage Tool', done: false },
      { id: 'name', label: 'Enter Procta as the Tool Provider Name', done: false },
      { id: 'login', label: 'Set Initiate Login URL (see below)', done: false },
      { id: 'redirect', label: 'Set Tool Redirect URL(s) (see below)', done: false },
      { id: 'jwks', label: 'Set JWKS URL (see below)', done: false },
      { id: 'services', label: 'Enable AGS + NRPS services', done: false },
    ],
    configUrl: null, // Blackboard doesn't support auto-config
    loginUrl: `${BASE}/lti/login`,
    launchUrl: `${BASE}/lti/launch`,
    jwksUrl: `${BASE}/lti/jwks`,
    tip: 'Blackboard requires manual URL entry. Copy each URL from the table below.',
  },
]

const overview_items = [
  {
    title: 'Grade Passback (AGS)',
    desc: 'Procta automatically pushes scores back to the LMS gradebook after each exam. No manual data entry needed.',
  },
  {
    title: 'Roster Sync (NRPS)',
    desc: 'Student rosters stay in sync automatically. When you publish an exam to a course in your LMS, all enrolled students are available in Procta without manual import.',
  },
  {
    title: 'Deep Linking',
    desc: 'Instructors can select Procta exams directly from within their LMS course — no need to copy-paste links or switch between platforms.',
  },
  {
    title: 'Single Sign-On (OIDC)',
    desc: 'Students and instructors log in via their LMS credentials. No separate Procta account needed.',
  },
]

export default function LtiSetup() {
  const [checkedSteps, setCheckedSteps] = useState({})
  const reduced = useReducedMotion()
  const childVar = pick(reduced, fadeUp)

  const toggleStep = (platformId, stepId) => {
    setCheckedSteps(prev => ({
      ...prev,
      [`${platformId}-${stepId}`]: !prev[`${platformId}-${stepId}`],
    }))
  }

  return (
    <div className="min-h-screen bg-navy-950">
      <Helmet>
        <title>LTI 1.3 Integration Setup — Procta for Canvas, Moodle &amp; Blackboard</title>
        <meta name="description" content="Step-by-step guide to integrating Procta AI proctoring with Canvas, Moodle, and Blackboard via LTI 1.3. Grade passback, roster sync, and SSO setup." />
        <link rel="canonical" href="https://www.procta.net/lti-setup" />
        <meta property="og:title" content="LTI 1.3 Integration Setup — Procta" />
        <meta property="og:description" content="Connect Procta with your LMS in minutes. Supports Canvas, Moodle, and Blackboard via LTI 1.3 Advantage standards." />
        <meta property="og:url" content="https://www.procta.net/lti-setup" />
        <meta name="twitter:card" content="summary_large_image" />
        <meta name="twitter:image" content="https://www.procta.net/og-image.png" />
      </Helmet>

      <Navbar />

      {/* Hero */}
      <section className="pt-36 pb-16 md:pt-44 md:pb-20">
        <motion.div className="mx-auto max-w-3xl px-6 text-center" variants={childVar} {...inViewProps}>
          <span className="label-mono text-accent">LMS Integration</span>
          <h1 className="mt-3 font-display text-4xl font-bold text-white md:text-5xl">
            Connect Procta with your LMS
          </h1>
          <p className="mx-auto mt-4 text-lg text-slate-400">
            Procta supports <strong className="text-white">Canvas</strong>,{' '}
            <strong className="text-white">Moodle</strong>, and{' '}
            <strong className="text-white">Blackboard</strong> via the LTI 1.3 Advantage
            standard. Grade passback, roster sync, deep linking, and SSO — all included.
          </p>
        </motion.div>
      </section>

      {/* Configuration URLs */}
      <section className="pb-8">
        <div className="mx-auto max-w-3xl px-6">
          <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-6 md:p-8">
            <h2 className="font-display text-xl font-bold text-white">Your Configuration URLs</h2>
            <p className="mt-2 text-sm text-slate-400">Use these URLs during LTI tool registration in your LMS.</p>
            <div className="mt-6 space-y-4">
              {[
                { label: 'Auto-config URL', url: `${BASE}/lti/auto-config` },
                { label: 'Login Initiation URL', url: `${BASE}/lti/login` },
                { label: 'Launch / Redirect URL', url: `${BASE}/lti/launch` },
                { label: 'Public JWKS URL', url: `${BASE}/lti/jwks` },
              ].map(item => (
                <div key={item.label} className="flex flex-col sm:flex-row sm:items-center gap-2">
                  <span className="text-sm font-medium text-slate-300 w-44 shrink-0">{item.label}</span>
                  <code className="flex-1 rounded-lg bg-navy-800 px-4 py-2.5 text-xs font-mono text-accent-light break-all select-all">
                    {item.url}
                  </code>
                  <button
                    onClick={() => { navigator.clipboard.writeText(item.url); }}
                    className="shrink-0 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-xs text-slate-400 hover:text-white transition-colors cursor-pointer"
                  >
                    Copy
                  </button>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* Platform tabs */}
      <section className="py-12">
        <div className="mx-auto max-w-4xl px-6">
          <h2 className="font-display text-2xl font-bold text-white text-center md:text-3xl">
            Setup guides by platform
          </h2>

          <motion.div className="mt-10 space-y-10" variants={stagger(0.1)} {...inViewProps}>
            {platforms.map(p => (
              <motion.div key={p.id} variants={childVar} className="rounded-2xl border border-white/[0.06] bg-white/[0.02] overflow-hidden">
                <div className="border-b border-white/[0.06] bg-white/[0.01] px-6 py-5">
                  <div className="flex items-center gap-3">
                    <span className="text-2xl">{p.logo}</span>
                    <h3 className="font-display text-lg font-bold text-white">{p.name}</h3>
                  </div>
                </div>
                <div className="px-6 py-6">
                  <ol className="space-y-4">
                    {p.steps.map((step) => {
                      const stepKey = `${p.id}-${step.id}`
                      const done = checkedSteps[stepKey]
                      return (
                        <li key={step.id} className="flex gap-3 items-start cursor-pointer" onClick={() => toggleStep(p.id, step.id)}>
                          <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded border mt-0.5 transition-colors ${done ? 'bg-accent border-accent' : 'border-white/20'}`}>
                            {done && <Check size={12} className="text-white" />}
                          </span>
                          <span className={`text-sm leading-relaxed transition-opacity ${done ? 'text-slate-500 line-through opacity-60' : 'text-slate-300'}`}>
                            {step.label}
                          </span>
                        </li>
                      )
                    })}
                  </ol>
                  {p.tip && (
                    <div className="mt-6 rounded-xl border border-accent/20 bg-accent/[0.04] px-4 py-3">
                      <p className="text-xs leading-relaxed text-accent-light">
                        <strong className="text-accent-light">💡 Tip:</strong> {p.tip}
                      </p>
                    </div>
                  )}
                </div>
              </motion.div>
            ))}
          </motion.div>
        </div>
      </section>

      {/* What it gives you */}
      <section className="py-16 bg-navy-900/30">
        <div className="mx-auto max-w-4xl px-6">
          <h2 className="font-display text-2xl font-bold text-white text-center md:text-3xl">
            What LTI integration gives you
          </h2>
          <motion.div className="mt-10 grid gap-6 sm:grid-cols-2" variants={stagger(0.08)} {...inViewProps}>
            {overview_items.map(item => (
              <motion.div key={item.title} variants={childVar} className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-6">
                <div className="mb-3 flex h-8 w-8 items-center justify-center rounded-lg bg-accent/10">
                  <CheckCircle size={18} className="text-accent-light" />
                </div>
                <h3 className="mb-1.5 text-sm font-semibold text-white">{item.title}</h3>
                <p className="text-sm leading-relaxed text-slate-400">{item.desc}</p>
              </motion.div>
            ))}
          </motion.div>
        </div>
      </section>

      {/* Troubleshooting */}
      <section className="py-16">
        <div className="mx-auto max-w-3xl px-6">
          <h2 className="font-display text-2xl font-bold text-white text-center md:text-3xl">
            Troubleshooting
          </h2>
          <div className="mt-10 space-y-6">
            {[
              {
                q: 'The tool doesn\'t appear in my course after registration',
                a: 'Ensure the tool status is set to "Active" or "Approved" in your LMS admin panel. Some LMS platforms (like Moodle) require a separate activation step after registration.',
              },
              {
                q: 'Students get a "Tool not found" error when launching Procta',
                a: 'Verify that the LTI tool is assigned to the correct course. In Canvas, check Course Navigation placements. In Moodle, ensure the tool is set to "Show" in course-level settings.',
              },
              {
                q: 'Grades are not syncing back to the gradebook',
                a: 'Confirm that AGS (Grade Passback) is enabled in your LTI tool configuration. In Canvas, ensure the Line Item and Result scopes are checked during registration.',
              },
              {
                q: 'Is Google Classroom supported?',
                a: 'Yes — Procta now has a native Google Classroom integration via the Google Classroom API. Teachers can connect their Google account, sync course rosters, link exams to courses, and push grades back to Classroom. It uses a different integration model than LTI 1.3 (since Google Classroom does not fully support LTI 1.3). Go to Tools → Google Classroom in your Procta dashboard to set it up.',
              },
              {
                q: 'Can I use Procta without LTI?',
                a: 'Yes. LTI integration is optional. You can use Procta as a standalone platform via the web dashboard — invite students by email or access code without connecting to any LMS.',
              },
            ].map((item, i) => (
              <details key={i} className="group rounded-xl border border-white/[0.06]">
                <summary className="flex cursor-pointer list-none items-center justify-between px-5 py-4 text-sm font-medium text-white transition-colors hover:text-accent-light">
                  {item.q}
                  <ArrowRight size={14} className="shrink-0 text-slate-500 transition-transform group-open:rotate-90" />
                </summary>
                <p className="px-5 pb-4 text-sm leading-relaxed text-slate-400">{item.a}</p>
              </details>
            ))}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="py-16 md:py-24 border-t border-white/[0.06]">
        <div className="mx-auto max-w-2xl px-6 text-center">
          <h2 className="font-display text-3xl font-bold text-white md:text-4xl">
            Ready to integrate Procta with your LMS?
          </h2>
          <p className="mt-4 text-lg text-slate-400">
            We'll help you set up the integration and run your first proctored exam.
          </p>
          <div className="mt-8 flex flex-col items-center gap-4 sm:flex-row sm:justify-center">
            <Link
              to="/signup"
              className="inline-flex items-center gap-2 rounded-xl bg-accent-dark px-7 py-3.5 text-sm font-semibold text-white glow-btn no-underline hover:bg-accent"
            >
              Start Free Trial
              <ArrowRight size={16} />
            </Link>
            <a
              href={`${APP}/dashboard`}
              className="inline-flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.03] px-7 py-3.5 text-sm font-semibold text-slate-300 transition-all hover:border-accent/30 no-underline"
              target="_blank"
              rel="noopener noreferrer"
            >
              Log In to Dashboard
              <ExternalLink size={14} />
            </a>
          </div>
        </div>
      </section>

      <Footer />
    </div>
  )
}
