import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'
import tailwindcss from '@tailwindcss/vite'

// API server URL - can be overridden with VITE_API_URL env var.
// Default uses 127.0.0.1 (not localhost) because Node 18+ resolves "localhost"
// to ::1 first on Windows, but uvicorn's 0.0.0.0 bind only listens on IPv4 →
// proxy gets ECONNREFUSED.
const apiTarget = process.env.VITE_API_URL || 'http://127.0.0.1:8000'

// VITE_ELECTRON=true switches the build for the Electron shell:
//   - outputs to ../electron/renderer/ (bundled into the .asar by electron-builder)
//   - the value is also exposed to client code as import.meta.env.VITE_ELECTRON
//     so vue-router can pick hash history (file:// compatible)
const isElectronBuild = process.env.VITE_ELECTRON === 'true'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    vue(),
    vueDevTools(),
    tailwindcss(),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    },
  },
  base: isElectronBuild ? './' : '/',
  build: {
    outDir: isElectronBuild ? '../electron/renderer' : '../static',
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
      '/mcp': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
})
