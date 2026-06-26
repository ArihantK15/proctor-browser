---
name: Procta Design System
colors:
  surface: '#13131b'
  surface-dim: '#13131b'
  surface-bright: '#393841'
  surface-container-lowest: '#0d0d15'
  surface-container-low: '#1b1b23'
  surface-container: '#1f1f27'
  surface-container-high: '#292932'
  surface-container-highest: '#34343d'
  on-surface: '#e4e1ed'
  on-surface-variant: '#c7c4d7'
  inverse-surface: '#e4e1ed'
  inverse-on-surface: '#303038'
  outline: '#908fa0'
  outline-variant: '#464554'
  surface-tint: '#c0c1ff'
  primary: '#c0c1ff'
  on-primary: '#1000a9'
  primary-container: '#8083ff'
  on-primary-container: '#0d0096'
  inverse-primary: '#494bd6'
  secondary: '#67df70'
  on-secondary: '#00390d'
  secondary-container: '#27a640'
  on-secondary-container: '#00320a'
  tertiary: '#fabc45'
  on-tertiary: '#422c00'
  tertiary-container: '#bd8708'
  on-tertiary-container: '#392600'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#e1e0ff'
  primary-fixed-dim: '#c0c1ff'
  on-primary-fixed: '#07006c'
  on-primary-fixed-variant: '#2f2ebe'
  secondary-fixed: '#83fc89'
  secondary-fixed-dim: '#67df70'
  on-secondary-fixed: '#002105'
  on-secondary-fixed-variant: '#005317'
  tertiary-fixed: '#ffdeaa'
  tertiary-fixed-dim: '#fabc45'
  on-tertiary-fixed: '#271900'
  on-tertiary-fixed-variant: '#5f4100'
  background: '#13131b'
  on-background: '#e4e1ed'
  surface-variant: '#34343d'
typography:
  display-lg:
    fontFamily: Inter
    fontSize: 48px
    fontWeight: '700'
    lineHeight: 56px
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.01em
  headline-md-mobile:
    fontFamily: Inter
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 28px
  body-base:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  body-sm:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  data-mono:
    fontFamily: JetBrains Mono
    fontSize: 13px
    fontWeight: '500'
    lineHeight: 18px
    letterSpacing: 0.02em
  label-caps:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '700'
    lineHeight: 16px
    letterSpacing: 0.05em
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  base: 4px
  xs: 8px
  sm: 12px
  md: 16px
  lg: 24px
  xl: 32px
  gutter: 20px
  margin-mobile: 16px
  margin-desktop: 32px
---

## Brand & Style

The design system is engineered for high-stakes digital environments, prioritizing focus, precision, and authority. It adopts a **Mission-Control Modern** aesthetic—a blend of sophisticated dark-mode UI with technical, systematic clarity. The platform must feel like an impenetrable security layer that remains unobtrusive until an anomaly is detected.

The visual language relies on high-density information layouts, subtle luminosity, and sharp functional accents. It targets educational institutions and certification bodies, evoking a sense of "watchful calm." By utilizing deep charcoal foundations and vibrant status signals, the system ensures that human proctors can scan vast amounts of data without cognitive fatigue.

## Colors

This design system utilizes a layered dark-mode palette to create depth without relying on heavy shadows. The primary indigo hue acts as the "action" color, reserved for focus states and primary buttons. 

A critical component of the color strategy is the **Status Palette**. Given the proctoring context:
- **Emerald (#3fb950)**: Indicates low-risk/authenticated sessions.
- **Amber (#d29922)**: Indicates warnings or flagged behaviors requiring review.
- **Red (#f85149)**: Indicates critical violations or identity failures.

Surface colors follow a hierarchical elevation model:
- **Level 0 (#0d1117)**: The global canvas.
- **Level 1 (#161b22)**: Main cards, sidebars, and navigation containers.
- **Level 2 (#21262d)**: Hover states and nested UI components.

## Typography

The typography strategy balances human-centric readability with technical data presentation. 

**Inter** is the primary workhorse for all UI elements, navigation, and body copy. It is chosen for its exceptional legibility in dark environments and its neutral, professional tone.

**JetBrains Mono** is utilized strictly for technical metadata. This includes:
- Student/Exam IDs
- Precision timestamps in activity feeds
- Log data and system coordinates
- IP addresses and hardware signatures

Use `label-caps` for table headers and section titles within the sidebar to provide clear structural scaffolding.

## Layout & Spacing

The design system employs a **12-column fluid grid** for the main dashboard content, with a fixed sidebar (260px). 

The spacing rhythm is built on a 4px baseline, ensuring all components align to a mathematical grid. For data-dense views (like the activity feed or student roster), use `sm` (12px) padding to maintain high information density without sacrificing touch targets.

**Breakpoints:**
- **Mobile (<768px):** Single column, 16px margins, hidden sidebar (drawer-style).
- **Tablet (768px - 1200px):** 8-column grid, collapsed sidebar (icon only).
- **Desktop (>1200px):** 12-column grid, full persistent sidebar, 32px external margins.

## Elevation & Depth

In this dark-themed ecosystem, depth is communicated through **Tonal Layering** and **Subtle Outlines** rather than heavy drop shadows.

- **Borders:** Every card and container must use a 1px solid border (`#30363d`). This defines the structure in low-light environments.
- **Glassmorphism:** Use a light backdrop-blur (12px) on the Top Bar and Modal overlays to maintain context of the underlying data.
- **Shadows:** Only use shadows for "floating" elements like dropdowns or tooltips. Use a large, soft blur with 40% opacity: `0px 10px 30px rgba(0, 0, 0, 0.4)`.
- **Inner Glow:** Active elements (like the selected exam card) should feature a subtle 1px inner stroke of the primary color to simulate luminosity.

## Shapes

The design system uses a **Rounded** (Level 2) shape language to soften the "industrial" nature of a proctoring tool.

- **Standard Components (Buttons, Inputs, Small Cards):** 8px radius (`rounded-md`).
- **Main Containers & Dashboard Cards:** 12px radius (`rounded-lg`).
- **Full Viewports/Modals:** 24px radius (`rounded-xl`).

Status indicators and badges should utilize "pill-shaped" radii for immediate visual distinction from actionable buttons.

## Components

### Buttons & Inputs
- **Primary Action:** Solid Indigo (#6366f1) with white text. No gradient.
- **Secondary Action:** Ghost style—transparent background with a 1px border (#30363d).
- **Input Fields:** Elevated charcoal (#161b22) background. On focus, the border changes to Indigo with a subtle outer glow.

### Cards & Data Grids
- **Cards:** Must include a 1px border. Titles should be `headline-md` or `body-base` bold.
- **Data Tables:** Use `data-mono` for all numeric values. Row hover states should use Surface Level 2 (#21262d).

### Activity Feeds
- **Timestamped Logs:** Each entry should have a status icon (Emerald/Amber/Red) on the left, a JetBrains Mono timestamp, and a short Inter description.

### Sidebar & Navigation
- **Active State:** The active menu item should have a vertical Indigo bar (4px wide) on the far left and a subtle background tint.
- **Icons:** Use linear, 2px stroke icons to match the systematic aesthetic.

### Video Monitor
- **Proctoring Feed:** Video containers should have the 12px radius. If a student is flagged, the border color of the video feed changes to Amber or Red dynamically.