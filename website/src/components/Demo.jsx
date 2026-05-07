import { motion } from 'framer-motion'
import { Play } from 'lucide-react'
import { Link } from 'react-router-dom'
import { useState, useRef, useEffect } from 'react'

export default function Demo() {
  // The demo is a 95 KB animated React app served from /demo.html. We only
  // load it after the user clicks Play — it pulls React + Babel from a CDN
  // (~250 KB) and immediately starts a requestAnimationFrame loop, neither
  // of which we want running on first paint of the marketing page.
  const [playing, setPlaying] = useState(false)
  const cardRef = useRef(null)

  // Lazy-mount the iframe when the demo card scrolls into view, so visitors
  // who scroll past it don't pay the load cost. Click-to-play is still
  // gated on the `playing` flag below — IntersectionObserver only warms
  // the connection by setting an early "almost playing" hint via prefetch.
  useEffect(() => {
    if (!cardRef.current || playing) return
    const io = new IntersectionObserver(([e]) => {
      if (e.isIntersecting) {
        // Prefetch the demo HTML so the click→play feels instant.
        const link = document.createElement('link')
        link.rel = 'prefetch'
        link.href = '/demo.html'
        link.as = 'document'
        document.head.appendChild(link)
        io.disconnect()
      }
    }, { rootMargin: '200px' })
    io.observe(cardRef.current)
    return () => io.disconnect()
  }, [playing])

  return (
    <section id="demo" className="relative py-24 md:py-32 bg-navy-900/30">
      <div className="pointer-events-none absolute inset-0 grain-overlay" />
      <div className="mx-auto max-w-7xl px-6 relative">
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-60px' }}
          transition={{ duration: 0.5 }}
          className="mx-auto max-w-2xl text-center"
        >
          <span className="label-mono text-accent">Request a Demo</span>
          <h2 className="mt-3 font-display text-3xl font-bold text-white md:text-4xl">
            See Procta running on your exam data
          </h2>
          <p className="mt-4 text-lg text-slate-400">
            We'll run a live walkthrough with a test exam from your syllabus —
            30 minutes, no sales pitch. Bring your IT manager; there's nothing
            to install on the server.
          </p>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 32 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-40px' }}
          transition={{ duration: 0.6, delay: 0.1 }}
          className="relative mx-auto mt-12 max-w-4xl"
        >
          <div
            ref={cardRef}
            onClick={() => !playing && setPlaying(true)}
            role={playing ? undefined : 'button'}
            tabIndex={playing ? undefined : 0}
            onKeyDown={(e) => {
              if (!playing && (e.key === 'Enter' || e.key === ' ')) {
                e.preventDefault()
                setPlaying(true)
              }
            }}
            className={`group relative aspect-video overflow-hidden rounded-2xl border border-white/[0.08] bg-navy-800 ${playing ? '' : 'cursor-pointer grain-overlay'}`}
          >
            {/* Accent top line */}
            <div className="absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r from-transparent via-accent/40 to-transparent z-10" />

            {playing ? (
              <iframe
                src="/demo.html"
                title="Procta product demo"
                loading="lazy"
                className="absolute inset-0 h-full w-full"
                style={{ border: 0 }}
                allow="autoplay"
              />
            ) : (
              <div className="absolute inset-0 flex flex-col items-center justify-center">
                <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-accent-dark shadow-lg transition-transform group-hover:scale-110 accent-glow-strong">
                  <Play size={28} className="ml-1 text-white" fill="white" />
                </div>
                <span className="label-mono text-slate-400" style={{ fontSize: '12px' }}>Product Demo — 1 min 38 s</span>
              </div>
            )}
          </div>
        </motion.div>

        <div className="mt-10 flex flex-col items-center gap-3 sm:flex-row sm:justify-center">
          <Link
            to="/signup"
            className="rounded-xl bg-accent-dark px-7 py-3.5 text-sm font-semibold text-white glow-btn no-underline"
          >
            Book Live Demo
          </Link>
          <a
            href="#features"
            className="rounded-xl border border-white/10 bg-white/[0.03] px-7 py-3.5 text-sm font-semibold text-slate-300 transition-all hover:border-accent/30 no-underline"
          >
            Explore Features
          </a>
        </div>
      </div>
    </section>
  )
}
