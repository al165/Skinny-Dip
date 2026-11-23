import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // The app calls /api/*; forward it to the dip server (no CORS needed).
    // Override the target with DIP_API_TARGET=http://host:port npm run dev.
    proxy: {
      '/api': {
        target: process.env.DIP_API_TARGET ?? 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
