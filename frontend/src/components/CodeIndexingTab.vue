<template>
  <div class="space-y-6">
    <div class="flex items-center justify-between">
      <h2 class="text-2xl font-bold">Code Repository Indexing</h2>
      <div class="badge badge-info gap-1">
        <Code :size="14" />
        Symbol-Aware Chunking
      </div>
    </div>

    <p class="text-base-content/70">
      Index source code repositories with intelligent chunking that preserves function and class boundaries.
      Supports Python, JavaScript, TypeScript, C#, Java, Go, Rust, C/C++, and many more.
    </p>

    <!-- Repository Selection -->
    <div class="card bg-base-200">
      <div class="card-body">
        <h3 class="card-title text-lg">
          <FolderOpen :size="20" />
          Select Repository
        </h3>

        <div class="flex flex-wrap gap-3">
          <button
            class="btn btn-primary gap-2"
            @click="selectFolder"
            :disabled="indexing"
          >
            <FolderOpen :size="18" />
            Select Folder
          </button>

          <button
            class="btn btn-outline gap-2"
            @click="selectFiles"
            :disabled="indexing"
          >
            <FileCode :size="18" />
            Select Files
          </button>
        </div>

        <!-- Selected Path -->
        <div v-if="selectedPath" class="mt-4">
          <div class="flex items-center gap-2 p-3 bg-base-100 rounded-lg">
            <FolderOpen v-if="isFolder" :size="20" class="text-primary" />
            <FileCode v-else :size="20" class="text-secondary" />
            <span class="flex-1 font-mono text-sm truncate">{{ selectedPath }}</span>
            <button class="btn btn-ghost btn-xs" @click="clearSelection">
              <X :size="16" />
            </button>
          </div>
        </div>

        <!-- Selected Files List -->
        <div v-if="selectedFiles.length > 0" class="mt-4 space-y-2">
          <div class="text-sm text-base-content/70">{{ selectedFiles.length }} files selected:</div>
          <div class="max-h-40 overflow-y-auto space-y-1">
            <div
              v-for="(file, index) in selectedFiles"
              :key="index"
              class="flex items-center gap-2 p-2 bg-base-100 rounded text-sm"
            >
              <FileCode :size="14" class="text-secondary flex-shrink-0" />
              <span class="truncate">{{ file.name }}</span>
              <button class="btn btn-ghost btn-xs ml-auto" @click="removeFile(index)">
                <X :size="14" />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Indexing Options -->
    <div class="card bg-base-200">
      <div class="card-body">
        <h3 class="card-title text-lg">
          <Settings :size="20" />
          Options
        </h3>

        <div class="form-control">
          <label class="label cursor-pointer justify-start gap-3">
            <input
              type="checkbox"
              v-model="recursive"
              class="checkbox checkbox-primary"
              :disabled="!isFolder"
            />
            <div>
              <span class="label-text font-medium">Include subdirectories</span>
              <p class="text-xs text-base-content/60">Recursively index all code files in subfolders</p>
            </div>
          </label>
        </div>

        <div class="form-control">
          <label class="label cursor-pointer justify-start gap-3">
            <input
              type="checkbox"
              v-model="includeDocumentation"
              class="checkbox checkbox-primary"
            />
            <div>
              <span class="label-text font-medium">Include documentation files</span>
              <p class="text-xs text-base-content/60">Also index .txt, .md, and .json files found in the repository</p>
            </div>
          </label>
        </div>

        <div class="form-control">
          <label class="label cursor-pointer justify-start gap-3">
            <input
              type="checkbox"
              v-model="copyToLibrary"
              class="checkbox checkbox-primary"
            />
            <div>
              <span class="label-text font-medium">Copy files to library</span>
              <p class="text-xs text-base-content/60">Copy files to the server. If unchecked, files are indexed in-place (requires local access)</p>
            </div>
          </label>
        </div>

        <!-- File Type Filters -->
        <div class="mt-4">
          <label class="label">
            <span class="label-text font-medium">File types to index</span>
          </label>
          <div class="flex flex-wrap gap-2">
            <label
              v-for="ext in fileExtensions"
              :key="ext.value"
              class="flex items-center gap-2 px-3 py-1.5 rounded-lg cursor-pointer transition-colors"
              :class="ext.enabled ? 'bg-primary/20 border border-primary' : 'bg-base-300 hover:bg-base-100'"
            >
              <input
                type="checkbox"
                v-model="ext.enabled"
                class="checkbox checkbox-xs checkbox-primary"
              />
              <span class="text-sm">{{ ext.label }}</span>
            </label>
          </div>
        </div>
      </div>
    </div>

    <!-- Supported Languages Info -->
    <div class="collapse collapse-arrow bg-base-200">
      <input type="checkbox" />
      <div class="collapse-title font-medium">
        Supported Languages & File Types
      </div>
      <div class="collapse-content">
        <div class="grid grid-cols-1 md:grid-cols-3 gap-4 pt-2">
          <div>
            <h4 class="font-semibold text-primary">Python</h4>
            <p class="text-sm text-base-content/70">.py, .pyw, .pyi</p>
          </div>
          <div>
            <h4 class="font-semibold text-warning">JavaScript / TypeScript</h4>
            <p class="text-sm text-base-content/70">.js, .jsx, .ts, .tsx, .mjs</p>
          </div>
          <div>
            <h4 class="font-semibold text-secondary">C# / Java</h4>
            <p class="text-sm text-base-content/70">.cs, .java</p>
          </div>
          <div>
            <h4 class="font-semibold text-info">Go / Rust</h4>
            <p class="text-sm text-base-content/70">.go, .rs</p>
          </div>
          <div>
            <h4 class="font-semibold text-accent">C / C++</h4>
            <p class="text-sm text-base-content/70">.c, .h, .cpp, .hpp, .cc</p>
          </div>
          <div>
            <h4 class="font-semibold text-error">PHP / Ruby</h4>
            <p class="text-sm text-base-content/70">.php, .rb, .rake</p>
          </div>
          <div>
            <h4 class="font-semibold text-success">Swift / Kotlin / Scala</h4>
            <p class="text-sm text-base-content/70">.swift, .kt, .scala</p>
          </div>
          <div>
            <h4 class="font-semibold text-primary">Pascal / Delphi</h4>
            <p class="text-sm text-base-content/70">.pas, .dpr, .dpk, .pp, .inc</p>
          </div>
          <div>
            <h4 class="font-semibold text-base-content/60">Documentation</h4>
            <p class="text-sm text-base-content/70">.txt, .md, .json, .jsonl</p>
          </div>
        </div>
        <div class="mt-4 p-3 bg-base-100 rounded-lg">
          <p class="text-sm text-base-content/70">
            <strong>Symbol-aware chunking</strong> preserves function, method, class, and interface boundaries
            for better RAG performance. Code is chunked at ~1500 characters with 200 character overlap to maintain context.
          </p>
        </div>
      </div>
    </div>

    <!-- Index Button -->
    <div class="flex gap-3">
      <button
        class="btn btn-primary btn-lg gap-2"
        @click="indexRepository"
        :disabled="!canIndex || indexing"
      >
        <span v-if="indexing" class="loading loading-spinner"></span>
        <Database v-else :size="20" />
        {{ indexing ? 'Indexing...' : 'Index Repository' }}
      </button>
    </div>

    <!-- Progress -->
    <div v-if="indexing" class="card bg-base-200">
      <div class="card-body">
        <div class="flex items-center gap-3">
          <span class="loading loading-spinner loading-md text-primary"></span>
          <div class="flex-1">
            <div class="font-medium">{{ progressMessage }}</div>
            <div v-if="currentFile" class="text-sm text-base-content/70 truncate">
              {{ currentFile }}
            </div>
          </div>
        </div>
        <progress
          v-if="progress > 0"
          class="progress progress-primary w-full mt-2"
          :value="progress"
          max="100"
        ></progress>
      </div>
    </div>

    <!-- Success Message -->
    <div v-if="indexSuccess" class="alert alert-success">
      <CheckCircle :size="20" />
      <div>
        <div class="font-bold">Indexing Complete!</div>
        <div class="text-sm">
          Indexed {{ result.files_indexed }} files with {{ result.total_chunks }} chunks
          <span v-if="result.skipped_files > 0">
            ({{ result.skipped_files }} files skipped)
          </span>
        </div>
      </div>
      <button class="btn btn-ghost btn-sm" @click="indexSuccess = false">
        <X :size="16" />
      </button>
    </div>

    <!-- Error Message -->
    <div v-if="error" class="alert alert-error">
      <XCircle :size="20" />
      <div>
        <div class="font-bold">Indexing Failed</div>
        <div class="text-sm">{{ error }}</div>
      </div>
      <button class="btn btn-ghost btn-sm" @click="error = ''">
        <X :size="16" />
      </button>
    </div>

    <!-- Recent Repositories -->
    <div v-if="recentRepos.length > 0" class="card bg-base-200">
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
            <FolderOpen :size="18" class="text-primary" />
            <div class="flex-1 min-w-0">
              <div class="font-medium truncate">{{ repo.name }}</div>
              <div class="text-xs text-base-content/60 truncate">{{ repo.path }}</div>
            </div>
            <div class="text-xs text-base-content/50">
              {{ formatDate(repo.indexedAt) }}
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import axios from 'axios'
import {
  Code, FolderOpen, FileCode, Settings, Database, X,
  CheckCircle, XCircle, History
} from 'lucide-vue-next'
import { useCollectionStore } from '../stores/collectionStore'

const emit = defineEmits(['indexed'])

const collectionStore = useCollectionStore()

// Selection state
const selectedPath = ref('')
const selectedFiles = ref([])
const isFolder = ref(true)

// Options
const recursive = ref(true)
const includeDocumentation = ref(true)
const copyToLibrary = ref(true)

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

// Progress state
const indexing = ref(false)
const progress = ref(0)
const progressMessage = ref('')
const currentFile = ref('')

// Result state
const indexSuccess = ref(false)
const error = ref('')
const result = ref({ files_indexed: 0, total_chunks: 0, skipped_files: 0 })

// Recent repos from localStorage
const recentRepos = ref([])

const canIndex = computed(() => {
  return selectedPath.value || selectedFiles.value.length > 0
})

const selectFolder = async () => {
  try {
    error.value = ''
    // First open folder picker
    const pickerResponse = await axios.post('/api/folder-picker')
    if (!pickerResponse.data.path) {
      return // User cancelled
    }

    const folderPath = pickerResponse.data.path
    selectedPath.value = folderPath

    // Build file extensions filter
    const enabledExts = fileExtensions.value
      .filter(e => e.enabled)
      .flatMap(e => e.exts)

    if (includeDocumentation.value) {
      enabledExts.push('.txt', '.md', '.json', '.jsonl')
    }

    // Scan folder for matching files
    progressMessage.value = 'Scanning folder...'
    const scanResponse = await axios.post('/api/scan-folder', {
      path: folderPath,
      recursive: recursive.value,
      file_extensions: enabledExts.length > 0 ? enabledExts : undefined
    })

    if (scanResponse.data.files && scanResponse.data.files.length > 0) {
      // Populate selectedFiles like file picker does
      selectedFiles.value = scanResponse.data.files.map(f => ({
        path: f.path,
        name: f.relative_path || f.name  // Show relative path for context
      }))
      isFolder.value = true
    } else {
      error.value = 'No matching files found in the selected folder'
      selectedPath.value = ''
    }
    progressMessage.value = ''
  } catch (err) {
    console.error('Folder picker error:', err)
    error.value = err.response?.data?.detail || 'Failed to open folder picker'
    progressMessage.value = ''
  }
}

const selectFiles = async () => {
  try {
    error.value = ''
    const response = await axios.post('/api/file-picker', null, {
      params: { multiple: true }
    })
    if (response.data.paths && response.data.paths.length > 0) {
      selectedPath.value = ''
      selectedFiles.value = response.data.paths.map(p => ({
        path: p,
        name: p.split(/[/\\]/).pop()
      }))
      isFolder.value = false
    }
  } catch (err) {
    console.error('File picker error:', err)
    error.value = err.response?.data?.detail || 'Failed to open file picker'
  }
}

const clearSelection = () => {
  selectedPath.value = ''
  selectedFiles.value = []
  indexSuccess.value = false
  error.value = ''
}

const removeFile = (index) => {
  selectedFiles.value.splice(index, 1)
}

const selectRecentRepo = async (repo) => {
  // When selecting a recent repo, scan it to get the file list
  selectedPath.value = repo.path
  isFolder.value = true

  try {
    // Build file extensions filter
    const enabledExts = fileExtensions.value
      .filter(e => e.enabled)
      .flatMap(e => e.exts)

    if (includeDocumentation.value) {
      enabledExts.push('.txt', '.md', '.json', '.jsonl')
    }

    progressMessage.value = 'Scanning folder...'
    const scanResponse = await axios.post('/api/scan-folder', {
      path: repo.path,
      recursive: recursive.value,
      file_extensions: enabledExts.length > 0 ? enabledExts : undefined
    })

    if (scanResponse.data.files && scanResponse.data.files.length > 0) {
      selectedFiles.value = scanResponse.data.files.map(f => ({
        path: f.path,
        name: f.relative_path || f.name
      }))
    } else {
      error.value = 'No matching files found in the selected folder'
      selectedPath.value = ''
      selectedFiles.value = []
    }
    progressMessage.value = ''
  } catch (err) {
    console.error('Folder scan error:', err)
    error.value = err.response?.data?.detail || 'Failed to scan folder'
    progressMessage.value = ''
  }
}

const indexRepository = async () => {
  if (!canIndex.value || selectedFiles.value.length === 0) return

  indexing.value = true
  progress.value = 0
  progressMessage.value = 'Preparing to index...'
  currentFile.value = ''
  indexSuccess.value = false
  error.value = ''

  try {
    // Index all selected files (works the same for folder or file selection)
    let filesIndexed = 0
    let totalChunks = 0
    const filePaths = selectedFiles.value.map(f => f.path)

    for (let i = 0; i < filePaths.length; i++) {
      const filePath = filePaths[i]
      const fileName = selectedFiles.value[i].name
      currentFile.value = fileName
      progressMessage.value = `Indexing ${i + 1} of ${filePaths.length}...`
      progress.value = ((i + 1) / filePaths.length) * 100

      try {
        const response = await axios.post('/documents/index-local', {
          file_path: filePath,
          collection_id: collectionStore.currentCollectionId,
          copy_to_library: copyToLibrary.value
        })
        filesIndexed++
        totalChunks += response.data.total_chunks || 0
      } catch (err) {
        console.error(`Failed to index ${filePath}:`, err)
      }
    }

    result.value = {
      files_indexed: filesIndexed,
      total_chunks: totalChunks,
      skipped_files: filePaths.length - filesIndexed
    }

    // Save folder to recent repos if this was a folder selection
    if (isFolder.value && selectedPath.value) {
      saveRecentRepo(selectedPath.value)
    }

    indexSuccess.value = true
    clearSelection()
    emit('indexed')

  } catch (err) {
    console.error('Indexing error:', err)
    error.value = err.response?.data?.detail || err.message || 'Failed to index files'
  } finally {
    indexing.value = false
    progressMessage.value = ''
    currentFile.value = ''
  }
}

const saveRecentRepo = (path) => {
  const repos = JSON.parse(localStorage.getItem('recentCodeRepos') || '[]')
  const name = path.split(/[/\\]/).pop() || path

  // Remove if already exists
  const filtered = repos.filter(r => r.path !== path)

  // Add to front
  filtered.unshift({
    path,
    name,
    indexedAt: Date.now()
  })

  // Keep only last 5
  const trimmed = filtered.slice(0, 5)
  localStorage.setItem('recentCodeRepos', JSON.stringify(trimmed))
  recentRepos.value = trimmed
}

const loadRecentRepos = () => {
  try {
    recentRepos.value = JSON.parse(localStorage.getItem('recentCodeRepos') || '[]')
  } catch {
    recentRepos.value = []
  }
}

const formatDate = (timestamp) => {
  const date = new Date(timestamp)
  const now = new Date()
  const diff = now - date

  if (diff < 60000) return 'just now'
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`
  return date.toLocaleDateString()
}

onMounted(() => {
  loadRecentRepos()
})
</script>
