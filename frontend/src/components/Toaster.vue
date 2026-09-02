<script setup>
import { useUiStore } from '../stores/uiStore'
import { X } from 'lucide-vue-next'

const ui = useUiStore()

const alertClass = (type) =>
  ({
    success: 'alert-success',
    error: 'alert-error',
    warning: 'alert-warning',
    info: 'alert-info',
  })[type] || 'alert-info'
</script>

<template>
  <!-- aria-live: screen readers announce new toasts without focus moving -->
  <div
    class="toast toast-end toast-bottom z-[100]"
    aria-live="polite"
    aria-atomic="false"
  >
    <div
      v-for="toast in ui.toasts"
      :key="toast.id"
      class="alert shadow-lg max-w-md"
      :class="alertClass(toast.type)"
      role="status"
    >
      <span class="text-sm whitespace-pre-line break-words">{{ toast.message }}</span>
      <button
        class="btn btn-ghost btn-xs btn-circle shrink-0"
        aria-label="Dismiss notification"
        @click="ui.dismiss(toast.id)"
      >
        <X class="w-3.5 h-3.5" aria-hidden="true" />
      </button>
    </div>
  </div>

  <!-- Backend-unreachable banner: cleared by the first successful request -->
  <div
    v-if="ui.offline"
    class="fixed top-0 inset-x-0 z-[101] flex justify-center pointer-events-none"
    role="alert"
  >
    <div class="alert alert-warning shadow-lg rounded-t-none rounded-b-lg max-w-md py-2 pointer-events-auto">
      <span class="text-sm">Cannot reach the server — retrying as you work. Your last change may not be saved.</span>
    </div>
  </div>
</template>
