import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { ChevronDown } from 'lucide-react'

const faqs = [
  {
    q: 'Does Procta record video of students?',
    a: 'No. Procta analyzes camera frames in real-time using on-device AI models. Raw video is never recorded, stored, or transmitted. We only log violation events and risk scores.'
  },
  {
    q: 'What happens if a student loses internet during the exam?',
    a: 'Procta auto-saves answers every 60 seconds to local storage. If connectivity drops, progress is preserved and synced automatically when the connection returns. No work is lost.'
  },
  {
    q: 'Can students cheat using their phone?',
    a: 'Procta uses YOLOv8n object detection to identify phones, tablets, and other unauthorized devices in the camera frame. Any detected device triggers a real-time violation log entry.'
  },
  {
    q: 'Is Procta privacy-safe?',
    a: 'Yes. We follow data minimization principles — no biometric templates, no video storage, no unnecessary PII. All data is encrypted in transit and at rest. We support data deletion requests.'
  },
  {
    q: 'How does the risk score work?',
    a: 'Each violation type (face absent, gaze deviation, object detected, audio anomaly) contributes weighted points. The score uses a log-saturating function normalized by exam duration, producing a 0-100 value. Higher scores indicate more suspicious behavior.'
  },
  {
    q: 'Can I use Procta for hiring assessments?',
    a: 'Absolutely. Procta works for any timed assessment — university exams, certification tests, or candidate screening. The secure browser and AI monitoring apply equally across all use cases.'
  },
  {
    q: 'How long does setup take?',
    a: 'Most institutions are running their first proctored exam within a day. Upload questions, configure the access code and time window, share the download link with students, and you\'re live.'
  },
  {
    q: 'Can I self-host Procta?',
    a: 'Yes. Procta runs in Docker and can be deployed on any server. We provide docker-compose configurations and deployment guides. Many institutions prefer self-hosting for data sovereignty.'
  },
]

function FAQItem({ item }) {
  const [open, setOpen] = useState(false)

  return (
    <div className="border-b border-white/[0.06]">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center justify-between bg-transparent border-none cursor-pointer px-6 py-5 text-left"
      >
        <span className="pr-4 text-sm font-medium text-white">{item.q}</span>
        <ChevronDown
          size={18}
          className={`shrink-0 text-slate-500 transition-transform duration-200 ${open ? 'rotate-180' : ''}`}
        />
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <p className="px-6 pb-5 text-sm leading-relaxed text-slate-400">{item.a}</p>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

export default function FAQ() {
  return (
    <section id="faq" className="relative py-24 md:py-32">
      <div className="mx-auto max-w-3xl px-6">
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-60px' }}
          transition={{ duration: 0.5 }}
          className="text-center"
        >
          <span className="text-sm font-medium uppercase tracking-wider text-accent">FAQ</span>
          <h2 className="mt-3 font-display text-3xl font-bold text-white md:text-4xl">
            Common Questions
          </h2>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-40px' }}
          transition={{ duration: 0.5, delay: 0.1 }}
          className="mt-12 overflow-hidden rounded-2xl border border-white/[0.06] bg-white/[0.02]"
        >
          {faqs.map(item => (
            <FAQItem key={item.q} item={item} />
          ))}
        </motion.div>
      </div>
    </section>
  )
}
