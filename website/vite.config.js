import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  // Explicit Safari-safe target. Without this, Vite's default target can emit
  // syntax that's fine in Chrome but brittle on current/older Safari — one of
  // the ways the marketing JS could fail to execute, leaving the navy shell.
  build: {
    target: ['safari15', 'es2020'],
  },
  plugins: [
    react(),
    tailwindcss(),
    // SERVICE WORKER REMOVAL (selfDestroying).
    //
    // The previous Workbox config precached index.html and served it for ALL
    // navigations (NavigationRoute → createHandlerBoundToURL('index.html')).
    // Its precache revision is computed during `vite build` — BEFORE the
    // `postbuild` prerender step rewrites index.html — so the SW's notion of
    // "did index.html change?" is wrong. Across deploys, Safari served a stale
    // cached shell pointing at old/now-404 JS chunks → blank navy background
    // until a hard reload bypassed the SW. A marketing site needs no offline
    // caching, so this is pure downside.
    //
    // We can't just delete the plugin: visitors who already registered the old
    // SW would keep getting stale content. `selfDestroying: true` ships a SW
    // that UNREGISTERS itself and clears its caches on next visit, healing the
    // field. The web manifest/icons (installability, favicons) are preserved.
    // Once this has rolled out to all users, the plugin can be removed entirely.
    VitePWA({
      selfDestroying: true,
      manifest: {
        name: 'Procta — Remote exams. Real results.',
        short_name: 'Procta',
        description: 'AI-proctored exam platform for Indian higher education. Remote exams. Real results.',
        start_url: '/',
        display: 'standalone',
        background_color: '#0F1629',
        theme_color: '#0F1629',
        icons: [
          { src: '/favicon-16.png',        sizes: '16x16',   type: 'image/png' },
          { src: '/favicon-32.png',        sizes: '32x32',   type: 'image/png' },
          { src: '/favicon-48.png',        sizes: '48x48',   type: 'image/png' },
          { src: '/apple-touch-icon.png',  sizes: '180x180', type: 'image/png', purpose: 'any' },
          { src: '/icon-192.png',          sizes: '192x192', type: 'image/png', purpose: 'any' },
          { src: '/icon-512.png',          sizes: '512x512', type: 'image/png', purpose: 'any' },
          { src: '/icon-192-maskable.png', sizes: '192x192', type: 'image/png', purpose: 'maskable' },
          { src: '/icon-512-maskable.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
    }),
  ],
})
