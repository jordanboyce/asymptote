<template>
  <div class="flex flex-col items-center justify-center gap-3 py-16 text-center" role="status">
    <template v-if="failed">
      <AlertTriangle :size="18" class="text-warning" aria-hidden="true" />
      <p class="text-sm text-base-content/70 max-w-xs">
        This view could not be loaded. Clio was most likely updated while this
        tab was open — reloading picks up the new version.
      </p>
      <button class="btn btn-sm btn-outline gap-1.5" @click="reload">
        <RefreshCw :size="13" aria-hidden="true" />
        Reload Clio
      </button>
    </template>
    <span v-else class="loading loading-spinner loading-sm text-base-content/40"></span>
  </div>
</template>

<script setup>
import { AlertTriangle, RefreshCw } from 'lucide-vue-next'

defineProps({ failed: { type: Boolean, default: false } })
const reload = () => window.location.reload()
</script>
