import { motion } from 'framer-motion'
import { ArrowRight, Play } from 'lucide-react'
import { Link } from 'react-router-dom'

export default function Hero() {
  return (
    <section className="relative overflow-hidden pt-32 pb-20 md:pt-44 md:pb-32">
      {/* Background grid */}
      <div className="pointer-events-none absolute inset-0 opacity-[0.03]"
        style={{
          backgroundImage: 'linear-gradient(rgba(255,255,255,0.1) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.1) 1px, transparent 1px)',
          backgroundSize: '64px 64px'
        }}
      />
      {/* Glow */}
      <div className="pointer-events-none absolute top-0 left-1/2 -translate-x-1/2 h-[600px] w-[800px] rounded-full bg-accent/5 blur-[120px]" />

      <div className="relative mx-auto max-w-7xl px-6">
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6 }}
          className="mx-auto max-w-4xl text-center"
        >
          <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-accent/20 bg-accent/5 px-4 py-1.5">
            <span className="h-1.5 w-1.5 rounded-full bg-accent animate-pulse" />
            <span className="text-xs font-medium tracking-wide text-accent-light uppercase">
              AI-Powered Proctoring
            </span>
          </div>

          <h1 className="font-display text-4xl font-extrabold leading-[1.1] tracking-tight text-white md:text-6xl lg:text-7xl">
            Secure Exams with
            <span className="relative ml-3">
              <span className="relative z-10 bg-gradient-to-r from-accent to-accent-light bg-clip-text text-transparent">
                Explainable AI
              </span>
            </span>
          </h1>

          <p className="mx-auto mt-6 max-w-2xl text-lg leading-relaxed text-slate-400 md:text-xl">
            Detect cheating in real-time with behavioral risk scoring,
            face verification, and device monitoring. Zero manual proctors needed.
          </p>

          <div className="mt-10 flex flex-col items-center gap-4 sm:flex-row sm:justify-center">
            <Link
              to="/signup"
              className="group flex items-center gap-2 rounded-xl bg-accent px-7 py-3.5 text-sm font-semibold text-white shadow-lg shadow-accent/20 transition-all hover:bg-accent-light hover:shadow-accent/30 no-underline"
            >
              Request Demo
              <ArrowRight size={16} className="transition-transform group-hover:translate-x-0.5" />
            </Link>
            <a
              href="#demo"
              className="flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.03] px-7 py-3.5 text-sm font-semibold text-slate-300 transition-all hover:border-white/20 hover:bg-white/[0.06] no-underline"
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
          <div className="overflow-hidden rounded-xl border border-white/[0.08] bg-navy-900 shadow-2xl shadow-black/40">
            <div className="flex items-center gap-2 border-b border-white/[0.06] px-4 py-3">
              <span className="h-2.5 w-2.5 rounded-full bg-white/10" />
              <span className="h-2.5 w-2.5 rounded-full bg-white/10" />
              <span className="h-2.5 w-2.5 rounded-full bg-white/10" />
              <span className="ml-3 text-xs text-slate-500">Procta Dashboard</span>
            </div>
            <div className="p-6 md:p-8">
              <div className="grid grid-cols-4 gap-4 mb-6">
                {[
                  { label: 'Active Students', value: '47', color: 'text-emerald' },
                  { label: 'Avg Risk Score', value: '12', color: 'text-accent-light' },
                  { label: 'Violations', value: '3', color: 'text-amber' },
                  { label: 'Completed', value: '128', color: 'text-slate-300' },
                ].map(s => (
                  <div key={s.label} className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-4">
                    <div className={`font-display text-2xl font-bold ${s.color}`}>{s.value}</div>
                    <div className="mt-1 text-xs text-slate-500">{s.label}</div>
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
                        <div className="text-xs text-slate-500">{row.roll}</div>
                      </div>
                    </div>
                    <div className="flex items-center gap-4">
                      <div className="flex items-center gap-2">
                        <div className="h-1.5 w-16 rounded-full bg-white/5 overflow-hidden">
                          <div
                            className="h-full rounded-full"
                            style={{
                              width: `${row.risk}%`,
                              backgroundColor: row.risk > 50 ? '#ef4444' : row.risk > 20 ? '#f59e0b' : '#10b981'
                            }}
                          />
                        </div>
                        <span className="text-xs tabular-nums text-slate-500 w-6 text-right">{row.risk}</span>
                      </div>
                      <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
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
