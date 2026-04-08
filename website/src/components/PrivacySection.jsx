import { motion } from 'framer-motion'
import { Shield, Camera, Database, Lock } from 'lucide-react'

export default function PrivacySection() {
  const items = [
    {
      icon: Camera,
      title: 'No Video Recording',
      desc: 'AI inference analyzes camera frames in real-time. Raw video is never stored or transmitted to our servers.',
    },
    {
      icon: Database,
      title: 'Minimal Data Collection',
      desc: 'We store violation logs and risk scores — not biometric templates. Your students\' data stays minimal and purposeful.',
    },
    {
      icon: Lock,
      title: 'Encrypted In Transit & At Rest',
      desc: 'All communication uses TLS. Database storage is encrypted. API access requires authenticated tokens.',
    },
    {
      icon: Shield,
      title: 'GDPR-Ready Design',
      desc: 'Built with data minimization and purpose limitation in mind. Easy data deletion on request.',
    },
  ]

  return (
    <section id="privacy" className="relative py-24 md:py-32">
      <div className="mx-auto max-w-7xl px-6">
        <div className="grid items-center gap-12 md:grid-cols-2">
          <motion.div
            initial={{ opacity: 0, x: -24 }}
            whileInView={{ opacity: 1, x: 0 }}
            viewport={{ once: true, margin: '-60px' }}
            transition={{ duration: 0.5 }}
          >
            <span className="text-sm font-medium uppercase tracking-wider text-accent">Privacy & Compliance</span>
            <h2 className="mt-3 font-display text-3xl font-bold text-white md:text-4xl">
              Security Without Surveillance
            </h2>
            <p className="mt-4 text-lg text-slate-400">
              Proctoring shouldn't mean invasive monitoring. Procta proves you can have exam integrity
              without compromising student privacy.
            </p>
          </motion.div>

          <div className="grid gap-4 sm:grid-cols-2">
            {items.map((item, i) => (
              <motion.div
                key={item.title}
                initial={{ opacity: 0, y: 16 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true, margin: '-40px' }}
                transition={{ duration: 0.4, delay: i * 0.08 }}
                className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-5"
              >
                <item.icon size={18} className="mb-3 text-emerald" />
                <h3 className="mb-1 text-sm font-semibold text-white">{item.title}</h3>
                <p className="text-xs leading-relaxed text-slate-400">{item.desc}</p>
              </motion.div>
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}
