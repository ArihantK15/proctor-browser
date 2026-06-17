// Shared Framer Motion variants for the marketing site.
//
// Grounded in Emil Kowalski's animation principles:
//   - Animate only transform + opacity (cheap, GPU-friendly).
//   - Strong ease-out for entrances; never scale from 0 (min 0.95).
//   - Stagger children for orchestration.
//   - Marketing pages may run slightly longer than the 300ms UI ceiling.
//   - prefers-reduced-motion collapses movement to a gentle opacity fade
//     (handled per-component via Framer's useReducedMotion hook).

// Emil's strong ease-out — matches --ease-out-quint in index.css.
export const EASE_OUT = [0.23, 1, 0.32, 1]

// Fade up — the default section / element entrance.
export const fadeUp = {
  hidden: { opacity: 0, y: 18 },
  show: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.6, ease: EASE_OUT },
  },
}

// Masked line reveal (Zajno-style): the child rises from behind a clip,
// giving motion a "soft, multi-layered" feel. Pair with an overflow-hidden
// parent (the MaskReveal component does this for you).
export const maskUp = {
  hidden: { y: '115%' },
  show: {
    y: 0,
    transition: { duration: 0.7, ease: EASE_OUT },
  },
}

// Subtle scale-in for cards / mockups (never from 0).
export const scaleIn = {
  hidden: { opacity: 0, scale: 0.97, y: 12 },
  show: {
    opacity: 1,
    scale: 1,
    y: 0,
    transition: { duration: 0.6, ease: EASE_OUT },
  },
}

// Parent container that staggers its children's entrances.
export const stagger = (staggerChildren = 0.07, delayChildren = 0) => ({
  hidden: {},
  show: {
    transition: { staggerChildren, delayChildren },
  },
})

// Reduced-motion fallback: opacity only, no movement, near-instant.
export const reducedFade = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { duration: 0.2, ease: 'linear' } },
}

// Pick the right variant set given the user's reduced-motion preference.
export const pick = (reduced, variant) => (reduced ? reducedFade : variant)

// Standard whileInView config so every scroll-reveal fires at the same
// threshold (Emil: trigger ~100px into the viewport, once).
export const inViewProps = {
  initial: 'hidden',
  whileInView: 'show',
  viewport: { once: true, margin: '0px 0px -100px 0px' },
}
