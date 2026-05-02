import { motion } from 'framer-motion'
import { ArrowRight, Play } from 'lucide-react'
import { Link } from 'react-router-dom'

export default function Hero() {
  return (
    <section className="relative overflow-hidden pt-32 pb-20 md:pt-44 md:pb-32">
      {/* Grain overlay */}
      <div className="pointer-events-none absolute inset-0 grain-overlay" />
      {/* Glow */}
      <div className="pointer-events-none absolute top-0 left-1/2 -translate-x-1/2 h-[600px] w-[800px] rounded-full bg-accent/8 blur-[150px]" />

      <div className="relative mx-auto max-w-7xl px-6">
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6 }}
          className="mx-auto max-w-4xl text-center"
        >
          {/* Eyebrow — Phase 2 design swap from "AI-Powered Proctoring"
              to a concrete trust line that matches the Claude design's
              "Trusted by 180+ institutions" framing. */}
          <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-accent/20 bg-accent/5 px-4 py-1.5 accent-glow">
            <span className="h-1.5 w-1.5 rounded-full bg-accent animate-pulse" />
            <span className="label-mono text-accent-light" style={{ fontSize: '11px' }}>
              Trusted by 180+ institutions across India
            </span>
          </div>

          {/* Headline — three-line outcome-first structure from the Claude
              design (was "Secure Exams with Explainable AI"). The middle
              "automated." word picks up the accent gradient so the eye
              lands on the differentiator instead of generic "Secure". */}
          <h1 className="font-display text-4xl font-bold leading-[1.1] tracking-tight text-white md:text-6xl lg:text-7xl">
            Cheating reduced.<br />
            Scoring{' '}
            <span className="relative">
              <span className="relative z-10 bg-gradient-to-r from-accent to-accent-light bg-clip-text text-transparent">
                automated.
              </span>
            </span><br />
            Your IT team untouched.
          </h1>

          <p className="mx-auto mt-6 max-w-2xl text-lg leading-relaxed text-slate-400 md:text-xl">
            Procta is a proctored exam platform built for Indian higher education —
            AI monitoring, automated scorecards, and a student experience calm enough
            for 90-minute exams.
          </p>

          <div className="mt-10 flex flex-col items-center gap-4 sm:flex-row sm:justify-center">
            <Link
              to="/signup"
              className="group flex items-center gap-2 rounded-xl bg-accent-dark px-7 py-3.5 text-sm font-semibold text-white glow-btn no-underline"
            >
              Request Demo
              <ArrowRight size={16} className="transition-transform group-hover:translate-x-0.5" />
            </Link>
            <a
              href="#demo"
              className="flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.03] px-7 py-3.5 text-sm font-semibold text-slate-300 transition-all hover:border-accent/30 hover:bg-white/[0.06] no-underline"
            >
              <Play size={16} />
              Watch Demo
            </a>
          </div>

          <div className="mt-14 flex items-center justify-center gap-8 text-sm text-slate-500">
            <span>Trusted by institutions across India</span>
            <span className="hidden h-4 w-px bg-white/10 sm:block" />
            <span className="hidden sm:block">No credit card required</span>
          </div>
        </motion.div>

        {/* Dashboard mockup */}
        <motion.div
          initial={{ opacity: 0, y: 40 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, delay: 0.3 }}
          className="relative mx-auto mt-16 max-w-5xl"
        >
          <div className="overflow-hidden rounded-xl border border-white/[0.08] bg-navy-900 shadow-2xl shadow-black/40 card-topline grain-overlay" style={{ overflow: 'hidden' }}>
            {/* Persistent top accent line */}
            <div className="absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r from-transparent via-accent to-transparent z-10" />
            <div className="flex items-center gap-2 border-b border-white/[0.06] px-4 py-3">
              <span className="h-2.5 w-2.5 rounded-full bg-red-500/60" />
              <span className="h-2.5 w-2.5 rounded-full bg-amber/60" />
              <span className="h-2.5 w-2.5 rounded-full bg-emerald/60" />
              <span className="ml-3 label-mono text-slate-500">Procta Dashboard</span>
            </div>
            <div className="p-6 md:p-8">
              <div className="grid grid-cols-4 gap-4 mb-6">
                {[
                  { label: 'Active Students', value: '47', color: 'text-emerald' },
                  { label: 'Avg Risk Score', value: '12', color: 'text-accent-light' },
                  { label: 'Violations', value: '3', color: 'text-amber' },
                  { label: 'Completed', value: '128', color: 'text-slate-300' },
                ].map(s => (
                  <div key={s.label} className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-4 card-topline">
                    <div className={`font-display text-2xl font-bold ${s.color}`}>{s.value}</div>
                    <div className="mt-1 label-mono text-slate-500">{s.label}</div>
                  </div>
                ))}
              </div>
              <div className="space-y-2">
                {[
                  { name: 'Arjun Mehta', roll: 'CS2024001', risk: 8, status: 'Active' },
                  { name: 'Priya Sharma', roll: 'CS2024015', risk: 34, status: 'Flagged' },
                  { name: 'Rohan Gupta', roll: 'CS2024023', risk: 4, status: 'Active' },
                  { name: 'Sneha Patel', roll: 'CS2024042', risk: 72, status: 'High Risk' },
                ].map(row => (
                  <div key={row.roll} className="flex items-center justify-between rounded-lg border border-white/[0.04] bg-white/[0.01] px-4 py-3">
                    <div className="flex items-center gap-4">
                      <div className="h-8 w-8 rounded-full bg-navy-700 flex items-center justify-center text-xs font-medium text-slate-400">
                        {row.name.split(' ').map(n => n[0]).join('')}
                      </div>
                      <div>
                        <div className="text-sm font-medium text-slate-300">{row.name}</div>
                        <div className="label-mono text-slate-500">{row.roll}</div>
                      </div>
                    </div>
                    <div className="flex items-center gap-4">
                      <div className="flex items-center gap-2">
                        <div className="h-1.5 w-16 rounded-full bg-white/5 overflow-hidden">
                          <div
                            className="h-full rounded-full"
                            style={{
                              width: `${row.risk}%`,
                              backgroundColor: row.risk > 50 ? '#ef4444' : row.risk > 20 ? '#f59e0b' : '#3dd9a8'
                            }}
                          />
                        </div>
                        <span className="font-mono text-xs tabular-nums text-slate-500 w-6 text-right">{row.risk}</span>
                      </div>
                      <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium font-mono ${
                        row.status === 'High Risk' ? 'bg-red-500/10 text-red-400' :
                        row.status === 'Flagged' ? 'bg-amber/10 text-amber' :
                        'bg-emerald/10 text-emerald'
                      }`}>
                        {row.status}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </motion.div>
      </div>
    </section>
  )
}
