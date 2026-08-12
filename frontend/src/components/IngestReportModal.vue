<!--
  The Trust Report shown once, right after an upload finishes.

  This is the moment the question "did my file actually land properly?" is
  loudest — and the moment a prospect watching a demo decides whether the
  parse was real. Showing the answer unprompted is the whole point; the same
  report stays available on the Overview tab afterwards.
-->
<template>
  <dialog ref="dialogEl" class="modal" @close="onClose">
    <div class="modal-box max-w-3xl p-0 overflow-hidden">
      <header class="flex items-start justify-between gap-3 px-5 py-4 border-b border-base-300">
        <div>
          <h2 class="text-base font-semibold tracking-tight">Here's what Finn made of your file</h2>
          <p class="mt-1 text-xs text-base-content/55">
            Checked locally the moment it finished loading.
          </p>
        </div>
        <button class="btn btn-sm btn-ghost btn-circle" @click="close" aria-label="Close">
          <X :size="16" />
        </button>
      </header>

      <div class="max-h-[70vh] overflow-y-auto">
        <IngestReportPanel
          ref="panel"
          variant="modal"
          :collection-id="collectionId"
          @send-to-chat="onSendToChat"
        />
      </div>

      <footer class="px-5 py-3 border-t border-base-300 flex justify-end">
        <button class="btn btn-sm btn-primary" @click="close">Got it</button>
      </footer>
    </div>
    <form method="dialog" class="modal-backdrop"><button>close</button></form>
  </dialog>
</template>

<script setup>
import { ref } from 'vue'
import { X } from 'lucide-vue-next'
import IngestReportPanel from './IngestReportPanel.vue'

defineProps({
  collectionId: { type: String, default: '' },
})

const emit = defineEmits(['send-to-chat'])

const dialogEl = ref(null)
const panel = ref(null)

const open = () => {
  // Re-fetch on every open so the report reflects the upload that just landed
  // rather than whatever was cached when the panel first mounted.
  panel.value?.load()
  dialogEl.value?.showModal()
}

const close = () => dialogEl.value?.close()
const onClose = () => {}

const onSendToChat = (text) => {
  close()
  emit('send-to-chat', text)
}

defineExpose({ open, close })
</script>
