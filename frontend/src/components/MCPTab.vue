<template>
  <div class="space-y-6">
    <!-- What this server exposes -->
    <div class="rounded-xl border border-base-300 bg-base-200/40 px-4 py-3 space-y-2">
      <p class="text-xs font-semibold text-base-content/60 uppercase tracking-wide">What the MCP server exposes</p>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1 text-xs text-base-content/70">
        <div>
          <p class="font-medium text-base-content/80 mb-0.5">Tools (call these)</p>
          <ul class="space-y-0.5 font-mono">
            <li>search_all_collections</li>
            <li>search_collection</li>
            <li>list_recent_documents</li>
            <li>get_document_context</li>
            <li>find_in_documents</li>
            <li>list_tables · query_table</li>
            <li>aggregate_table · get_table_rows</li>
            <li>list_collections · health_check</li>
          </ul>
        </div>
        <div>
          <p class="font-medium text-base-content/80 mb-0.5">Resources (load as context)</p>
          <ul class="space-y-0.5 font-mono">
            <li>collections://all</li>
            <li>collection://&#123;id&#125;</li>
            <li>collection://&#123;id&#125;/guide</li>
            <li>collection://&#123;id&#125;/schema</li>
            <li>collection://&#123;id&#125;/tables</li>
            <li>document://&#123;id&#125;</li>
            <li>table://&#123;id&#125;</li>
          </ul>
        </div>
      </div>
    </div>

    <!-- Enable toggle -->
    <div class="card bg-base-200">
      <div class="card-body py-4">
        <div class="flex items-center justify-between">
          <div>
            <span class="font-semibold text-sm">MCP Server</span>
            <span :class="mcpSettings.enable_mcp ? 'badge badge-success badge-sm ml-2' : 'badge badge-ghost badge-sm ml-2'">
              {{ mcpSettings.enable_mcp ? 'Enabled' : 'Disabled' }}
            </span>
          </div>
          <input v-model="mcpSettings.enable_mcp" type="checkbox" class="toggle toggle-primary" @change="saveMcpToggle" />
        </div>
      </div>
    </div>

    <div v-if="!mcpSettings.enable_mcp" class="alert">
      <span class="text-sm">Enable the MCP server above to connect Claude Code, Codex, or GitHub Copilot to your documents.</span>
    </div>

    <template v-else>

      <!-- Project Connections -->
      <div class="card bg-base-200">
        <div class="card-body space-y-4">
          <div class="flex items-start justify-between gap-3">
            <div>
              <h3 class="card-title text-base">Project Connections</h3>
              <p class="mt-1 text-sm text-base-content/60">
                Pin a collection to a specific repo. The generated config bakes a <code class="font-mono text-xs">?collection_id=</code> into the MCP URL so your agent always searches that collection by default — no need to pass it on every call.
              </p>
              <p class="mt-1 text-xs text-base-content/40">
                Drop the file in the project root (Claude Code) or <code class="font-mono">.vscode/mcp.json</code> (Copilot). One connection per collection.
              </p>
            </div>
            <button class="btn btn-sm btn-primary shrink-0" :disabled="availableCollections.length === 0" @click="openAddResource">+ Add</button>
          </div>

          <div v-if="mcpResources.length === 0" class="text-center py-6 text-base-content/40 text-sm">
            No project connections yet. Click <strong>+ Add</strong> to create one.
          </div>

          <div v-else class="space-y-3">
            <div
              v-for="resource in mcpResources"
              :key="resource.id"
              class="rounded-lg border border-base-300 bg-base-100 overflow-hidden"
            >
              <!-- Resource header -->
              <div
                class="p-3 flex items-center justify-between gap-3 cursor-pointer hover:bg-base-200/50 select-none"
                @click="toggleExpanded(resource.id)"
              >
                <div class="min-w-0 flex-1">
                  <div class="flex items-center gap-2">
                    <span class="font-medium text-sm">{{ resource.name }}</span>
                    <span class="text-xs text-base-content/40 font-mono">{{ resource.collection_name }}</span>
                    <span v-if="resource.repo_url" class="text-xs text-base-content/40 truncate">· {{ resource.repo_url }}</span>
                  </div>
                </div>
                <div class="flex items-center gap-1 shrink-0">
                  <svg
                    class="w-4 h-4 text-base-content/40 transition-transform duration-200"
                    :class="{ 'rotate-180': expandedResource === resource.id }"
                    fill="none" viewBox="0 0 24 24" stroke="currentColor"
                  >
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                  </svg>
                  <button
                    class="btn btn-xs btn-ghost text-error ml-1"
                    @click.stop="deleteResource(resource.id)"
                    title="Remove connection"
                    :aria-label="`Remove connection: ${resource.name}`"
                  >✕</button>
                </div>
              </div>

              <!-- Expanded config panel -->
              <div v-if="expandedResource === resource.id" class="border-t border-base-300">
                <!-- Tab bar -->
                <div class="flex border-b border-base-300 bg-base-200/30">
                  <button
                    v-for="tab in configTabs"
                    :key="tab.key"
                    class="px-4 py-2 text-xs font-medium transition-colors relative"
                    :class="activeTab === tab.key
                      ? 'text-primary border-b-2 border-primary -mb-px bg-base-100/50'
                      : 'text-base-content/50 hover:text-base-content/80'"
                    @click="activeTab = tab.key"
                  >
                    {{ tab.label }}
                  </button>
                </div>

                <!-- Config content -->
                <div class="p-3 space-y-2">
                  <div class="flex items-center justify-between">
                    <span class="text-xs text-base-content/50">{{ activeTabMeta.filename(resource) }}</span>
                    <div class="flex items-center gap-1">
                      <button
                        class="btn btn-xs btn-ghost gap-1"
                        @click="copyText(activeTabMeta.content(resource), activeTabMeta.label)"
                      >
                        <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                        </svg>
                        Copy
                      </button>
                      <button
                        class="btn btn-xs btn-ghost gap-1"
                        @click="downloadText(activeTabMeta.content(resource), activeTabMeta.filename(resource))"
                      >
                        <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                        </svg>
                        Download
                      </button>
                    </div>
                  </div>
                  <pre
                    class="bg-neutral text-neutral-content rounded-lg p-3 text-xs font-mono overflow-x-auto whitespace-pre select-all leading-relaxed"
                  >{{ activeTabMeta.content(resource) }}</pre>
                  <p class="text-xs text-base-content/40">{{ activeTabMeta.hint }}</p>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Add Resource Modal -->
      <dialog ref="addResourceModal" class="modal" aria-labelledby="mcp-add-title">
        <div class="modal-box">
          <h3 id="mcp-add-title" class="font-bold text-lg mb-4">Add Project Connection</h3>
          <div class="space-y-3">
            <div class="form-control">
              <label class="label p-0 pb-1" for="mcp-resource-name"><span class="label-text font-medium">Name</span></label>
              <input id="mcp-resource-name" v-model="newResource.name" type="text" placeholder="e.g. Saphire Docs" class="input input-bordered w-full" />
            </div>
            <div class="form-control">
              <label class="label p-0 pb-1" for="mcp-resource-collection"><span class="label-text font-medium">Collection</span></label>
              <select id="mcp-resource-collection" v-model="newResource.collection_id" class="select select-bordered w-full" :aria-describedby="availableCollections.length === 0 ? 'mcp-collection-help' : undefined">
                <option v-for="c in availableCollections" :key="c.id" :value="c.id">{{ c.name }}</option>
              </select>
              <p v-if="availableCollections.length === 0" id="mcp-collection-help" class="label-text-alt text-warning mt-1">
                All collections already have a connection
              </p>
            </div>
            <div class="form-control">
              <label class="label p-0 pb-1" for="mcp-resource-repo">
                <span class="label-text font-medium">Repo URL <span class="font-normal text-base-content/40">(optional)</span></span>
              </label>
              <input id="mcp-resource-repo" v-model="newResource.repo_url" type="url" placeholder="https://github.com/you/project" class="input input-bordered w-full" />
            </div>
          </div>
          <div class="modal-action">
            <button class="btn btn-ghost" @click="addResourceModal?.close()">Cancel</button>
            <button class="btn btn-primary" @click="createResource" :disabled="mcpSaving || !newResource.name || !newResource.collection_id">
              <span v-if="mcpSaving" class="loading loading-spinner loading-xs"></span>
              Add
            </button>
          </div>
        </div>
        <form method="dialog" class="modal-backdrop"><button>close</button></form>
      </dialog>

      <!-- Advanced / Global server defaults -->
      <div class="card bg-base-200">
        <div class="card-body p-0">
          <details class="group">
            <summary class="cursor-pointer px-5 py-4 text-sm font-medium text-base-content/60 hover:text-base-content select-none list-none flex items-center justify-between">
              <span>Advanced server defaults</span>
              <span class="text-xs text-base-content/40">Fallback settings when no profile URL is used</span>
            </summary>
            <div class="px-5 pb-5 space-y-4 border-t border-base-300 pt-4">
              <p class="text-xs text-base-content/50">
                These are used as fallbacks when the MCP server is called without a profile URL. The exported configs above override these automatically via query params.
              </p>
              <div class="grid gap-3 md:grid-cols-2">
                <div class="form-control">
                  <label class="label p-0 pb-1" for="mcp-default-collection"><span class="label-text font-medium">Default collection</span></label>
                  <select id="mcp-default-collection" v-model="mcpSettings.mcp_default_collection" class="select select-bordered w-full">
                    <option v-for="c in mcpCollections" :key="c.id" :value="c.id">{{ c.name }}</option>
                  </select>
                </div>
                <div class="form-control">
                  <label class="label p-0 pb-1" for="mcp-default-mode"><span class="label-text font-medium">Default search mode</span></label>
                  <select id="mcp-default-mode" v-model="mcpSettings.mcp_mode" class="select select-bordered w-full">
                    <option value="semantic">Semantic</option>
                    <option value="keyword">Keyword</option>
                    <option value="hybrid">Hybrid</option>
                  </select>
                </div>
                <div class="form-control">
                  <label class="label p-0 pb-1" for="mcp-default-topk"><span class="label-text font-medium">Default top K</span></label>
                  <input id="mcp-default-topk" v-model.number="mcpSettings.mcp_top_k" type="number" min="1" max="20" class="input input-bordered w-full" />
                </div>
                <div class="form-control">
                  <label class="label p-0 pb-1" for="mcp-excerpt-length"><span class="label-text font-medium">Default excerpt length</span></label>
                  <input id="mcp-excerpt-length" v-model.number="mcpSettings.mcp_max_source_length" type="number" min="100" max="2000" step="50" class="input input-bordered w-full" />
                </div>
              </div>
              <div v-if="mcpSettings.mcp_mode === 'hybrid'" class="form-control">
                <label class="label p-0 pb-1" for="mcp-semantic-weight">
                  <span class="label-text font-medium">Semantic weight</span>
                  <span class="label-text-alt">{{ mcpSettings.mcp_semantic_weight.toFixed(2) }}</span>
                </label>
                <input id="mcp-semantic-weight" v-model.number="mcpSettings.mcp_semantic_weight" type="range" min="0" max="1" step="0.05" class="range range-primary range-sm" :aria-valuetext="`${Math.round(mcpSettings.mcp_semantic_weight * 100)} percent semantic`" />
              </div>
              <label class="label cursor-pointer justify-start gap-3 p-0">
                <input v-model="mcpSettings.mcp_include_sources" type="checkbox" class="checkbox checkbox-sm checkbox-primary" />
                <span class="label-text">Include excerpts by default</span>
              </label>
              <div class="flex justify-end">
                <button class="btn btn-sm btn-primary" @click="saveMcpSettings" :disabled="mcpSaving || mcpLoading">
                  <span v-if="mcpSaving" class="loading loading-spinner loading-xs"></span>
                  Save server defaults
                </button>
              </div>
            </div>
          </details>
        </div>
      </div>

    </template>

    <div v-if="mcpError" class="alert alert-error py-2">
      <span>{{ mcpError }}</span>
    </div>
    <div v-if="mcpStatus" class="alert alert-success py-2">
      <span>{{ mcpStatus }}</span>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import axios from 'axios'

// MCP state
const mcpLoading = ref(false)
const mcpSaving = ref(false)
const mcpError = ref('')
const mcpStatus = ref('')
const mcpCollections = ref([])
const mcpResources = ref([])
const addResourceModal = ref(null)
const newResource = ref({ name: '', collection_id: 'default', repo_url: '' })
const expandedResource = ref(null)
const activeTab = ref('claude')
const mcpSettings = ref({
  enable_mcp: true,
  mcp_default_collection: 'default',
  mcp_top_k: 5,
  mcp_mode: 'semantic',
  mcp_semantic_weight: 0.7,
  mcp_include_sources: true,
  mcp_max_source_length: 500,
})

const configTabs = [
  { key: 'claude', label: 'Claude Code' },
  { key: 'codex', label: 'Codex' },
  { key: 'copilot', label: 'GitHub Copilot' },
]

const activeTabMeta = computed(() => {
  const tabs = {
    claude: {
      label: 'Claude Code config',
      content: (r) => r.claude_json,
      filename: (r) => `.mcp-${r.collection_id}.json`,
      hint: 'Place this file in your project root, or merge into ~/.claude/mcp.json for global access.',
    },
    codex: {
      label: 'Codex config',
      content: (r) => r.codex_toml,
      filename: (r) => `config-${r.collection_id}.toml`,
      hint: 'Merge this into your Codex config.toml.',
    },
    copilot: {
      label: 'GitHub Copilot config',
      content: (r) => r.copilot_json,
      filename: () => '.vscode/mcp.json',
      hint: 'Place this file at .vscode/mcp.json in your project, or merge into your VS Code settings.',
    },
  }
  return tabs[activeTab.value]
})

const availableCollections = computed(() => {
  const connectedIds = new Set(mcpResources.value.map(r => r.collection_id))
  return mcpCollections.value.filter(c => !connectedIds.has(c.id))
})

const toggleExpanded = (id) => {
  expandedResource.value = expandedResource.value === id ? null : id
}

const loadMcpCollections = async () => {
  try {
    const response = await axios.get('/api/collections')
    mcpCollections.value = response.data.collections || []
    if (mcpCollections.value.length > 0 && !mcpCollections.value.find(c => c.id === newResource.value.collection_id)) {
      newResource.value.collection_id = mcpCollections.value[0].id
    }
  } catch {
    mcpCollections.value = [{ id: 'default', name: 'Default' }]
  }
}

const loadMcpSettings = async () => {
  mcpLoading.value = true
  mcpError.value = ''
  try {
    const response = await axios.get('/api/mcp/config')
    mcpSettings.value = { ...mcpSettings.value, ...response.data }
  } catch (error) {
    mcpError.value = error.response?.data?.detail || 'Failed to load MCP settings'
  } finally {
    mcpLoading.value = false
  }
}

const loadMcpResources = async () => {
  try {
    const response = await axios.get('/api/mcp/resources')
    mcpResources.value = response.data
  } catch (error) {
    mcpError.value = error.response?.data?.detail || 'Failed to load project connections'
  }
}

const openAddResource = () => {
  newResource.value = {
    name: '',
    collection_id: availableCollections.value[0]?.id || '',
    repo_url: '',
  }
  addResourceModal.value?.showModal()
}

const createResource = async () => {
  mcpSaving.value = true
  mcpError.value = ''
  try {
    const response = await axios.post('/api/mcp/resources', {
      name: newResource.value.name.trim(),
      collection_id: newResource.value.collection_id,
      repo_url: newResource.value.repo_url.trim() || null,
    })
    mcpResources.value.push(response.data)
    addResourceModal.value?.close()
    expandedResource.value = response.data.id
    mcpStatus.value = `Added "${response.data.name}"`
  } catch (error) {
    mcpError.value = error.response?.data?.detail || 'Failed to add project connection'
  } finally {
    mcpSaving.value = false
  }
}

const deleteResource = async (id) => {
  try {
    await axios.delete(`/api/mcp/resources/${id}`)
    mcpResources.value = mcpResources.value.filter(r => r.id !== id)
    if (expandedResource.value === id) expandedResource.value = null
  } catch (error) {
    mcpError.value = error.response?.data?.detail || 'Failed to delete connection'
  }
}

const copyText = async (value, label) => {
  if (!value) return
  try {
    await navigator.clipboard.writeText(value)
    mcpStatus.value = `${label} copied`
    mcpError.value = ''
  } catch {
    mcpError.value = `Failed to copy ${label}`
  }
}

const downloadText = (value, filename) => {
  if (!value) return
  const blob = new Blob([value], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
  mcpStatus.value = `${filename} downloaded`
}

const saveMcpToggle = async () => {
  mcpError.value = ''
  mcpStatus.value = ''
  try {
    await axios.post('/api/mcp/config', { enable_mcp: mcpSettings.value.enable_mcp })
  } catch (error) {
    mcpError.value = error.response?.data?.detail || 'Failed to update MCP status'
    mcpSettings.value.enable_mcp = !mcpSettings.value.enable_mcp
  }
}

const saveMcpSettings = async () => {
  mcpSaving.value = true
  mcpError.value = ''
  mcpStatus.value = ''

  try {
    const payload = {
      ...mcpSettings.value,
      mcp_top_k: Number(mcpSettings.value.mcp_top_k) || 5,
      mcp_semantic_weight: Number(mcpSettings.value.mcp_semantic_weight) || 0.7,
      mcp_max_source_length: Number(mcpSettings.value.mcp_max_source_length) || 500,
    }

    const response = await axios.post('/api/mcp/config', payload)
    if (!response.data.success) {
      mcpError.value = response.data.errors?.join(', ') || 'Failed to save MCP settings'
      return
    }

    mcpStatus.value = 'MCP settings saved'
    await loadMcpSettings()
  } catch (error) {
    mcpError.value = error.response?.data?.detail || 'Failed to save MCP settings'
  } finally {
    mcpSaving.value = false
  }
}

onMounted(() => {
  loadMcpCollections()
  loadMcpSettings()
  loadMcpResources()
})
</script>
