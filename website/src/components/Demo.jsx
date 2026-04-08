import { motion } from 'framer-motion'
import { Play } from 'lucide-react'
import { Link } from 'react-router-dom'

export default function Demo() {
  return (
    <section id="demo" className="relative py-24 md:py-32 bg-navy-900/30">
      <div className="mx-auto max-w-7xl px-6">
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-60px' }}
          transition={{ duration: 0.5 }}
          className="mx-auto max-w-2xl text-center"
        >
          <span className="text-sm font-medium uppercase tracking-wider text-accent">See It In Action</span>
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
          <div className="group relative aspect-video cursor-pointer overflow-hidden rounded-2xl border border-white/[0.08] bg-navy-800">
            {/* Placeholder — replace with actual video embed */}
            <div className="absolute inset-0 flex flex-col items-center justify-center">
              <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-accent/90 shadow-lg shadow-accent/30 transition-transform group-hover:scale-110">
                <Play size={28} className="ml-1 text-white" fill="white" />
              </div>
              <span className="text-sm font-medium text-slate-400">Product Demo — 2 min</span>
            </div>
            {/* Decorative grid overlay */}
            <div className="pointer-events-none absolute inset-0 opacity-[0.02]"
              style={{
                backgroundImage: 'linear-gradient(rgba(255,255,255,0.1) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.1) 1px, transparent 1px)',
                backgroundSize: '32px 32px'
              }}
            />
          </div>
        </motion.div>

        <div className="mt-10 flex flex-col items-center gap-3 sm:flex-row sm:justify-center">
          <Link
            to="/signup"
            className="rounded-xl bg-accent px-7 py-3.5 text-sm font-semibold text-white shadow-lg shadow-accent/20 transition-all hover:bg-accent-light no-underline"
          >
            Book Live Demo
          </Link>
          <a
            href="#features"
            className="rounded-xl border border-white/10 bg-white/[0.03] px-7 py-3.5 text-sm font-semibold text-slate-300 transition-all hover:border-white/20 no-underline"
          >
            Explore Features
          </a>
        </div>
      </div>
    </section>
  )
}
