import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: 'autoUpdate',
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
      workbox: {
        globPatterns: ['**/*.{html,js,css,svg,ico,woff2}'],
        runtimeCaching: [
          {
            urlPattern: /^\/assets\/.*/,
            handler: 'CacheFirst',
            options: { cacheName: 'assets', expiration: { maxEntries: 100, maxAgeSeconds: 86400 * 365 } },
          },
        ],
      },
    }),
  ],
})
