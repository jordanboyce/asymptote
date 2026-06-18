<template>
  <div class="flex flex-col h-full">

    <!-- Header bar -->
    <div class="flex items-center gap-2 px-3 py-2.5 border-b border-base-300 flex-shrink-0 bg-base-100" role="region" aria-label="Sources">
      <Library :size="15" class="text-base-content/60 flex-shrink-0" aria-hidden="true" />
      <span class="font-semibold text-sm flex-1">Sources</span>
      <span v-if="documents.length > 0" class="badge badge-xs badge-neutral" :aria-label="`${documents.length} source${documents.length === 1 ? '' : 's'}`">{{ documents.length }}</span>
      <button
        class="btn btn-ghost btn-xs btn-circle"
        @click="loadDocuments"
        title="Refresh sources"
        aria-label="Refresh sources list"
      >
        <RefreshCw :size="13" :class="loading ? 'animate-spin' : ''" />
      </button>
      <button
        class="btn btn-ghost btn-xs btn-circle"
        @click="$emit('close')"
        title="Close sidebar"
        aria-label="Close sources sidebar"
      >
        <X :size="13" />
      </button>
    </div>

    <!-- Scrollable body -->
    <div class="flex-1 overflow-y-auto">

      <!-- Add Sources section (collapsible) -->
      <div class="border-b border-base-300">
        <button class="w-full flex items-center gap-2 px-3 py-2.5 hover:bg-base-200 transition-colors text-left" @click="addSectionOpen = !addSectionOpen">
          <Plus :size="13" class="text-primary flex-shrink-0" />
          <span class="text-xs font-semibold text-primary flex-1">Add Sources</span>
          <ChevronDown :size="12" class="text-base-content/40 transition-transform" :class="addSectionOpen ? 'rotate-180' : ''" />
        </button>

        <div v-show="addSectionOpen" class="px-3 pb-3 space-y-2">
          <!-- File/folder/record buttons -->
          <div class="flex gap-1.5">
            <button @click="openFilePicker" class="btn btn-primary btn-xs flex-1 gap-1" :disabled="indexing || isRecording">
              <FileText :size="12" />
              Files
            </button>
            <button @click="openFolderPicker" class="btn btn-outline btn-xs flex-1 gap-1" :disabled="indexing || isRecording">
              <FolderOpen :size="12" />
              Folder
            </button>
            <button
              @click="toggleRecording"
              class="btn btn-xs flex-1 gap-1"
              :class="isRecording ? 'btn-error' : 'btn-outline btn-secondary'"
              :disabled="indexing || transcribing"
              :title="isRecording ? 'Stop recording' : 'Record a meeting'"
            >
              <Square v-if="isRecording" :size="10" class="fill-current" />
              <Mic v-else :size="12" />
              {{ isRecording ? 'Stop' : 'Record' }}
            </button>
          </div>

          <!-- Recording / transcription panel -->
          <div
            v-if="isRecording || transcribing || recordError"
            class="rounded border text-xs px-2 py-1.5"
            :class="recordError ? 'border-error/40 bg-error/10' : (isRecording ? 'border-error/40 bg-error/5' : 'border-base-300 bg-base-200')"
          >
            <div v-if="isRecording" class="flex items-center gap-2">
              <span class="inline-block w-2 h-2 rounded-full bg-error animate-pulse" aria-hidden="true"></span>
              <span class="flex-1 font-medium">Recording — {{ formattedElapsed }}</span>
              <button class="btn btn-ghost btn-xs" @click="cancelRecording" :aria-label="'Cancel recording'">Cancel</button>
            </div>
            <div v-else-if="transcribing" class="flex items-center gap-2">
              <span class="loading loading-spinner loading-xs"></span>
              <span class="flex-1">{{ transcribeStatus || 'Transcribing recording…' }}</span>
            </div>
            <div v-else-if="recordError" class="flex items-start gap-2">
              <span class="flex-1 text-error">{{ recordError }}</span>
              <button class="btn btn-ghost btn-xs" @click="recordError = ''">Dismiss</button>
            </div>
          </div>

          <!-- Selected paths -->
          <div v-if="selectedPaths.length > 0" class="space-y-1.5">
            <div class="flex items-center justify-between">
              <span class="text-xs text-base-content/60">{{ selectedPaths.length }} selected</span>
              <button class="btn btn-ghost btn-xs" @click="clearAllPaths" :disabled="indexing">Clear</button>
            </div>

            <!-- compact path list -->
            <div class="max-h-24 overflow-y-auto space-y-0.5">
              <div v-for="(item, idx) in selectedPaths" :key="idx" class="flex items-center gap-1 text-xs">
                <span class="flex-1 truncate text-base-content/70" :title="item.path">{{ item.name }}</span>
                <button
                  class="btn btn-ghost btn-xs btn-circle p-0 w-5 h-5 min-h-0"
                  @click="removePath(idx)"
                  :disabled="indexing"
                  :aria-label="`Remove ${item.name} from selection`"
                >
                  <X :size="10" />
                </button>
              </div>
            </div>

            <!-- Add button -->
            <div class="flex items-center text-xs">
              <button class="btn btn-primary btn-xs ml-auto gap-1" @click="indexFiles" :disabled="indexing || selectedPaths.length === 0">
                <span v-if="indexing" class="loading loading-spinner loading-xs"></span>
                <FileSearch v-else :size="11" />
                {{ indexing ? 'Adding...' : 'Add to Collection' }}
              </button>
            </div>

            <!-- Progress -->
            <div v-if="indexing && !useBackgroundIndexing" class="space-y-1">
              <progress class="progress progress-primary w-full h-1.5" :value="indexProgressPercent" max="100"></progress>
              <div v-if="currentIndexingFile" class="text-xs text-base-content/50 truncate">{{ currentIndexingFile }}</div>
            </div>
          </div>

          <!-- Success -->
          <div v-if="indexSuccess" class="flex items-center gap-1.5 text-xs text-success bg-success/10 rounded px-2 py-1.5" role="status">
            <CheckCircle :size="12" aria-hidden="true" />
            <span v-if="indexResult.background">Started in background</span>
            <span v-else>{{ indexResult.count }} file(s), {{ indexResult.chunks }} chunks</span>
            <button
              class="ml-auto btn btn-ghost btn-xs p-0 h-4 min-h-0"
              @click="indexSuccess = false"
              aria-label="Dismiss success message"
            >✕</button>
          </div>

          <!-- Error -->
          <div v-if="indexError" class="text-xs text-error bg-error/10 rounded px-2 py-1.5">
            {{ indexError }}
            <button class="ml-1 underline" @click="indexError = ''">Dismiss</button>
          </div>

          <!-- Advanced options hidden for financial advisor build -->

        </div>
      </div>

      <!-- Applied Expertise section (collapsible) -->
      <div class="border-b border-base-300">
        <button
          class="w-full flex items-center gap-2 px-3 py-2.5 hover:bg-base-200 transition-colors text-left"
          @click="expertiseSectionOpen = !expertiseSectionOpen"
          aria-label="Toggle Applied Expertise section"
        >
          <BookOpen :size="13" class="text-accent flex-shrink-0" aria-hidden="true" />
          <span class="text-xs font-semibold text-accent flex-1">Applied Expertise</span>
          <span v-if="attachedPackIds.length > 0" class="badge badge-xs badge-accent">{{ attachedPackIds.length }}</span>
          <ChevronDown :size="12" class="text-base-content/40 transition-transform" :class="expertiseSectionOpen ? 'rotate-180' : ''" />
        </button>

        <div v-show="expertiseSectionOpen" class="px-3 pb-3 space-y-2">
          <!-- Attached packs -->
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

          <!-- Add pack dropdown -->
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
      </div>

      <!-- Document list header -->
      <div class="flex items-center gap-2 px-3 py-2 border-b border-base-300 flex-shrink-0">
        <input
          v-if="documents.length > 0"
          type="checkbox"
          class="checkbox checkbox-xs flex-shrink-0"
          :checked="isAllSelected"
          :indeterminate="selectedDocuments.length > 0 && !isAllSelected"
          @change="toggleSelectAll"
          :disabled="deleting"
          title="Select all sources"
          aria-label="Select all sources"
        />
        <span class="text-xs font-semibold text-base-content/60 flex-1" id="sources-list-heading">Your Sources</span>
        <button
          v-if="selectedDocuments.length > 0"
          class="btn btn-xs btn-error gap-1"
          @click="confirmBulkDelete"
          :disabled="deleting"
          :aria-label="`Delete ${selectedDocuments.length} selected source${selectedDocuments.length === 1 ? '' : 's'}`"
        >
          <Trash2 :size="11" aria-hidden="true" />
          {{ selectedDocuments.length }}
        </button>
      </div>

      <!-- Loading spinner -->
      <div v-if="loading" class="flex justify-center py-8">
        <span class="loading loading-spinner loading-sm"></span>
      </div>

      <!-- Empty state -->
      <div v-else-if="documents.length === 0" class="py-8 px-4 text-center">
        <p class="text-xs text-base-content/50">No sources yet. Use Add Sources above to get started.</p>
      </div>

      <!-- Document list -->
      <div v-else class="divide-y divide-base-300/60">
        <div
          v-for="doc in documents"
          :key="doc.document_id"
          class="flex items-start gap-2 px-3 py-2.5 hover:bg-base-200/60 transition-colors"
          :class="{ 'bg-primary/5': isSelected(doc.document_id) }"
        >
          <!-- Checkbox -->
          <input
            type="checkbox"
            class="checkbox checkbox-xs mt-1 flex-shrink-0"
            :checked="isSelected(doc.document_id)"
            @change="toggleSelect(doc.document_id)"
            :disabled="deleting"
            :aria-label="`Select ${doc.filename}`"
          />

          <!-- File icon -->
          <div class="flex items-center justify-center w-7 h-7 rounded flex-shrink-0 mt-0.5" :class="getFileIconClass(doc.filename)">
            <component :is="getFileIcon(doc.filename)" :size="14" :class="getFileIconTextClass(doc.filename)" />
          </div>

          <!-- Info -->
          <div class="flex-1 min-w-0">
            <div class="text-xs font-semibold truncate leading-tight" :title="doc.filename">{{ doc.filename }}</div>
            <div class="flex items-center gap-1 mt-0.5 flex-wrap">
              <span class="text-xs text-base-content/50">{{ doc.total_pages }}p · {{ doc.total_chunks }}ch</span>
              <span class="badge badge-xs" :class="doc.source_type === 'local_reference' ? 'badge-ghost' : 'badge-primary'">
                {{ doc.source_type === 'local_reference' ? 'local' : 'lib' }}
              </span>
              <span
                v-if="isTabularFile(doc.filename)"
                class="badge badge-xs badge-success gap-0.5"
                title="Queryable as a typed SQL table — numeric questions run real SQL against this file"
              >
                <Table2 :size="9" />
                table
              </span>
              <button
                v-if="doc.injection_warnings && Object.keys(doc.injection_warnings).length > 0"
                class="badge badge-xs badge-warning gap-0.5 cursor-pointer hover:badge-error transition-colors"
                @click.stop="openInjectionWarnings(doc)"
                title="Prompt injection warnings detected — click to view"
              >
                <ShieldAlert :size="9" />
                {{ Object.keys(doc.injection_warnings).length }}p
              </button>
            </div>
          </div>

          <!-- Action buttons -->
          <div class="flex items-center flex-shrink-0 gap-0.5">
            <button
              class="btn btn-ghost btn-xs btn-circle"
              @click="openChunks(doc)"
              :disabled="deleting"
              title="View indexed chunks"
              :aria-label="`View indexed chunks for ${doc.filename}`"
            >
              <FileSearch :size="12" />
            </button>
            <a
              :href="`/documents/${doc.document_id}/pdf?collection_id=${collectionStore.currentCollectionId}`"
              target="_blank"
              rel="noopener"
              class="btn btn-ghost btn-xs btn-circle"
              title="Open source document"
              :aria-label="`Open source document ${doc.filename} in a new tab`"
            >
              <Eye :size="12" />
            </a>
            <button
              class="btn btn-ghost btn-xs btn-circle text-error"
              @click="confirmDelete(doc)"
              :disabled="deleting"
              title="Delete source"
              :aria-label="`Delete source ${doc.filename}`"
            >
              <Trash2 :size="12" />
            </button>
          </div>
        </div>
      </div>

      <!-- Error -->
      <div v-if="error" class="mx-3 my-2 text-xs text-error bg-error/10 rounded px-2 py-1.5">{{ error }}</div>

    </div>

    <!-- Delete confirmation modal (same as DocumentsTab) -->
    <dialog ref="deleteModal" class="modal" aria-labelledby="sidebar-delete-title">
      <div class="modal-box">
        <h3 id="sidebar-delete-title" class="font-bold text-lg">Confirm Delete</h3>
        <p v-if="documentToDelete" class="py-4">
          Delete <strong>{{ documentToDelete.filename }}</strong>?
          <span v-if="documentToDelete.source_type === 'local_reference'" class="block text-sm text-base-content/70 mt-2">
            Note: The original file will not be deleted, only the index entry.
          </span>
        </p>
        <p v-else class="py-4">Delete <strong>{{ selectedDocuments.length }} source(s)</strong>?</p>
        <div class="modal-action">
          <button class="btn" @click="closeDeleteModal" :disabled="deleting">Cancel</button>
          <button class="btn btn-error" @click="documentToDelete ? deleteDocument() : deleteBulk()" :disabled="deleting">
            <span v-if="deleting" class="loading loading-spinner"></span>
            {{ deleting ? 'Deleting...' : 'Delete' }}
          </button>
        </div>
      </div>
      <form method="dialog" class="modal-backdrop"><button @click="closeDeleteModal">close</button></form>
    </dialog>

    <!-- Chunks viewer modal (same as DocumentsTab) -->
    <dialog ref="chunksModal" class="modal" aria-labelledby="sidebar-chunks-title">
      <div class="modal-box max-w-6xl">
        <h3 id="sidebar-chunks-title" class="font-bold text-lg">Indexed Chunks</h3>
        <p v-if="chunkDocument" class="text-sm text-base-content/70 mt-1">{{ chunkDocument.filename }}</p>
        <div v-if="chunksLoading" class="flex justify-center py-10"><span class="loading loading-spinner loading-lg"></span></div>
        <div v-else-if="chunksError" class="alert alert-error mt-4"><XCircle :size="20" /><span>{{ chunksError }}</span></div>
        <div v-else class="mt-4 space-y-4">
          <div class="flex items-center justify-between gap-3 flex-wrap">
            <div class="flex gap-2 flex-wrap">
              <span class="badge badge-outline">{{ chunkResponse.returned_chunks }} shown</span>
              <span class="badge badge-outline">{{ chunkResponse.total_chunks }} total</span>
              <span class="badge badge-info">{{ chunkResponse.extraction_method || 'text' }}</span>
              <span class="badge badge-success">{{ chunksWithFieldsCount }} with fields</span>
            </div>
            <label class="label cursor-pointer gap-2 py-0">
              <span class="label-text text-sm">Only chunks with fields</span>
              <input type="checkbox" class="toggle toggle-sm" v-model="showOnlyChunksWithFields" />
            </label>
          </div>
          <div v-if="filteredChunks.length === 0" class="alert alert-info"><span>No chunks match the current filter.</span></div>
          <div v-else class="space-y-3 max-h-[60vh] overflow-y-auto pr-1">
            <div v-for="chunk in filteredChunks" :key="chunk.chunk_id" class="card bg-base-200 border border-base-300">
              <div class="card-body p-4">
                <div class="flex items-center justify-between flex-wrap gap-2">
                  <div class="font-mono text-xs text-base-content/60">{{ chunk.chunk_id }}</div>
                  <div class="flex gap-2">
                    <span class="badge badge-sm">p{{ chunk.page_number }} c{{ chunk.chunk_index }}</span>
                    <span v-if="chunk.extraction_method === 'ocr'" class="badge badge-warning badge-sm">OCR</span>
                    <span v-else-if="chunk.extraction_method === 'hybrid'" class="badge badge-info badge-sm">Hybrid</span>
                    <span v-if="chunkFieldCount(chunk) > 0" class="badge badge-success badge-sm">{{ chunkFieldCount(chunk) }} fields</span>
                  </div>
                </div>
                <div v-if="chunk.extracted_fields && Object.keys(chunk.extracted_fields).length > 0" class="overflow-x-auto">
                  <table class="table table-xs table-zebra">
                    <thead><tr><th>Field</th><th>Value</th></tr></thead>
                    <tbody>
                      <tr v-for="(value, field) in chunk.extracted_fields" :key="`${chunk.chunk_id}_${field}`">
                        <td class="font-semibold">{{ field }}</td><td class="break-all">{{ value }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <details>
                  <summary class="cursor-pointer text-sm font-medium text-primary">Show chunk text</summary>
                  <pre class="mt-2 text-xs whitespace-pre-wrap bg-base-100 p-3 rounded border border-base-300">{{ chunk.text }}</pre>
                </details>
              </div>
            </div>
          </div>
        </div>
        <div class="modal-action"><button class="btn" @click="closeChunksModal">Close</button></div>
      </div>
      <form method="dialog" class="modal-backdrop"><button @click="closeChunksModal">close</button></form>
    </dialog>

    <!-- Injection warnings modal -->
    <dialog ref="injectionModal" class="modal" aria-labelledby="injection-warnings-title">
      <div class="modal-box max-w-2xl">
        <h3 id="injection-warnings-title" class="font-bold text-lg flex items-center gap-2">
          <ShieldAlert :size="18" class="text-warning" aria-hidden="true" />
          Prompt Injection Warnings
        </h3>
        <p v-if="injectionDoc" class="text-sm text-base-content/70 mt-1">{{ injectionDoc.filename }}</p>
        <div v-if="injectionDoc" class="mt-4 space-y-3 max-h-[60vh] overflow-y-auto pr-1">
          <div
            v-for="entry in injectionWarningPages(injectionDoc)"
            :key="entry.page"
            class="card bg-base-200 border border-warning/40"
          >
            <div class="card-body p-3">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="font-semibold text-sm">Page {{ entry.page }}</span>
                <span class="badge badge-sm badge-warning">score {{ entry.risk_score }}</span>
                <span class="badge badge-sm badge-outline">{{ entry.finding_count }} finding{{ entry.finding_count !== 1 ? 's' : '' }}</span>
              </div>
              <div class="space-y-2 mt-2">
                <div
                  v-for="(finding, idx) in entry.findings"
                  :key="idx"
                  class="rounded bg-base-100 border border-base-300 p-2 text-xs space-y-1"
                >
                  <div class="flex items-center gap-2 flex-wrap">
                    <span class="font-semibold capitalize">{{ finding.category.replace(/_/g, ' ') }}</span>
                    <span class="badge badge-xs" :class="{ 'badge-error': finding.severity === 'high', 'badge-warning': finding.severity === 'medium', 'badge-info': finding.severity === 'low' }">{{ finding.severity }}</span>
                    <span class="text-base-content/50 font-mono">{{ finding.pattern_name }}</span>
                  </div>
                  <div v-if="finding.matched_text" class="font-mono text-base-content/70 bg-base-200 rounded px-2 py-1 break-all">{{ finding.matched_text }}</div>
                </div>
              </div>
            </div>
          </div>
        </div>
        <div class="modal-action"><button class="btn" @click="closeInjectionModal">Close</button></div>
      </div>
      <form method="dialog" class="modal-backdrop"><button @click="closeInjectionModal">close</button></form>
    </dialog>

  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import axios from 'axios'
import { FileText, Eye, Trash2, RefreshCw, X, FolderOpen, FileCode, FileSearch, CheckCircle, XCircle, Library, Plus, ChevronDown, ShieldAlert, Table2, Mic, Square, BookOpen } from 'lucide-vue-next'
import { useCollectionStore } from '../stores/collectionStore'
import { useBackgroundJobsStore } from '../stores/backgroundJobsStore'
import { useExpertiseStore } from '../stores/expertiseStore'

const emit = defineEmits(['document-deleted', 'background-job-started', 'close'])

const collectionStore = useCollectionStore()
const backgroundJobsStore = useBackgroundJobsStore()
const expertiseStore = useExpertiseStore()

// Sidebar-specific state
const addSectionOpen = ref(true)
const advancedOpen = ref(false)
const expertiseSectionOpen = ref(false)
const expertiseLoading = ref(false)

// Source code mode
const isSourceCode = ref(false)
const includeDocumentation = ref(true)
const fileExtensions = ref([
  { value: 'python', label: 'Python', enabled: true, exts: ['.py', '.pyw', '.pyi'] },
  { value: 'javascript', label: 'JS/TS', enabled: true, exts: ['.js', '.jsx', '.mjs', '.ts', '.tsx', '.mts'] },
  { value: 'csharp', label: 'C#', enabled: true, exts: ['.cs'] },
  { value: 'java', label: 'Java', enabled: true, exts: ['.java'] },
  { value: 'go', label: 'Go', enabled: true, exts: ['.go'] },
  { value: 'rust', label: 'Rust', enabled: true, exts: ['.rs'] },
  { value: 'cpp', label: 'C/C++', enabled: true, exts: ['.c', '.h', '.cpp', '.hpp', '.cc', '.cxx'] },
  { value: 'php', label: 'PHP', enabled: true, exts: ['.php', '.phtml'] },
  { value: 'ruby', label: 'Ruby', enabled: true, exts: ['.rb', '.rake'] },
  { value: 'swift', label: 'Swift', enabled: true, exts: ['.swift'] },
  { value: 'kotlin', label: 'Kotlin', enabled: true, exts: ['.kt', '.kts'] },
  { value: 'scala', label: 'Scala', enabled: true, exts: ['.scala', '.sc'] },
  { value: 'pascal', label: 'Pascal/Delphi', enabled: true, exts: ['.pas', '.dpr', '.dpk', '.pp', '.inc', '.dfm'] },
  { value: 'modula2', label: 'Modula-2', enabled: false, exts: ['.mod', '.def', '.mi'] },
  { value: 'assembly', label: 'Assembly', enabled: false, exts: ['.asm', '.s'] },
])
const recentRepos = ref([])

// Index state (using native file picker)
const selectedPaths = ref([]) // Array of { path: string, name: string, isFolder?: boolean, size?: number }
const copyToLibrary = ref(false) // Default OFF - index in-place
const useBackgroundIndexing = ref(false) // Default OFF - synchronous indexing
const indexing = ref(false)
const indexProgress = ref(0)
const indexProgressPercent = ref(0)
const currentIndexingFile = ref('')
const indexSuccess = ref(false)
const indexError = ref('')
const indexResult = ref({ count: 0, chunks: 0 })

// Document management state
const documents = ref([])
const loading = ref(false)
const deleting = ref(false)
const error = ref('')
const deleteModal = ref(null)
const documentToDelete = ref(null)
const selectedDocuments = ref([])
const chunksModal = ref(null)
const chunkDocument = ref(null)
const chunksLoading = ref(false)
const chunksError = ref('')
const showOnlyChunksWithFields = ref(false)
const chunkResponse = ref({
  document_id: '',
  filename: '',
  extraction_method: '',
  total_chunks: 0,
  returned_chunks: 0,
  chunks: []
})

// Meeting recording state
const isRecording = ref(false)
const transcribing = ref(false)
const transcribeStatus = ref('')
const recordError = ref('')
const elapsedSeconds = ref(0)
let mediaRecorder = null
let recordedChunks = []
let mediaStream = null
let elapsedTimer = null
let cancelled = false

const formattedElapsed = computed(() => {
  const mm = String(Math.floor(elapsedSeconds.value / 60)).padStart(2, '0')
  const ss = String(elapsedSeconds.value % 60).padStart(2, '0')
  return `${mm}:${ss}`
})

function pickRecordingMime() {
  // Prefer opus/webm (small, widely supported). Fall back to browser default.
  const candidates = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/ogg;codecs=opus',
    'audio/mp4',
  ]
  if (typeof MediaRecorder === 'undefined') return ''
  for (const mime of candidates) {
    if (MediaRecorder.isTypeSupported(mime)) return mime
  }
  return ''
}

function stopMediaTracks() {
  if (mediaStream) {
    mediaStream.getTracks().forEach(t => t.stop())
    mediaStream = null
  }
  if (elapsedTimer) {
    clearInterval(elapsedTimer)
    elapsedTimer = null
  }
}

async function startRecording() {
  if (!navigator.mediaDevices || typeof MediaRecorder === 'undefined') {
    recordError.value = 'Recording is not supported in this browser.'
    return
  }
  if (!collectionStore.canEditCurrent) {
    recordError.value = 'You do not have permission to add sources to this collection.'
    return
  }
  recordError.value = ''
  cancelled = false
  recordedChunks = []
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true })
  } catch (err) {
    console.error('getUserMedia failed:', err)
    recordError.value = err?.message?.includes('Permission')
      ? 'Microphone permission denied.'
      : 'Could not access microphone.'
    return
  }

  const mime = pickRecordingMime()
  try {
    mediaRecorder = mime
      ? new MediaRecorder(mediaStream, { mimeType: mime })
      : new MediaRecorder(mediaStream)
  } catch (err) {
    console.error('MediaRecorder init failed:', err)
    recordError.value = 'Failed to start recorder.'
    stopMediaTracks()
    return
  }

  mediaRecorder.ondataavailable = (e) => {
    if (e.data && e.data.size > 0) recordedChunks.push(e.data)
  }
  mediaRecorder.onstop = async () => {
    stopMediaTracks()
    if (cancelled) {
      recordedChunks = []
      isRecording.value = false
      return
    }
    const blob = new Blob(recordedChunks, { type: mediaRecorder.mimeType || 'audio/webm' })
    recordedChunks = []
    isRecording.value = false
    await uploadRecording(blob)
  }

  elapsedSeconds.value = 0
  elapsedTimer = setInterval(() => { elapsedSeconds.value += 1 }, 1000)
  mediaRecorder.start()
  isRecording.value = true
}

function stopRecording() {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop()
  }
}

function cancelRecording() {
  cancelled = true
  stopRecording()
}

function toggleRecording() {
  if (isRecording.value) stopRecording()
  else startRecording()
}

function extensionForMime(mime) {
  if (!mime) return 'webm'
  if (mime.includes('webm')) return 'webm'
  if (mime.includes('ogg')) return 'ogg'
  if (mime.includes('mp4')) return 'm4a'
  if (mime.includes('wav')) return 'wav'
  return 'webm'
}

async function uploadRecording(blob) {
  transcribing.value = true
  transcribeStatus.value = 'Uploading recording…'
  try {
    const ext = extensionForMime(blob.type)
    const now = new Date()
    const pad = (n) => String(n).padStart(2, '0')
    const stamp = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
    const filename = `meeting-${stamp}.${ext}`
    const file = new File([blob], filename, { type: blob.type })

    const form = new FormData()
    form.append('files', file)

    transcribeStatus.value = 'Transcribing with Whisper (may take a minute)…'
    const response = await axios.post('/documents/upload', form, {
      params: { collection_id: collectionStore.currentCollectionId },
      headers: { 'Content-Type': 'multipart/form-data' },
    })

    indexSuccess.value = true
    indexResult.value = {
      count: response.data.documents_processed || 1,
      chunks: response.data.total_chunks || 0,
    }
    await loadDocuments()
    emit('document-deleted')
  } catch (err) {
    console.error('Recording upload failed:', err)
    recordError.value = err.response?.data?.detail || err.message || 'Failed to transcribe recording'
  } finally {
    transcribing.value = false
    transcribeStatus.value = ''
  }
}

// Injection warnings modal state
const injectionModal = ref(null)
const injectionDoc = ref(null)

function openInjectionWarnings(doc) {
  injectionDoc.value = doc
  injectionModal.value?.showModal()
}

function closeInjectionModal() {
  injectionModal.value?.close()
}

function injectionWarningPages(doc) {
  if (!doc.injection_warnings) return []
  return Object.entries(doc.injection_warnings).map(([page, scan]) => ({ page: parseInt(page), ...scan }))
}

const chunksWithFieldsCount = computed(() => {
  return chunkResponse.value.chunks.filter(chunk => chunkFieldCount(chunk) > 0).length
})

const filteredChunks = computed(() => {
  if (!showOnlyChunksWithFields.value) {
    return chunkResponse.value.chunks
  }
  return chunkResponse.value.chunks.filter(chunk => chunkFieldCount(chunk) > 0)
})

const chunkFieldCount = (chunk) => {
  if (!chunk?.extracted_fields) return 0
  return Object.keys(chunk.extracted_fields).length
}

// Extract filename from path
const getFilename = (path) => {
  return path.split(/[/\\]/).pop()
}

// Open native file picker via backend API
const openFilePicker = async () => {
  try {
    indexError.value = ''
    const response = await axios.post('/api/file-picker', null, {
      params: { multiple: true, include_sizes: true }
    })

    if (response.data.paths && response.data.paths.length > 0) {
      const existingPaths = new Set(selectedPaths.value.map(p => p.path))
      const sizes = response.data.sizes || {}
      for (const path of response.data.paths) {
        if (!existingPaths.has(path)) {
          selectedPaths.value.push({
            path: path,
            name: getFilename(path),
            size: sizes[path] || 0
          })
        }
      }
    }
  } catch (err) {
    console.error('File picker error:', err)
    indexError.value = err.response?.data?.detail || 'Failed to open file picker'
  }
}

// Open native folder picker via backend API
const openFolderPicker = async () => {
  try {
    indexError.value = ''
    const response = await axios.post('/api/folder-picker')

    if (response.data.path) {
      const folderPath = response.data.path

      if (isSourceCode.value) {
        // In code mode: scan folder with extension filter, add individual files
        const enabledExts = fileExtensions.value
          .filter(e => e.enabled)
          .flatMap(e => e.exts)
        if (includeDocumentation.value) {
          enabledExts.push('.txt', '.md', '.json', '.jsonl')
        }

        const scanResponse = await axios.post('/api/scan-folder', {
          path: folderPath,
          recursive: true,
          file_extensions: enabledExts.length > 0 ? enabledExts : undefined
        })

        if (scanResponse.data.files && scanResponse.data.files.length > 0) {
          const existingPaths = new Set(selectedPaths.value.map(p => p.path))
          for (const f of scanResponse.data.files) {
            if (!existingPaths.has(f.path)) {
              selectedPaths.value.push({
                path: f.path,
                name: f.relative_path || f.name
              })
            }
          }
          saveRecentRepo(folderPath)
        } else {
          indexError.value = 'No matching files found in the selected folder'
        }
      } else {
        // In document mode: scan folder for supported document types, add individual files
        const scanResponse = await axios.post('/api/scan-folder', {
          path: folderPath,
          recursive: true,
          file_extensions: ['.pdf', '.txt', '.docx', '.csv', '.xlsx', '.xls', '.md', '.json', '.jsonl']
        })

        if (scanResponse.data.files && scanResponse.data.files.length > 0) {
          const existingPaths = new Set(selectedPaths.value.map(p => p.path))
          for (const f of scanResponse.data.files) {
            if (!existingPaths.has(f.path)) {
              selectedPaths.value.push({
                path: f.path,
                name: f.relative_path || f.name
              })
            }
          }
        } else {
          indexError.value = 'No supported files found (.pdf, .txt, .docx, .csv, .xlsx, .xls, .md, .json)'
        }
      }
    }
  } catch (err) {
    console.error('Folder picker error:', err)
    indexError.value = err.response?.data?.detail || 'Failed to open folder picker'
  }
}

const removePath = (index) => {
  selectedPaths.value.splice(index, 1)
}

const clearAllPaths = () => {
  selectedPaths.value = []
  indexSuccess.value = false
  indexError.value = ''
}

// Main indexing function
const indexFiles = async () => {
  if (selectedPaths.value.length === 0) return

  // Separate files and folders up front
  const files = selectedPaths.value.filter(p => !p.isFolder)
  const filePaths = files.map(p => p.path)

  indexing.value = true
  indexProgress.value = 0
  indexProgressPercent.value = 0
  currentIndexingFile.value = ''
  indexSuccess.value = false
  indexError.value = ''

  const folders = selectedPaths.value.filter(p => p.isFolder)

  // Use background indexing based on toggle
  const useBackground = useBackgroundIndexing.value

  let successCount = 0
  let totalChunks = 0
  const errors = []

  try {
    if (filePaths.length > 0) {
      if (useBackground) {
        // Use background indexing for large file sets
        try {
          const response = await axios.post('/documents/index-local-async', {
            file_paths: filePaths,
            collection_id: collectionStore.currentCollectionId,
            copy_to_library: copyToLibrary.value
          })

          // Add job to background jobs store for tracking
          backgroundJobsStore.addUploadJob(response.data)

          // Emit event to open the jobs drawer
          emit('background-job-started')

          // Show background notification
          indexSuccess.value = true
          indexResult.value = { count: filePaths.length, chunks: 0, background: true }
          selectedPaths.value = folders.length > 0 ? folders : []

        } catch (err) {
          const errorMsg = err.response?.data?.detail || err.message
          errors.push(`Files: ${errorMsg}`)
          console.error('Failed to start background indexing:', err)
        }
      } else {
        // Use synchronous indexing
        for (let i = 0; i < filePaths.length; i++) {
          const filename = getFilename(filePaths[i])
          currentIndexingFile.value = filename
          indexProgress.value = i + 1
          indexProgressPercent.value = ((i) / filePaths.length) * 100

          try {
            const response = await axios.post('/documents/index-local', {
              file_path: filePaths[i],
              collection_id: collectionStore.currentCollectionId,
              copy_to_library: copyToLibrary.value
            })
            successCount++
            totalChunks += response.data.total_chunks || 0
            // Update progress after successful index
            indexProgressPercent.value = ((i + 1) / filePaths.length) * 100
          } catch (err) {
            const errorMsg = err.response?.data?.detail || err.message
            errors.push(`${filename}: ${errorMsg}`)
            console.error(`Failed to index ${filePaths[i]}:`, err)
          }
        }

        if (successCount > 0) {
          indexSuccess.value = true
          indexResult.value = { count: successCount, chunks: totalChunks }
          selectedPaths.value = folders
          loadDocuments()
          emit('document-deleted')
        }
      }
    }

    // Index folders via repo endpoint (synchronous)
    for (let i = 0; i < folders.length; i++) {
      const folder = folders[i]
      currentIndexingFile.value = folder.name
      indexProgress.value = filePaths.length + i + 1

      try {
        const response = await axios.post('/documents/upload-repo', {
          path: folder.path,
          collection_id: collectionStore.currentCollectionId,
          recursive: true
        })
        successCount += response.data.files_indexed || 0
        totalChunks += response.data.total_chunks || 0
      } catch (err) {
        const errorMsg = err.response?.data?.detail || err.message
        errors.push(`${folder.name}: ${errorMsg}`)
        console.error(`Failed to index ${folder.path}:`, err)
      }
    }

    // If we indexed folders synchronously, show results and always refresh
    if (folders.length > 0) {
      if (successCount > 0) {
        indexSuccess.value = true
        indexResult.value = { count: successCount, chunks: totalChunks }
      }
      selectedPaths.value = []
      loadDocuments()
      emit('document-deleted')
    }

  } finally {
    indexing.value = false
    currentIndexingFile.value = ''
    indexProgressPercent.value = 0
  }

  if (errors.length > 0) {
    indexError.value = errors.join('; ')
  }

  // Clear selection if everything was submitted successfully
  if (errors.length === 0) {
    selectedPaths.value = []
  }
}

// Document management functions
const isAllSelected = computed(() => {
  return documents.value.length > 0 && selectedDocuments.value.length === documents.value.length
})

const isSelected = (docId) => {
  return selectedDocuments.value.includes(docId)
}

const toggleSelect = (docId) => {
  const index = selectedDocuments.value.indexOf(docId)
  if (index > -1) {
    selectedDocuments.value.splice(index, 1)
  } else {
    selectedDocuments.value.push(docId)
  }
}

const toggleSelectAll = () => {
  if (isAllSelected.value) {
    selectedDocuments.value = []
  } else {
    selectedDocuments.value = documents.value.map(doc => doc.document_id)
  }
}

const loadDocuments = async () => {
  loading.value = true
  error.value = ''

  try {
    const collectionId = collectionStore.currentCollectionId
    const response = await axios.get(`/documents?collection_id=${collectionId}`)
    documents.value = response.data.documents || []
  } catch (err) {
    error.value = err.response?.data?.detail || 'Failed to load sources'
  } finally {
    loading.value = false
  }
}

const openChunks = async (doc) => {
  chunkDocument.value = doc
  chunksLoading.value = true
  chunksError.value = ''
  showOnlyChunksWithFields.value = false
  chunkResponse.value = {
    document_id: doc.document_id,
    filename: doc.filename,
    extraction_method: '',
    total_chunks: 0,
    returned_chunks: 0,
    chunks: []
  }

  chunksModal.value?.showModal()

  try {
    const collectionId = collectionStore.currentCollectionId
    const response = await axios.get(
      `/documents/${doc.document_id}/chunks`,
      {
        params: {
          collection_id: collectionId,
          include_fields: true
        }
      }
    )
    chunkResponse.value = response.data
  } catch (err) {
    chunksError.value = err.response?.data?.detail || 'Failed to load document chunks'
  } finally {
    chunksLoading.value = false
  }
}

const closeChunksModal = () => {
  chunksModal.value?.close()
}

// Code file extensions for icon display
const CODE_EXTENSIONS = ['.pas', '.dpr', '.dpk', '.pp', '.inc', '.dfm', '.mod', '.def', '.mi', '.asm', '.s']
const TABULAR_EXTENSIONS = ['.csv', '.xlsx', '.xls']

const getExt = (filename) => '.' + (filename || '').split('.').pop().toLowerCase()

const isCodeFile = (filename) => CODE_EXTENSIONS.includes(getExt(filename))
const isTabularFile = (filename) => TABULAR_EXTENSIONS.includes(getExt(filename))

const getFileIcon = (filename) => {
  if (isTabularFile(filename)) return Table2
  return isCodeFile(filename) ? FileCode : FileText
}

const getFileIconClass = (filename) => {
  if (isTabularFile(filename)) return 'bg-success/20'
  return isCodeFile(filename) ? 'bg-primary/20' : 'bg-error/20'
}

const getFileIconTextClass = (filename) => {
  if (isTabularFile(filename)) return 'text-success'
  return isCodeFile(filename) ? 'text-primary' : 'text-error'
}

const confirmDelete = (doc) => {
  documentToDelete.value = doc
  deleteModal.value?.showModal()
}

const closeDeleteModal = () => {
  if (!deleting.value) {
    deleteModal.value?.close()
    documentToDelete.value = null
  }
}

const deleteDocument = async () => {
  if (!documentToDelete.value) return

  deleting.value = true
  error.value = ''

  try {
    const collectionId = collectionStore.currentCollectionId
    await axios.delete(`/documents/${documentToDelete.value.document_id}?collection_id=${collectionId}`)

    documents.value = documents.value.filter(
      doc => doc.document_id !== documentToDelete.value.document_id
    )

    const index = selectedDocuments.value.indexOf(documentToDelete.value.document_id)
    if (index > -1) {
      selectedDocuments.value.splice(index, 1)
    }

    emit('document-deleted')
  } catch (err) {
    error.value = err.response?.data?.detail || 'Failed to delete source'
  } finally {
    deleting.value = false
    closeDeleteModal()
  }
}

const confirmBulkDelete = () => {
  documentToDelete.value = null
  deleteModal.value?.showModal()
}

const deleteBulk = async () => {
  if (selectedDocuments.value.length === 0) return

  deleting.value = true
  error.value = ''

  try {
    const collectionId = collectionStore.currentCollectionId
    for (const docId of selectedDocuments.value) {
      await axios.delete(`/documents/${docId}?collection_id=${collectionId}`)
    }

    documents.value = documents.value.filter(
      doc => !selectedDocuments.value.includes(doc.document_id)
    )

    selectedDocuments.value = []
    emit('document-deleted')
  } catch (err) {
    error.value = err.response?.data?.detail || 'Failed to delete sources'
  } finally {
    deleting.value = false
    closeDeleteModal()
  }
}

// Watch for collection changes
watch(() => collectionStore.currentCollectionId, (newId) => {
  loadDocuments()
  selectedDocuments.value = []
  loadExpertise(newId)
})

// Applied Expertise helpers

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
    // non-fatal
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

// Watch for completed background uploads to reload documents
watch(() => backgroundJobsStore.uploadJobs, (jobs) => {
  const completedJob = jobs.find(j => j.status === 'completed' && !j.reloaded)
  if (completedJob) {
    completedJob.reloaded = true
    loadDocuments()
    emit('document-deleted')
  }
}, { deep: true })

// Watch for completed reindex jobs to reload documents
watch(() => backgroundJobsStore.reindexJob?.status, (newStatus, oldStatus) => {
  if (newStatus === 'completed' && oldStatus && oldStatus !== 'completed') {
    loadDocuments()
    emit('document-deleted')
  }
})

// Warn user before leaving page during indexing
const beforeUnloadHandler = (e) => {
  if (indexing.value || isRecording.value || transcribing.value) {
    e.preventDefault()
    e.returnValue = isRecording.value
      ? 'Recording in progress. Are you sure you want to leave?'
      : 'Indexing in progress. Are you sure you want to leave?'
    return e.returnValue
  }
}

const saveRecentRepo = (path) => {
  const repos = JSON.parse(localStorage.getItem('recentCodeRepos') || '[]')
  const name = path.split(/[/\\]/).pop() || path
  const filtered = repos.filter(r => r.path !== path)
  filtered.unshift({ path, name, indexedAt: Date.now() })
  localStorage.setItem('recentCodeRepos', JSON.stringify(filtered.slice(0, 5)))
  recentRepos.value = filtered.slice(0, 5)
}

const loadRecentRepos = () => {
  try {
    recentRepos.value = JSON.parse(localStorage.getItem('recentCodeRepos') || '[]')
  } catch {
    recentRepos.value = []
  }
}

const selectRecentRepo = async (repo) => {
  indexError.value = ''
  const enabledExts = fileExtensions.value
    .filter(e => e.enabled)
    .flatMap(e => e.exts)
  if (includeDocumentation.value) {
    enabledExts.push('.txt', '.md', '.json', '.jsonl')
  }

  try {
    const scanResponse = await axios.post('/api/scan-folder', {
      path: repo.path,
      recursive: true,
      file_extensions: enabledExts.length > 0 ? enabledExts : undefined
    })
    if (scanResponse.data.files && scanResponse.data.files.length > 0) {
      selectedPaths.value = scanResponse.data.files.map(f => ({
        path: f.path,
        name: f.relative_path || f.name
      }))
    } else {
      indexError.value = 'No matching files found in the selected folder'
    }
  } catch (err) {
    indexError.value = err.response?.data?.detail || 'Failed to scan folder'
  }
}


onMounted(() => {
  loadDocuments()
  loadRecentRepos()
  loadExpertise()
  window.addEventListener('beforeunload', beforeUnloadHandler)
})

onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', beforeUnloadHandler)
  if (isRecording.value) {
    cancelled = true
    try { mediaRecorder?.stop() } catch (_) { /* ignore */ }
  }
  stopMediaTracks()
})
</script>
