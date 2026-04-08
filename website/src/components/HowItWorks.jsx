import { motion } from 'framer-motion'
import { Upload, Monitor, Scan, FileText } from 'lucide-react'

export default function HowItWorks() {
  const steps = [
    {
      icon: Upload,
      step: '01',
      title: 'Create Your Exam',
      desc: 'Upload questions, set time limits, configure access codes, and schedule the exam window.',
    },
    {
      icon: Monitor,
      step: '02',
      title: 'Student Launches Secure Browser',
      desc: 'Candidates download the Procta desktop app. Kiosk mode locks down the system — no tab switching, no screen sharing.',
    },
    {
      icon: Scan,
      step: '03',
      title: 'AI Monitors in Real-Time',
      desc: 'Face detection, gaze tracking, object detection, and audio analysis run continuously. Every anomaly is logged.',
    },
    {
      icon: FileText,
      step: '04',
      title: 'Review Reports & Scores',
      desc: 'Get a risk score for every student with a full violation timeline. Export results, flag suspicious sessions, or force-submit.',
    },
  ]

  return (
    <section id="how-it-works" className="relative py-24 md:py-32 bg-navy-900/30">
      <div className="mx-auto max-w-7xl px-6">
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-60px' }}
          transition={{ duration: 0.5 }}
          className="mx-auto max-w-2xl text-center"
        >
          <span className="text-sm font-medium uppercase tracking-wider text-accent">How It Works</span>
          <h2 className="mt-3 font-display text-3xl font-bold text-white md:text-4xl">
            Four Steps to Secure Exams
          </h2>
        </motion.div>

        <div className="relative mt-16">
          {/* Connection line */}
          <div className="absolute top-12 left-0 right-0 hidden h-px bg-gradient-to-r from-transparent via-accent/20 to-transparent md:block" />

          <div className="grid gap-8 md:grid-cols-4">
            {steps.map((item, i) => (
              <motion.div
                key={item.step}
                initial={{ opacity: 0, y: 24 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true, margin: '-40px' }}
                transition={{ duration: 0.5, delay: i * 0.12 }}
                className="relative text-center"
              >
                <div className="relative mx-auto mb-6 flex h-24 w-24 items-center justify-center">
                  <div className="absolute inset-0 rounded-2xl border border-white/[0.06] bg-navy-800" />
                  <item.icon size={28} className="relative z-10 text-accent-light" />
                  <span className="absolute -top-2 -right-2 z-10 flex h-6 w-6 items-center justify-center rounded-full bg-accent text-[10px] font-bold text-white">
                    {item.step}
                  </span>
                </div>
                <h3 className="mb-2 text-base font-semibold text-white">{item.title}</h3>
                <p className="text-sm leading-relaxed text-slate-400">{item.desc}</p>
              </motion.div>
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}
