<template>
  <div v-if="capturedError" class="h-full w-full flex items-center justify-center p-6">
    <div
      class="max-w-lg w-full bg-base-100 border border-base-300 rounded-lg shadow-sm p-6 space-y-5"
      role="alert"
      aria-labelledby="error-boundary-title"
    >
      <div class="flex items-start gap-3">
        <AlertTriangle :size="24" class="text-error flex-shrink-0 mt-0.5" aria-hidden="true" />
        <div class="flex-1 min-w-0">
          <h2 id="error-boundary-title" class="text-lg font-semibold tracking-tight">
            Something went wrong
          </h2>
          <p class="text-sm text-base-content/70 mt-1 leading-relaxed">
            Finn ran into a problem rendering this view. Your data is safe — it's still on this device.
          </p>
        </div>
      </div>

      <div v-if="isExpertMode" class="bg-base-200/60 rounded-md p-3 text-xs font-mono break-all max-h-40 overflow-y-auto">
        {{ errorSummary }}
      </div>

      <div class="flex flex-wrap gap-2">
        <button class="btn btn-primary btn-sm gap-1.5" @click="reload">
          <RefreshCw :size="14" aria-hidden="true" />
          Reload
        </button>
        <button class="btn btn-ghost btn-sm gap-1.5" @click="resetCache">
          <Trash2 :size="14" aria-hidden="true" />
          Reset cache
        </button>
        <button class="btn btn-ghost btn-sm gap-1.5" @click="copyDiagnostics">
          <Clipboard :size="14" aria-hidden="true" />
          {{ copyLabel }}
        </button>
      </div>

      <p class="text-xs text-base-content/50">
        "Reset cache" clears your local Finn settings (theme, expert mode, recent searches). It does not delete any client data, collections, or documents.
      </p>
    </div>
  </div>
  <slot v-else />
</template>

<script setup>
import { ref, computed, onErrorCaptured } from 'vue'
import { AlertTriangle, RefreshCw, Trash2, Clipboard } from 'lucide-vue-next'
import { isExpertMode } from '../utils/expertMode.js'

const capturedError = ref(null)
const copyLabel = ref('Copy diagnostics')

const errorSummary = computed(() => {
  const e = capturedError.value
  if (!e) return ''
  const parts = []
  if (e.message) parts.push(e.message)
  if (e.stack) parts.push(e.stack)
  return parts.join('\n\n') || String(e)
})

onErrorCaptured((err) => {
  capturedError.value = err
  console.error('ErrorBoundary captured:', err)
  return false
})

function reload() {
  window.location.reload()
}

function resetCache() {
  try {
    const sentinel = localStorage.getItem('finn_storage_migrated')
    localStorage.clear()
    if (sentinel) localStorage.setItem('finn_storage_migrated', sentinel)
  } catch { /* localStorage disabled — non-fatal */ }
  window.location.reload()
}

async function copyDiagnostics() {
  const payload = [
    `Finn diagnostics — ${new Date().toISOString()}`,
    `URL: ${window.location.href}`,
    `User agent: ${navigator.userAgent}`,
    '',
    'Error:',
    errorSummary.value,
  ].join('\n')
  try {
    await navigator.clipboard.writeText(payload)
    copyLabel.value = 'Copied'
    setTimeout(() => { copyLabel.value = 'Copy diagnostics' }, 1800)
  } catch {
    copyLabel.value = 'Copy failed'
    setTimeout(() => { copyLabel.value = 'Copy diagnostics' }, 1800)
  }
}
</script>
