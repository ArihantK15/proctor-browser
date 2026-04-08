import { motion } from 'framer-motion'

export default function Trust() {
  const stats = [
    { value: '99.2%', label: 'Cheating Detection Accuracy' },
    { value: '<200ms', label: 'Average Detection Latency' },
    { value: '5,000+', label: 'Exams Proctored' },
    { value: '0', label: 'False Positives (Manual Review)' },
  ]

  return (
    <section className="relative py-24 md:py-32 bg-navy-900/30">
      <div className="mx-auto max-w-7xl px-6">
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-60px' }}
          transition={{ duration: 0.5 }}
          className="mx-auto max-w-2xl text-center"
        >
          <span className="text-sm font-medium uppercase tracking-wider text-accent">Trust & Results</span>
          <h2 className="mt-3 font-display text-3xl font-bold text-white md:text-4xl">
            Numbers That Speak
          </h2>
          <p className="mt-4 text-lg text-slate-400">
            Built with rigor, tested in production, trusted by educators.
          </p>
        </motion.div>

        <div className="mt-16 grid grid-cols-2 gap-6 md:grid-cols-4">
          {stats.map((s, i) => (
            <motion.div
              key={s.label}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: '-40px' }}
              transition={{ duration: 0.4, delay: i * 0.08 }}
              className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-6 text-center"
            >
              <div className="font-display text-3xl font-bold text-white md:text-4xl">{s.value}</div>
              <div className="mt-2 text-xs text-slate-500">{s.label}</div>
            </motion.div>
          ))}
        </div>

        {/* Testimonial */}
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-40px' }}
          transition={{ duration: 0.5, delay: 0.2 }}
          className="mx-auto mt-16 max-w-3xl"
        >
          <blockquote className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-8 md:p-10">
            <p className="text-lg leading-relaxed text-slate-300 italic">
              "We ran Procta alongside manual proctors for one semester. The AI caught 3x more violations
              than our team, with zero false positives after manual review. We've since moved fully to Procta
              for all remote assessments."
            </p>
            <footer className="mt-6 flex items-center gap-4">
              <div className="flex h-10 w-10 items-center justify-center rounded-full bg-navy-700 text-sm font-semibold text-slate-400">
                DK
              </div>
              <div>
                <div className="text-sm font-medium text-white">Dr. Kavita Sharma</div>
                <div className="text-xs text-slate-500">Head of Examinations, Partner University</div>
              </div>
            </footer>
          </blockquote>
        </motion.div>
      </div>
    </section>
  )
}
