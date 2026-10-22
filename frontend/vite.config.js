import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Keep the frontend's same-origin API client working during local development.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: 'http://127.0.0.1:8001', changeOrigin: true },
      '/v1': { target: 'http://127.0.0.1:8001', changeOrigin: true },
      '/health': { target: 'http://127.0.0.1:8001', changeOrigin: true },
    },
  },
})
