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
          className="relative overflow-hidden rounded-3xl border border-accent/20 bg-gradient-to-br from-accent/10 via-navy-800 to-navy-900 px-8 py-16 text-center md:px-16 md:py-24"
        >
          {/* Background glow */}
          <div className="pointer-events-none absolute top-0 left-1/2 -translate-x-1/2 h-64 w-[500px] rounded-full bg-accent/10 blur-[100px]" />

          <div className="relative">
            <h2 className="font-display text-3xl font-bold text-white md:text-5xl">
              Secure Your Exams Today
            </h2>
            <p className="mx-auto mt-4 max-w-xl text-lg text-slate-400">
              Join institutions that trust Procta to maintain exam integrity.
              Get started with a free demo — no commitment required.
            </p>
            <div className="mt-10 flex flex-col items-center gap-4 sm:flex-row sm:justify-center">
              <Link
                to="/signup"
                className="group flex items-center gap-2 rounded-xl bg-accent px-8 py-4 text-base font-semibold text-white shadow-lg shadow-accent/25 transition-all hover:bg-accent-light hover:shadow-accent/35 no-underline"
              >
                Request Demo
                <ArrowRight size={18} className="transition-transform group-hover:translate-x-0.5" />
              </Link>
              <a
                href={`${APP_URL}/dashboard`}
                className="rounded-xl border border-white/10 bg-white/[0.03] px-8 py-4 text-base font-semibold text-slate-300 transition-all hover:border-white/20 no-underline"
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
