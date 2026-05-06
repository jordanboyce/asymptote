<template>
  <dialog ref="modalEl" class="modal" aria-labelledby="note-of-record-title">
    <div class="modal-box max-w-5xl w-full max-h-[92vh] flex flex-col gap-3 overflow-hidden">

      <!-- Header -->
      <header class="flex items-start justify-between gap-3">
        <div class="flex-1 min-w-0">
          <h2 id="note-of-record-title" class="text-xl font-bold flex items-center gap-2">
            <FileText :size="20" class="text-primary" aria-hidden="true" />
            <span>
              Draft Note of Record<span v-if="collectionName" class="text-base-content/60 font-normal"> — {{ collectionName }}</span>
            </span>
          </h2>
          <p class="text-xs text-base-content/55 mt-0.5">
            Streaming a compliance Note from the latest meeting transcript. Edit before saving.
          </p>
        </div>
        <button class="btn btn-ghost btn-sm btn-circle" @click="cancel" aria-label="Close drafting view">
          <X :size="16" aria-hidden="true" />
        </button>
      </header>

      <!-- Body: editor (left) + redaction summary (right) -->
      <div class="flex-1 grid grid-cols-1 md:grid-cols-[1fr_280px] gap-3 overflow-hidden">

        <!-- Draft editor -->
        <section class="flex flex-col gap-2 min-h-0 min-w-0">
          <div class="flex items-center gap-3 text-xs text-base-content/55 px-1">
            <span v-if="streaming" class="flex items-center gap-1.5">
              <span class="loading loading-spinner loading-xs"></span>
              Drafting…
            </span>
            <span v-else-if="draft.length > 0">
              {{ wordCount }} words · editable
            </span>
            <span v-else class="text-base-content/40">Draft will appear here.</span>
          </div>

          <div v-if="error" class="alert alert-error text-sm">
            <AlertTriangle :size="16" aria-hidden="true" />
            <span>{{ error }}</span>
          </div>

          <textarea
            ref="draftEl"
            v-model="draft"
            class="textarea textarea-bordered w-full flex-1 resize-none text-sm leading-relaxed min-h-[360px]"
            :placeholder="streaming ? 'Generating draft…' : 'Draft will appear here.'"
            spellcheck="true"
            aria-label="Note of Record draft (editable)"
          ></textarea>
        </section>

        <!-- Redaction summary panel (R8.3 / R9.6) -->
        <aside
          class="rounded-lg border border-base-300 bg-base-200/40 p-3 text-xs flex flex-col min-h-0 overflow-hidden"
          aria-label="PII redaction summary"
        >
          <div class="flex items-center gap-1.5 font-semibold text-sm text-base-content/80 mb-1">
            <ShieldCheck :size="14" class="text-success" aria-hidden="true" />
            Redactions in this draft
          </div>
          <p class="text-[11px] text-base-content/55 leading-snug mb-2">
            PII detected and replaced before reaching the AI provider.
          </p>

          <div v-if="redactionsLoading && totalRedactions === 0" class="text-base-content/50 italic">
            Tallying…
          </div>
          <div v-else-if="totalRedactions === 0" class="text-base-content/55 italic">
            No PII detected so far in this drafting session.
          </div>
          <div v-else class="flex-1 overflow-y-auto space-y-1.5 pr-1">
            <div class="flex items-baseline justify-between border-b border-base-300 pb-1.5 mb-1">
              <span class="font-semibold text-base-content/75">Total</span>
              <span class="tabular-nums font-semibold">{{ totalRedactions }}</span>
            </div>
            <div
              v-for="entry in entityRows"
              :key="entry.type"
              class="flex items-baseline justify-between"
            >
              <span class="truncate text-base-content/70" :title="entry.type">{{ formatEntity(entry.type) }}</span>
              <span class="tabular-nums text-base-content/80">{{ entry.count }}</span>
            </div>
          </div>

          <div class="text-[11px] text-base-content/45 mt-2 pt-2 border-t border-base-300/60 leading-snug">
            Counts only — original text never leaves this device.
          </div>
        </aside>
      </div>

      <!-- Footer actions -->
      <footer class="flex items-center justify-end gap-2 border-t border-base-300 pt-3">
        <button
          class="btn btn-ghost btn-sm"
          @click="cancel"
          :disabled="saving"
        >
          Cancel
        </button>
        <button
          v-if="!streaming && !error && draft.length === 0"
          class="btn btn-outline btn-sm"
          @click="restart"
          :disabled="streaming || saving"
        >
          Retry draft
        </button>
        <button
          class="btn btn-primary btn-sm"
          :disabled="streaming || saving || !draft.trim()"
          @click="save"
        >
          <span v-if="saving" class="loading loading-spinner loading-xs"></span>
          {{ saving ? 'Saving…' : 'Save to collection' }}
        </button>
      </footer>
    </div>

    <form method="dialog" class="modal-backdrop" @submit.prevent="cancel">
      <button>close</button>
    </form>
  </dialog>
</template>

<script setup>
import { ref, computed, onBeforeUnmount } from 'vue'
import { FileText, X, AlertTriangle, ShieldCheck } from 'lucide-vue-next'
import axios from 'axios'
import { buildProviderHeaders, getActiveProvider, getAPIProviderName } from '../utils/aiProviders.js'

const props = defineProps({
  collectionId: { type: String, default: '' },
  collectionName: { type: String, default: '' },
})
const emit = defineEmits(['saved'])

const modalEl = ref(null)
const draftEl = ref(null)
const draft = ref('')
const streaming = ref(false)
const saving = ref(false)
const error = ref('')

// Redaction summary panel state
const redactionsLoading = ref(false)
const totalRedactions = ref(0)
const byEntityType = ref({})
let sessionStartIso = ''
let pollTimer = null
let abortController = null

const wordCount = computed(() => {
  const t = draft.value.trim()
  if (!t) return 0
  return t.split(/\s+/).length
})

const entityRows = computed(() => {
  const rows = Object.entries(byEntityType.value || {})
    .map(([type, count]) => ({ type, count }))
  rows.sort((a, b) => b.count - a.count)
  return rows
})

function formatEntity(type) {
  if (!type) return '—'
  // PERSON → Person · PHONE_NUMBER → Phone Number
  return type
    .toLowerCase()
    .split('_')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ')
}

async function open() {
  draft.value = ''
  error.value = ''
  totalRedactions.value = 0
  byEntityType.value = {}
  sessionStartIso = new Date().toISOString()
  modalEl.value?.showModal()
  await startStreaming()
}

function cancel() {
  cancelStreaming()
  stopPolling()
  modalEl.value?.close()
}

async function restart() {
  if (streaming.value) return
  draft.value = ''
  error.value = ''
  await startStreaming()
}

function cancelStreaming() {
  if (abortController) {
    try { abortController.abort() } catch { /* ignore */ }
    abortController = null
  }
  streaming.value = false
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function startPolling() {
  stopPolling()
  // Poll every 2.5s while streaming so the redaction count climbs as
  // /notes/stream's done-event redactor logs new events. After streaming
  // ends we do one final fetch and stop the loop.
  pollTimer = setInterval(loadRedactionSummary, 2500)
}

async function loadRedactionSummary() {
  if (!props.collectionId) return
  redactionsLoading.value = true
  try {
    const { data } = await axios.get('/api/redactions/summary', {
      params: { collection_id: props.collectionId, since: sessionStartIso },
    })
    totalRedactions.value = data?.total_redactions || 0
    byEntityType.value = data?.by_entity_type || {}
  } catch (err) {
    // Non-fatal — the draft still works without the summary panel.
    console.warn('Failed to load redaction summary:', err)
  } finally {
    redactionsLoading.value = false
  }
}

async function startStreaming() {
  if (!props.collectionId) {
    error.value = 'No collection selected.'
    return
  }
  streaming.value = true
  error.value = ''
  startPolling()

  abortController = new AbortController()
  try {
    const headers = buildProviderHeaders(getActiveProvider())
    const response = await fetch(
      `/api/collections/${props.collectionId}/notes/stream`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...headers,
        },
        body: JSON.stringify({
          messages: [],
          provider: getAPIProviderName(getActiveProvider()),
        }),
        signal: abortController.signal,
      },
    )

    if (!response.ok) {
      const errBody = await response.json().catch(() => ({}))
      throw new Error(errBody.detail || `HTTP ${response.status}`)
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let finalContent = null

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const parts = buffer.split('\n\n')
      buffer = parts.pop()

      for (const part of parts) {
        const line = part.trim()
        if (!line.startsWith('data:')) continue
        const raw = line.slice(5).trim()
        if (!raw || raw === '[DONE]') continue
        let event
        try { event = JSON.parse(raw) } catch { continue }
        if (event.type === 'text_delta') {
          draft.value += event.delta || ''
        } else if (event.type === 'done') {
          finalContent = event.content || null
        } else if (event.type === 'error') {
          throw new Error(event.message || 'Streaming failed')
        }
      }
    }

    // Swap the streamed preview for the redacted final so the saved note
    // is the scrubbed version even if the LLM echoed something the
    // prompt-side redaction missed.
    if (finalContent != null) {
      draft.value = finalContent
    }
  } catch (err) {
    if (err.name !== 'AbortError') {
      error.value = err.message || 'Streaming failed'
    }
  } finally {
    streaming.value = false
    abortController = null
    // One last refresh to catch the done-event redactions, then stop polling.
    await loadRedactionSummary()
    stopPolling()
  }
}

async function save() {
  if (!draft.value.trim() || !props.collectionId) return
  saving.value = true
  error.value = ''
  try {
    const { data } = await axios.post(
      `/api/collections/${props.collectionId}/notes/save`,
      {
        content: draft.value.trim(),
        // Server uses this to scope the redaction-summary footer to events
        // logged during this drafting session.
        redaction_since: sessionStartIso || null,
      },
    )
    emit('saved', data)
    modalEl.value?.close()
  } catch (err) {
    error.value = err.response?.data?.detail || err.message || 'Failed to save note'
  } finally {
    saving.value = false
  }
}

onBeforeUnmount(() => {
  cancelStreaming()
  stopPolling()
})

defineExpose({ open, close: cancel })
</script>
