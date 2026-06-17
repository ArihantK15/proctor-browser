import { motion, useReducedMotion } from 'framer-motion'
import { fadeUp, inViewProps, pick } from '../lib/motion'

// Scroll-reveal wrapper: fades + rises its children into view once, with
// Emil's ease-out curve and a consistent ~100px trigger threshold.
// Collapses to an opacity-only fade under prefers-reduced-motion.
//
// Usage:
//   <Reveal>...</Reveal>
//   <Reveal as="section" delay={0.1} className="...">...</Reveal>
export default function Reveal({
  as = 'div',
  delay = 0,
  variants = fadeUp,
  className,
  children,
  ...rest
}) {
  const reduced = useReducedMotion()
  const Comp = motion[as] || motion.div
  const v = pick(reduced, variants)
  return (
    <Comp
      className={className}
      variants={v}
      {...inViewProps}
      transition={delay ? { ...(v.show.transition || {}), delay } : undefined}
      {...rest}
    >
      {children}
    </Comp>
  )
}
