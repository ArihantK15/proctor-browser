/** Tailwind config for the vanilla dashboard rebuild (dashboard_next).
 * Transcribed from the Stitch export's inline `tailwind.config` so the static
 * build reproduces the exact design. Build:
 *   npx tailwindcss -c tailwind.config.js -i tailwind.input.css -o css/tailwind.css --minify
 * The output is a static, purged stylesheet served from /static (CSP-safe;
 * replaces the CDN <script> the export used). */
module.exports = {
  darkMode: "class",
  // Also scan the student dashboard (same design tokens, shares this one build so we
  // don't run a second Tailwind pipeline / node_modules just for it).
  content: ["./**/*.html", "./js/*.js", "../student_next/**/*.html", "../student_next/js/*.js"],
  // live.js builds risk-tone classes via template literals (bg-${t}/10) that the
  // content scan can't see — safelist them so the purge keeps them.
  safelist: [
    { pattern: /(bg|text|border|hover:border)-(error|tertiary|secondary|primary)(\/(10|20|30|50))?/ },
    "risk-red-glow", "risk-amber-glow", "risk-emerald-glow",
  ],
  theme: {
    extend: {
      // Procta brand palette — Stitch's Material-3 token NAMES kept (so the
      // design maps 1:1) but VALUES remapped to the canonical procta.net brand
      // (navy surfaces, periwinkle-blue #5b8af0 accent, slate text, emerald/amber
      // semantics). The Stitch purple (#c0c1ff) was off-brand; this is the fix.
      colors: {
        // surfaces — marketing navy tiers (#06080d..#243044)
        "background": "#06080d", "surface": "#06080d", "surface-dim": "#06080d",
        "surface-container-lowest": "#0a0d14", "surface-container-low": "#0c1018",
        "surface-container": "#121824", "surface-container-high": "#1a2233",
        "surface-container-highest": "#243044", "surface-bright": "#243044",
        "surface-variant": "#1a2233", "surface-tint": "#5b8af0",
        // text / outline — slate
        "on-surface": "#e2e8f0", "on-background": "#e2e8f0", "inverse-surface": "#e2e8f0",
        "on-surface-variant": "#94a3b8", "outline": "#94a3b8", "outline-variant": "#243044",
        "inverse-on-surface": "#0c1018",
        // primary — periwinkle blue accent
        "primary": "#5b8af0", "primary-fixed": "#7ba1f5", "primary-fixed-dim": "#5b8af0",
        "primary-container": "#4a78dc", "inverse-primary": "#4a78dc",
        "on-primary": "#ffffff", "on-primary-container": "#dbe7ff",
        "on-primary-fixed": "#06080d", "on-primary-fixed-variant": "#06080d",
        // secondary — emerald (success)
        "secondary": "#3fb950", "secondary-fixed": "#4ade80", "secondary-fixed-dim": "#3fb950",
        "secondary-container": "#1a7a32", "on-secondary": "#06080d",
        "on-secondary-container": "#d1fadf", "on-secondary-fixed": "#06080d",
        "on-secondary-fixed-variant": "#16602a",
        // tertiary — amber (warning)
        "tertiary": "#f59e0b", "tertiary-fixed": "#fcd34d", "tertiary-fixed-dim": "#f59e0b",
        "tertiary-container": "#92610a", "on-tertiary": "#06080d",
        "on-tertiary-container": "#ffedc2", "on-tertiary-fixed": "#06080d",
        "on-tertiary-fixed-variant": "#5f4100",
        // error — red
        "error": "#ff6b6b", "error-container": "#7a1216", "on-error": "#06080d",
        "on-error-container": "#ffdad6"
      },
      borderRadius: { DEFAULT: "0.25rem", lg: "0.5rem", xl: "0.75rem", full: "9999px" },
      spacing: {
        xs: "8px", lg: "24px", "margin-mobile": "16px", "margin-desktop": "32px",
        xl: "32px", md: "16px", base: "4px", sm: "12px", gutter: "20px"
      },
      fontFamily: {
        "data-mono": ["IBM Plex Mono", "monospace"], "headline-md": ["IBM Plex Sans", "sans-serif"],
        "body-base": ["IBM Plex Sans", "sans-serif"], "body-sm": ["IBM Plex Sans", "sans-serif"],
        "display-lg": ["IBM Plex Sans", "sans-serif"], "label-caps": ["IBM Plex Sans", "sans-serif"],
        "headline-md-mobile": ["IBM Plex Sans", "sans-serif"]
      },
      fontSize: {
        "data-mono": ["13px", { lineHeight: "18px", letterSpacing: "0.02em", fontWeight: "500" }],
        "headline-md": ["24px", { lineHeight: "32px", letterSpacing: "-0.01em", fontWeight: "600" }],
        "body-base": ["16px", { lineHeight: "24px", fontWeight: "400" }],
        "body-sm": ["14px", { lineHeight: "20px", fontWeight: "400" }],
        "display-lg": ["48px", { lineHeight: "56px", letterSpacing: "-0.02em", fontWeight: "700" }],
        "label-caps": ["12px", { lineHeight: "16px", letterSpacing: "0.05em", fontWeight: "700" }],
        "headline-md-mobile": ["20px", { lineHeight: "28px", fontWeight: "600" }]
      }
    }
  },
  plugins: [require("@tailwindcss/forms"), require("@tailwindcss/container-queries")],
};
