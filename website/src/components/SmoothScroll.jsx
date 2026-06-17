import { useEffect } from 'react'
import Lenis from 'lenis'
import { setLenis } from '../lib/smoothScroll'

// Momentum smooth-scroll (Lenis) — the "buttery" feel of modern motion
// sites. Renders nothing; sets up once on the client.
//
// Guards:
//   - Disabled entirely under prefers-reduced-motion (native scrolling).
//   - Same-page #anchor links are eased via lenis.scrollTo, offset for the
//     fixed navbar. The a11y skip link (#main-content) is left to the
//     browser so focus handling is preserved.
//   - rAF loop + listeners + instance are cleaned up on unmount.
export default function SmoothScroll() {
  useEffect(() => {
    if (typeof window === 'undefined') return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return

    const lenis = new Lenis({
      duration: 1.1,
      easing: (t) => Math.min(1, 1.001 - Math.pow(2, -10 * t)),
      smoothWheel: true,
    })
    setLenis(lenis)

    let rafId = 0
    const loop = (time) => {
      lenis.raf(time)
      rafId = requestAnimationFrame(loop)
    }
    rafId = requestAnimationFrame(loop)

    const onClick = (e) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return
      const a = e.target.closest('a[href^="#"]')
      if (!a) return
      const href = a.getAttribute('href')
      if (!href || href === '#' || href === '#main-content') return
      const target = document.querySelector(href)
      if (!target) return
      e.preventDefault()
      lenis.scrollTo(target, { offset: -80 })
    }
    document.addEventListener('click', onClick)

    return () => {
      document.removeEventListener('click', onClick)
      cancelAnimationFrame(rafId)
      lenis.destroy()
      setLenis(null)
    }
  }, [])

  return null
}
