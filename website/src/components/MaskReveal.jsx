import { motion, useReducedMotion } from 'framer-motion'
import { maskUp, reducedFade, inViewProps } from '../lib/motion'

// Masked reveal: wraps content in an overflow-hidden box and slides it up
// from behind the mask on scroll-in — the signature "soft" reveal. Best on
// short headings/lines. Falls back to a plain opacity fade under reduced
// motion. Content/markup unchanged; this only adds the reveal motion.
//
//   <MaskReveal as="h2" className="...">Everything You Need</MaskReveal>
export default function MaskReveal({
  as = 'div',
  className,
  delay = 0,
  children,
  ...rest
}) {
  const reduced = useReducedMotion()
  const Comp = motion[as] || motion.div
  if (reduced) {
    return (
      <Comp className={className} variants={reducedFade} {...inViewProps} {...rest}>
        {children}
      </Comp>
    )
  }
  return (
    <span className="block overflow-hidden" style={{ paddingBottom: '0.04em' }}>
      <Comp
        className={className}
        variants={maskUp}
        {...inViewProps}
        transition={delay ? { ...maskUp.show.transition, delay } : undefined}
        {...rest}
      >
        {children}
      </Comp>
    </span>
  )
}
