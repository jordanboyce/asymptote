<!--
  The prep page as a modal, opened from the Meetings tab.

  Same component as the inline placement — only the chrome differs, matching
  IngestReportModal / IngestReportPanel.
-->
<template>
  <dialog ref="dialogEl" class="modal">
    <div class="modal-box max-w-4xl p-0 overflow-hidden">
      <header class="flex items-start justify-between gap-3 px-5 py-4 border-b border-base-300">
        <div class="min-w-0">
          <h2 class="text-base font-semibold tracking-tight">Ready for this meeting</h2>
          <p class="mt-1 text-xs text-base-content/55">
            Everything worth raising, ordered — and everything Finn couldn't verify.
          </p>
        </div>
        <button class="btn btn-sm btn-ghost btn-circle" @click="close" aria-label="Close">
          <X :size="16" />
        </button>
      </header>

      <div class="max-h-[72vh] overflow-y-auto">
        <MeetingPrepPanel ref="panel" variant="modal" :collection-id="collectionId" />
      </div>

      <footer class="px-5 py-3 border-t border-base-300 flex justify-end">
        <button class="btn btn-sm btn-primary" @click="close">Done</button>
      </footer>
    </div>
    <form method="dialog" class="modal-backdrop"><button>close</button></form>
  </dialog>
</template>

<script setup>
import { ref } from 'vue'
import { X } from 'lucide-vue-next'
import MeetingPrepPanel from './MeetingPrepPanel.vue'

defineProps({
  collectionId: { type: String, default: '' },
})

const dialogEl = ref(null)
const panel = ref(null)

const open = () => {
  // Rebuild on every open — holdings or action items may have changed since
  // the panel last mounted.
  panel.value?.load()
  dialogEl.value?.showModal()
}

const close = () => dialogEl.value?.close()

// Exposed so saving the client profile can rebuild the page behind the modal
// — the profile is exactly what the policy section measures against.
const load = () => panel.value?.load()

defineExpose({ open, close, load })
</script>
