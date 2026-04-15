import { motion } from 'framer-motion'
import { Play } from 'lucide-react'
import { Link } from 'react-router-dom'

export default function Demo() {
  return (
    <section id="demo" className="relative py-24 md:py-32 bg-navy-900/30">
      <div className="pointer-events-none absolute inset-0 grain-overlay" />
      <div className="mx-auto max-w-7xl px-6 relative">
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-60px' }}
          transition={{ duration: 0.5 }}
          className="mx-auto max-w-2xl text-center"
        >
          <span className="label-mono text-accent">See It In Action</span>
          <h2 className="mt-3 font-display text-3xl font-bold text-white md:text-4xl">
            Watch Procta Detect Cheating
          </h2>
          <p className="mt-4 text-lg text-slate-400">
            See how the AI identifies violations, builds risk scores, and delivers actionable reports.
          </p>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 32 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-40px' }}
          transition={{ duration: 0.6, delay: 0.1 }}
          className="relative mx-auto mt-12 max-w-4xl"
        >
          <div className="group relative aspect-video cursor-pointer overflow-hidden rounded-2xl border border-white/[0.08] bg-navy-800 grain-overlay">
            {/* Accent top line */}
            <div className="absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r from-transparent via-accent/40 to-transparent z-10" />
            {/* Placeholder — replace with actual video embed */}
            <div className="absolute inset-0 flex flex-col items-center justify-center">
              <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-accent-dark shadow-lg transition-transform group-hover:scale-110 accent-glow-strong">
                <Play size={28} className="ml-1 text-white" fill="white" />
              </div>
              <span className="label-mono text-slate-400" style={{ fontSize: '12px' }}>Product Demo — 2 min</span>
            </div>
          </div>
        </motion.div>

        <div className="mt-10 flex flex-col items-center gap-3 sm:flex-row sm:justify-center">
          <Link
            to="/signup"
            className="rounded-xl bg-accent-dark px-7 py-3.5 text-sm font-semibold text-white glow-btn no-underline"
          >
            Book Live Demo
          </Link>
          <a
            href="#features"
            className="rounded-xl border border-white/10 bg-white/[0.03] px-7 py-3.5 text-sm font-semibold text-slate-300 transition-all hover:border-accent/30 no-underline"
          >
            Explore Features
          </a>
        </div>
      </div>
    </section>
  )
}
