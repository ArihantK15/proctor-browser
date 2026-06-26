---
name: Mission Control Precision
colors:
  surface: '#10141a'
  surface-dim: '#10141a'
  surface-bright: '#353940'
  surface-container-lowest: '#0a0e14'
  surface-container-low: '#181c22'
  surface-container: '#1c2026'
  surface-container-high: '#262a31'
  surface-container-highest: '#31353c'
  on-surface: '#dfe2eb'
  on-surface-variant: '#c7c4d7'
  inverse-surface: '#dfe2eb'
  inverse-on-surface: '#2d3137'
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
  background: '#10141a'
  on-background: '#dfe2eb'
  surface-variant: '#31353c'
typography:
  display:
    fontFamily: Inter
    fontSize: 28px
    fontWeight: '700'
    lineHeight: 36px
    letterSpacing: -0.02em
  h1:
    fontFamily: Inter
    fontSize: 22px
    fontWeight: '700'
    lineHeight: 28px
  h2:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '600'
    lineHeight: 24px
  h3:
    fontFamily: Inter
    fontSize: 15px
    fontWeight: '600'
    lineHeight: 20px
  body:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  small:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 18px
  caption:
    fontFamily: Inter
    fontSize: 11px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: 0.05em
  data-mono:
    fontFamily: JetBrains Mono
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 18px
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  base: 4px
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  xl: 32px
  gutter: 20px
  margin-mobile: 16px
  margin-desktop: 32px
---

## Brand & Style

The design system is engineered for the high-stakes environment of AI-driven academic integrity. It embodies a **"Mission Control"** aesthetic—quiet, authoritative, and profoundly precise. The interface avoids unnecessary flair to maintain a calm atmosphere for administrators managing sensitive data. 

The style utilizes **Corporate Minimalism** with a technical edge. It prioritizes data density without sacrificing legibility, using generous internal component padding to balance the high information volume. The visual narrative is built on dark-first logic, emphasizing depth through tonal layering rather than aggressive shadows, ensuring the focus remains on real-time proctoring metrics and anomaly detection.

## Colors

The palette is optimized for long-duration monitoring. The **Dark Mode** (default) uses a deep navy-obsidian base to reduce eye strain, with indigo serving as the primary action color. The **Light Mode** counterpart provides a high-clarity alternative for traditional office environments.

### Semantic Logic
- **Status Indicators:** Use 15% opacity background tints of the status color for "pills" or "badges" to ensure the background doesn't compete with the text.
- **Data Surfaces:** Use `inset` colors (#0B0E14 dark / #EEF1F4 light) for code blocks, terminal outputs, or secondary data containers to create clear structural hierarchy.
- **Interactive States:** Hover states move toward the primary accent in dark mode and toward a neutral gray in light mode.

## Typography

This design system uses a dual-type approach. **Inter** handles all UI labels, headings, and body copy to provide a modern, highly legible humanist touch. **JetBrains Mono** is reserved for technical data, including Student IDs, Log Timestamps, Code Snippets, and Exam Hash Strings.

### Usage Notes
- **Display & H1:** Used for dashboard titles and session headers.
- **Caption:** Used for metadata labels (e.g., "TIMESTAMP", "IP ADDRESS"). Always uppercase with increased tracking.
- **Data-Mono:** Applied to any value that requires character-level distinction (e.g., distinguishing '0' from 'O' in exam keys).

## Layout & Spacing

The system follows a strict **4px baseline grid**. Layouts are structured using a 12-column fluid grid on desktop, transitioning to a specialized "Icon Rail" navigation for tablets.

### Breakpoints & Adaptation
- **Desktop (>1200px):** Full expanded sidebar (240px). Multi-column dashboard widgets.
- **Tablet (768px - 1199px):** Sidebar collapses to a 64px icon rail. Main content area gains horizontal breathing room. Data tables may trigger horizontal scroll for secondary columns.
- **Mobile (<768px):** Global navigation moves to a bottom tab bar for primary actions (Monitor, Reports, Alerts). The "Profile" and "Settings" move to a top-right hamburger menu. All grid-based cards stack vertically.

## Elevation & Depth

In this design system, depth is communicated through **Tonal Layering** rather than traditional drop shadows. This creates a "flat-depth" look that feels more technical and precise.

1.  **Level 0 (Base):** The page background (#0D1117). Used for the foundation of the app.
2.  **Level 1 (Surface):** Cards and primary containers (#161B22). These feature a subtle #2A2F3A border to define their edges against the base.
3.  **Level 2 (Overlay):** Modals and dropdown menus. These use the same surface color but add a semi-transparent stroke and a slight 8px blur to any content beneath them to focus the user's attention.
4.  **Inset:** Used for input fields and code blocks (#0B0E14), creating a "carved out" appearance that suggests data entry or output.

## Shapes

The shape language is controlled and systematic. A "Soft" roundedness is used to prevent the interface from feeling overly aggressive or "brutalist," while maintaining the professional structure of an enterprise tool.

- **Small (6px):** Checkboxes, small buttons, and nested input elements.
- **Medium (10px):** Standard buttons, text inputs, and small cards.
- **Large (14px):** Main dashboard widgets and modal containers.
- **Pill (999px):** Status badges, Risk Pills, and toggle switches.

## Components

### Risk Pills
The signature "Risk Pill" is a composite component. It features a colored background (15% opacity) and matching text.
- **Low (0-30):** Emerald.
- **Medium (31-70):** Amber.
- **High (71-100):** Red.
On hover or click, the pill expands to show a "Reasoning Breakdown" (e.g., "Rapid Eye Movement: 40%, Audio Anomaly: 20%").

### Sidebar Navigation
The sidebar uses a dark-base background. The **Active State** is indicated by a subtle indigo tint across the entire row and a 3px solid indigo vertical bar on the extreme left edge.

### Data Tables
Tables are high-density.
- **Header:** Use `Caption` typography, sticky on scroll.
- **ID Column:** Always uses `JetBrains Mono` for precise identification.
- **Row Interaction:** On hover, the row background shifts to #1C2230.

### Input Fields
Inputs use the `Inset` background color. The border is `Subtle` by default, becoming `Primary Indigo` (1px solid) on focus with a soft indigo outer glow (no blur, just a 2px offset ring).

### Question Type Badges
Small, pill-shaped tags used in exam builders:
- **MCQ:** Indigo tint.
- **Coding:** Amber tint.
- **Numeric:** Emerald tint.