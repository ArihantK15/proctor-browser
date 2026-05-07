import { motion } from 'framer-motion'
import { Play, Maximize2 } from 'lucide-react'
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
    <section id="demo" className="relative py-24 md:py-32 bg-navy-900/30 overflow-hidden">
      <div className="pointer-events-none absolute inset-0 grain-overlay" />

      {/* Heading + paragraph stay in a narrow column for readable line length. */}
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
      </div>

      {/* Demo card escapes the max-w-7xl heading column to span up to 1600 px
          on big monitors — gives the embedded animation real estate without
          wrecking readability of the surrounding copy. Padding scales with
          breakpoint so phones get tight gutters and desktops get breathing
          room. */}
      <motion.div
        initial={{ opacity: 0, y: 32 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, margin: '-40px' }}
        transition={{ duration: 0.6, delay: 0.1 }}
        className="relative mx-auto mt-12 w-full px-4 sm:px-6 md:px-8"
        style={{ maxWidth: '1600px' }}
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
          // `block w-full aspect-video` keeps the card 16:9 across all
          // breakpoints. On a 375 px phone that's ~211 px tall; on a
          // 1600 px desktop it's ~900 px tall. Without `block` the card
          // can collapse to content size and the iframe gets a tiny
          // clientWidth, which forces the inner Stage to scale down.
          className={`group relative block w-full aspect-video overflow-hidden rounded-xl md:rounded-2xl border border-white/[0.08] bg-navy-800 ${playing ? '' : 'cursor-pointer grain-overlay'}`}
        >
          {/* Accent top line */}
          <div className="absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r from-transparent via-accent/40 to-transparent z-10 pointer-events-none" />

          {playing ? (
            <iframe
              src="/demo.html"
              title="Procta product demo"
              loading="lazy"
              // width/height attributes (not just CSS) — without these,
              // some browsers fall back to the iframe's intrinsic
              // 300×150 default for layout calculation, then the
              // inner document sees a squished viewport.
              width="100%"
              height="100%"
              className="absolute inset-0 block h-full w-full"
              style={{ border: 0, display: 'block' }}
              allow="autoplay; fullscreen"
              scrolling="no"
            />
          ) : (
            <div className="absolute inset-0 flex flex-col items-center justify-center">
              <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-accent-dark shadow-lg transition-transform group-hover:scale-110 accent-glow-strong md:h-16 md:w-16">
                <Play size={20} className="ml-0.5 text-white md:hidden" fill="white" />
                <Play size={28} className="ml-1 hidden text-white md:block" fill="white" />
              </div>
              <span className="label-mono text-slate-400" style={{ fontSize: '11px' }}>
                <span className="hidden sm:inline">Product Demo — </span>1 min 38 s
              </span>
            </div>
          )}
        </div>

        {/* Mobile escape hatch: at <640 px the card is ~191–360 px tall and
            the demo's text is hard to read. Offer a tap-to-fullscreen link
            so phone users can view it in a real-sized tab. Hidden on sm+
            where the embed is already legible. */}
        <a
          href="/demo.html"
          target="_blank"
          rel="noopener"
          className="mt-3 flex items-center justify-center gap-1.5 text-xs text-slate-400 hover:text-accent transition-colors sm:hidden"
        >
          <Maximize2 size={12} />
          Open demo full-screen
        </a>
      </motion.div>

      {/* CTAs sit inside their own narrow column again, below the wide card. */}
      <div className="mx-auto max-w-7xl px-6 relative">
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
