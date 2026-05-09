<template>
  <dialog ref="modalEl" class="modal" :class="{ 'modal-open': open }" aria-labelledby="feedback-title">
    <div class="modal-box max-w-xl">
      <header class="flex items-start justify-between gap-3 mb-3">
        <div>
          <h2 id="feedback-title" class="text-lg font-semibold flex items-center gap-2">
            <Bug :size="18" class="text-primary" aria-hidden="true" />
            Report an issue
          </h2>
          <p class="text-xs text-base-content/55 mt-1">
            Send a short note to the Finn team. PII is redacted before anything leaves your machine.
          </p>
        </div>
        <button
          class="btn btn-ghost btn-sm btn-circle"
          @click="close"
          aria-label="Close feedback"
          :disabled="submitting"
        >
          <X :size="16" aria-hidden="true" />
        </button>
      </header>

      <div v-if="!cfg.enabled && cfg.checked" class="alert alert-warning text-xs mb-3">
        <span>
          Email delivery isn't configured on this install yet.
          Set <code>RESEND_API_KEY</code> in <code>.env</code> and restart the backend to enable.
        </span>
      </div>

      <form @submit.prevent="submit" class="space-y-3">
        <div class="form-control">
          <label for="feedback-description" class="label py-1">
            <span class="label-text text-sm">What happened?</span>
            <span class="label-text-alt text-base-content/45 tabular-nums">
              {{ description.length }} / 8000
            </span>
          </label>
          <textarea
            id="feedback-description"
            v-model="description"
            class="textarea textarea-bordered w-full text-sm"
            rows="6"
            maxlength="8000"
            placeholder="Steps to reproduce, what you expected, what you saw…"
            :disabled="submitting"
            ref="descriptionEl"
          ></textarea>
        </div>

        <label class="label cursor-pointer justify-start gap-3 py-1">
          <input
            type="checkbox"
            v-model="includeDiagnostics"
            class="checkbox checkbox-sm"
            :disabled="submitting"
          />
          <span class="label-text text-sm">
            Include recent logs &amp; chat events (PII-redacted)
          </span>
        </label>

        <p v-if="submitError" class="text-xs text-error">{{ submitError }}</p>
        <p v-if="submitOk" class="text-xs text-success">Thanks — your report was sent.</p>

        <div class="modal-action mt-4">
          <button
            type="button"
            class="btn btn-ghost btn-sm"
            @click="close"
            :disabled="submitting"
          >
            Cancel
          </button>
          <button
            type="submit"
            class="btn btn-primary btn-sm gap-1"
            :disabled="!canSubmit"
          >
            <Loader2 v-if="submitting" :size="14" class="animate-spin" aria-hidden="true" />
            <Send v-else :size="14" aria-hidden="true" />
            {{ submitting ? 'Sending…' : 'Send report' }}
          </button>
        </div>
      </form>
    </div>
    <form method="dialog" class="modal-backdrop">
      <button @click="close">close</button>
    </form>
  </dialog>
</template>

<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import axios from 'axios'
import { Bug, X, Send, Loader2 } from 'lucide-vue-next'

const props = defineProps({
  open: { type: Boolean, default: false },
  appRoute: { type: String, default: '' },
  collectionId: { type: String, default: '' },
})

const emit = defineEmits(['close'])

const description = ref('')
const includeDiagnostics = ref(true)
const submitting = ref(false)
const submitError = ref('')
const submitOk = ref(false)
const cfg = ref({ enabled: true, checked: false })
const descriptionEl = ref(null)

const canSubmit = computed(() =>
  !submitting.value && description.value.trim().length > 0 && cfg.value.enabled
)

async function checkConfig() {
  try {
    const { data } = await axios.get('/api/feedback/config')
    cfg.value = { enabled: !!data?.enabled, checked: true }
  } catch {
    cfg.value = { enabled: false, checked: true }
  }
}

watch(() => props.open, async (open) => {
  if (open) {
    submitError.value = ''
    submitOk.value = false
    await checkConfig()
    await nextTick()
    descriptionEl.value?.focus()
  }
})

async function submit() {
  if (!canSubmit.value) return
  submitting.value = true
  submitError.value = ''
  submitOk.value = false
  try {
    const { data } = await axios.post('/api/feedback', {
      description: description.value.trim(),
      include_diagnostics: includeDiagnostics.value,
      app_route: props.appRoute || null,
      collection_id: props.collectionId || null,
    })
    if (data?.ok) {
      submitOk.value = true
      description.value = ''
      // Auto-close after a moment so the user sees the success state.
      setTimeout(() => close(), 900)
    } else {
      submitError.value = data?.error || 'Sending failed.'
    }
  } catch (err) {
    submitError.value = err?.response?.data?.error
      || err?.response?.data?.detail
      || err?.message
      || 'Sending failed.'
  } finally {
    submitting.value = false
  }
}

function close() {
  if (submitting.value) return
  emit('close')
}
</script>
