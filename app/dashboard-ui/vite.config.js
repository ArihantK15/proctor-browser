import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  base: '/static/dashboard-react/',
  build: {
    outDir: '../static/dashboard-react',
    emptyOutDir: true,
  },
})
