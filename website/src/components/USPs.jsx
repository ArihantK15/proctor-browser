import { motion } from 'framer-motion'
import { BarChart3, Brain, Smartphone, WifiOff, ShieldCheck } from 'lucide-react'

export default function USPs() {
  const usps = [
    {
      icon: BarChart3,
      title: 'Behavioral Risk Score',
      desc: 'Every student gets a 0-100 risk score based on gaze patterns, face presence, object detection, and audio anomalies. No binary pass/fail — you see the full picture.',
    },
    {
      icon: Brain,
      title: 'Explainable AI Decisions',
      desc: 'Every flag comes with a timestamped violation log. Know exactly what triggered it, when, and how severe it was. No black-box judgments.',
    },
    {
      icon: Smartphone,
      title: 'Second Device Detection',
      desc: 'YOLO-based object detection identifies phones, tablets, and other devices in the camera frame. Real-time alerts for unauthorized items.',
    },
    {
      icon: WifiOff,
      title: 'Offline-Resilient Exams',
      desc: 'Auto-save with local persistence. If connectivity drops, answers are preserved and synced when connection returns. No lost progress.',
    },
    {
      icon: ShieldCheck,
      title: 'Privacy-First Architecture',
      desc: 'No video recording by default. AI inference runs analysis on frames without storing raw footage. Minimal data collection, maximum insight.',
    },
  ]

  return (
    <section id="differentiators" className="relative py-24 md:py-32">
      <div className="mx-auto max-w-7xl px-6">
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-60px' }}
          transition={{ duration: 0.5 }}
          className="mx-auto max-w-2xl text-center"
        >
          <span className="text-sm font-medium uppercase tracking-wider text-accent">Why Procta</span>
          <h2 className="mt-3 font-display text-3xl font-bold text-white md:text-4xl">
            Built Different
          </h2>
          <p className="mt-4 text-lg text-slate-400">
            Not another webcam recorder. Procta is an intelligence layer that understands exam behavior.
          </p>
        </motion.div>

        <div className="mt-16 grid gap-px overflow-hidden rounded-2xl border border-white/[0.06] bg-white/[0.03] md:grid-cols-3">
          {usps.map((item, i) => (
            <motion.div
              key={item.title}
              initial={{ opacity: 0 }}
              whileInView={{ opacity: 1 }}
              viewport={{ once: true, margin: '-40px' }}
              transition={{ duration: 0.4, delay: i * 0.08 }}
              className="border-white/[0.04] bg-navy-950 p-8 transition-colors hover:bg-white/[0.02]"
              style={{
                borderRight: (i + 1) % 3 !== 0 ? '1px solid rgba(255,255,255,0.04)' : 'none',
                borderBottom: i < 3 ? '1px solid rgba(255,255,255,0.04)' : 'none',
              }}
            >
              <div className="mb-5 inline-flex rounded-lg border border-accent/20 bg-accent/5 p-2.5">
                <item.icon size={20} className="text-accent-light" />
              </div>
              <h3 className="mb-2 text-base font-semibold text-white">{item.title}</h3>
              <p className="text-sm leading-relaxed text-slate-400">{item.desc}</p>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  )
}
