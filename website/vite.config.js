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
