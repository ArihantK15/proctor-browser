# Design System Unification Plan

## Current State: 9 Surfaces

| Surface | Stack | CSS |
|---------|-------|-----|
| Marketing site (22 pages) | React + Tailwind v4 | `@theme` tokens |
| Teacher dashboard | Vanilla JS | `tokens.css` + `components.css` + `theme.css` + `dashboard.css` (~4k lines) |
| Student app | Vanilla JS | Inline `<style>` (~380 lines) |
| Electron renderer | Inline JS | Embedded CSS + Google Fonts CDN |
| Privacy/DPA/trust-center | Python-generated | Self-contained `<style>` blocks, flat hex |
| Register page | Inline | ~137 lines `<style>` |
| Download page | Inline | ~74 lines `<style>` |
| Phone cam page | Inline | ~22 lines `<style>` |
| Invite landing | Python-generated | Inline styles |

## Key Problems

1. **Two parallel design systems** — Tailwind `@theme` tokens vs CSS custom properties, different color scales, different spacing values
2. **Info pages are orphans** — Privacy, DPA, trust-center, register, download, phone-cam, invite landing don't reference any shared CSS
3. **~341 inline `style=""` attrs** in `dashboard-app.js` — `components.css` exists but is mostly ignored in favor of inline styles
4. **Font loading fragmentation** — IBM Plex (marketing), pre-loaded (app), Google Fonts CDN (renderer)
5. **Underused token file** — `tokens.css` has rich color scales, spacing, type, shadows, border-radius, motion, z-index but isn't consumed by marketing site or info pages

## Phase 1: Token Consolidation (1-2 days)

- Rename `tokens.css` → `_design-tokens.css` as the single source of truth
- Re-export from `theme.css` for backward compatibility with existing dashboard CSS
- Configure Tailwind `@theme` in marketing site to reference the same CSS custom properties
- Ship a `design-system.css` bundle (tokens + minimal base reset) that all info pages can import via `<link>`

**Tradeoff**: Tailwind v4 `@theme` can reference CSS custom properties but there may be edge cases (breakpoints, animation keyframes). Decision: exact pixel parity or "close enough"?

## Phase 2: Info Page Integration (1-2 days)

- Import `design-system.css` into privacy, DPA, trust-center, register, download, phone-cam, invite landing pages
- Replace flat hex colors with `var(--)` references
- Normalize font stack to IBM Plex Mono (consistent with rest of product)

## Phase 3: components.css Adoption (2-3 days)

- Refactor ~341 inline `style=""` attrs in `dashboard-app.js` to use existing `components.css` classes
- Add any missing components (the inline styles reveal real gaps in the component library)
- Remove inline styles that now have CSS class equivalents

**Tradeoff**: Blanket refactor of all 341 inline styles, or just the most visual/broken ones?

## Phase 4: Renderer Unification (1 day)

- Replace Google Fonts CDN with pre-loaded fonts (consistent with Electron app's existing font strategy)
- Replace inline hex colors with `var(--)` references
- Import `_design-tokens.css` or define a minimal subset inline

## Phase 5: Student App CSS Extraction (1 day)

- Extract inline `<style>` from `student.html` into a separate `student.css`
- Reference design tokens from `_design-tokens.css`
- Remove inline `<style>` block

## Font Strategy Decision

- Centralize on IBM Plex Mono everywhere (renderer, info pages, student app), OR
- Keep mixing based on surface?
