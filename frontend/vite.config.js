import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'
import tailwindcss from '@tailwindcss/vite'

// API server URL - can be overridden with VITE_API_URL env var
const apiTarget = process.env.VITE_API_URL || 'http://localhost:8473'

// https://vite.dev/config/
export default defineConfig(({ command }) => ({
  plugins: [
    vue(),
    // Dev-only: the devtools overlay must never ship in production builds.
    command === 'serve' && vueDevTools(),
    tailwindcss(),
  ].filter(Boolean),
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    },
  },
  build: {
    // Default Vite output (frontend/dist). Not committed to git — the backend
    // serves it directly and Docker builds it in a dedicated stage.
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      // Proxy API requests to the backend server
      '/api': {
        target: apiTarget,
        changeOrigin: true,
      },
      '/documents': {
        target: apiTarget,
        changeOrigin: true,
      },
      '/search': {
        target: apiTarget,
        changeOrigin: true,
      },
      '/health': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
}))
