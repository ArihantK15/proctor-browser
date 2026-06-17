import { ArrowRight, Play } from 'lucide-react'
import { Link } from 'wouter'
import { useState, useEffect, useRef } from 'react'
import { motion, useReducedMotion, useScroll, useTransform } from 'framer-motion'
import { fadeUp, scaleIn, stagger, reducedFade, maskUp, EASE_OUT } from '../lib/motion'
import CountUp from './CountUp'

// Risk → colour + label, shared by the live mockup rows below.
const riskColor = (r) => (r > 50 ? '#ef4444' : r > 20 ? '#f59e0b' : '#3dd9a8')
const statusFor = (r) => (r > 50 ? 'High Risk' : r > 20 ? 'Flagged' : 'Active')

export default function Hero() {
  const reduced = useReducedMotion()
  const childVar = reduced ? reducedFade : fadeUp
  const mockVar = reduced ? reducedFade : scaleIn
  const headingVar = reduced ? reducedFade : maskUp

  // Scroll parallax: the dashboard mockup drifts up slightly as the hero
  // scrolls away, giving the layered "different speeds" depth. Disabled
  // under reduced motion (range collapses to 0).
  const heroRef = useRef(null)
  const { scrollYProgress } = useScroll({
    target: heroRef,
    offset: ['start start', 'end start'],
  })
  const mockY = useTransform(scrollYProgress, [0, 1], reduced ? [0, 0] : [0, -70])

  // Live monitoring mockup: rows tick over time so the dashboard reads as a
  // real product feed, not a screenshot. Communicates status (allowed loop
  // per the motion guidance) and pauses entirely under reduced motion.
  const [rows, setRows] = useState([
    { name: 'Arjun Mehta', roll: 'CS2024001', risk: 8 },
    { name: 'Priya Sharma', roll: 'CS2024015', risk: 34 },
    { name: 'Rohan Gupta', roll: 'CS2024023', risk: 4 },
    { name: 'Sneha Patel', roll: 'CS2024042', risk: 72 },
  ])

  useEffect(() => {
    if (reduced) return
    const id = setInterval(() => {
      setRows((prev) => {
        const next = prev.map((r) => ({ ...r }))
        const i = Math.floor(Math.random() * next.length)
        const delta = Math.round(Math.random() * 16) - 6 // -6 … +10
        next[i].risk = Math.max(2, Math.min(95, next[i].risk + delta))
        return next
      })
    }, 2800)
    return () => clearInterval(id)
  }, [reduced])

  return (
    <section ref={heroRef} className="relative overflow-hidden pt-32 pb-20 md:pt-44 md:pb-32">
      {/* Grain overlay */}
      <div className="pointer-events-none absolute inset-0 grain-overlay" />
      {/* Ambient glow — primary blob drifts, a second offset blob adds depth */}
      <div className="pointer-events-none absolute top-0 left-1/2 -translate-x-1/2 h-[600px] w-[800px] rounded-full bg-accent/8 blur-[150px] hero-aurora" />
      <div className="pointer-events-none absolute -top-10 left-[18%] h-[420px] w-[420px] rounded-full bg-accent-light/[0.06] blur-[140px] hero-aurora" style={{ animationDelay: '-6s', animationDuration: '20s' }} />

      <div className="relative mx-auto max-w-7xl px-6">
        <motion.div
          className="mx-auto max-w-4xl text-center"
          variants={stagger(0.08)}
          initial="hidden"
          animate="show"
        >
          {/* Eyebrow — concrete trust signal: proven results over
              an unverifiable institution count. */}
          <motion.div variants={childVar} className="mb-6 inline-flex items-center gap-2 rounded-full border border-accent/20 bg-accent/5 px-4 py-1.5 accent-glow">
            <span className="h-1.5 w-1.5 rounded-full bg-accent animate-pulse" />
            <span className="label-mono text-accent-light" style={{ fontSize: '11px' }}>
              Built for Indian colleges, coaching institutes, and exam cells
            </span>
          </motion.div>

          {/* Brand slogan kicker — sits just above the H1 in display type
              so the brand promise is always paired with the headline.
              Repeated in <title>, og:title, twitter:title, JSON-LD slogan,
              webmanifest name + footer wordmark so it shows up wherever
              someone encounters the brand. */}
          <motion.p variants={childVar} className="mb-4 font-display text-sm font-semibold uppercase tracking-[0.18em] text-accent-light/90 md:text-base">
            <span className="whitespace-nowrap">Remote exams.</span>{' '}
            <span className="whitespace-nowrap text-accent">Real results.</span>
          </motion.p>

          {/* Headline — three-line outcome-first structure from the Claude
              design (was "Secure Exams with Explainable AI"). The middle
              "automated." word picks up the accent gradient so the eye
              lands on the differentiator instead of generic "Secure". */}
          <span className="block overflow-hidden" style={{ paddingBottom: '0.06em' }}>
            <motion.h1 variants={headingVar} className="font-display text-4xl font-bold leading-[1.1] tracking-tight text-white md:text-6xl lg:text-7xl">
              Run remote exams.<br />
              Proctor with{' '}
              <span className="relative">
                <span className="relative z-10 bg-gradient-to-r from-accent to-accent-light bg-clip-text text-transparent">
                  evidence.
                </span>
              </span><br />
              Publish results faster.
            </motion.h1>
          </span>

          <motion.p variants={childVar} className="mx-auto mt-6 max-w-2xl text-lg leading-relaxed text-slate-400 md:text-xl">
            Procta is the full exam workflow: Procta Secure Browser (PSB), on-device
            AI proctoring, phone-camera room scan, live teacher dashboard,
            AI grading suggestions, scorecards, LTI, and Razorpay billing.
          </motion.p>

          <motion.div variants={childVar} className="mt-10 flex flex-col items-center gap-4 sm:flex-row sm:justify-center">
            <Link
              to="/signup"
              className="group flex items-center gap-2 rounded-xl bg-accent-dark px-7 py-3.5 text-sm font-semibold text-white glow-btn btn-sheen no-underline transition-transform active:scale-[0.97]"
            >
              Start Free Trial
              <ArrowRight size={16} className="transition-transform group-hover:translate-x-0.5" />
            </Link>
            <a
              href="#demo"
              className="flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.03] px-7 py-3.5 text-sm font-semibold text-slate-300 transition-all hover:border-accent/30 hover:bg-white/[0.06] no-underline active:scale-[0.97]"
            >
              <Play size={16} />
              Watch Demo
            </a>
          </motion.div>

          <motion.div variants={childVar} className="mt-14 flex items-center justify-center gap-8 text-sm text-slate-500">
            <span>1,500 VU clean load test</span>
            <span className="hidden h-4 w-px bg-white/10 sm:block" />
            <span className="hidden sm:block">3,500-student architecture target</span>
            <span className="hidden h-4 w-px bg-white/10 md:block" />
            <span className="hidden md:block">890+ backend tests</span>
          </motion.div>
        </motion.div>

        {/* Dashboard mockup — outer layer drives scroll parallax, inner
            layer the entrance scale/fade. */}
        <motion.div className="relative mx-auto mt-16 max-w-5xl" style={{ y: mockY }}>
        <motion.div
          variants={mockVar}
          initial="hidden"
          animate="show"
          transition={{ delay: reduced ? 0 : 0.5 }}
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
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-6">
                {[
                  { label: 'Active Students', value: '1.5k', color: 'text-emerald' },
                  { label: 'Live Cache Cap', value: '6.5k', color: 'text-accent-light' },
                  { label: 'AI Grades', value: '~3s', color: 'text-amber' },
                  { label: 'Tests Passing', value: '890+', color: 'text-slate-300' },
                ].map(s => (
                  <div key={s.label} className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-4 card-topline">
                    <div className={`font-display text-2xl font-bold ${s.color}`}><CountUp value={s.value} /></div>
                    <div className="mt-1 label-mono text-slate-500">{s.label}</div>
                  </div>
                ))}
              </div>
              <div className="space-y-2">
                {rows.map((row) => {
                  const status = statusFor(row.risk)
                  return (
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
                          {/* Risk bar fills on reveal via a transform-only scaleX
                              (origin-left) so the mockup reads as a live product
                              rather than a static screenshot. */}
                          <motion.div
                            className="h-full rounded-full"
                            style={{ backgroundColor: riskColor(row.risk) }}
                            initial={reduced ? false : { width: 0 }}
                            animate={{ width: `${row.risk}%` }}
                            transition={{ duration: 0.6, ease: EASE_OUT }}
                          />
                        </div>
                        <span className="font-mono text-xs tabular-nums text-slate-500 w-6 text-right">{row.risk}</span>
                      </div>
                      <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium font-mono transition-colors duration-300 ${
                        status === 'High Risk' ? 'bg-red-500/10 text-red-400' :
                        status === 'Flagged' ? 'bg-amber/10 text-amber' :
                        'bg-emerald/10 text-emerald'
                      }`}>
                        {status}
                      </span>
                    </div>
                  </div>
                  )
                })}
              </div>
            </div>
          </div>
        </motion.div>
        </motion.div>
      </div>
    </section>
  )
}
