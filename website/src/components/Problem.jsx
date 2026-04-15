import { motion } from 'framer-motion'
import { AlertTriangle, Users, IndianRupee } from 'lucide-react'

const fadeUp = {
  initial: { opacity: 0, y: 24 },
  whileInView: { opacity: 1, y: 0 },
  viewport: { once: true, margin: '-60px' },
  transition: { duration: 0.5 }
}

export default function Problem() {
  return (
    <section className="relative py-24 md:py-32">
      <div className="mx-auto max-w-7xl px-6">
        <motion.div {...fadeUp} className="mx-auto max-w-2xl text-center">
          <h2 className="font-display text-3xl font-bold text-white md:text-4xl">
            The Problem with Remote Exams
          </h2>
          <p className="mt-4 text-lg text-slate-400">
            Institutions lose credibility when exam integrity cannot be guaranteed.
            Manual proctoring is expensive, inconsistent, and doesn't scale.
          </p>
        </motion.div>

        <div className="mt-16 grid gap-6 md:grid-cols-3">
          {[
            {
              icon: AlertTriangle,
              title: 'Rampant Cheating',
              desc: 'Students use second devices, screen sharing, and impersonation. Traditional monitoring catches a fraction of violations.',
              stat: '73%',
              statLabel: 'of students admit to cheating online'
            },
            {
              icon: Users,
              title: 'Manual Proctoring Fails',
              desc: 'Human proctors can monitor 4-6 students at once. Fatigue, bias, and inconsistency make it unreliable at scale.',
              stat: '1:6',
              statLabel: 'maximum proctor-to-student ratio'
            },
            {
              icon: IndianRupee,
              title: 'Prohibitive Cost',
              desc: 'Hiring trained proctors for every exam is unsustainable. Institutions need a solution that works for 50 or 5,000 students.',
              stat: '10x',
              statLabel: 'cost of manual vs. AI proctoring'
            }
          ].map((item, i) => (
            <motion.div
              key={item.title}
              initial={{ opacity: 0, y: 24 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: '-60px' }}
              transition={{ duration: 0.5, delay: i * 0.1 }}
              className="group relative overflow-hidden rounded-xl border border-white/[0.06] bg-white/[0.02] p-8 transition-colors hover:border-red-500/20 hover:bg-white/[0.04] card-topline grain-overlay"
            >
              <div className="mb-6 inline-flex rounded-lg border border-red-500/20 bg-red-500/5 p-2.5">
                <item.icon size={20} className="text-red-400" />
              </div>
              <h3 className="mb-3 text-lg font-semibold text-white">{item.title}</h3>
              <p className="text-sm leading-relaxed text-slate-400">{item.desc}</p>
              <div className="mt-6 border-t border-white/[0.06] pt-4">
                <span className="font-display text-2xl font-bold text-red-400">{item.stat}</span>
                <span className="ml-2 label-mono text-slate-500">{item.statLabel}</span>
              </div>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  )
}
