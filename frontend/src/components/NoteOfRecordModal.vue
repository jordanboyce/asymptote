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
        <div class="flex items-center gap-2 flex-shrink-0">
          <!-- Preview redactions toggle (R9.7 — Advisor Desktop UX §9.3).
               When enabled, the modal opens in preview mode: the transcript is
               run through the redaction engine before any AI call so the
               advisor can see exactly what the provider will receive. -->
          <label
            class="flex items-center gap-1.5 text-xs cursor-pointer select-none"
            :title="previewRedactions ? 'Preview what the AI will see before sending' : 'Send transcript to AI without preview'"
          >
            <input
              type="checkbox"
              class="toggle toggle-xs toggle-primary"
              v-model="previewRedactions"
              :disabled="streaming || saving"
              @change="onPreviewToggleChange"
            />
            <span class="text-base-content/70">Preview redactions</span>
          </label>
          <button class="btn btn-ghost btn-sm btn-circle" @click="cancel" aria-label="Close drafting view">
            <X :size="16" aria-hidden="true" />
          </button>
        </div>
      </header>

      <!-- Preview redactions view (R9.7 — Advisor Desktop UX §9.3). Replaces
           the editor when previewMode is true: shows the transcript as it will
           reach the AI, requiring an explicit "Send to AI" confirm before any
           prompt leaves the box. -->
      <div v-if="previewMode" class="flex-1 flex flex-col gap-3 overflow-hidden min-h-0">
        <div class="flex items-center gap-2 px-1">
          <ShieldCheck :size="16" class="text-success" aria-hidden="true" />
          <span class="text-sm font-semibold">Redaction preview</span>
          <span v-if="previewLoading" class="loading loading-spinner loading-xs ml-1"></span>
        </div>
        <p class="text-xs text-base-content/65 px-1 leading-snug">
          This is the redacted transcript as the AI provider will receive it. Names, account numbers, and other identifiers are replaced on-device. Review then confirm to send.
        </p>

        <div v-if="previewError" class="alert alert-error text-sm">
          <AlertTriangle :size="16" aria-hidden="true" />
          <span>{{ previewError }}</span>
        </div>

        <div class="flex-1 grid grid-cols-1 md:grid-cols-[1fr_280px] gap-3 overflow-hidden min-h-0">
          <!-- Redacted transcript -->
          <section class="flex flex-col gap-2 min-h-0 min-w-0">
            <div class="text-[11px] uppercase tracking-wider text-base-content/55 px-1">
              Transcript (redacted)
            </div>
            <pre
              class="textarea textarea-bordered w-full flex-1 resize-none text-xs leading-relaxed min-h-[260px] overflow-auto whitespace-pre-wrap font-sans"
              aria-label="Redacted transcript preview"
            >{{ previewLoading ? 'Computing preview…' : (previewData?.redacted || '(no transcript content available)') }}</pre>
          </section>

          <!-- Preview entity counts -->
          <aside
            class="rounded-lg border border-base-300 bg-base-200/40 p-3 text-xs flex flex-col min-h-0 overflow-hidden"
            aria-label="Preview redaction counts"
          >
            <div class="flex items-center gap-1.5 font-semibold text-sm text-base-content/80 mb-1">
              <ShieldCheck :size="14" class="text-success" aria-hidden="true" />
              Will be redacted
            </div>
            <p class="text-[11px] text-base-content/55 leading-snug mb-2">
              No PII has been sent yet — counts reflect a local dry-run only.
            </p>
            <div v-if="previewLoading" class="text-base-content/50 italic">
              Computing…
            </div>
            <div v-else-if="!previewData?.had_pii" class="text-base-content/55 italic">
              No PII detected in the transcript.
            </div>
            <div v-else class="flex-1 overflow-y-auto space-y-1.5 pr-1">
              <div class="flex items-baseline justify-between border-b border-base-300 pb-1.5 mb-1">
                <span class="font-semibold text-base-content/75">Total</span>
                <span class="tabular-nums font-semibold">{{ previewData.entity_count }}</span>
              </div>
              <div
                v-for="(count, type) in previewEntityCounts"
                :key="type"
                class="flex items-baseline justify-between"
              >
                <span class="truncate text-base-content/70" :title="type">{{ formatEntity(type) }}</span>
                <span class="tabular-nums text-base-content/80">{{ count }}</span>
              </div>
            </div>
            <div class="text-[11px] text-base-content/45 mt-2 pt-2 border-t border-base-300/60 leading-snug">
              Counts only — original text never leaves this device.
            </div>
          </aside>
        </div>
      </div>

      <!-- Body: editor (left) + redaction summary (right) -->
      <div v-else class="flex-1 grid grid-cols-1 md:grid-cols-[1fr_280px] gap-3 overflow-hidden">

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

        <!-- Preview mode: explicit confirm before any AI call -->
        <template v-if="previewMode">
          <button
            class="btn btn-primary btn-sm gap-1.5"
            :disabled="previewLoading"
            @click="confirmSendToAI"
          >
            <span v-if="previewLoading" class="loading loading-spinner loading-xs"></span>
            Send to AI
          </button>
        </template>

        <!-- Drafting mode: existing retry / save -->
        <template v-else>
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
        </template>
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
import { apiUrl } from '../utils/apiUrl.js'

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

// Preview mode (R9.7 — Advisor Desktop UX §9.3). When the toggle is on, the
// modal opens in preview mode: we fetch the transcript and dry-run it through
// the redaction engine *before* any AI call, then wait for an explicit
// "Send to AI" confirm. Default off — preserves the streaming-by-default UX
// for the common path. Persisted in localStorage so the advisor's preference
// sticks across sessions.
const PREVIEW_TOGGLE_KEY = 'finn_note_preview_redactions'
const previewRedactions = ref(localStorage.getItem(PREVIEW_TOGGLE_KEY) === 'true')
const previewMode = ref(false)
const previewLoading = ref(false)
const previewError = ref('')
const previewData = ref(null)
const previewEntityCounts = computed(() => {
  const counts = {}
  for (const e of previewData.value?.entities || []) {
    counts[e.entity_type] = (counts[e.entity_type] || 0) + 1
  }
  return counts
})

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
  previewMode.value = false
  previewData.value = null
  previewError.value = ''
  sessionStartIso = new Date().toISOString()
  modalEl.value?.showModal()
  if (previewRedactions.value) {
    await enterPreviewMode()
  } else {
    await startStreaming()
  }
}

async function enterPreviewMode() {
  previewMode.value = true
  previewLoading.value = true
  previewError.value = ''
  previewData.value = null
  if (!props.collectionId) {
    previewError.value = 'No collection selected.'
    previewLoading.value = false
    return
  }
  try {
    // 1. Fetch the latest transcript content (no AI call).
    const tx = await axios.get(
      `/api/collections/${props.collectionId}/transcript/latest`,
    )
    const content = tx?.data?.content || ''
    if (!content.trim()) {
      previewError.value =
        'No transcript content found. Record a meeting or upload an audio file first.'
      previewLoading.value = false
      return
    }
    // 2. Dry-run the transcript through the redaction engine — same engine
    // that wraps the actual AI call, so the preview is exact.
    const dry = await axios.post('/api/redactions/dry-run', {
      text: content,
      collection_id: props.collectionId,
    })
    previewData.value = dry?.data || null
  } catch (err) {
    previewError.value = err.response?.data?.detail || err.message || 'Preview failed'
  } finally {
    previewLoading.value = false
  }
}

async function confirmSendToAI() {
  // Leave preview mode and start the actual streaming draft. The session
  // start window for the redaction summary panel is already pinned.
  previewMode.value = false
  previewData.value = null
  previewError.value = ''
  await startStreaming()
}

function onPreviewToggleChange() {
  // Persist the user's choice for next time.
  try {
    localStorage.setItem(PREVIEW_TOGGLE_KEY, previewRedactions.value ? 'true' : 'false')
  } catch {
    /* localStorage unavailable — preference just resets next time */
  }
  // Mid-session toggle handling:
  // - turning ON before any draft exists: enter preview mode now
  // - turning OFF while in preview: jump directly to streaming
  // Once a draft has started streaming, the toggle becomes informational
  // for the next open() — we don't interrupt an in-flight generation.
  if (streaming.value || saving.value) return
  if (previewRedactions.value && !previewMode.value && draft.value.length === 0) {
    enterPreviewMode()
  } else if (!previewRedactions.value && previewMode.value) {
    confirmSendToAI()
  }
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
      apiUrl(`/api/collections/${props.collectionId}/notes/stream`),
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
