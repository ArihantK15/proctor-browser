import useInView from '../hooks/useInView'
import { Play, Maximize2, X } from 'lucide-react'
import { Link } from 'wouter'
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
 * The demo is play-once. When it ends it postMessages 'procta-demo-ended'
 * to the parent; we unmount the iframe and return to the idle card so
 * the user can click play again to restart.
 *
 * Mobile (<sm): the inline 16:9 card is too small to read text, so phones
 * skip the inline embed and tap straight into the fullscreen modal. The
 * modal additionally requests fullscreen + landscape on mobile so the
 * demo plays edge-to-edge instead of squeezed into a portrait viewport.
 */
export default function Demo() {
  const [playing, setPlaying] = useState(false)   // inline embed mounted
  const [fullscreen, setFullscreen] = useState(false) // modal open
  const cardRef = useRef(null)
  const modalRef = useRef(null)

  // Prefetch /demo.html when the card scrolls into view → first click
  // feels instant (no CDN-fetch penalty waiting for React+Babel).
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

  // Listen for end-of-demo from the iframe. When fired we unmount both
  // the inline embed and the modal — user can click again to replay.
  useEffect(() => {
    const onMessage = (e) => {
      if (e?.data?.type === 'procta-demo-ended') {
        setPlaying(false)
        setFullscreen(false)
      }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [])

  // Body scroll lock + Esc-to-close while modal is open.
  useEffect(() => {
    if (!fullscreen) return
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const onKey = (e) => { if (e.key === 'Escape') closeFullscreen() }
    window.addEventListener('keydown', onKey)
    return () => {
      document.body.style.overflow = prev
      window.removeEventListener('keydown', onKey)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fullscreen])

  // On mobile fullscreen open: request browser fullscreen on the modal
  // element AND lock orientation to landscape. Browsers only honour
  // orientation lock while in true fullscreen — the two API calls are
  // tied. Both can fail silently (Safari iOS doesn't support either on
  // arbitrary elements), in which case the user just sees the demo at
  // portrait width and can rotate their device manually.
  useEffect(() => {
    if (!fullscreen) return
    const isCoarsePointer =
      typeof window !== 'undefined' &&
      window.matchMedia('(pointer: coarse)').matches
    if (!isCoarsePointer) return
    const el = modalRef.current
    if (!el) return
    const req = el.requestFullscreen || el.webkitRequestFullscreen
    if (!req) return
    let active = true
    Promise.resolve(req.call(el))
      .then(() => {
        if (!active) return
        if (screen.orientation && screen.orientation.lock) {
          // Some browsers reject the promise instead of throwing —
          // catch + ignore. Best-effort enhancement.
          screen.orientation.lock('landscape').catch(() => {})
        }
      })
      .catch(() => {})
    return () => {
      active = false
      if (screen.orientation && screen.orientation.unlock) {
        try { screen.orientation.unlock() } catch (e) { /* noop */ }
      }
      const exit = document.exitFullscreen || document.webkitExitFullscreen
      if (exit && (document.fullscreenElement || document.webkitFullscreenElement)) {
        Promise.resolve(exit.call(document)).catch(() => {})
      }
    }
  }, [fullscreen])

  const openFullscreen = useCallback(() => setFullscreen(true), [])
  const closeFullscreen = useCallback(() => setFullscreen(false), [])

  return (
    <section id="demo" className="relative py-24 md:py-32 bg-navy-900/30 overflow-hidden">
      <div className="pointer-events-none absolute inset-0 grain-overlay" />

      {/* Heading column — narrow for readable line length */}
      <div className="mx-auto max-w-7xl px-6 relative">
        <div
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
        </div>
      </div>

      {/* Demo card — escapes the heading column for more real estate. */}
      <div
        className="relative mx-auto mt-12 w-full px-4 sm:px-6 md:px-8"
        style={{ maxWidth: '1600px' }}
      >
        {/* Mobile: static CTA → fullscreen modal. */}
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
                Tap for landscape fullscreen
              </span>
            </div>
          </button>
        </div>

        {/* Desktop / tablet: inline embed with an expand button. */}
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
      </div>

      {/* CTAs */}
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

      {/* Fullscreen modal in a portal to escape any ancestor transform
          context that would break position:fixed. */}
      {typeof document !== 'undefined' && createPortal(
        fullscreen ? <>
            <div
              onClick={closeFullscreen}
              className="fixed inset-0 z-[100] bg-black/95 backdrop-blur-sm flex items-center justify-center p-2 sm:p-6 md:p-10"
              style={{ touchAction: 'none' }}
            >
              <div
                ref={modalRef}
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
              </div>
            </div>
          </> : null,
        document.body
      )}
    </section>
  )
}
