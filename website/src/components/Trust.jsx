import useInView from '../hooks/useInView'

export default function Trust() {
  const [headRef, headInView] = useInView({ margin: '-60px' })
  const [statsRef, statsInView] = useInView({ margin: '-40px' })
  const [quoteRef, quoteInView] = useInView({ margin: '-40px' })

  const stats = [
    { value: '180+',  label: 'Institutions across India' },
    { value: '2.4M',  label: 'Exams proctored' },
    { value: '99.2%', label: 'Uptime (2025)' },
    { value: '<200ms', label: 'Avg detection latency' },
  ]

  return (
    <section className="relative py-24 md:py-32 bg-navy-900/30">
      <div className="pointer-events-none absolute inset-0 grain-overlay" />
      <div className="mx-auto max-w-7xl px-6 relative">
        <div
          ref={headRef}
          className={`mx-auto max-w-2xl text-center transition-all duration-500 ${
            headInView ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-6'
          }`}
        >
          <span className="label-mono text-accent">Trust & Results</span>
          <h2 className="mt-3 font-display text-3xl font-bold text-white md:text-4xl">
            Numbers That Speak
          </h2>
          <p className="mt-4 text-lg text-slate-400">
            Built with rigor, tested in production, trusted by educators.
          </p>
        </div>

        <div ref={statsRef} className="mt-16 grid grid-cols-2 gap-6 md:grid-cols-4">
          {stats.map((s, i) => (
            <div
              key={s.label}
              style={{ transitionDelay: `${i * 80}ms` }}
              className={`relative rounded-xl border border-white/[0.06] bg-white/[0.02] p-6 text-center card-topline grain-overlay transition-all duration-400 ${
                statsInView ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-5'
              }`}
            >
              <div className="font-display text-3xl font-bold text-white md:text-4xl">{s.value}</div>
              <div className="mt-2 label-mono text-slate-500">{s.label}</div>
            </div>
          ))}
        </div>

        <div
          ref={quoteRef}
          className={`mx-auto mt-16 max-w-3xl transition-all duration-500 ${
            quoteInView ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-6'
          }`}
          style={{ transitionDelay: '200ms' }}
        >
          <blockquote className="relative rounded-2xl border border-white/[0.06] bg-white/[0.02] p-8 md:p-10 grain-overlay overflow-hidden">
            {/* Accent left border */}
            <div className="absolute top-0 left-0 bottom-0 w-[3px] bg-gradient-to-b from-accent via-accent/50 to-transparent" />
            <p className="text-lg leading-relaxed text-slate-300 italic pl-4">
              "We ran Procta alongside manual proctors for one semester. The AI caught 3x more violations
              than our team, with zero false positives after manual review. We've since moved fully to Procta
              for all remote assessments."
            </p>
            <footer className="mt-6 flex items-center gap-4 pl-4">
              <div className="flex h-10 w-10 items-center justify-center rounded-full bg-accent/10 text-sm font-semibold text-accent-light border border-accent/20">
                KS
              </div>
              <div>
                <div className="text-sm font-medium text-white">Dr. Kavita Sharma</div>
                <div className="label-mono text-slate-500">Head of Examinations, Partner University</div>
              </div>
            </footer>
          </blockquote>
        </div>
      </div>
    </section>
  )
}
