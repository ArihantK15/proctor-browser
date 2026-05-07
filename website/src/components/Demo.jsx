import { motion, AnimatePresence } from 'framer-motion'
import { Play, Maximize2, X } from 'lucide-react'
import { Link } from 'react-router-dom'
import { useState, useRef, useEffect, useCallback } from 'react'
import { createPortal } from 'react-dom'

/**
 * Demo section.
 *
 * Three viewing modes:
 *   1. Idle card with play button (no iframe mounted, no CDN cost)
 *   2. Inline embedded play (click play → iframe mounts in card)
 *   3. Fullscreen modal (click expand → iframe takes viewport, in-page)
 *
 * Mobile (<sm): the inline card is small enough that legibility suffers,
 * so phones get a static "Watch demo fullscreen" CTA that goes straight
 * into mode 3. Desktop offers the inline embed with an expand button
 * overlay so users can pop it up on demand.
 *
 * Modal stays inside the same SPA history — no new tab, Esc closes,
 * backdrop click closes, body scroll locks while open.
 */
export default function Demo() {
  const [playing, setPlaying] = useState(false)   // inline embed mounted
  const [fullscreen, setFullscreen] = useState(false) // modal open
  const cardRef = useRef(null)

  // Prefetch the demo HTML when the card scrolls into view so the
  // first click→iframe-load is near-instant. We only do this once.
  useEffect(() => {
    if (!cardRef.current) return
    const io = new IntersectionObserver(([e]) => {
      if (e.isIntersecting) {
        const link = document.createElement('link')
        link.rel = 'prefetch'
        link.href = '/demo.html'
        link.as = 'document'
        document.head.appendChild(link)
        io.disconnect()
      }
    }, { rootMargin: '300px' })
    io.observe(cardRef.current)
    return () => io.disconnect()
  }, [])

  // Body scroll lock while modal is open + Esc key closes.
  useEffect(() => {
    if (!fullscreen) return
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const onKey = (e) => { if (e.key === 'Escape') setFullscreen(false) }
    window.addEventListener('keydown', onKey)
    return () => {
      document.body.style.overflow = prev
      window.removeEventListener('keydown', onKey)
    }
  }, [fullscreen])

  const openFullscreen = useCallback(() => setFullscreen(true), [])
  const closeFullscreen = useCallback(() => setFullscreen(false), [])

  return (
    <section id="demo" className="relative py-24 md:py-32 bg-navy-900/30 overflow-hidden">
      <div className="pointer-events-none absolute inset-0 grain-overlay" />

      {/* Heading column — narrow for readable line length */}
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

      {/* Demo card — wider container than the heading so the embed reads
          well on big monitors. Capped at 1600 px so it doesn't sprawl
          on ultrawide displays. */}
      <motion.div
        initial={{ opacity: 0, y: 32 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, margin: '-40px' }}
        transition={{ duration: 0.6, delay: 0.1 }}
        className="relative mx-auto mt-12 w-full px-4 sm:px-6 md:px-8"
        style={{ maxWidth: '1600px' }}
      >
        {/* ── Mobile-first: under sm breakpoint we render a static CTA card.
              The inline embed at ~210 px tall on a phone is illegible; the
              CTA tee's the user up for the fullscreen modal which is the
              only viable mobile experience. */}
        <div className="sm:hidden">
          <button
            type="button"
            onClick={openFullscreen}
            className="group relative block w-full aspect-video overflow-hidden rounded-xl border border-white/[0.08] bg-navy-800 grain-overlay cursor-pointer text-left p-0"
          >
            <div className="absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r from-transparent via-accent/40 to-transparent z-10 pointer-events-none" />
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-2">
              <div className="flex h-12 w-12 items-center justify-center rounded-full bg-accent-dark shadow-lg accent-glow-strong">
                <Play size={20} className="ml-0.5 text-white" fill="white" />
              </div>
              <span className="label-mono text-slate-400" style={{ fontSize: '11px' }}>
                Watch product demo · 1 min 38 s
              </span>
              <span className="text-[10px] text-slate-500 mt-1">
                Tap for fullscreen
              </span>
            </div>
          </button>
        </div>

        {/* ── Desktop / tablet: inline embed with an expand button. */}
        <div className="hidden sm:block">
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
            className={`group relative block w-full aspect-video overflow-hidden rounded-2xl border border-white/[0.08] bg-navy-800 ${playing ? '' : 'cursor-pointer grain-overlay'}`}
          >
            <div className="absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r from-transparent via-accent/40 to-transparent z-10 pointer-events-none" />

            {playing ? (
              <iframe
                src="/demo.html"
                title="Procta product demo"
                width="100%"
                height="100%"
                className="absolute inset-0 block h-full w-full"
                style={{ border: 0, display: 'block' }}
                allow="autoplay; fullscreen"
                scrolling="no"
              />
            ) : (
              <div className="absolute inset-0 flex flex-col items-center justify-center">
                <div className="flex h-16 w-16 items-center justify-center rounded-full bg-accent-dark shadow-lg transition-transform group-hover:scale-110 accent-glow-strong">
                  <Play size={28} className="ml-1 text-white" fill="white" />
                </div>
                <span className="label-mono text-slate-400 mt-4" style={{ fontSize: '12px' }}>
                  Product Demo — 1 min 38 s
                </span>
              </div>
            )}

            {/* Expand-to-fullscreen button — only useful when an embed
                exists or the user can imagine a bigger view. Stop the
                click from bubbling to the card so we don't toggle play. */}
            {playing && (
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); openFullscreen() }}
                aria-label="Open demo fullscreen"
                className="absolute top-3 right-3 z-20 flex h-9 w-9 items-center justify-center rounded-lg bg-black/60 backdrop-blur-md text-slate-200 hover:bg-accent-dark hover:text-white transition-colors"
              >
                <Maximize2 size={16} />
              </button>
            )}
          </div>
        </div>
      </motion.div>

      {/* CTAs — back to narrow column */}
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

      {/* ── Fullscreen modal — rendered into a portal so its fixed
            positioning escapes any ancestor `transform` that would
            otherwise turn `position:fixed` into `position:absolute`
            (e.g. framer-motion creates a transform context on the
            parent). */}
      {typeof document !== 'undefined' && createPortal(
        <AnimatePresence>
          {fullscreen && (
            <motion.div
              key="demo-fs"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
              onClick={closeFullscreen}
              className="fixed inset-0 z-[100] bg-black/90 backdrop-blur-sm flex items-center justify-center p-2 sm:p-6 md:p-10"
              style={{ touchAction: 'none' }}
            >
              <motion.div
                initial={{ scale: 0.96, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                exit={{ scale: 0.96, opacity: 0 }}
                transition={{ duration: 0.25, ease: 'easeOut' }}
                onClick={(e) => e.stopPropagation()}
                className="relative w-full max-h-full aspect-video bg-navy-900 rounded-xl md:rounded-2xl overflow-hidden border border-white/[0.08] shadow-2xl"
                style={{ maxWidth: 'min(100%, calc((100vh - 6rem) * 16 / 9))' }}
              >
                <iframe
                  src="/demo.html"
                  title="Procta product demo (fullscreen)"
                  width="100%"
                  height="100%"
                  className="absolute inset-0 block h-full w-full"
                  style={{ border: 0, display: 'block' }}
                  allow="autoplay; fullscreen"
                  scrolling="no"
                />

                <button
                  type="button"
                  onClick={closeFullscreen}
                  aria-label="Close demo"
                  className="absolute top-3 right-3 z-10 flex h-10 w-10 items-center justify-center rounded-lg bg-black/70 backdrop-blur-md text-slate-100 hover:bg-accent-dark hover:text-white transition-colors"
                >
                  <X size={18} />
                </button>
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>,
        document.body
      )}
    </section>
  )
}
