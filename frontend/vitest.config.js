import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

const nodeMajor = Number(process.versions.node.split('.')[0])

export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'jsdom',
    globals: false,
    // Node 22+ exposes an experimental global localStorage that shadows
    // jsdom's implementation unless the worker disables it explicitly.
    execArgv: nodeMajor >= 22 ? ['--no-experimental-webstorage'] : [],
  },
})
