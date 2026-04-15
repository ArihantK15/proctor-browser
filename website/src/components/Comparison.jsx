import { motion } from 'framer-motion'
import { Check, X } from 'lucide-react'

export default function Comparison() {
  const rows = [
    { feature: 'Behavioral Risk Score (0-100)', procta: true, others: false },
    { feature: 'Explainable AI Decisions', procta: true, others: false },
    { feature: 'Offline-Resilient Exams', procta: true, others: false },
    { feature: 'No Video Recording Required', procta: true, others: false },
    { feature: 'Real-Time Object Detection', procta: true, others: 'partial' },
    { feature: 'Secure Desktop Browser', procta: true, others: true },
    { feature: 'Gaze & Face Tracking', procta: true, others: true },
    { feature: 'Self-Hosted Option', procta: true, others: false },
  ]

  return (
    <section className="relative py-24 md:py-32 bg-navy-900/30">
      <div className="pointer-events-none absolute inset-0 grain-overlay" />
      <div className="mx-auto max-w-4xl px-6 relative">
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-60px' }}
          transition={{ duration: 0.5 }}
          className="mx-auto max-w-2xl text-center"
        >
          <span className="label-mono text-accent">Comparison</span>
          <h2 className="mt-3 font-display text-3xl font-bold text-white md:text-4xl">
            How Procta Stacks Up
          </h2>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-40px' }}
          transition={{ duration: 0.5, delay: 0.1 }}
          className="mt-12 relative overflow-hidden rounded-2xl border border-white/[0.06]"
        >
          {/* Accent top line */}
          <div className="absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r from-transparent via-accent to-transparent z-10" />
          <table className="w-full">
            <thead>
              <tr className="border-b border-white/[0.06] bg-white/[0.02]">
                <th className="px-6 py-4 text-left text-sm font-medium text-slate-400">Feature</th>
                <th className="px-6 py-4 text-center text-sm font-semibold text-accent-light">Procta</th>
                <th className="px-6 py-4 text-center text-sm font-medium text-slate-500">Others</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={row.feature} className={`border-b border-white/[0.04] ${i % 2 === 0 ? 'bg-white/[0.01]' : ''}`}>
                  <td className="px-6 py-3.5 text-sm text-slate-300">{row.feature}</td>
                  <td className="px-6 py-3.5 text-center">
                    <Check size={18} className="mx-auto text-accent" />
                  </td>
                  <td className="px-6 py-3.5 text-center">
                    {row.others === true ? (
                      <Check size={18} className="mx-auto text-slate-500" />
                    ) : row.others === 'partial' ? (
                      <span className="label-mono text-slate-500" style={{ fontSize: '11px' }}>Partial</span>
                    ) : (
                      <X size={18} className="mx-auto text-slate-600" />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </motion.div>
      </div>
    </section>
  )
}
