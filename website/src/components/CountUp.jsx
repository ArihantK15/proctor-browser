import { useEffect, useRef, useState } from 'react'
import { useInView, useReducedMotion } from 'framer-motion'

// Animated number that counts up from 0 to its target when scrolled into
// view, preserving any prefix/suffix in the source string so the final
// rendered text is byte-identical to the static value.
//   "1.5k" -> counts "0.0k" … "1.5k"
//   "~3s"  -> "~0s" … "~3s"
//   "890+" -> "0+" … "890+"
// Reduced-motion (or a value with no number) renders the value directly.
// Purposeful per Emil: it draws the eye to the proof metrics, not decoration.
function parse(v) {
  const m = String(v).match(/^(\D*)([\d,]+(?:\.\d+)?)(.*)$/)
  if (!m) return null
  const clean = m[2].replace(/,/g, '')
  if (!/\d/.test(clean)) return null
  const decimals = clean.includes('.') ? clean.split('.')[1].length : 0
  return { prefix: m[1], target: parseFloat(clean), suffix: m[3], decimals, grouped: m[2].includes(',') }
}

function fmt(n, p) {
  const fixed = n.toFixed(p.decimals)
  if (!p.grouped) return fixed
  const [intPart, dec] = fixed.split('.')
  const grouped = Number(intPart).toLocaleString('en-US')
  return dec ? `${grouped}.${dec}` : grouped
}

export default function CountUp({ value, duration = 1.2, className }) {
  const ref = useRef(null)
  const inView = useInView(ref, { once: true, margin: '0px 0px -80px 0px' })
  const reduced = useReducedMotion()
  const parsed = parse(value)
  const [display, setDisplay] = useState(() =>
    parsed ? `${parsed.prefix}${fmt(0, parsed)}${parsed.suffix}` : value
  )

  useEffect(() => {
    if (!parsed || reduced || !inView) return
    let raf = 0
    let start = 0
    const easeOut = (t) => 1 - Math.pow(1 - t, 3)
    const tick = (now) => {
      if (!start) start = now
      const prog = Math.min(1, (now - start) / (duration * 1000))
      // setState only ever runs inside requestAnimationFrame (async), never
      // synchronously in the effect body.
      setDisplay(`${parsed.prefix}${fmt(parsed.target * easeOut(prog), parsed)}${parsed.suffix}`)
      if (prog < 1) raf = requestAnimationFrame(tick)
      else setDisplay(value)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inView, reduced, value, duration])

  // ref stays attached in every branch so useInView keeps working.
  if (!parsed || reduced) return <span ref={ref} className={className}>{value}</span>
  return <span ref={ref} className={className}>{display}</span>
}
