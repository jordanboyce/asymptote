<template>
  <div class="space-y-6">

    <!-- Header -->
    <div class="flex items-center justify-between flex-wrap gap-2">
      <div>
        <h2 class="text-xl font-bold">Expertise Library</h2>
        <p class="text-sm text-base-content/60 mt-0.5">
          Create reusable advisor guidance packs. Attach them to client collections to shape every AI analysis.
        </p>
      </div>
      <div class="flex gap-2">
        <button class="btn btn-outline" @click="openGenerator" :disabled="editing">
          <Sparkles :size="18" />
          Generate with AI
        </button>
        <button class="btn btn-primary" @click="startCreate">
          <Plus :size="18" />
          New Pack
        </button>
      </div>
    </div>

    <!-- AI generator modal -->
    <dialog ref="generatorModal" class="modal">
      <div class="modal-box max-w-2xl">
        <div class="flex items-center justify-between mb-2">
          <h3 class="font-bold text-lg flex items-center gap-2">
            <Sparkles :size="18" class="text-primary" />
            Generate expertise pack
          </h3>
          <button class="btn btn-ghost btn-sm" @click="closeGenerator" :disabled="generating">
            <X :size="16" />
          </button>
        </div>
        <p class="text-sm text-base-content/60 mb-4">
          Describe the topic. The AI drafts a pack using prompt-engineering patterns — imperative directives, concrete thresholds, labeled sections — that steer the downstream chat assistant well. You'll review and edit before saving.
        </p>

        <!-- Topic + context form (shown before generation) -->
        <div v-if="!draftBody && !generating" class="space-y-3">
          <div class="form-control">
            <label class="label pb-1">
              <span class="label-text font-medium">Topic <span class="text-error">*</span></span>
            </label>
            <input
              v-model="genTopic"
              type="text"
              placeholder="e.g. Municipal bond ladders for retirees in high tax brackets"
              class="input input-bordered w-full"
              :class="{ 'input-error': genError && !genTopic.trim() }"
              @keyup.enter="runGenerate"
            />
          </div>
          <div class="form-control">
            <label class="label pb-1">
              <span class="label-text font-medium">Extra context</span>
              <span class="label-text-alt text-base-content/50">optional — constraints, frameworks you favor, client situation</span>
            </label>
            <textarea
              v-model="genContext"
              class="textarea textarea-bordered w-full resize-y"
              rows="3"
              placeholder="e.g. Pre-retirees with $2–10M, AMT-sensitive, prefer in-state munis. Use the bucket strategy framework."
            />
          </div>
          <div v-if="genError" class="alert alert-error py-2 text-sm">
            {{ genError }}
          </div>
        </div>

        <!-- Live preview while streaming or after completion -->
        <div v-else class="space-y-3">
          <div class="text-xs text-base-content/60">
            <span v-if="generating" class="flex items-center gap-2">
              <span class="loading loading-spinner loading-xs" />
              Drafting pack on topic: <strong class="text-base-content">{{ genTopic }}</strong>
            </span>
            <span v-else>
              Draft complete ({{ draftBody.length.toLocaleString() }} characters). Review below, then open it in the editor to refine and save.
            </span>
          </div>
          <pre
            ref="previewEl"
            class="bg-base-200 rounded-lg p-3 text-sm font-mono whitespace-pre-wrap max-h-96 overflow-y-auto leading-relaxed"
          >{{ draftBody }}</pre>
          <div v-if="genError" class="alert alert-error py-2 text-sm">
            {{ genError }}
          </div>
        </div>

        <div class="modal-action">
          <button
            v-if="generating"
            class="btn btn-ghost btn-sm"
            @click="cancelGenerate"
          >
            Stop
          </button>
          <template v-else-if="!draftBody">
            <button class="btn btn-ghost btn-sm" @click="closeGenerator">Cancel</button>
            <button
              class="btn btn-primary btn-sm"
              :disabled="!genTopic.trim()"
              @click="runGenerate"
            >
              <Sparkles :size="14" />
              Generate
            </button>
          </template>
          <template v-else>
            <button class="btn btn-ghost btn-sm" @click="resetGenerator">
              <RotateCcw :size="14" />
              Try a different topic
            </button>
            <button class="btn btn-primary btn-sm" @click="acceptDraft">
              Open in editor
            </button>
          </template>
        </div>
      </div>
      <form method="dialog" class="modal-backdrop">
        <button>close</button>
      </form>
    </dialog>

    <!-- Editor panel (create / edit) -->
    <ExpertisePackEditor
      v-if="editing"
      :pack="editingPack"
      @save="onSave"
      @cancel="editing = false"
      @delete="onDelete"
    />

    <!-- Pack list -->
    <div v-if="!editing">
      <div v-if="store.loading" class="flex justify-center py-12">
        <span class="loading loading-spinner loading-md text-primary" />
      </div>

      <div v-else-if="store.error" class="alert alert-error">
        <span>{{ store.error }}</span>
      </div>

      <div v-else-if="store.packs.length === 0" class="card bg-base-200">
        <div class="card-body items-center text-center py-12">
          <BookOpen :size="40" class="text-base-content/30 mb-2" />
          <p class="text-base-content/60">No expertise packs yet.</p>
          <p class="text-sm text-base-content/40">Create your first pack to get started.</p>
          <button class="btn btn-primary btn-sm mt-4" @click="startCreate">
            <Plus :size="16" />
            New Pack
          </button>
        </div>
      </div>

      <div v-else class="space-y-3">
        <div
          v-for="pack in store.packs"
          :key="pack.id"
          class="card bg-base-200 hover:bg-base-300 transition-colors cursor-pointer"
          @click="startEdit(pack)"
        >
          <div class="card-body py-4 px-5">
            <div class="flex items-start justify-between gap-3">
              <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2">
                  <FileText :size="16" class="text-primary shrink-0" />
                  <h3 class="font-semibold truncate">{{ pack.name }}</h3>
                </div>
                <p v-if="pack.description" class="text-sm text-base-content/60 mt-1 line-clamp-2">
                  {{ pack.description }}
                </p>
                <p class="text-xs text-base-content/40 mt-1">
                  Updated {{ formatDate(pack.updated_at) }}
                </p>
              </div>
              <div class="flex items-center gap-2 shrink-0">
                <button
                  class="btn btn-ghost btn-xs"
                  title="Edit"
                  @click.stop="startEdit(pack)"
                >
                  <Pencil :size="14" />
                </button>
                <button
                  class="btn btn-ghost btn-xs text-error"
                  title="Delete"
                  @click.stop="confirmDelete(pack)"
                >
                  <Trash2 :size="14" />
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Delete confirmation modal -->
    <dialog ref="deleteModal" class="modal">
      <div class="modal-box">
        <h3 class="font-bold text-lg">Delete expertise pack?</h3>
        <p class="py-4 text-base-content/70">
          "<strong>{{ deletingPack?.name }}</strong>" will be removed from the library
          and detached from all collections. This cannot be undone.
        </p>
        <div class="modal-action">
          <button class="btn btn-ghost" @click="deleteModal.close()">Cancel</button>
          <button class="btn btn-error" :disabled="deleting" @click="doDelete">
            <span v-if="deleting" class="loading loading-spinner loading-xs" />
            Delete
          </button>
        </div>
      </div>
      <form method="dialog" class="modal-backdrop">
        <button>close</button>
      </form>
    </dialog>

  </div>
</template>

<script setup>
import { ref, onMounted, nextTick } from 'vue'
import { Plus, FileText, Pencil, Trash2, BookOpen, Sparkles, X, RotateCcw } from 'lucide-vue-next'
import { useExpertiseStore } from '../stores/expertiseStore'
import { buildProviderHeaders, getActiveProvider, getAPIProviderName } from '../utils/aiProviders'
import { apiUrl } from '../utils/apiUrl.js'
import ExpertisePackEditor from './ExpertisePackEditor.vue'

const store = useExpertiseStore()

const editing = ref(false)
const editingPack = ref(null)   // null = creating new
const deletingPack = ref(null)
const deleting = ref(false)
const deleteModal = ref(null)

// AI-generator state
const generatorModal = ref(null)
const previewEl = ref(null)
const genTopic = ref('')
const genContext = ref('')
const generating = ref(false)
const draftBody = ref('')
const genError = ref('')
let genAbort = null

onMounted(() => store.fetchPacks())

function startCreate() {
  editingPack.value = null
  editing.value = true
}

function openGenerator() {
  genTopic.value = ''
  genContext.value = ''
  draftBody.value = ''
  genError.value = ''
  generating.value = false
  generatorModal.value?.showModal()
}

function closeGenerator() {
  if (generating.value) cancelGenerate()
  generatorModal.value?.close()
}

function resetGenerator() {
  draftBody.value = ''
  genError.value = ''
}

function cancelGenerate() {
  if (genAbort) {
    genAbort.abort()
    genAbort = null
  }
  generating.value = false
}

async function runGenerate() {
  const topic = genTopic.value.trim()
  if (!topic) {
    genError.value = 'Topic is required.'
    return
  }
  genError.value = ''
  draftBody.value = ''
  generating.value = true
  genAbort = new AbortController()

  try {
    const providerId = getActiveProvider()
    const headers = buildProviderHeaders(providerId)
    const response = await fetch(apiUrl('/api/expertise/packs/generate/stream'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...headers },
      body: JSON.stringify({
        topic,
        context: genContext.value.trim() || null,
        provider: getAPIProviderName(providerId),
      }),
      signal: genAbort.signal,
    })

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
          draftBody.value += event.delta || ''
          nextTick(() => {
            if (previewEl.value) previewEl.value.scrollTop = previewEl.value.scrollHeight
          })
        } else if (event.type === 'done') {
          finalContent = event.content || null
        } else if (event.type === 'error') {
          throw new Error(event.message || 'Streaming failed')
        }
      }
    }

    if (finalContent != null) draftBody.value = finalContent
  } catch (err) {
    if (err.name === 'AbortError') {
      // Stop button — keep whatever was streamed so far
    } else {
      genError.value = err.message || 'Generation failed'
      draftBody.value = ''
    }
  } finally {
    generating.value = false
    genAbort = null
  }
}

function acceptDraft() {
  // Hand off to the editor as a new pack: topic → name, first non-empty line
  // of the draft → description, remaining text → body. The editor treats any
  // pack-prop without an id as "new", so this routes through createPack on save.
  const lines = draftBody.value.split('\n')
  let firstLine = ''
  let bodyStart = 0
  for (let i = 0; i < lines.length; i++) {
    const t = lines[i].trim()
    if (t) {
      firstLine = t.replace(/^#+\s*/, '')
      bodyStart = i + 1
      break
    }
  }
  if (lines[bodyStart] !== undefined && lines[bodyStart].trim() === '') bodyStart += 1
  const body = lines.slice(bodyStart).join('\n').trim() || draftBody.value.trim()

  editingPack.value = {
    name: genTopic.value.trim(),
    description: firstLine,
    body,
  }
  editing.value = true
  generatorModal.value?.close()
}

function startEdit(pack) {
  editingPack.value = pack
  editing.value = true
}

function confirmDelete(pack) {
  deletingPack.value = pack
  deleteModal.value.showModal()
}

async function doDelete() {
  if (!deletingPack.value) return
  deleting.value = true
  try {
    await store.deletePack(deletingPack.value.id)
    deleteModal.value.close()
  } finally {
    deleting.value = false
    deletingPack.value = null
  }
}

async function onSave() {
  editing.value = false
  editingPack.value = null
}

function onDelete() {
  editing.value = false
  editingPack.value = null
}

function formatDate(iso) {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
  } catch {
    return iso
  }
}
</script>
