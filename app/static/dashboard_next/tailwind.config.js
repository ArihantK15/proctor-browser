/** Tailwind config for the vanilla dashboard rebuild (dashboard_next).
 * Transcribed from the Stitch export's inline `tailwind.config` so the static
 * build reproduces the exact design. Build:
 *   npx tailwindcss -c tailwind.config.js -i tailwind.input.css -o css/tailwind.css --minify
 * The output is a static, purged stylesheet served from /static (CSP-safe;
 * replaces the CDN <script> the export used). */
module.exports = {
  darkMode: "class",
  content: ["./**/*.html", "./js/*.js"],
  // live.js builds risk-tone classes via template literals (bg-${t}/10) that the
  // content scan can't see — safelist them so the purge keeps them.
  safelist: [
    { pattern: /(bg|text|border|hover:border)-(error|tertiary|secondary|primary)(\/(10|20|30|50))?/ },
    "risk-red-glow", "risk-amber-glow", "risk-emerald-glow",
  ],
  theme: {
    extend: {
      colors: {
        "tertiary-fixed": "#ffdeaa", "secondary-fixed": "#83fc89",
        "primary-container": "#8083ff", "on-primary-container": "#0d0096",
        "on-background": "#e4e1ed", "inverse-surface": "#e4e1ed",
        "surface-container": "#1f1f27", "tertiary-container": "#bd8708",
        "on-tertiary": "#422c00", "on-primary": "#1000a9",
        "surface-container-high": "#292932", "surface-container-lowest": "#0d0d15",
        "inverse-primary": "#494bd6", "error-container": "#93000a",
        "inverse-on-surface": "#303038", "surface": "#13131b",
        "surface-tint": "#c0c1ff", "primary-fixed": "#e1e0ff",
        "error": "#ffb4ab", "on-tertiary-container": "#392600",
        "surface-dim": "#13131b", "outline-variant": "#464554",
        "on-surface": "#e4e1ed", "on-secondary": "#00390d",
        "surface-container-low": "#1b1b23", "on-primary-fixed-variant": "#2f2ebe",
        "primary-fixed-dim": "#c0c1ff", "secondary-fixed-dim": "#67df70",
        "on-secondary-fixed": "#002105", "primary": "#c0c1ff",
        "surface-bright": "#393841", "tertiary": "#fabc45",
        "secondary": "#67df70", "on-error": "#690005",
        "tertiary-fixed-dim": "#fabc45", "surface-variant": "#34343d",
        "on-surface-variant": "#c7c4d7", "on-secondary-container": "#00320a",
        "secondary-container": "#27a640", "background": "#13131b",
        "surface-container-highest": "#34343d", "on-secondary-fixed-variant": "#005317",
        "on-tertiary-fixed": "#271900", "on-primary-fixed": "#07006c",
        "on-tertiary-fixed-variant": "#5f4100", "on-error-container": "#ffdad6",
        "outline": "#908fa0"
      },
      borderRadius: { DEFAULT: "0.25rem", lg: "0.5rem", xl: "0.75rem", full: "9999px" },
      spacing: {
        xs: "8px", lg: "24px", "margin-mobile": "16px", "margin-desktop": "32px",
        xl: "32px", md: "16px", base: "4px", sm: "12px", gutter: "20px"
      },
      fontFamily: {
        "data-mono": ["JetBrains Mono"], "headline-md": ["Inter"],
        "body-base": ["Inter"], "body-sm": ["Inter"],
        "display-lg": ["Inter"], "label-caps": ["Inter"]
      },
      fontSize: {
        "data-mono": ["13px", { lineHeight: "18px", letterSpacing: "0.02em", fontWeight: "500" }],
        "headline-md": ["24px", { lineHeight: "32px", letterSpacing: "-0.01em", fontWeight: "600" }],
        "body-base": ["16px", { lineHeight: "24px", fontWeight: "400" }],
        "body-sm": ["14px", { lineHeight: "20px", fontWeight: "400" }],
        "display-lg": ["48px", { lineHeight: "56px", letterSpacing: "-0.02em", fontWeight: "700" }],
        "label-caps": ["12px", { lineHeight: "16px", letterSpacing: "0.05em", fontWeight: "700" }]
      }
    }
  },
  plugins: [require("@tailwindcss/forms"), require("@tailwindcss/container-queries")],
};
