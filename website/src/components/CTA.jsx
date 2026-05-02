import { motion } from 'framer-motion'
import { ArrowRight } from 'lucide-react'
import { Link } from 'react-router-dom'
import { APP_URL } from '../config'

export default function CTA() {
  return (
    <section className="relative py-24 md:py-32">
      <div className="mx-auto max-w-7xl px-6">
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-60px' }}
          transition={{ duration: 0.5 }}
          className="relative overflow-hidden rounded-3xl border border-accent/20 bg-gradient-to-br from-accent/10 via-navy-800 to-navy-900 px-8 py-16 text-center md:px-16 md:py-24 grain-overlay"
        >
          {/* Background glow */}
          <div className="pointer-events-none absolute top-0 left-1/2 -translate-x-1/2 h-64 w-[500px] rounded-full bg-accent/10 blur-[100px]" />
          {/* Top accent line */}
          <div className="absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r from-transparent via-accent to-transparent z-10" />

          <div className="relative">
            <h2 className="font-display text-3xl font-bold text-white md:text-5xl">
              Cheating reduced. Scoring automated.<br />
              Your IT team untouched.
            </h2>
            <p className="mx-auto mt-4 max-w-xl text-lg text-slate-400">
              30-minute live walkthrough with a test exam from your syllabus —
              no sales pitch. We'll show you the dashboard, the student experience,
              and the AI risk scoring on real data.
            </p>
            <div className="mt-10 flex flex-col items-center gap-4 sm:flex-row sm:justify-center">
              <Link
                to="/signup"
                className="group flex items-center gap-2 rounded-xl bg-accent-dark px-8 py-4 text-base font-semibold text-white glow-btn no-underline"
              >
                Request Demo
                <ArrowRight size={18} className="transition-transform group-hover:translate-x-0.5" />
              </Link>
              <a
                href={`${APP_URL}/dashboard`}
                className="rounded-xl border border-white/10 bg-white/[0.03] px-8 py-4 text-base font-semibold text-slate-300 transition-all hover:border-accent/30 no-underline"
              >
                Log In
              </a>
            </div>
          </div>
        </motion.div>
      </div>
    </section>
  )
}
