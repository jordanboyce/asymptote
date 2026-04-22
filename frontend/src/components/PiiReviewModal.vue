<template>
  <dialog ref="modalEl" class="modal" aria-labelledby="pii-review-title">
    <div class="modal-box max-w-4xl max-h-[90vh] flex flex-col gap-4 overflow-hidden">

      <!-- Header -->
      <div class="flex items-start justify-between gap-4">
        <div>
          <h2 id="pii-review-title" class="text-lg font-bold flex items-center gap-2">
            <ShieldAlert :size="20" class="text-warning" />
            PII Review<span v-if="filename"> — {{ filename }}</span>
          </h2>
          <p class="text-sm text-base-content/60 mt-1">
            Review what will be redacted before this file is indexed. The AI will only see
            investment data — names, account numbers, and addresses are stripped at the source.
          </p>
        </div>
        <button class="btn btn-ghost btn-sm btn-circle" @click="cancel">
          <X :size="16" />
        </button>
      </div>

      <!-- Scrollable body -->
      <div class="overflow-y-auto flex-1 flex flex-col gap-6 pr-1">

        <!-- Loading state: preflight scan in progress -->
        <div v-if="loading" class="flex flex-col items-center justify-center gap-4 py-16">
          <span class="loading loading-spinner loading-lg text-primary"></span>
          <div class="text-center">
            <p class="text-sm font-medium">Scanning file for PII…</p>
            <p class="text-xs text-base-content/50 mt-1">
              Detecting columns, sampling values, and matching against your blacklist.
            </p>
          </div>
        </div>

        <!-- Error state -->
        <div v-else-if="error" class="alert alert-error">
          <ShieldAlert :size="18" />
          <span>{{ error }}</span>
        </div>

        <!-- Empty (shouldn't usually hit — preflight always returns sheets) -->
        <div v-else-if="sheets.length === 0" class="text-center py-16 text-sm text-base-content/50">
          No columns detected in this file.
        </div>

        <!-- Per-sheet column table -->
        <template v-else>
        <div v-for="sheet in sheets" :key="sheet.sheet_name">
          <div v-if="sheets.length > 1" class="text-xs font-semibold text-base-content/50 uppercase tracking-wider mb-2">
            Sheet: {{ sheet.sheet_name }} · {{ sheet.total_rows }} rows
          </div>

          <table class="table table-sm w-full">
            <thead>
              <tr>
                <th>Column</th>
                <th>Role detected</th>
                <th>Action</th>
                <th>Sample values <span class="font-normal text-base-content/40">(click to blacklist)</span></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="col in sheet.columns" :key="col.name"
                  :class="col.pii_flag ? 'bg-warning/5' : ''">
                <td class="font-mono text-sm">{{ col.name }}</td>
                <td class="text-xs text-base-content/60">{{ col.role || '—' }}</td>
                <td>
                  <span class="badge badge-sm"
                    :class="{
                      'badge-error':   col.action === 'drop',
                      'badge-warning': col.action === 'hash',
                      'badge-success': col.action === 'keep',
                    }">
                    {{ actionLabel(col.action) }}
                  </span>
                </td>
                <td>
                  <div class="flex flex-wrap gap-1">
                    <template v-if="col.action === 'keep'">
                      <!-- Kept columns: samples are clickable to add to blacklist -->
                      <button
                        v-for="val in col.sample_values"
                        :key="val"
                        class="badge badge-ghost badge-sm cursor-pointer hover:badge-error transition-colors"
                        :class="{ 'badge-error line-through': isBlacklisted(val) }"
                        :title="isBlacklisted(val) ? 'Already blacklisted — click to remove' : 'Click to blacklist this value'"
                        @click="toggleBlacklist(val)">
                        {{ val }}
                      </button>
                    </template>
                    <template v-else>
                      <!-- Redacted columns: show samples greyed out / strikethrough -->
                      <span
                        v-for="val in col.sample_values"
                        :key="val"
                        class="badge badge-ghost badge-sm line-through opacity-40 select-none">
                        {{ val }}
                      </span>
                    </template>
                    <span v-if="col.sample_values.length === 0" class="text-xs text-base-content/30 italic">
                      (empty)
                    </span>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- Custom blacklist manager -->
        <div class="border border-base-300 rounded-lg p-4 flex flex-col gap-3">
          <div class="flex items-center justify-between">
            <h3 class="font-semibold text-sm flex items-center gap-2">
              <Ban :size="15" class="text-error" />
              Custom blacklist for this collection
              <span class="badge badge-sm badge-neutral">{{ localBlacklist.length }}</span>
            </h3>
          </div>

          <p class="text-xs text-base-content/50">
            Terms added here are stripped from every file indexed into this collection.
            Click a sample value above, or type below to add anything the auto-detection missed.
          </p>

          <!-- Existing terms -->
          <div v-if="localBlacklist.length > 0" class="flex flex-wrap gap-2">
            <span
              v-for="term in localBlacklist"
              :key="term"
              class="badge badge-error gap-1 cursor-pointer"
              title="Click to remove"
              @click="removeTerm(term)">
              {{ term }}
              <X :size="10" />
            </span>
          </div>
          <p v-else class="text-xs text-base-content/30 italic">No custom terms yet.</p>

          <!-- Add custom term -->
          <div class="flex gap-2">
            <input
              v-model="customTermInput"
              type="text"
              placeholder="Type a name, phrase, or account number…"
              class="input input-bordered input-sm flex-1"
              @keydown.enter="addCustomTerm"
            />
            <button class="btn btn-sm btn-outline" @click="addCustomTerm" :disabled="!customTermInput.trim()">
              Add
            </button>
          </div>
        </div>

        <!-- Legend -->
        <div class="flex flex-wrap gap-4 text-xs text-base-content/50">
          <span><span class="badge badge-error badge-sm mr-1">Dropped</span> — column is excluded entirely</span>
          <span><span class="badge badge-warning badge-sm mr-1">Hashed</span> — replaced with a short ID so grouping still works</span>
          <span><span class="badge badge-success badge-sm mr-1">Kept</span> — financial data, passed through unchanged</span>
        </div>
        </template>

      </div>

      <!-- Footer -->
      <div class="modal-action border-t border-base-300 pt-4 mt-0">
        <button class="btn btn-ghost" @click="cancel">Cancel</button>
        <button
          class="btn btn-primary gap-2"
          :disabled="loading || !!error || sheets.length === 0"
          @click="confirm">
          <ShieldCheck v-if="!loading" :size="16" />
          <span v-else class="loading loading-spinner loading-xs"></span>
          {{ loading ? 'Scanning…' : 'Confirm & Index' }}
        </button>
      </div>
    </div>

    <form method="dialog" class="modal-backdrop" @submit.prevent="cancel">
      <button>close</button>
    </form>
  </dialog>
</template>

<script setup>
import { ref, watch, computed } from 'vue'
import { ShieldAlert, ShieldCheck, X, Ban } from 'lucide-vue-next'
import axios from 'axios'

const props = defineProps({
  filePath: { type: String, default: '' },
  collectionId: { type: String, default: 'default' },
})

const emit = defineEmits(['confirmed', 'cancelled'])

// ── state ────────────────────────────────────────────────────────────────
const modalEl = ref(null)
const loading = ref(false)
const error = ref('')

const filename = ref('')
const sheets = ref([])
const localBlacklist = ref([])   // working copy — saved on confirm
const pendingBlacklist = ref([]) // terms added this session (to diff against saved)
const removedTerms = ref([])     // terms removed this session
const customTermInput = ref('')

// ── open / close ─────────────────────────────────────────────────────────
// Accepts an explicit filePath so callers don't have to wait for prop reactivity
// to propagate (Vue 3 prop updates trail the parent's ref mutation by one tick).
async function open(filePath) {
  const path = filePath ?? props.filePath
  error.value = ''
  loading.value = true
  filename.value = ''
  sheets.value = []
  modalEl.value?.showModal()

  if (!path) {
    error.value = 'No file path provided for PII review.'
    loading.value = false
    return
  }

  try {
    const { data } = await axios.post('/api/pii/preflight', null, {
      params: { file_path: path, collection_id: props.collectionId }
    })
    filename.value = data.filename
    sheets.value = data.sheets
    localBlacklist.value = [...(data.collection_blacklist || [])]
    pendingBlacklist.value = []
    removedTerms.value = []
    customTermInput.value = ''
  } catch (err) {
    error.value = err.response?.data?.detail || 'Failed to scan file for PII.'
  } finally {
    loading.value = false
  }
}

function cancel() {
  modalEl.value?.close()
  emit('cancelled')
}

async function confirm() {
  // Persist blacklist changes to the collection
  try {
    if (pendingBlacklist.value.length > 0) {
      await axios.post(`/api/collections/${props.collectionId}/pii-blacklist`, {
        terms: pendingBlacklist.value,
      })
    }
    if (removedTerms.value.length > 0) {
      await axios.delete(`/api/collections/${props.collectionId}/pii-blacklist`, {
        data: { terms: removedTerms.value },
      })
    }
  } catch (err) {
    console.warn('Could not persist blacklist changes:', err)
    // Non-fatal — indexing can still proceed
  }

  modalEl.value?.close()
  emit('confirmed')
}

// ── blacklist helpers ─────────────────────────────────────────────────────
function isBlacklisted(val) {
  return localBlacklist.value.some(t => t.toLowerCase() === val.toLowerCase())
}

function toggleBlacklist(val) {
  if (isBlacklisted(val)) {
    removeTerm(val)
  } else {
    addTerm(val)
  }
}

function addTerm(val) {
  val = val.trim()
  if (!val || isBlacklisted(val)) return
  localBlacklist.value.push(val)
  pendingBlacklist.value.push(val)
  // Un-remove if it was previously removed this session
  removedTerms.value = removedTerms.value.filter(t => t.toLowerCase() !== val.toLowerCase())
}

function removeTerm(val) {
  localBlacklist.value = localBlacklist.value.filter(t => t.toLowerCase() !== val.toLowerCase())
  removedTerms.value.push(val)
  // Un-add if it was pending this session
  pendingBlacklist.value = pendingBlacklist.value.filter(t => t.toLowerCase() !== val.toLowerCase())
}

function addCustomTerm() {
  const val = customTermInput.value.trim()
  if (!val) return
  addTerm(val)
  customTermInput.value = ''
}

// ── helpers ───────────────────────────────────────────────────────────────
function actionLabel(action) {
  return { keep: 'Kept', hash: 'Hashed', drop: 'Dropped' }[action] ?? action
}

// Expose open() so the parent can call it
defineExpose({ open })
</script>
