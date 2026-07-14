<template>
  <div class="space-y-6">

    <!-- Index Documents Section -->
    <div class="card bg-base-200">
      <div class="card-body">
        <div class="flex items-center justify-between flex-wrap gap-2">
          <h3 class="card-title">Index Sources</h3>
          <div class="flex items-center gap-3">
            <div v-if="isSourceCode" class="badge badge-info gap-1">
              <Code :size="14" />
              Symbol-Aware Chunking
            </div>
            <label class="label cursor-pointer gap-2 py-0">
              <Code :size="16" class="text-base-content/60" />
              <span class="label-text font-medium">Source code</span>
              <input
                type="checkbox"
                class="toggle toggle-sm toggle-primary"
                v-model="isSourceCode"
                :disabled="indexing"
              />
            </label>
          </div>
        </div>

        <!-- File/Folder Picker Buttons -->
        <div class="flex flex-wrap gap-3 mt-2">
          <template v-if="capabilities.native_file_picker === false">
            <!-- Headless/Docker: labels directly trigger hidden inputs (no JS click chain needed) -->
            <label for="dt-file-input" class="btn btn-primary cursor-pointer" :class="{ 'btn-disabled pointer-events-none': indexing }">
              <FileText :size="20" />
              Select Files...
            </label>
            <label for="dt-folder-input" class="btn btn-outline cursor-pointer" :class="{ 'btn-disabled pointer-events-none': indexing }">
              <FolderOpen :size="20" />
              Select Folder...
            </label>
            <input id="dt-file-input" type="file" multiple class="sr-only" @change="handleBrowserFiles" :disabled="indexing" />
            <input id="dt-folder-input" type="file" webkitdirectory class="sr-only" @change="handleBrowserFolder" :disabled="indexing" />
          </template>
          <template v-else>
            <!-- Desktop: buttons call native OS file picker via backend -->
            <button @click="openFilePicker" class="btn btn-primary" :disabled="indexing">
              <FileText :size="20" />
              Select Files...
            </button>
            <button @click="openFolderPicker" class="btn btn-outline" :disabled="indexing">
              <FolderOpen :size="20" />
              Select Folder...
            </button>
          </template>
        </div>

        <!-- Upload-mode notice (shown in Docker/headless environments) -->
        <div v-if="capabilities.native_file_picker === false" class="alert alert-info alert-sm mt-2 py-2 text-sm">
          <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" class="stroke-current shrink-0 w-4 h-4">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
          </svg>
          Running in server/Docker mode — files are uploaded through the browser and copied to the library.
        </div>

        <!-- Indexing options — always visible so users know they exist before picking files -->
        <div class="flex flex-wrap items-center gap-x-6 gap-y-2 mt-3">
          <label class="label cursor-pointer gap-2 py-0">
            <span class="label-text text-sm">Copy to library</span>
            <input
              type="checkbox"
              class="toggle toggle-sm"
              v-model="copyToLibrary"
              :disabled="indexing"
            />
          </label>
          <label class="label cursor-pointer gap-2 py-0">
            <span class="label-text text-sm">Background indexing</span>
            <input
              type="checkbox"
              class="toggle toggle-sm toggle-secondary"
              v-model="useBackgroundIndexing"
              :disabled="indexing"
            />
          </label>
        </div>
        <p class="text-xs text-base-content/50 mt-1">
          <span v-if="copyToLibrary">Files will be copied to the library — safe to move or delete originals.</span>
          <span v-else>Files indexed in-place — search breaks if originals are moved.</span>
          <span v-if="useBackgroundIndexing" class="text-secondary"> Indexing runs in the background; track progress via the notification bell.</span>
        </p>

        <p class="text-sm text-base-content/60 mt-2">
          <span v-if="isSourceCode">
            Select source code files or a folder to index with symbol-aware chunking.
          </span>
          <span v-else>
            Select files or a folder to index. Supported: PDF, TXT, DOCX, CSV, MD, JSON, JSONL
          </span>
        </p>

        <!-- Source Code Options -->
        <div v-if="isSourceCode" class="mt-3 space-y-3">
          <div class="form-control">
            <label class="label cursor-pointer justify-start gap-3 py-1">
              <input
                type="checkbox"
                v-model="includeDocumentation"
                class="checkbox checkbox-sm checkbox-primary"
                :disabled="indexing"
              />
              <span class="label-text text-sm">Include documentation files (.txt, .md, .json)</span>
            </label>
          </div>

          <div>
            <p class="text-sm font-medium mb-2">Languages to index</p>
            <div class="flex flex-wrap gap-2">
              <label
                v-for="ext in fileExtensions"
                :key="ext.value"
                class="flex items-center gap-1.5 px-2.5 py-1 rounded-lg cursor-pointer transition-colors text-sm"
                :class="ext.enabled ? 'bg-primary/20 border border-primary' : 'bg-base-300 hover:bg-base-100'"
              >
                <input
                  type="checkbox"
                  v-model="ext.enabled"
                  class="checkbox checkbox-xs checkbox-primary"
                  :disabled="indexing"
                />
                {{ ext.label }}
              </label>
            </div>
          </div>
        </div>

        <!-- Selected Items -->
        <div v-if="selectedPaths.length > 0" class="mt-4 space-y-3">
          <div class="flex items-center justify-between flex-wrap gap-2">
            <span class="text-sm font-semibold">
              {{ selectedPaths.length }} item{{ selectedPaths.length !== 1 ? 's' : '' }} selected
            </span>
            <div class="flex gap-2">
              <button
                class="btn btn-ghost btn-sm"
                @click="clearAllPaths"
                :disabled="indexing"
              >
                Clear
              </button>
              <button
                class="btn btn-primary btn-sm"
                @click="indexFiles"
                :disabled="indexing || selectedPaths.length === 0"
              >
                <span v-if="indexing" class="loading loading-spinner loading-sm"></span>
                <FileSearch v-else :size="16" />
                {{ indexing ? 'Indexing...' : 'Index' }}
              </button>
            </div>
          </div>

          <!-- Sync Progress Indicator -->
          <div v-if="indexing && !useBackgroundIndexing" class="mt-3 space-y-2">
            <div class="flex items-center justify-between text-sm">
              <span class="font-medium">{{ indexProgress }} of {{ selectedPaths.filter(p => !p.isFolder).length }} files</span>
              <span class="text-base-content/60">{{ Math.round(indexProgressPercent) }}%</span>
            </div>
            <progress
              class="progress progress-primary w-full"
              :value="indexProgressPercent"
              max="100"
            ></progress>
            <div v-if="currentIndexingFile" class="text-xs text-base-content/60 truncate">
              <span class="opacity-70">Current:</span> {{ currentIndexingFile }}
            </div>
          </div>

          <!-- File List -->
          <div class="overflow-x-auto max-h-48 overflow-y-auto border rounded">
            <table class="table table-xs table-zebra">
              <tbody>
                <tr v-for="(item, index) in selectedPaths" :key="index">
                  <td class="truncate max-w-xs font-medium" :title="item.name">
                    {{ item.name }}
                    <span v-if="item.isFolder" class="badge badge-sm badge-ghost ml-1">folder</span>
                  </td>
                  <td class="text-xs text-base-content/50 truncate max-w-xs" :title="item.path">
                    {{ item.path }}
                  </td>
                  <td class="w-8">
                    <button
                      class="btn btn-ghost btn-xs text-error"
                      @click="removePath(index)"
                      :disabled="indexing"
                      :aria-label="`Remove ${item.name} from selection`"
                    >
                      <X :size="14" />
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Index Success -->
        <div v-if="indexSuccess" class="alert alert-success alert-sm mt-3">
          <CheckCircle :size="20" />
          <div class="text-sm">
            <span v-if="indexResult.background">
              <strong>Indexing started in background.</strong>
              Track progress in the notification bell.
            </span>
            <span v-else>
              Indexed {{ indexResult.count }} file(s), {{ indexResult.chunks }} chunks
            </span>
          </div>
          <button class="btn btn-ghost btn-xs" @click="indexSuccess = false">Dismiss</button>
        </div>

        <!-- Index Error -->
        <div v-if="indexError" class="alert alert-error alert-sm mt-3">
          <XCircle :size="20" />
          <span class="text-sm">{{ indexError }}</span>
          <button class="btn btn-ghost btn-xs" @click="indexError = ''">Dismiss</button>
        </div>
      </div>
    </div>

    <!-- Document List Header -->
    <div class="flex justify-between items-center">
      <div class="flex items-center gap-4">
        <h3 class="text-lg font-bold">Your Sources</h3>
        <div v-if="selectedDocuments.length > 0" class="badge badge-primary">
          {{ selectedDocuments.length }} selected
        </div>
      </div>
      <div class="flex gap-2">
        <button
          v-if="selectedDocuments.length > 0"
          class="btn btn-sm btn-error"
          @click="confirmBulkDelete"
          :disabled="deleting"
        >
          <Trash2 :size="16" />
          Delete Selected
        </button>
        <button class="btn btn-sm btn-ghost" @click="loadDocuments">
          <RefreshCw :size="16" />
          Refresh
        </button>
      </div>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="flex justify-center py-12">
      <span class="loading loading-spinner loading-lg"></span>
    </div>

    <!-- Documents Table -->
    <div v-else-if="documents.length > 0" class="overflow-x-auto">
      <table class="table table-zebra">
        <thead>
          <tr>
            <th>
              <input
                type="checkbox"
                class="checkbox checkbox-sm"
                :checked="isAllSelected"
                @change="toggleSelectAll"
                :disabled="deleting"
                aria-label="Select all sources"
              />
            </th>
            <th scope="col">Filename</th>
            <th scope="col">Pages</th>
            <th scope="col">Chunks</th>
            <th scope="col">Source</th>
            <th scope="col">Indexed</th>
            <th scope="col">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="doc in documents" :key="doc.document_id" :class="{ 'bg-primary/10': isSelected(doc.document_id) }">
            <td>
              <input
                type="checkbox"
                class="checkbox checkbox-sm"
                :checked="isSelected(doc.document_id)"
                @change="toggleSelect(doc.document_id)"
                :disabled="deleting"
                :aria-label="`Select ${doc.filename}`"
              />
            </td>
            <td>
              <div class="flex items-center gap-3">
                <div class="flex items-center justify-center w-10 h-10 rounded-lg" :class="getFileIconClass(doc.filename)">
                  <component :is="getFileIcon(doc.filename)" :size="24" :class="getFileIconTextClass(doc.filename)" />
                </div>
                <div>
                  <div class="font-bold">{{ doc.filename }}</div>
                  <div class="text-sm opacity-50">{{ doc.document_id.substring(0, 8) }}...</div>
                </div>
              </div>
            </td>
            <td>{{ doc.total_pages }}</td>
            <td>{{ doc.total_chunks }}</td>
            <td>
              <span v-if="doc.source_type === 'local_reference'" class="badge badge-sm badge-ghost" title="Indexed in-place">
                local
              </span>
              <span v-else class="badge badge-sm badge-primary" title="Copied to library">
                library
              </span>
            </td>
            <td>{{ formatDate(doc.indexed_at) }}</td>
            <td>
              <div class="flex gap-2">
                <button
                  class="btn btn-ghost btn-xs"
                  @click="openChunks(doc)"
                  :disabled="deleting"
                  title="View indexed chunks"
                  :aria-label="`View indexed chunks for ${doc.filename}`"
                >
                  <FileSearch :size="16" />
                </button>
                <a
                  :href="`/documents/${doc.document_id}/pdf?collection_id=${collectionStore.currentCollectionId}`"
                  target="_blank"
                  rel="noopener"
                  class="btn btn-ghost btn-xs"
                  title="Open source document"
                  :aria-label="`Open source document ${doc.filename} in a new tab`"
                >
                  <Eye :size="16" />
                </a>
                <button
                  class="btn btn-ghost btn-xs text-error"
                  @click="confirmDelete(doc)"
                  :disabled="deleting"
                  title="Delete source"
                  :aria-label="`Delete source ${doc.filename}`"
                >
                  <Trash2 :size="16" />
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- No Documents -->
    <div v-else class="alert alert-info">
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" class="stroke-current shrink-0 w-6 h-6">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
      </svg>
      <span>No sources found. Select files above to get started!</span>
    </div>

    <!-- Error Alert -->
    <div v-if="error" class="alert alert-error">
      <XCircle :size="24" />
      <span>{{ error }}</span>
    </div>

    <!-- Delete Confirmation Modal -->
    <dialog ref="deleteModal" class="modal" aria-labelledby="docs-delete-title">
      <div class="modal-box">
        <h3 id="docs-delete-title" class="font-bold text-lg">Confirm Delete</h3>
        <p v-if="documentToDelete" class="py-4">
          Are you sure you want to delete <strong>{{ documentToDelete.filename }}</strong>?
          <span v-if="documentToDelete.source_type === 'local_reference'" class="block text-sm text-base-content/70 mt-2">
            Note: The original file will not be deleted, only the index entry.
          </span>
        </p>
        <p v-else class="py-4">
          Are you sure you want to delete <strong>{{ selectedDocuments.length }} source(s)</strong>?
        </p>
        <div v-if="!documentToDelete && selectedDocuments.length > 0" class="max-h-40 overflow-y-auto mb-4">
          <ul class="list-disc list-inside text-sm space-y-1">
            <li v-for="docId in selectedDocuments" :key="docId">
              {{ getDocumentName(docId) }}
            </li>
          </ul>
        </div>
        <div v-if="deleting && deleteProgress.total > 1" class="mb-3">
          <div class="flex justify-between text-sm text-base-content/70 mb-1">
            <span>Deleting {{ deleteProgress.current }} of {{ deleteProgress.total }}...</span>
          </div>
          <progress class="progress progress-error w-full" :value="deleteProgress.current" :max="deleteProgress.total"></progress>
        </div>
        <div class="modal-action">
          <button class="btn" @click="closeDeleteModal" :disabled="deleting">Cancel</button>
          <button class="btn btn-error" @click="documentToDelete ? deleteDocument() : deleteBulk()" :disabled="deleting">
            <span v-if="deleting" class="loading loading-spinner"></span>
            {{ deleting ? 'Deleting...' : 'Delete' }}
          </button>
        </div>
      </div>
      <form method="dialog" class="modal-backdrop">
        <button @click="closeDeleteModal">close</button>
      </form>
    </dialog>

    <!-- Recent Repos (code mode) -->
    <div v-if="isSourceCode && recentRepos.length > 0" class="card bg-base-200">
      <div class="card-body">
        <h3 class="card-title text-lg">
          <History :size="20" />
          Recently Indexed
        </h3>
        <div class="space-y-2">
          <div
            v-for="(repo, index) in recentRepos"
            :key="index"
            class="flex items-center gap-3 p-3 bg-base-100 rounded-lg hover:bg-base-300 cursor-pointer transition-colors"
            @click="selectRecentRepo(repo)"
          >
            <FolderOpen :size="18" class="text-primary flex-shrink-0" />
            <div class="flex-1 min-w-0">
              <div class="font-medium truncate">{{ repo.name }}</div>
              <div class="text-xs text-base-content/60 truncate">{{ repo.path }}</div>
            </div>
            <div class="text-xs text-base-content/50 flex-shrink-0">{{ formatRepoDate(repo.indexedAt) }}</div>
          </div>
        </div>
      </div>
    </div>

    <!-- Chunks Viewer Modal -->
    <dialog ref="chunksModal" class="modal" aria-labelledby="docs-chunks-title">
      <div class="modal-box max-w-6xl">
        <h3 id="docs-chunks-title" class="font-bold text-lg">Indexed Chunks</h3>
        <p v-if="chunkDocument" class="text-sm text-base-content/70 mt-1">
          {{ chunkDocument.filename }}
        </p>

        <div v-if="chunksLoading" class="flex justify-center py-10">
          <span class="loading loading-spinner loading-lg"></span>
        </div>

        <div v-else-if="chunksError" class="alert alert-error mt-4">
          <XCircle :size="20" />
          <span>{{ chunksError }}</span>
        </div>

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

          <div v-if="filteredChunks.length === 0" class="alert alert-info">
            <span>No chunks match the current filter.</span>
          </div>

          <div v-else class="space-y-3 max-h-[60vh] overflow-y-auto pr-1">
            <div
              v-for="chunk in filteredChunks"
              :key="chunk.chunk_id"
              class="card bg-base-200 border border-base-300"
            >
              <div class="card-body p-4">
                <div class="flex items-center justify-between flex-wrap gap-2">
                  <div class="font-mono text-xs text-base-content/60">
                    {{ chunk.chunk_id }}
                  </div>
                  <div class="flex gap-2">
                    <span class="badge badge-sm">p{{ chunk.page_number }} c{{ chunk.chunk_index }}</span>
                    <span v-if="chunk.extraction_method === 'ocr'" class="badge badge-warning badge-sm">OCR</span>
                    <span v-else-if="chunk.extraction_method === 'hybrid'" class="badge badge-info badge-sm">Hybrid</span>
                    <span v-if="chunkFieldCount(chunk) > 0" class="badge badge-success badge-sm">
                      {{ chunkFieldCount(chunk) }} fields
                    </span>
                  </div>
                </div>

                <div v-if="chunk.extracted_fields && Object.keys(chunk.extracted_fields).length > 0" class="overflow-x-auto">
                  <table class="table table-xs table-zebra">
                    <thead>
                      <tr>
                        <th>Field</th>
                        <th>Value</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="(value, field) in chunk.extracted_fields" :key="`${chunk.chunk_id}_${field}`">
                        <td class="font-semibold">{{ field }}</td>
                        <td class="break-all">{{ value }}</td>
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

        <div class="modal-action">
          <button class="btn" @click="closeChunksModal">Close</button>
        </div>
      </div>
      <form method="dialog" class="modal-backdrop">
        <button @click="closeChunksModal">close</button>
      </form>
    </dialog>

  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import axios from 'axios'
import { FileText, Eye, Trash2, RefreshCw, X, FolderOpen, FileCode, FileSearch, CheckCircle, XCircle, Code, History } from 'lucide-vue-next'
import { useCollectionStore } from '../stores/collectionStore'
import { useBackgroundJobsStore } from '../stores/backgroundJobsStore'

const emit = defineEmits(['document-deleted', 'background-job-started'])

const collectionStore = useCollectionStore()
const backgroundJobsStore = useBackgroundJobsStore()

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

// Server capability flags (hydrated on mount); native_file_picker=false → Docker/headless mode
const capabilities = ref({})

// Index state (using native file picker or browser upload)
const selectedPaths = ref([]) // Array of { path: string, name: string, isFolder?: boolean, size?: number, file?: File }
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
const deleteProgress = ref({ current: 0, total: 0 })
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

// Open native OS file picker (desktop only — headless uses label/input directly in template)
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

// Handle files selected via browser <input type="file"> (headless/Docker mode)
const handleBrowserFiles = (event) => {
  const files = Array.from(event.target.files || [])
  const existingNames = new Set(selectedPaths.value.map(p => p.name))
  for (const file of files) {
    if (!existingNames.has(file.name)) {
      selectedPaths.value.push({ path: '', name: file.name, size: file.size, file })
    }
  }
  // Reset so the same file can be re-selected if cleared
  event.target.value = ''
}

// Handle folder selected via browser <input webkitdirectory> (headless/Docker mode)
const handleBrowserFolder = (event) => {
  let files = Array.from(event.target.files || [])
  if (isSourceCode.value) {
    const enabledExts = new Set(
      fileExtensions.value.filter(e => e.enabled).flatMap(e => e.exts)
    )
    if (includeDocumentation.value) {
      ['.txt', '.md', '.json', '.jsonl'].forEach(e => enabledExts.add(e))
    }
    files = files.filter(f => {
      const ext = '.' + f.name.split('.').pop().toLowerCase()
      return enabledExts.has(ext)
    })
  }
  const existingNames = new Set(selectedPaths.value.map(p => p.name))
  for (const file of files) {
    const relativeName = file.webkitRelativePath || file.name
    if (!existingNames.has(relativeName)) {
      selectedPaths.value.push({ path: '', name: relativeName, size: file.size, file })
    }
  }
  event.target.value = ''
}

// Open native OS folder picker (desktop only — headless uses label/input directly in template)
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
        const existingPaths = new Set(selectedPaths.value.map(p => p.path))
        if (!existingPaths.has(folderPath)) {
          selectedPaths.value.push({
            path: folderPath,
            name: getFilename(folderPath),
            isFolder: true
          })
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

  // Separate browser-uploaded File objects from server-side path references.
  // In practice these modes are mutually exclusive (desktop vs headless/Docker),
  // but we handle both defensively.
  const browserItems = selectedPaths.value.filter(p => p.file)
  const pathItems = selectedPaths.value.filter(p => !p.file)
  const files = pathItems.filter(p => !p.isFolder)
  const filePaths = files.map(p => p.path)
  const folders = pathItems.filter(p => p.isFolder)

  indexing.value = true
  indexProgress.value = 0
  indexProgressPercent.value = 0
  currentIndexingFile.value = ''
  indexSuccess.value = false
  indexError.value = ''

  let successCount = 0
  let totalChunks = 0
  const errors = []
  let backgroundStarted = false

  try {
    // Browser-uploaded File objects (headless/Docker mode).
    // Uses upload-async + SSE stream so progress updates render while indexing runs.
    if (browserItems.length > 0) {
      try {
        const formData = new FormData()
        for (const item of browserItems) formData.append('files', item.file, item.name)
        formData.append('collection_id', collectionStore.currentCollectionId)

        const jobResp = await axios.post('/documents/upload-async', formData, {
          headers: { 'Content-Type': 'multipart/form-data' }
        })
        const jobId = jobResp.data.job_id

        await new Promise((resolve, reject) => {
          const es = new EventSource(`/documents/upload/${jobId}/stream`)

          es.addEventListener('file_start', (e) => {
            const data = JSON.parse(e.data)
            currentIndexingFile.value = data.current_file || ''
            indexProgressPercent.value = data.overall_percent || 0
            indexProgress.value = data.file_index || 0
          })

          es.addEventListener('phase_progress', (e) => {
            const data = JSON.parse(e.data)
            indexProgressPercent.value = data.overall_percent || 0
            if (data.current_file) currentIndexingFile.value = data.current_file
          })

          es.addEventListener('file_complete', (e) => {
            const data = JSON.parse(e.data)
            successCount++
            totalChunks += data.chunks_total || 0
            indexProgressPercent.value = data.overall_percent || 0
            indexProgress.value = data.file_index || 0
          })

          es.addEventListener('job_complete', () => {
            es.close()
            resolve()
          })

          es.addEventListener('job_error', (e) => {
            const data = JSON.parse(e.data)
            es.close()
            reject(new Error(data.error || 'Upload job failed'))
          })

          es.addEventListener('job_cancelled', () => {
            es.close()
            reject(new Error('Upload was cancelled'))
          })

          es.onerror = () => {
            es.close()
            reject(new Error('Connection lost during upload'))
          }
        })
      } catch (err) {
        errors.push(`Upload: ${err.message || err.response?.data?.detail || 'Upload failed'}`)
      }
    }

    // Path-based files
    if (filePaths.length > 0) {
      if (useBackgroundIndexing.value) {
        try {
          const response = await axios.post('/documents/index-local-async', {
            file_paths: filePaths,
            collection_id: collectionStore.currentCollectionId,
            copy_to_library: copyToLibrary.value
          })
          backgroundJobsStore.addUploadJob(response.data)
          emit('background-job-started')
          backgroundStarted = true
        } catch (err) {
          errors.push(`Files: ${err.response?.data?.detail || err.message}`)
        }
      } else {
        for (let i = 0; i < filePaths.length; i++) {
          const filename = getFilename(filePaths[i])
          currentIndexingFile.value = filename
          indexProgress.value = i + 1
          indexProgressPercent.value = (i / filePaths.length) * 100
          try {
            const response = await axios.post('/documents/index-local', {
              file_path: filePaths[i],
              collection_id: collectionStore.currentCollectionId,
              copy_to_library: copyToLibrary.value
            })
            successCount++
            totalChunks += response.data.total_chunks || 0
            indexProgressPercent.value = ((i + 1) / filePaths.length) * 100
          } catch (err) {
            errors.push(`${filename}: ${err.response?.data?.detail || err.message}`)
          }
        }
      }
    }

    // Folders are always indexed synchronously via the repo endpoint
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
        errors.push(`${folder.name}: ${err.response?.data?.detail || err.message}`)
      }
    }

  } finally {
    indexing.value = false
    currentIndexingFile.value = ''
    indexProgressPercent.value = 0
  }

  // Show outcome once, after all work completes
  if (backgroundStarted) {
    indexSuccess.value = true
    indexResult.value = { count: filePaths.length, chunks: 0, background: true }
    // Keep any folders in the list (they were not submitted)
    selectedPaths.value = selectedPaths.value.filter(p => p.isFolder)
  } else if (successCount > 0) {
    indexSuccess.value = true
    indexResult.value = { count: successCount, chunks: totalChunks }
    loadDocuments()
    emit('document-deleted')
    if (errors.length === 0) selectedPaths.value = []
  }

  if (errors.length > 0) {
    indexError.value = errors.join('; ')
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

const getDocumentName = (docId) => {
  const doc = documents.value.find(d => d.document_id === docId)
  return doc ? doc.filename : 'Unknown'
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

const formatDate = (dateString) => {
  if (!dateString) return 'N/A'
  const date = new Date(dateString)
  return date.toLocaleString()
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

const isCodeFile = (filename) => {
  const ext = '.' + filename.split('.').pop().toLowerCase()
  return CODE_EXTENSIONS.includes(ext)
}

const getFileIcon = (filename) => {
  return isCodeFile(filename) ? FileCode : FileText
}

const getFileIconClass = (filename) => {
  return isCodeFile(filename) ? 'bg-primary/20' : 'bg-error/20'
}

const getFileIconTextClass = (filename) => {
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
  deleteProgress.value = { current: 0, total: selectedDocuments.value.length }

  const toDelete = [...selectedDocuments.value]
  const failed = []

  try {
    const collectionId = collectionStore.currentCollectionId
    for (const docId of toDelete) {
      try {
        await axios.delete(`/documents/${docId}?collection_id=${collectionId}`)
      } catch {
        failed.push(docId)
      }
      deleteProgress.value.current++
    }

    const deletedIds = new Set(toDelete.filter(id => !failed.includes(id)))
    documents.value = documents.value.filter(doc => !deletedIds.has(doc.document_id))
    selectedDocuments.value = failed

    if (failed.length > 0) {
      error.value = `${failed.length} file(s) could not be deleted.`
    }

    emit('document-deleted')
    await loadDocuments()
  } catch (err) {
    error.value = err.response?.data?.detail || 'Failed to delete sources'
  } finally {
    deleting.value = false
    deleteProgress.value = { current: 0, total: 0 }
    closeDeleteModal()
  }
}

// Watch for collection changes
watch(() => collectionStore.currentCollectionId, () => {
  loadDocuments()
  selectedDocuments.value = []
})

// Watch for completed background uploads to reload documents
watch(() => backgroundJobsStore.uploadJobs, (jobs) => {
  const completedJob = jobs.find(j => j.status === 'completed' && !j.reloaded)
  if (completedJob) {
    completedJob.reloaded = true
    loadDocuments()
    emit('document-deleted')
  }
}, { deep: true })

// Warn user before leaving page during indexing
const beforeUnloadHandler = (e) => {
  if (indexing.value) {
    e.preventDefault()
    e.returnValue = 'Indexing in progress. Are you sure you want to leave?'
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

const formatRepoDate = (timestamp) => {
  const diff = Date.now() - timestamp
  if (diff < 60000) return 'just now'
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`
  return new Date(timestamp).toLocaleDateString()
}

onMounted(async () => {
  loadDocuments()
  loadRecentRepos()
  window.addEventListener('beforeunload', beforeUnloadHandler)
  try {
    const resp = await axios.get('/api/capabilities')
    capabilities.value = resp.data
  } catch {
    // If check fails, leave capabilities empty — native picker remains the default
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', beforeUnloadHandler)
})
</script>
