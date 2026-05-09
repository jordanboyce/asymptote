<template>
  <!-- Collapsed rail: thin column of section icons, always visible -->
  <div v-if="!expanded" class="flex flex-col h-full w-11 items-center py-2 gap-1.5 flex-shrink-0">
    <button
      class="btn btn-ghost btn-sm btn-square"
      @click="$emit('toggle')"
      title="Open Studio panel"
      aria-label="Open Studio panel"
    >
      <PanelRightOpen :size="16" />
    </button>
    <div class="w-6 border-t border-base-300 my-0.5" aria-hidden="true"></div>

    <button
      v-if="summary && summary.positions > 0"
      class="btn btn-ghost btn-sm btn-square text-primary"
      @click="emit('open-brief')"
      title="Generate Meeting Brief"
      aria-label="Generate Meeting Brief"
    >
      <FileText :size="14" />
    </button>
    <button
      v-if="hasTranscript"
      class="btn btn-ghost btn-sm btn-square"
      :class="showTranscriptNudge ? 'text-info' : ''"
      @click="emit('open-note-of-record')"
      title="Draft Note of Record"
      aria-label="Draft Note of Record"
    >
      <Pencil :size="14" />
    </button>
    <button
      class="btn btn-ghost btn-sm btn-square"
      :class="attachedPackIds.length > 0 ? 'text-accent' : ''"
      @click="openSection('expertise')"
      :title="`Applied Expertise${attachedPackIds.length > 0 ? ` (${attachedPackIds.length})` : ''}`"
      aria-label="Applied Expertise"
    >
      <BookOpen :size="14" />
    </button>
    <button
      class="btn btn-ghost btn-sm btn-square"
      @click="openSection('metrics')"
      title="Portfolio Metrics"
      aria-label="Portfolio Metrics"
    >
      <TrendingUp :size="14" />
    </button>
    <button
      class="btn btn-ghost btn-sm btn-square"
      @click="openSection('tables')"
      title="Structured Tables"
      aria-label="Structured Tables"
    >
      <Table2 :size="14" />
    </button>
    <button
      class="btn btn-ghost btn-sm btn-square"
      @click="openSection('scratchpad')"
      title="Scratchpad"
      aria-label="Scratchpad"
    >
      <StickyNote :size="14" />
    </button>
    <button
      class="btn btn-ghost btn-sm btn-square"
      @click="openSection('export')"
      title="Export"
      aria-label="Export"
    >
      <Download :size="14" />
    </button>
  </div>

  <!-- Expanded panel -->
  <div v-else class="flex flex-col h-full">

    <!-- Header bar -->
    <div class="flex items-center gap-2 px-3 py-2.5 border-b border-base-300 flex-shrink-0 bg-base-100" role="region" aria-label="Studio">
      <FlaskConical :size="15" class="text-base-content/60 flex-shrink-0" aria-hidden="true" />
      <span class="font-semibold text-sm flex-1">Studio</span>
      <button
        class="btn btn-ghost btn-xs btn-circle"
        @click="$emit('toggle')"
        title="Collapse Studio"
        aria-label="Collapse Studio panel"
      >
        <PanelRightClose :size="13" />
      </button>
    </div>

    <!-- Scrollable body -->
    <div class="flex-1 overflow-y-auto">

      <!-- Portfolio snapshot + primary tools (top of Studio) -->
      <div
        v-if="summary && summary.positions > 0"
        class="px-3 py-3 border-b border-base-300 bg-base-200/40"
        role="region"
        aria-label="Portfolio snapshot for this collection"
      >
        <div class="flex items-baseline gap-3">
          <div class="flex items-baseline gap-1">
            <span class="text-sm font-semibold tabular-nums">{{ summary.positions }}</span>
            <span class="text-[10px] uppercase tracking-wider text-base-content/50">
              {{ summary.positions === 1 ? 'position' : 'positions' }}
            </span>
          </div>
          <div v-if="summary.accounts > 0" class="flex items-baseline gap-1">
            <span class="text-sm font-semibold tabular-nums">{{ summary.accounts }}</span>
            <span class="text-[10px] uppercase tracking-wider text-base-content/50">
              {{ summary.accounts === 1 ? 'account' : 'accounts' }}
            </span>
          </div>
        </div>
        <div
          v-if="summary.most_recent_export_iso"
          class="text-[11px] text-base-content/55 mt-1"
          :title="`Most recent brokerage export: ${summary.most_recent_export_iso}`"
        >
          Most recent export: {{ formatExportDate(summary.most_recent_export_iso) }}
        </div>
        <button
          class="btn btn-primary btn-sm w-full mt-2 gap-1.5"
          @click="emit('open-brief')"
          title="Compute a pre-meeting brief from this collection's holdings"
        >
          <FileText :size="13" aria-hidden="true" />
          Generate Meeting Brief
        </button>
        <button
          v-if="hasTranscript"
          class="btn btn-outline btn-sm w-full mt-2 gap-1.5"
          @click="emit('open-note-of-record')"
          title="Draft a compliance Note of Record from the latest meeting transcript"
        >
          <FileText :size="13" aria-hidden="true" />
          Draft Note of Record
        </button>
      </div>

      <!-- Fallback Note of Record entry: snapshot is hidden when there are no
           positions, but a transcript-only collection should still expose the
           drafting button. -->
      <div
        v-else-if="hasTranscript"
        class="px-3 py-3 border-b border-base-300 bg-base-200/40"
        role="region"
        aria-label="Note of Record drafting"
      >
        <button
          class="btn btn-outline btn-sm w-full gap-1.5"
          @click="emit('open-note-of-record')"
          title="Draft a compliance Note of Record from the latest meeting transcript"
        >
          <FileText :size="13" aria-hidden="true" />
          Draft Note of Record
        </button>
      </div>

      <!-- Post-transcription nudge: surfaces when a fresh Meeting Notes file
           has landed and no Note of Record has been drafted for it yet. -->
      <div
        v-if="showTranscriptNudge"
        class="px-3 py-2 border-b border-base-300 bg-info/10 flex items-center gap-2"
        role="region"
        aria-label="Draft Note of Record nudge"
      >
        <FileText :size="13" class="text-info flex-shrink-0" aria-hidden="true" />
        <span class="text-xs flex-1 leading-tight">
          Transcript saved — draft a Note of Record?
        </span>
        <button
          class="btn btn-primary btn-xs"
          @click="emit('open-note-of-record')"
          title="Open the Note of Record drafting view"
        >
          Draft
        </button>
        <button
          class="btn btn-ghost btn-xs"
          @click="dismissTranscriptNudge"
          aria-label="Dismiss this nudge"
          title="Dismiss for this transcript"
        >
          Dismiss
        </button>
      </div>

      <!-- Applied Expertise -->
      <section class="border-b border-base-300">
        <button
          class="w-full flex items-center gap-2 px-3 py-2.5 hover:bg-base-200 transition-colors text-left"
          @click="open.expertise = !open.expertise"
          :aria-expanded="open.expertise"
        >
          <BookOpen :size="13" class="text-accent flex-shrink-0" aria-hidden="true" />
          <span class="text-xs font-semibold flex-1">Applied Expertise</span>
          <span v-if="attachedPackIds.length > 0" class="badge badge-xs badge-accent">{{ attachedPackIds.length }}</span>
          <ChevronDown :size="12" class="text-base-content/40 transition-transform" :class="open.expertise ? 'rotate-180' : ''" />
        </button>
        <div v-show="open.expertise" class="px-3 pb-3 space-y-2">
          <div v-if="attachedPackIds.length === 0" class="text-xs text-base-content/50 py-1">
            No expertise packs applied. Add one below to shape AI analysis.
          </div>
          <div v-else class="space-y-1">
            <div
              v-for="pack in attachedPacks"
              :key="pack.id"
              class="flex items-center gap-1.5 bg-accent/10 border border-accent/20 rounded px-2 py-1.5"
            >
              <FileText :size="11" class="text-accent shrink-0" aria-hidden="true" />
              <span class="text-xs flex-1 truncate" :title="pack.name">{{ pack.name }}</span>
              <button
                class="btn btn-ghost btn-xs btn-circle text-error"
                :title="`Remove ${pack.name}`"
                :disabled="expertiseLoading"
                @click.stop="removeExpertisePack(pack.id)"
              >
                <X :size="11" />
              </button>
            </div>
          </div>

          <div v-if="availablePacks.length > 0" class="dropdown w-full">
            <label
              tabindex="0"
              class="btn btn-outline btn-xs w-full gap-1"
              :class="{ 'btn-disabled': expertiseLoading }"
            >
              <Plus :size="11" />
              Add expertise…
              <ChevronDown :size="11" class="ml-auto" />
            </label>
            <ul tabindex="0" class="dropdown-content z-[50] menu p-1 shadow-lg bg-base-100 border border-base-300 rounded-box w-full max-h-48 overflow-y-auto flex-nowrap">
              <li v-for="pack in availablePacks" :key="pack.id">
                <a class="text-xs py-1.5" @click.prevent="addExpertisePack(pack.id)">
                  <FileText :size="11" class="shrink-0" />
                  <span class="truncate">{{ pack.name }}</span>
                </a>
              </li>
            </ul>
          </div>
          <p v-else-if="expertiseStore.packs.length === 0" class="text-xs text-base-content/40">
            No packs in the Expertise Library yet.
          </p>
          <p v-else class="text-xs text-base-content/40">
            All packs are already applied.
          </p>
        </div>
      </section>

      <!-- Portfolio Metrics -->
      <section class="border-b border-base-300">
        <button
          class="w-full flex items-center gap-2 px-3 py-2.5 hover:bg-base-200 transition-colors text-left"
          @click="open.metrics = !open.metrics"
          :aria-expanded="open.metrics"
        >
          <TrendingUp :size="13" class="text-primary flex-shrink-0" />
          <span class="text-xs font-semibold flex-1">Portfolio Metrics</span>
          <ChevronDown :size="12" class="text-base-content/40 transition-transform" :class="open.metrics ? 'rotate-180' : ''" />
        </button>
        <div v-show="open.metrics" class="px-3 pb-3 space-y-1">
          <p class="text-[11px] text-base-content/50 leading-snug pb-1">
            Run a canned metric on a structured table in this collection.
          </p>
          <button
            v-for="m in portfolioMetrics"
            :key="m.id"
            class="w-full text-left px-2 py-1.5 rounded hover:bg-base-200 transition-colors group"
            @click="runMetric(m)"
          >
            <div class="flex items-center gap-1.5">
              <component :is="m.icon" :size="12" class="text-base-content/50 group-hover:text-primary" />
              <span class="text-xs font-medium flex-1">{{ m.label }}</span>
              <ChevronRight :size="11" class="text-base-content/30 group-hover:text-base-content/60" />
            </div>
            <p class="text-[10px] text-base-content/45 ml-[18px]">{{ m.hint }}</p>
          </button>
        </div>
      </section>

      <!-- Structured Tables -->
      <section class="border-b border-base-300">
        <button
          class="w-full flex items-center gap-2 px-3 py-2.5 hover:bg-base-200 transition-colors text-left"
          @click="open.tables = !open.tables"
          :aria-expanded="open.tables"
        >
          <Table2 :size="13" class="text-primary flex-shrink-0" />
          <span class="text-xs font-semibold flex-1">Structured Tables</span>
          <ChevronDown :size="12" class="text-base-content/40 transition-transform" :class="open.tables ? 'rotate-180' : ''" />
        </button>
        <div v-show="open.tables" class="px-3 pb-3 space-y-1.5">
          <p class="text-[11px] text-base-content/50 leading-snug">
            Query a CSV/Excel table loaded into this collection in plain English.
          </p>
          <div class="flex gap-1.5">
            <button class="btn btn-outline btn-xs flex-1 gap-1" @click="sendToChat('List all structured tables in this collection and describe their schemas.')">
              <List :size="11" />
              List tables
            </button>
            <button class="btn btn-outline btn-xs flex-1 gap-1" @click="sendToChat('Show me a 10-row sample from each structured table in this collection.')">
              <Eye :size="11" />
              Preview
            </button>
          </div>
        </div>
      </section>

      <!-- Scratchpad (renamed from "Notes" so it doesn't collide with Note of Record) -->
      <section class="border-b border-base-300">
        <button
          class="w-full flex items-center gap-2 px-3 py-2.5 hover:bg-base-200 transition-colors text-left"
          @click="open.scratchpad = !open.scratchpad"
          :aria-expanded="open.scratchpad"
        >
          <StickyNote :size="13" class="text-primary flex-shrink-0" />
          <span class="text-xs font-semibold flex-1">Scratchpad</span>
          <span v-if="notes.trim()" class="badge badge-xs badge-neutral">{{ noteCount }}</span>
          <ChevronDown :size="12" class="text-base-content/40 transition-transform" :class="open.scratchpad ? 'rotate-180' : ''" />
        </button>
        <div v-show="open.scratchpad" class="px-3 pb-3 space-y-1.5">
          <p class="text-[11px] text-base-content/50 leading-snug">
            Personal scratch pad scoped to this collection. Saved locally — separate from the Note of Record above.
          </p>
          <textarea
            v-model="notes"
            class="textarea textarea-bordered textarea-xs w-full text-xs leading-snug min-h-[120px]"
            placeholder="Jot down findings, hypotheses, follow-ups…"
          ></textarea>
        </div>
      </section>

      <!-- Export -->
      <section>
        <button
          class="w-full flex items-center gap-2 px-3 py-2.5 hover:bg-base-200 transition-colors text-left"
          @click="open.export = !open.export"
          :aria-expanded="open.export"
        >
          <Download :size="13" class="text-primary flex-shrink-0" />
          <span class="text-xs font-semibold flex-1">Export</span>
          <ChevronDown :size="12" class="text-base-content/40 transition-transform" :class="open.export ? 'rotate-180' : ''" />
        </button>
        <div v-show="open.export" class="px-3 pb-3 space-y-1.5">
          <button class="btn btn-outline btn-xs w-full gap-1" @click="exportNotes">
            <FileText :size="11" />
            Scratchpad (.md)
          </button>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import axios from 'axios'
import {
  X, ChevronDown, ChevronRight, TrendingUp, Table2, StickyNote,
  Download, FlaskConical, FileText, List, Eye, BookOpen, Plus, Pencil,
  BarChart3, PieChart, Layers, Target, Activity, DollarSign,
  PanelRightOpen, PanelRightClose
} from 'lucide-vue-next'
import { useCollectionStore } from '../stores/collectionStore'
import { useExpertiseStore } from '../stores/expertiseStore'

const props = defineProps({
  expanded: { type: Boolean, default: true },
})
const emit = defineEmits(['toggle', 'send-to-chat', 'open-brief', 'open-note-of-record'])

// Rail icon → expand the panel and pop open the matching section.
function openSection(section) {
  if (section in open) open[section] = true
  emit('toggle')
}
const collectionStore = useCollectionStore()
const expertiseStore = useExpertiseStore()

const open = reactive({
  expertise: false,
  metrics: false,
  tables: false,
  scratchpad: false,
  export: false,
})

// ── Portfolio snapshot ────────────────────────────────────────────────────
const summary = ref(null)
const loadSummary = async () => {
  summary.value = null
  const collectionId = collectionStore.currentCollectionId
  if (!collectionId) return
  try {
    const response = await axios.get(`/api/collections/${collectionId}/summary`)
    summary.value = response.data
  } catch (err) {
    // Snapshot is non-essential — log and stay quiet.
    console.warn('Failed to load collection summary:', err)
  }
}

const formatExportDate = (iso) => {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

// ── Documents (only for transcript / Note of Record detection) ────────────
// SourcesSidebar owns the canonical document list; Studio fetches its own
// slim copy so the Brief/NoR/nudge buttons don't depend on the left panel
// being mounted.
const documents = ref([])
const loadDocuments = async () => {
  const collectionId = collectionStore.currentCollectionId
  if (!collectionId) {
    documents.value = []
    return
  }
  try {
    const response = await axios.get(`/documents?collection_id=${collectionId}`)
    documents.value = response.data.documents || []
  } catch (err) {
    console.warn('Failed to load documents for Studio:', err)
  }
}

// Filenames embed an ISO-ish timestamp ("Meeting Notes - YYYY-MM-DD HH-MM.md"
// / "Note of Record - YYYY-MM-DD HH-MM[ - title].md"), so a lexicographic
// reverse sort puts the most recent first.
const meetingNotesDocs = computed(() =>
  documents.value
    .filter((d) => (d.filename || '').startsWith('Meeting Notes'))
    .slice()
    .sort((a, b) => (b.filename || '').localeCompare(a.filename || ''))
)
const noteOfRecordDocs = computed(() =>
  documents.value
    .filter((d) => (d.filename || '').startsWith('Note of Record'))
    .slice()
    .sort((a, b) => (b.filename || '').localeCompare(a.filename || ''))
)
const latestTranscript = computed(() => meetingNotesDocs.value[0] || null)
const latestNote = computed(() => noteOfRecordDocs.value[0] || null)
const hasTranscript = computed(() => meetingNotesDocs.value.length > 0)

// ── Transcript nudge (per-transcript, per-collection dismissal) ───────────
const NUDGE_STORAGE_KEY = 'finn_transcript_nudge_dismissed'

function loadNudgeDismissals() {
  try {
    const raw = localStorage.getItem(NUDGE_STORAGE_KEY)
    return raw ? JSON.parse(raw) : {}
  } catch {
    return {}
  }
}
const nudgeDismissals = ref(loadNudgeDismissals())

function persistNudgeDismissals(value) {
  try {
    localStorage.setItem(NUDGE_STORAGE_KEY, JSON.stringify(value))
  } catch {
    /* localStorage full or disabled — non-fatal */
  }
}

function transcriptStamp(name) {
  const m = (name || '').match(/^Meeting Notes - (\d{4}-\d{2}-\d{2} \d{2}-\d{2})/)
  return m ? m[1] : ''
}
function noteStamp(name) {
  const m = (name || '').match(/^Note of Record - (\d{4}-\d{2}-\d{2} \d{2}-\d{2})/)
  return m ? m[1] : ''
}

const showTranscriptNudge = computed(() => {
  const transcript = latestTranscript.value
  if (!transcript) return false
  const tStamp = transcriptStamp(transcript.filename)
  const note = latestNote.value
  if (note && tStamp) {
    const nStamp = noteStamp(note.filename)
    if (nStamp && nStamp >= tStamp) return false
  }
  const colId = collectionStore.currentCollectionId
  if (!colId) return false
  const dismissed = nudgeDismissals.value[colId] || []
  if (dismissed.includes(transcript.document_id)) return false
  return true
})

function dismissTranscriptNudge() {
  const colId = collectionStore.currentCollectionId
  const transcript = latestTranscript.value
  if (!colId || !transcript) return
  const next = { ...nudgeDismissals.value }
  const list = next[colId] ? [...next[colId]] : []
  if (!list.includes(transcript.document_id)) list.push(transcript.document_id)
  next[colId] = list
  nudgeDismissals.value = next
  persistNudgeDismissals(next)
}

// ── Applied Expertise ─────────────────────────────────────────────────────
const expertiseLoading = ref(false)

const attachedPackIds = computed(() =>
  expertiseStore.attachedPackIds[collectionStore.currentCollectionId] || []
)
const attachedPacks = computed(() =>
  expertiseStore.getAttachedPackObjects(collectionStore.currentCollectionId)
)
const availablePacks = computed(() =>
  expertiseStore.packs.filter(p => !attachedPackIds.value.includes(p.id))
)

async function loadExpertise(collId) {
  const id = collId || collectionStore.currentCollectionId
  try {
    await Promise.all([
      expertiseStore.fetchPacks(),
      expertiseStore.fetchAttached(id),
    ])
  } catch (e) {
    /* non-fatal */
  }
}

async function addExpertisePack(packId) {
  expertiseLoading.value = true
  try {
    const newIds = [...attachedPackIds.value, packId]
    await expertiseStore.setAttached(collectionStore.currentCollectionId, newIds)
  } finally {
    expertiseLoading.value = false
  }
}

async function removeExpertisePack(packId) {
  expertiseLoading.value = true
  try {
    const newIds = attachedPackIds.value.filter(id => id !== packId)
    await expertiseStore.setAttached(collectionStore.currentCollectionId, newIds)
  } finally {
    expertiseLoading.value = false
  }
}

// ── Portfolio metric quick actions ────────────────────────────────────────
const portfolioMetrics = [
  { id: 'total_market_value', label: 'Total market value', icon: DollarSign, hint: 'Sum of current holdings' },
  { id: 'total_pnl',          label: 'Total P&L',          icon: TrendingUp, hint: 'Unrealized gains and losses' },
  { id: 'top_holdings',       label: 'Top holdings',       icon: BarChart3, hint: 'Largest positions by value' },
  { id: 'concentration',      label: 'Concentration',      icon: Target,    hint: 'Share held by top N positions' },
  { id: 'breakdown_by_sector',     label: 'By sector',     icon: PieChart,  hint: 'Allocation across sectors' },
  { id: 'breakdown_by_asset_class',label: 'By asset class',icon: Layers,    hint: 'Allocation across asset classes' },
  { id: 'weighted_return',    label: 'Weighted return',    icon: Activity,  hint: 'Value-weighted portfolio return' },
]

const sendToChat = (prompt) => {
  emit('send-to-chat', prompt)
}

const runMetric = (m) => {
  sendToChat(
    `Run the canned portfolio metric "${m.id}" on the structured table in this collection and explain the result in plain English.`
  )
}

// ── Scratchpad (per-collection localStorage) ──────────────────────────────
// Key kept as `analysis_notes:` so existing entries survive the rename.
const notesKey = computed(() => `analysis_notes:${collectionStore.currentCollectionId || 'default'}`)
const notes = ref(localStorage.getItem(notesKey.value) || '')
const noteCount = computed(() => notes.value.trim().split(/\s+/).filter(Boolean).length)

watch(notesKey, (key) => {
  notes.value = localStorage.getItem(key) || ''
})
watch(notes, (v) => {
  localStorage.setItem(notesKey.value, v)
})

const exportNotes = () => {
  const name = collectionStore.currentCollection?.name || 'collection'
  const blob = new Blob([notes.value || ''], { type: 'text/markdown' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${name.replace(/[^a-z0-9-_]+/gi, '_')}-scratchpad.md`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

// ── Lifecycle ─────────────────────────────────────────────────────────────
function refreshAll() {
  loadDocuments()
  loadSummary()
  loadExpertise()
}

watch(() => collectionStore.currentCollectionId, () => {
  refreshAll()
})

const handleTranscriptSaved = () => {
  loadDocuments()
}

onMounted(() => {
  refreshAll()
  window.addEventListener('finn:transcript-saved', handleTranscriptSaved)
})

onBeforeUnmount(() => {
  window.removeEventListener('finn:transcript-saved', handleTranscriptSaved)
})

// Exposed so App.vue can refresh after Note-of-Record save lands a new file.
defineExpose({ loadDocuments, loadSummary })
</script>
