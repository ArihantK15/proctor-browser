import { useEffect, useRef, useState } from 'react'

export default function useInView({ once = true, margin = '0px', threshold = 0 } = {}) {
  const ref = useRef(null)
  const [inView, setInView] = useState(false)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    if (typeof IntersectionObserver === 'undefined') {
      // Defer fallback state so the effect body does not synchronously cascade a render.
      queueMicrotask(() => setInView(true))
      return
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setInView(true)
          if (once) observer.unobserve(el)
        }
      },
      { rootMargin: margin, threshold },
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [once, margin, threshold])

  return [ref, inView]
}
