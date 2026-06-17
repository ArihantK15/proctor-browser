// Module-level handle to the active Lenis instance so non-provider
// components (e.g. the Demo modal) can pause/resume momentum scrolling
// without prop-drilling. Null when reduced-motion is on or before mount.
let _lenis = null

export function setLenis(l) { _lenis = l }
export function getLenis() { return _lenis }
export function lenisStop() { if (_lenis) _lenis.stop() }
export function lenisStart() { if (_lenis) _lenis.start() }
