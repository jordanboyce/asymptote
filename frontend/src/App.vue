<template>
  <div class="h-screen flex flex-col overflow-hidden bg-base-200" :class="{ 'select-none cursor-col-resize': isResizing }">

    <!-- ── Header ── -->
    <header class="flex items-center gap-3 px-4 h-12 bg-base-100 border-b border-base-300 flex-shrink-0 z-50">

      <!-- Logo + title -->
      <img src="/icon_black.svg" alt="Asymptote" class="logo-header h-6 w-6 flex-shrink-0">
      <span class="font-bold text-sm tracking-tight">Asymptote</span>

      <div class="w-px h-5 bg-base-300 mx-0.5 flex-shrink-0"></div>

      <!-- Collection selector -->
      <div class="dropdown dropdown-bottom">
        <label tabindex="0" class="btn btn-xs btn-ghost gap-1.5 normal-case font-normal h-8 min-h-0">
          <div
            class="w-2 h-2 rounded-full flex-shrink-0"
            :style="{ backgroundColor: collectionStore.currentCollection?.color || '#3b82f6' }"
          ></div>
          <span class="max-w-36 truncate text-sm">{{ collectionStore.currentCollection?.name || 'Default' }}</span>
          <ChevronDown :size="13" />
        </label>
        <ul tabindex="0" class="dropdown-content z-[100] menu p-2 shadow-lg bg-base-100 border border-base-300 rounded-box w-64 text-base-content">
          <li class="menu-title"><span>Collections</span></li>
          <li v-for="collection in collectionStore.sortedCollections" :key="collection.id">
            <a
              @click="selectCollection(collection.id)"
              :class="{ 'active': collection.id === collectionStore.currentCollectionId }"
              class="group"
            >
              <div class="w-2.5 h-2.5 rounded-full flex-shrink-0" :style="{ backgroundColor: collection.color }"></div>
              <span class="flex-1 truncate">{{ collection.name }}</span>
              <span v-if="collection.shared" class="badge badge-xs badge-outline badge-info">shared</span>
              <span class="badge badge-sm badge-ghost">{{ collection.document_count || 0 }}</span>
              <button
                v-if="collectionStore.multiUser && collection.permission === 'owner'"
                @click.stop="openShareModal(collection)"
                class="btn btn-ghost btn-xs opacity-0 group-hover:opacity-100 transition-opacity p-1"
                title="Share collection"
              >
                <Share2 :size="13" />
              </button>
              <button
                @click.stop="openEditCollectionModal(collection)"
                class="btn btn-ghost btn-xs opacity-0 group-hover:opacity-100 transition-opacity p-1"
                title="Edit collection"
                :disabled="collection.shared && collection.permission === 'read'"
              >
                <Pencil :size="13" />
              </button>
            </a>
          </li>
          <div class="divider my-1"></div>
          <li>
            <a @click="openCreateCollectionModal" class="text-primary">
              <Plus :size="14" />
              New Collection
            </a>
          </li>
        </ul>
      </div>

      <!-- Spacer -->
      <div class="flex-1"></div>

      <!-- Stats (compact, hidden on small screens) -->
      <div class="hidden lg:flex items-center gap-3 text-xs text-base-content/50 mr-2">
        <span>{{ stats.documents }} sources</span>
        <span class="w-px h-3 bg-base-300"></span>
        <span>{{ stats.pages }} pages</span>
        <span class="w-px h-3 bg-base-300"></span>
        <span>{{ stats.chunks }} chunks</span>
      </div>

      <!-- User identity (multi-user mode) -->
      <div v-if="userStore.isMultiUser" class="hidden md:flex items-center gap-1.5 text-xs text-base-content/50 mr-1">
        <Users :size="12" />
        <span class="max-w-24 truncate">{{ userStore.displayName }}</span>
      </div>

      <!-- Jobs button -->
      <button
        class="btn btn-ghost btn-circle btn-sm relative"
        @click="showJobsDrawer = true"
        title="Background Jobs"
      >
        <PanelRightOpen :size="16" :class="{ 'animate-pulse text-warning': backgroundJobsStore.hasActiveJobs }" />
        <span
          v-if="backgroundJobsStore.activeJobCount > 0"
          class="absolute -top-0.5 -right-0.5 badge badge-xs badge-warning"
        >{{ backgroundJobsStore.activeJobCount }}</span>
      </button>

      <!-- Settings button -->
      <button
        class="btn btn-ghost btn-circle btn-sm"
        :class="{ 'bg-base-300': activeTab === 'settings' }"
        @click="activeTab = 'settings'"
        title="Settings"
      >
        <Settings :size="16" />
      </button>
    </header>

    <!-- ── Body ── -->
    <div class="flex flex-1 overflow-hidden min-h-0">

      <!-- Left sidebar: sources (resizable) -->
      <aside
        class="flex-shrink-0 bg-base-100 overflow-hidden flex flex-col relative"
        :class="isResizing ? '' : 'transition-all duration-200 ease-in-out'"
        :style="{ width: sourcesSidebarOpen ? sidebarWidth + 'px' : '0px' }"
      >
        <!-- Sidebar content fixed to sidebarWidth so it doesn't shrink during close animation -->
        <div class="h-full flex flex-col" :style="{ width: sidebarWidth + 'px' }">
          <SourcesSidebar
            @document-deleted="handleDocumentDeleted"
            @background-job-started="showJobsDrawer = true"
            @close="sourcesSidebarOpen = false"
          />
        </div>

        <!-- Drag handle -->
        <div
          v-if="sourcesSidebarOpen"
          class="absolute right-0 top-0 h-full w-1 cursor-col-resize group z-10 hover:bg-primary/40 transition-colors"
          :class="isResizing ? 'bg-primary/60' : ''"
          @mousedown.prevent="startResize"
        >
          <!-- Visual grip dots -->
          <div class="absolute inset-y-0 right-0 flex items-center justify-center w-1">
            <div class="flex flex-col gap-1 opacity-0 group-hover:opacity-60 transition-opacity">
              <div class="w-1 h-1 rounded-full bg-base-content"></div>
              <div class="w-1 h-1 rounded-full bg-base-content"></div>
              <div class="w-1 h-1 rounded-full bg-base-content"></div>
            </div>
          </div>
        </div>
      </aside>

      <!-- Main panel -->
      <main class="flex-1 flex flex-col overflow-hidden min-w-0">

        <!-- Tab bar -->
        <div class="flex items-center gap-0.5 px-3 h-10 bg-base-100 border-b border-base-300 flex-shrink-0">
          <!-- Sources toggle -->
          <button
            class="btn btn-xs btn-ghost gap-1 mr-2"
            :class="sourcesSidebarOpen ? 'btn-active' : ''"
            @click="sourcesSidebarOpen = !sourcesSidebarOpen"
            title="Toggle sources panel"
          >
            <Library :size="14" />
            <span class="hidden md:inline text-xs">Sources</span>
          </button>
          <div class="w-px h-4 bg-base-300 mx-1 flex-shrink-0"></div>

          <!-- Tabs -->
          <button
            v-for="tab in tabs"
            :key="tab.id"
            class="btn btn-xs btn-ghost gap-1.5 rounded-md transition-all"
            :class="activeTab === tab.id ? 'bg-base-200 font-semibold' : 'font-normal'"
            @click="activeTab = tab.id"
          >
            <component :is="tab.icon" :size="14" />
            <span class="hidden sm:inline">{{ tab.label }}</span>
          </button>
        </div>

        <!-- Tab content -->
        <div
          class="flex-1 min-h-0"
          :class="activeTab === 'chat' ? 'overflow-hidden p-0' : 'overflow-y-auto'"
        >
          <!-- Chat gets full height, no padding wrapper -->
          <div v-if="activeTab === 'chat'" class="h-full p-4">
            <ChatTab :chunk-count="stats.chunks" @switch-tab="switchTab" />
          </div>

          <!-- All other tabs: padded scroll container -->
          <div v-else class="px-6 py-6">
            <SearchTab v-if="activeTab === 'search'" :chunk-count="stats.chunks" @stats-updated="loadStats" @switch-tab="switchTab" />
            <OCRPlaygroundTab v-if="activeTab === 'ocr'" />
            <TokenizerTab v-if="activeTab === 'tokenizer'" />
            <SettingsTab v-if="activeTab === 'settings'" @data-cleared="handleDataCleared" @stats-updated="loadStats" @switch-tab="switchTab" />
          </div>
        </div>

      </main>
    </div>

    <!-- Create Collection Modal -->
    <dialog class="modal" :class="{ 'modal-open': showCollectionModal }">
      <div class="modal-box">
        <h3 class="font-bold text-lg mb-4">Create New Collection</h3>

        <div class="form-control w-full mb-4">
          <label class="label">
            <span class="label-text">Collection Name</span>
          </label>
          <input
            v-model="newCollectionName"
            type="text"
            placeholder="e.g., Research Papers"
            class="input input-bordered w-full"
            @keyup.enter="createCollection"
          />
        </div>

        <div class="form-control w-full mb-4">
          <label class="label">
            <span class="label-text">Description (optional)</span>
          </label>
          <textarea
            v-model="newCollectionDescription"
            class="textarea textarea-bordered"
            placeholder="What kind of sources will this collection contain?"
          ></textarea>
        </div>

        <div class="form-control w-full mb-4">
          <label class="label">
            <span class="label-text">Color</span>
          </label>
          <div class="flex items-center gap-3">
            <input
              v-model="newCollectionColor"
              type="color"
              class="w-12 h-12 rounded cursor-pointer border-2 border-base-300"
            />
            <div class="flex gap-2">
              <button
                v-for="color in ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899']"
                :key="color"
                class="w-8 h-8 rounded cursor-pointer border-2"
                :class="newCollectionColor === color ? 'border-base-content' : 'border-transparent'"
                :style="{ backgroundColor: color }"
                @click="newCollectionColor = color"
              ></button>
            </div>
          </div>
        </div>

        <div class="modal-action">
          <button class="btn btn-ghost" @click="showCollectionModal = false" :disabled="creatingCollection">
            Cancel
          </button>
          <button
            class="btn btn-primary"
            @click="createCollection"
            :disabled="!newCollectionName.trim() || creatingCollection"
          >
            <span v-if="creatingCollection" class="loading loading-spinner loading-sm"></span>
            Create Collection
          </button>
        </div>
      </div>
      <form method="dialog" class="modal-backdrop">
        <button @click="showCollectionModal = false">close</button>
      </form>
    </dialog>

    <!-- Edit Collection Modal -->
    <dialog class="modal" :class="{ 'modal-open': showEditModal }">
      <div class="modal-box">
        <h3 class="font-bold text-lg mb-4">Edit Collection</h3>

        <div class="form-control w-full mb-4">
          <label class="label">
            <span class="label-text">Collection Name</span>
          </label>
          <input
            v-model="editCollectionName"
            type="text"
            placeholder="e.g., Research Papers"
            class="input input-bordered w-full"
            :disabled="editingCollectionId === 'default'"
          />
          <label v-if="editingCollectionId === 'default'" class="label">
            <span class="label-text-alt text-warning">Default collection name cannot be changed</span>
          </label>
        </div>

        <div class="form-control w-full mb-4">
          <label class="label">
            <span class="label-text">Description</span>
          </label>
          <textarea
            v-model="editCollectionDescription"
            class="textarea textarea-bordered"
            placeholder="What kind of sources does this collection contain?"
          ></textarea>
        </div>

        <div class="form-control w-full mb-4">
          <label class="label">
            <span class="label-text">Color</span>
          </label>
          <div class="flex items-center gap-3">
            <input
              v-model="editCollectionColor"
              type="color"
              class="w-12 h-12 rounded cursor-pointer border-2 border-base-300"
            />
            <div class="flex gap-2">
              <button
                v-for="color in ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899']"
                :key="color"
                class="w-8 h-8 rounded cursor-pointer border-2"
                :class="editCollectionColor === color ? 'border-base-content' : 'border-transparent'"
                :style="{ backgroundColor: color }"
                @click="editCollectionColor = color"
              ></button>
            </div>
          </div>
        </div>

        <div class="modal-action justify-between">
          <button
            v-if="editingCollectionId !== 'default'"
            class="btn btn-error btn-outline"
            @click="confirmDeleteCollection"
            :disabled="updatingCollection"
          >
            <Trash2 :size="16" />
            Delete
          </button>
          <div v-else></div>
          <div class="flex gap-2">
            <button class="btn btn-ghost" @click="showEditModal = false" :disabled="updatingCollection">
              Cancel
            </button>
            <button
              class="btn btn-primary"
              @click="updateCollection"
              :disabled="!editCollectionName.trim() || updatingCollection"
            >
              <span v-if="updatingCollection" class="loading loading-spinner loading-sm"></span>
              Save Changes
            </button>
          </div>
        </div>
      </div>
      <form method="dialog" class="modal-backdrop">
        <button @click="showEditModal = false">close</button>
      </form>
    </dialog>

    <!-- Delete Collection Confirmation Modal -->
    <dialog class="modal" :class="{ 'modal-open': showDeleteConfirmModal }">
      <div class="modal-box">
        <h3 class="font-bold text-lg text-error mb-4">Delete Collection</h3>
        <p class="mb-2">Are you sure you want to delete <strong>{{ editCollectionName }}</strong>?</p>

        <div class="alert alert-warning my-4">
          <svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-6 w-6" fill="none" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
          </svg>
          <div>
            <div class="font-bold">This will permanently delete:</div>
            <ul class="list-disc list-inside text-sm mt-1">
              <li>All uploaded sources and files</li>
              <li>All vector indexes and embeddings</li>
              <li>All search history for this collection</li>
            </ul>
            <div class="text-sm font-semibold mt-2">This action cannot be undone.</div>
          </div>
        </div>

        <!-- Error display -->
        <div v-if="deleteError" class="alert alert-error mb-4">
          <svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-6 w-6" fill="none" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <span>{{ deleteError }}</span>
        </div>

        <div class="modal-action">
          <button class="btn btn-ghost" @click="closeDeleteModal" :disabled="deletingCollection">
            Cancel
          </button>
          <button
            class="btn btn-error"
            @click="deleteCollection"
            :disabled="deletingCollection"
          >
            <span v-if="deletingCollection" class="loading loading-spinner loading-sm"></span>
            Delete Collection
          </button>
        </div>
      </div>
      <form method="dialog" class="modal-backdrop">
        <button @click="closeDeleteModal">close</button>
      </form>
    </dialog>

    <!-- Share Modal -->
    <ShareModal
      :visible="showShareModal"
      :collection-id="shareCollectionId"
      :collection-name="shareCollectionName"
      @close="showShareModal = false"
      @shared="handleShared"
    />

    <!-- Background Jobs Sidebar Drawer -->
    <div
      v-if="showJobsDrawer"
      class="fixed inset-0 z-[200]"
      @click.self="showJobsDrawer = false"
    >
      <!-- Backdrop -->
      <div class="absolute inset-0 bg-black/30" @click="showJobsDrawer = false"></div>

      <!-- Drawer Panel -->
      <div class="absolute right-0 top-0 h-full w-96 max-w-[90vw] bg-base-100 shadow-2xl flex flex-col">
        <!-- Header -->
        <div class="flex items-center justify-between p-4 border-b border-base-300">
          <h3 class="text-lg font-bold">Background Jobs</h3>
          <button class="btn btn-ghost btn-sm btn-circle" @click="showJobsDrawer = false">
            <X :size="20" />
          </button>
        </div>

        <!-- Content -->
        <div class="flex-1 overflow-y-auto p-4">
          <div v-if="backgroundJobsStore.allJobs.length === 0" class="text-center py-12 text-base-content/50">
            <Bell :size="48" class="mx-auto mb-4 opacity-30" />
            <p>No background jobs</p>
            <p class="text-sm mt-1">Large file uploads will appear here</p>
          </div>

          <div v-else class="space-y-4">
            <div
              v-for="job in backgroundJobsStore.allJobs"
              :key="`${job.type}-${job.id}`"
              class="card bg-base-200"
            >
              <div class="card-body p-4">
                <!-- Job Header -->
                <div class="flex items-center justify-between">
                  <div class="flex items-center gap-2">
                    <Loader2
                      v-if="job.status === 'pending' || job.status === 'running'"
                      :size="18"
                      class="animate-spin text-primary"
                    />
                    <CheckCircle
                      v-else-if="job.status === 'completed'"
                      :size="18"
                      class="text-success"
                    />
                    <XCircle
                      v-else-if="job.status === 'failed' || job.status === 'cancelled'"
                      :size="18"
                      class="text-error"
                    />
                    <span class="font-semibold capitalize">
                      {{ job.type === 'index' ? 'Indexing' : job.type === 'upload' ? 'Upload' : job.type }}
                    </span>
                    <span class="badge badge-sm" :class="{
                      'badge-warning': job.status === 'pending',
                      'badge-info': job.status === 'running',
                      'badge-success': job.status === 'completed',
                      'badge-error': job.status === 'failed' || job.status === 'cancelled'
                    }">{{ job.status }}</span>
                  </div>
                  <button
                    v-if="(job.type === 'upload' || job.type === 'index') && (job.status === 'pending' || job.status === 'running')"
                    @click="cancelJob(job.id)"
                    class="btn btn-ghost btn-xs text-error"
                    title="Cancel"
                  >
                    <XCircle :size="16" />
                  </button>
                </div>

                <!-- Current File -->
                <div v-if="job.currentFile" class="text-sm text-base-content/70 truncate">
                  {{ job.currentFile }}
                </div>

                <!-- Phase Info -->
                <div v-if="job.phase && (job.status === 'running' || job.status === 'pending')" class="flex items-center gap-2 text-sm">
                  <span class="text-base-content/50">Phase:</span>
                  <span class="badge badge-sm badge-primary capitalize">{{ job.phase }}</span>
                  <span v-if="job.chunksTotal > 0" class="text-base-content/50">
                    ({{ job.chunksProcessed }}/{{ job.chunksTotal }} chunks)
                  </span>
                </div>

                <!-- Progress -->
                <div v-if="job.status === 'running' || job.status === 'pending'" class="space-y-1">
                  <progress
                    class="progress progress-primary w-full"
                    :value="job.progress"
                    max="100"
                  ></progress>
                  <div class="flex justify-between text-xs text-base-content/60">
                    <span>{{ Math.round(job.progress) }}%</span>
                    <span>{{ job.processedFiles || 0 }}/{{ job.totalFiles || '?' }} files</span>
                  </div>
                </div>

                <!-- Error -->
                <div v-if="job.error" class="text-sm text-error">
                  {{ job.error }}
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Footer -->
        <div v-if="backgroundJobsStore.allJobs.some(j => j.status === 'completed' || j.status === 'failed' || j.status === 'cancelled')" class="p-4 border-t border-base-300">
          <button class="btn btn-ghost btn-sm w-full" @click="clearCompletedJobs">
            Clear completed jobs
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'
import axios from 'axios'
import { Search, FileText, Settings, Plus, ChevronDown, Pencil, Trash2, Bell, Loader2, CheckCircle, XCircle, X, PanelRightOpen, FileSearch, MessageSquare, Hash, Library, Share2, Users } from 'lucide-vue-next'

const tabs = [
  { id: 'search', label: 'Search', icon: Search },
  { id: 'chat', label: 'Chat', icon: MessageSquare },
  { id: 'ocr', label: 'OCR', icon: FileSearch },
  { id: 'tokenizer', label: 'Tokenizer', icon: Hash },
]
import SearchTab from './components/SearchTab.vue'
import SourcesSidebar from './components/SourcesSidebar.vue'
import OCRPlaygroundTab from './components/OCRPlaygroundTab.vue'
import TokenizerTab from './components/TokenizerTab.vue'
import ChatTab from './components/ChatTab.vue'
import SettingsTab from './components/SettingsTab.vue'
import ShareModal from './components/ShareModal.vue'
import { useCollectionStore } from './stores/collectionStore'
import { useUserStore } from './stores/userStore'
import { useSearchStore } from './stores/searchStore'
import { useBackgroundJobsStore } from './stores/backgroundJobsStore'

const collectionStore = useCollectionStore()
const searchStore = useSearchStore()
const backgroundJobsStore = useBackgroundJobsStore()
const userStore = useUserStore()

const activeTab = ref('search')
const currentTheme = ref('light')

// Resizable sidebar
const SIDEBAR_MIN = 220
const SIDEBAR_MAX = 600
const sidebarWidth = ref(parseInt(localStorage.getItem('sidebar_width') || '320'))
const isResizing = ref(false)

const startResize = (e) => {
  isResizing.value = true
  const startX = e.clientX
  const startWidth = sidebarWidth.value

  const onMove = (e) => {
    const delta = e.clientX - startX
    sidebarWidth.value = Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, startWidth + delta))
  }

  const onUp = () => {
    isResizing.value = false
    localStorage.setItem('sidebar_width', String(sidebarWidth.value))
    window.removeEventListener('mousemove', onMove)
    window.removeEventListener('mouseup', onUp)
  }

  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', onUp)
}

const stats = ref({
  documents: 0,
  pages: 0,
  chunks: 0
})

// Sources sidebar state (persisted)
const sourcesSidebarOpen = ref(localStorage.getItem('sources_sidebar_open') !== 'false')

watch(sourcesSidebarOpen, v => localStorage.setItem('sources_sidebar_open', String(v)))

// Create collection modal state
const showCollectionModal = ref(false)
const newCollectionName = ref('')
const newCollectionDescription = ref('')
const newCollectionColor = ref('#3b82f6')
const creatingCollection = ref(false)

// Edit collection modal state
const showEditModal = ref(false)
const editingCollectionId = ref('')
const editCollectionName = ref('')
const editCollectionDescription = ref('')
const editCollectionColor = ref('#3b82f6')
const updatingCollection = ref(false)

// Delete confirmation modal state
const showDeleteConfirmModal = ref(false)
const deletingCollection = ref(false)
const deleteError = ref('')

// Share modal state
const showShareModal = ref(false)
const shareCollectionId = ref('')
const shareCollectionName = ref('')

// Background jobs drawer state
const showJobsDrawer = ref(false)

const updateThemeFromStorage = () => {
  const savedTheme = localStorage.getItem('theme')
  if (savedTheme) {
    currentTheme.value = savedTheme
    document.documentElement.setAttribute('data-theme', savedTheme)
  } else {
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
    currentTheme.value = prefersDark ? 'dark' : 'light'
    document.documentElement.setAttribute('data-theme', currentTheme.value)
  }
}

const loadStats = async () => {
  try {
    const collectionId = collectionStore.currentCollectionId
    const [docsResponse, healthResponse] = await Promise.all([
      axios.get(`/documents?collection_id=${collectionId}`),
      axios.get(`/health?collection_id=${collectionId}`)
    ])

    stats.value.documents = docsResponse.data.documents?.length || 0
    stats.value.pages = docsResponse.data.documents?.reduce((sum, doc) => sum + (doc.total_pages || 0), 0) || 0
    stats.value.chunks = healthResponse.data.indexed_chunks || 0
  } catch (error) {
    console.error('Error loading stats:', error)
  }
}

const handleDocumentDeleted = async () => {
  // Reload both stats and collections list (for document_count in dropdown)
  await Promise.all([
    loadStats(),
    collectionStore.loadCollections()
  ])
}

const handleDataCleared = async () => {
  // Reload both stats and collections list (for document_count in dropdown)
  await Promise.all([
    loadStats(),
    collectionStore.loadCollections()
  ])
}

const switchTab = (tabName) => {
  activeTab.value = tabName
}

const selectCollection = (collectionId) => {
  collectionStore.setCurrentCollection(collectionId)
}

const openCreateCollectionModal = () => {
  newCollectionName.value = ''
  newCollectionDescription.value = ''
  newCollectionColor.value = '#3b82f6'
  showCollectionModal.value = true
}

const createCollection = async () => {
  if (!newCollectionName.value.trim()) return

  creatingCollection.value = true
  try {
    const collection = await collectionStore.createCollection({
      name: newCollectionName.value.trim(),
      description: newCollectionDescription.value.trim(),
      color: newCollectionColor.value
    })
    collectionStore.setCurrentCollection(collection.id)
    showCollectionModal.value = false
  } catch (err) {
    console.error('Failed to create collection:', err)
  } finally {
    creatingCollection.value = false
  }
}

const openEditCollectionModal = (collection) => {
  editingCollectionId.value = collection.id
  editCollectionName.value = collection.name
  editCollectionDescription.value = collection.description || ''
  editCollectionColor.value = collection.color || '#3b82f6'
  showEditModal.value = true
}

const updateCollection = async () => {
  if (!editCollectionName.value.trim()) return

  updatingCollection.value = true
  try {
    await collectionStore.updateCollection(editingCollectionId.value, {
      name: editingCollectionId.value === 'default' ? undefined : editCollectionName.value.trim(),
      description: editCollectionDescription.value.trim(),
      color: editCollectionColor.value
    })
    showEditModal.value = false
  } catch (err) {
    console.error('Failed to update collection:', err)
  } finally {
    updatingCollection.value = false
  }
}

const openShareModal = (collection) => {
  shareCollectionId.value = collection.id
  shareCollectionName.value = collection.name
  showShareModal.value = true
}

const handleShared = () => {
  collectionStore.loadCollections()
}

const confirmDeleteCollection = () => {
  deleteError.value = ''
  showDeleteConfirmModal.value = true
}

const closeDeleteModal = () => {
  if (!deletingCollection.value) {
    showDeleteConfirmModal.value = false
    deleteError.value = ''
  }
}

const deleteCollection = async () => {
  deletingCollection.value = true
  deleteError.value = ''
  try {
    const deletedCollectionId = editingCollectionId.value
    const wasCurrentCollection = collectionStore.currentCollectionId === deletedCollectionId

    await collectionStore.deleteCollection(deletedCollectionId)
    // Clear search history for the deleted collection
    searchStore.clearCollectionCache(deletedCollectionId)

    // Close modals
    showDeleteConfirmModal.value = false
    showEditModal.value = false
    deleteError.value = ''

    // Switch to default collection if we deleted the current one
    if (wasCurrentCollection) {
      collectionStore.setCurrentCollection('default')
    }

    // Reload stats for the new current collection
    await loadStats()
  } catch (err) {
    console.error('Failed to delete collection:', err)
    deleteError.value = err.response?.data?.detail || err.message || 'Failed to delete collection. Please try again.'
  } finally {
    deletingCollection.value = false
  }
}

// Watch for collection changes to reload stats
watch(() => collectionStore.currentCollectionId, () => {
  loadStats()
})

// Clear completed jobs from the drawer
const clearCompletedJobs = () => {
  // Remove completed upload jobs
  backgroundJobsStore.uploadJobs = backgroundJobsStore.uploadJobs.filter(
    j => j.status === 'pending' || j.status === 'running'
  )
  // Clear completed reindex job
  if (backgroundJobsStore.reindexJob &&
      (backgroundJobsStore.reindexJob.status === 'completed' || backgroundJobsStore.reindexJob.status === 'failed')) {
    backgroundJobsStore.clearReindexJob()
  }
}

// Cancel an active upload job
const cancelJob = async (jobId) => {
  try {
    await backgroundJobsStore.cancelUploadJob(jobId)
  } catch (err) {
    console.error('Failed to cancel job:', err)
  }
}

onMounted(async () => {
  // Load user info
  await userStore.loadCurrentUser()

  // Load collections first
  await collectionStore.loadCollections()

  // Then load stats for current collection
  loadStats()

  // Check for any active background jobs
  backgroundJobsStore.checkActiveJobs()

  // Initialize theme
  updateThemeFromStorage()

  // Listen for theme changes (from Settings tab via custom event)
  window.addEventListener('theme-changed', () => {
    updateThemeFromStorage()
  })

  // Listen for storage changes (theme changed in another tab)
  window.addEventListener('storage', (e) => {
    if (e.key === 'theme') {
      updateThemeFromStorage()
    }
  })

  // Listen for system theme changes (only if user hasn't set a preference)
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (!localStorage.getItem('theme')) {
      updateThemeFromStorage()
    }
  })
})

onBeforeUnmount(() => {
  backgroundJobsStore.cleanup()
})
</script>
