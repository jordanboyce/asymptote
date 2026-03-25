<template>
  <div class="space-y-6">
    <h2 class="text-2xl font-bold">Settings</h2>

    <!-- AI Integration -->
    <div class="card bg-base-200">
      <div class="card-body">
        <h3 class="card-title">AI Integration</h3>
        <p class="text-sm text-base-content/70 mb-2">
          Connect your own AI provider to get answers from your sources, not just search results.
          Your key is stored only in your browser and sent per request.
        </p>

        <!-- Provider Selection -->
        <div class="rounded-xl border border-base-300 bg-base-100 p-4 shadow-sm">
          <label class="label p-0">
            <span class="label-text text-sm font-semibold uppercase tracking-[0.18em] text-base-content/70">Provider</span>
          </label>
          <p class="mt-1 text-xs text-base-content/60">
            Pick the backend for reranking and answer synthesis.
          </p>
          <div class="mt-3 grid grid-cols-2 gap-2 xl:grid-cols-4">
            <button
              class="btn btn-sm relative min-h-12 justify-start px-4 text-left"
              :class="aiSettings.provider === 'anthropic' ? 'btn-primary' : 'btn-ghost border border-base-300 bg-base-200/40'"
              @click="setProvider('anthropic')"
            >
              Anthropic
              <span v-if="hasAnthropicKey" class="absolute right-2 top-2 badge badge-success badge-xs">&#10003;</span>
            </button>
            <button
              class="btn btn-sm relative min-h-12 justify-start px-4 text-left"
              :class="aiSettings.provider === 'openai' ? 'btn-primary' : 'btn-ghost border border-base-300 bg-base-200/40'"
              @click="setProvider('openai')"
            >
              OpenAI
              <span v-if="hasOpenAIKey" class="absolute right-2 top-2 badge badge-success badge-xs">&#10003;</span>
            </button>
            <button
              class="btn btn-sm relative min-h-12 justify-start px-4 text-left"
              :class="aiSettings.provider === 'inl_hpc' ? 'btn-primary' : 'btn-ghost border border-base-300 bg-base-200/40'"
              @click="setProvider('inl_hpc')"
            >
              INL HPC API
              <span v-if="hasINLHpcKey" class="absolute right-2 top-2 badge badge-success badge-xs">&#10003;</span>
            </button>
            <button
              class="btn btn-sm relative min-h-12 justify-start px-4 text-left"
              :class="aiSettings.provider === 'ollama' ? 'btn-primary' : 'btn-ghost border border-base-300 bg-base-200/40'"
              @click="setProvider('ollama')"
            >
              Ollama
              <span v-if="ollamaAvailable" class="absolute right-2 top-2 badge badge-success badge-xs">&#10003;</span>
            </button>
          </div>
        </div>

        <!-- Ollama Model Selection -->
        <div v-if="aiSettings.provider === 'ollama'" class="form-control">
          <div v-if="checkingOllama" class="flex justify-center py-4">
            <span class="loading loading-spinner"></span>
          </div>

          <div v-else-if="!ollamaAvailable" class="alert alert-warning">
            <div>
              <div class="font-bold">Ollama not detected</div>
              <div class="text-sm">Install from <a href="https://ollama.com" target="_blank" class="link link-primary">ollama.com</a></div>
            </div>
          </div>

          <div v-else class="space-y-3">
            <div class="alert alert-success py-2">
              <span class="text-sm">Ollama running - {{ ollamaModels.length }} model(s) available</span>
            </div>

            <select
              v-model="selectedOllamaModel"
              class="select select-bordered w-full"
              @change="saveOllamaModel"
            >
              <option v-for="model in ollamaModels" :key="model.name" :value="model.name">
                {{ model.name }} ({{ formatBytes(model.size) }})
              </option>
            </select>
          </div>

          <button class="btn btn-sm btn-ghost mt-2" @click="checkOllamaStatus">
            Refresh
          </button>
        </div>

        <!-- API Key Input (Cloud Providers) -->
        <div v-else class="rounded-xl border border-base-300 bg-base-100 p-4 shadow-sm">
          <label class="label p-0 pb-1">
            <span class="label-text text-sm font-semibold uppercase tracking-[0.18em] text-base-content/70">Credentials</span>
          </label>
          <p class="mb-3 text-xs text-base-content/60">
            {{ providerDisplayName }} is used only when you enable AI reranking or synthesis.
          </p>
          <label class="label p-0 pb-1">
            <span class="label-text font-medium">{{ providerDisplayName }} API Key</span>
          </label>
          <div class="join join-vertical w-full md:join-horizontal">
            <input
              v-model="apiKey"
              :type="showKey ? 'text' : 'password'"
              :placeholder="providerPlaceholder"
              class="input input-bordered join-item flex-1"
              @input="apiKeyDirty = true; apiKeyStatus = ''"
            />
            <button class="btn join-item md:w-24" @click="showKey = !showKey">
              {{ showKey ? 'Hide' : 'Show' }}
            </button>
            <button
              class="btn btn-primary join-item md:w-24"
              @click="validateAndSaveKey"
              :disabled="validatingKey || !apiKey.trim()"
            >
              <span v-if="validatingKey" class="loading loading-spinner loading-xs"></span>
              {{ validatingKey ? '' : 'Save' }}
            </button>
          </div>
          <div class="mt-3 flex flex-col gap-2 text-sm md:flex-row md:items-center md:justify-between">
            <span class="text-base-content/70">
              <a v-if="aiSettings.provider === 'openai'" href="https://platform.openai.com/api-keys" target="_blank" class="link link-primary">Get an OpenAI key</a>
              <a v-else-if="aiSettings.provider === 'anthropic'" href="https://console.anthropic.com/settings/keys" target="_blank" class="link link-primary">Get an Anthropic key</a>
              <span v-else>Use your INL HPC API key</span>
            </span>
            <span v-if="apiKeyStatus === 'valid'" class="font-semibold text-success">Key valid</span>
            <span v-else-if="apiKeyStatus === 'invalid'" class="font-semibold text-error">Invalid key</span>
            <span v-else-if="apiKeyStatus === 'saved'" class="font-semibold text-success">Saved</span>
          </div>
        </div>

        <!-- INL HPC Model Selection -->
        <div v-if="aiSettings.provider === 'inl_hpc'" class="form-control mt-3">
          <div v-if="checkingInlHpc" class="flex justify-center py-3">
            <span class="loading loading-spinner"></span>
          </div>

          <div v-else-if="!hasStoredKey" class="alert alert-info py-2">
            <span class="text-sm">Save your INL HPC key to load available models</span>
          </div>

          <div v-else-if="inlHpcModels.length === 0" class="alert alert-warning py-2">
            <span class="text-sm">No INL HPC models found for this key</span>
          </div>

          <div v-else class="space-y-3">
            <div class="alert alert-success py-2">
              <span class="text-sm">INL HPC - {{ inlHpcModels.length }} model(s) available</span>
            </div>

            <select
              v-model="selectedInlHpcModel"
              class="select select-bordered w-full"
              @change="saveInlHpcModel"
            >
              <option v-for="model in inlHpcModels" :key="model.id" :value="model.id">
                {{ formatInlHpcModel(model) }}
              </option>
            </select>
          </div>

          <button class="btn btn-sm btn-ghost mt-2" @click="checkInlHpcStatus">
            Refresh
          </button>
        </div>

        <!-- Anthropic Model Selection -->
        <div v-if="aiSettings.provider === 'anthropic' && hasStoredKey" class="form-control mt-3">
          <label class="label p-0 pb-1">
            <span class="label-text text-sm font-semibold uppercase tracking-[0.18em] text-base-content/70">Model</span>
          </label>
          <p class="mb-2 text-xs text-base-content/60">Select which model to use for reranking and synthesis. Leave blank to use provider defaults.</p>
          <select
            v-model="selectedAnthropicModel"
            class="select select-bordered w-full"
            @change="saveAnthropicModel"
          >
            <option value="">Use defaults (Haiku for rerank, Sonnet for synthesis)</option>
            <option v-for="model in ANTHROPIC_MODELS" :key="model.id" :value="model.id">
              {{ model.label }}
            </option>
          </select>
        </div>

        <!-- OpenAI Model Selection -->
        <div v-if="aiSettings.provider === 'openai' && hasStoredKey" class="form-control mt-3">
          <label class="label p-0 pb-1">
            <span class="label-text text-sm font-semibold uppercase tracking-[0.18em] text-base-content/70">Model</span>
          </label>
          <p class="mb-2 text-xs text-base-content/60">Select which model to use for reranking and synthesis. Leave blank to use provider defaults.</p>
          <select
            v-model="selectedOpenAIModel"
            class="select select-bordered w-full"
            @change="saveOpenAIModel"
          >
            <option value="">Use defaults (GPT-4o Mini for rerank, GPT-4o for synthesis)</option>
            <option v-for="model in OPENAI_MODELS" :key="model.id" :value="model.id">
              {{ model.label }}
            </option>
          </select>
        </div>

        <!-- Remove Key -->
        <div v-if="aiSettings.provider !== 'ollama' && hasStoredKey && !apiKeyDirty" class="mt-2">
          <button class="btn btn-ghost btn-sm text-error" @click="removeKey">
            Remove stored key
          </button>
        </div>

        <!-- AI Feature Toggles -->
        <div v-if="hasStoredKey || (aiSettings.provider === 'ollama' && ollamaAvailable)" class="divider"></div>
        <div v-if="hasStoredKey || (aiSettings.provider === 'ollama' && ollamaAvailable)" class="rounded-xl border border-base-300 bg-base-100 p-4 shadow-sm">
          <h4 class="text-sm font-semibold uppercase tracking-[0.18em] text-base-content/70">AI Features</h4>
          <p class="mt-1 text-xs text-base-content/60">
            Enable these only if you want the external model involved in ranking or answering.
          </p>

          <div class="mt-4 space-y-3">
            <div class="rounded-lg border border-base-300 bg-base-200/50 p-3">
              <label class="label cursor-pointer justify-start gap-4 p-0">
                <input
                  type="checkbox"
                  class="toggle toggle-primary toggle-sm"
                  :checked="aiSettings.rerank"
                  @change="toggleAI('rerank')"
                />
                <div>
                  <span class="label-text font-medium">Result Reranking</span>
                  <p class="text-xs text-base-content/60">Use AI to judge which results actually answer your question.</p>
                </div>
              </label>
            </div>

            <div class="rounded-lg border border-base-300 bg-base-200/50 p-3">
              <label class="label cursor-pointer justify-start gap-4 p-0">
                <input
                  type="checkbox"
                  class="toggle toggle-primary toggle-sm"
                  :checked="aiSettings.synthesize"
                  @change="toggleAI('synthesize')"
                />
                <div>
                  <span class="label-text font-medium">Answer Synthesis</span>
                  <p class="text-xs text-base-content/60">Generate a direct answer from your indexed sources with citations.</p>
                </div>
              </label>
            </div>
          </div>
        </div>
      </div>
    </div>


    <!-- MCP Integration -->
    <div class="card bg-base-200">
      <div class="card-body space-y-5">

        <!-- Header -->
        <div class="flex items-start justify-between gap-4">
          <div>
            <h3 class="card-title">MCP Server</h3>
            <p class="text-sm text-base-content/70 mt-1">
              Connect Claude Code or Codex directly to your indexed document collections.
              The server returns ranked chunks with similarity scores — your AI agent handles reasoning.
            </p>
          </div>
          <div class="flex items-center gap-2 shrink-0 pt-1">
            <span :class="mcpSettings.enable_mcp ? 'badge badge-success' : 'badge badge-ghost'">
              {{ mcpSettings.enable_mcp ? 'Enabled' : 'Disabled' }}
            </span>
            <input v-model="mcpSettings.enable_mcp" type="checkbox" class="toggle toggle-primary" @change="saveMcpToggle" />
          </div>
        </div>

        <div v-if="!mcpSettings.enable_mcp" class="alert">
          <span class="text-sm">Enable the MCP server above to connect Claude Code or Codex to your documents.</span>
        </div>

        <template v-else>

          <!-- Project Connections -->
          <div class="rounded-xl border border-base-300 bg-base-100 p-4 shadow-sm space-y-4">
            <div class="flex items-start justify-between gap-3">
              <div>
                <h4 class="text-sm font-semibold uppercase tracking-[0.18em] text-base-content/70">Project Connections</h4>
                <p class="mt-1 text-xs text-base-content/60">
                  Map a collection to a project. Drop the exported config into your repo root and your agent will search that collection automatically.
                </p>
              </div>
              <button class="btn btn-sm btn-primary shrink-0" @click="openAddResource">+ Add</button>
            </div>

            <div v-if="mcpResources.length === 0" class="text-center py-6 text-base-content/40 text-sm">
              No project connections yet. Click <strong>+ Add</strong> to create one.
            </div>

            <div v-else class="space-y-2">
              <div
                v-for="resource in mcpResources"
                :key="resource.id"
                class="rounded-lg border border-base-300 bg-base-200/30 p-3 flex items-start justify-between gap-3"
              >
                <div class="min-w-0 space-y-0.5">
                  <div class="font-medium text-sm">{{ resource.name }}</div>
                  <div class="text-xs text-base-content/50">
                    <span>{{ resource.collection_name }}</span>
                    <span class="font-mono opacity-50 ml-1">({{ resource.collection_id }})</span>
                    <span v-if="resource.repo_url" class="ml-2 opacity-70">· {{ resource.repo_url }}</span>
                  </div>
                  <div class="font-mono text-xs text-base-content/35 truncate">{{ resource.server_url }}</div>
                </div>
                <div class="flex items-center gap-1 shrink-0">
                  <div class="dropdown dropdown-end">
                    <button tabindex="0" class="btn btn-xs btn-ghost">Export ▾</button>
                    <ul tabindex="0" class="dropdown-content menu menu-sm bg-base-100 rounded-box border border-base-300 shadow-lg z-10 w-48 p-1">
                      <li><a @click="copyResourceText(resource.claude_json, 'Claude Code config')">Copy Claude Code (.mcp.json)</a></li>
                      <li><a @click="downloadResourceText(resource.claude_json, `.mcp-${resource.collection_id}.json`)">Download .mcp.json</a></li>
                      <li class="divider my-0.5"></li>
                      <li><a @click="copyResourceText(resource.codex_toml, 'Codex config')">Copy Codex (config.toml)</a></li>
                      <li><a @click="downloadResourceText(resource.codex_toml, `config-${resource.collection_id}.toml`)">Download config.toml</a></li>
                    </ul>
                  </div>
                  <button class="btn btn-xs btn-ghost text-error" @click="deleteResource(resource.id)" title="Remove">✕</button>
                </div>
              </div>
            </div>
          </div>

          <!-- Add Resource Modal -->
          <dialog ref="addResourceModal" class="modal">
            <div class="modal-box">
              <h3 class="font-bold text-lg mb-4">Add Project Connection</h3>
              <div class="space-y-3">
                <div class="form-control">
                  <label class="label p-0 pb-1"><span class="label-text font-medium">Name</span></label>
                  <input v-model="newResource.name" type="text" placeholder="e.g. Saphire Docs" class="input input-bordered w-full" />
                </div>
                <div class="form-control">
                  <label class="label p-0 pb-1"><span class="label-text font-medium">Collection</span></label>
                  <select v-model="newResource.collection_id" class="select select-bordered w-full">
                    <option v-for="c in mcpCollections" :key="c.id" :value="c.id">{{ c.name }}</option>
                  </select>
                </div>
                <div class="form-control">
                  <label class="label p-0 pb-1">
                    <span class="label-text font-medium">Repo URL <span class="font-normal text-base-content/40">(optional)</span></span>
                  </label>
                  <input v-model="newResource.repo_url" type="url" placeholder="https://github.com/you/project" class="input input-bordered w-full" />
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
          <details class="rounded-xl border border-base-300 bg-base-100 shadow-sm">
            <summary class="cursor-pointer px-4 py-3 text-sm font-medium text-base-content/60 hover:text-base-content select-none list-none flex items-center justify-between">
              <span>Advanced server defaults</span>
              <span class="text-xs text-base-content/40">Fallback settings when no profile URL is used</span>
            </summary>
            <div class="px-4 pb-4 space-y-4 border-t border-base-300 pt-4">
              <p class="text-xs text-base-content/50">
                These are used as fallbacks when the MCP server is called without a profile URL. The exported configs above override these automatically via query params.
              </p>
              <div class="grid gap-3 md:grid-cols-2">
                <div class="form-control">
                  <label class="label p-0 pb-1"><span class="label-text font-medium">Default collection</span></label>
                  <select v-model="mcpSettings.mcp_default_collection" class="select select-bordered w-full">
                    <option v-for="c in mcpCollections" :key="c.id" :value="c.id">{{ c.name }}</option>
                  </select>
                </div>
                <div class="form-control">
                  <label class="label p-0 pb-1"><span class="label-text font-medium">Default search mode</span></label>
                  <select v-model="mcpSettings.mcp_mode" class="select select-bordered w-full">
                    <option value="semantic">Semantic</option>
                    <option value="keyword">Keyword</option>
                    <option value="hybrid">Hybrid</option>
                  </select>
                </div>
                <div class="form-control">
                  <label class="label p-0 pb-1"><span class="label-text font-medium">Default top K</span></label>
                  <input v-model.number="mcpSettings.mcp_top_k" type="number" min="1" max="20" class="input input-bordered w-full" />
                </div>
                <div class="form-control">
                  <label class="label p-0 pb-1"><span class="label-text font-medium">Default excerpt length</span></label>
                  <input v-model.number="mcpSettings.mcp_max_source_length" type="number" min="100" max="2000" step="50" class="input input-bordered w-full" />
                </div>
              </div>
              <div v-if="mcpSettings.mcp_mode === 'hybrid'" class="form-control">
                <label class="label p-0 pb-1">
                  <span class="label-text font-medium">Semantic weight</span>
                  <span class="label-text-alt">{{ mcpSettings.mcp_semantic_weight.toFixed(2) }}</span>
                </label>
                <input v-model.number="mcpSettings.mcp_semantic_weight" type="range" min="0" max="1" step="0.05" class="range range-primary range-sm" />
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

        </template>

        <div v-if="mcpError" class="alert alert-error py-2">
          <span>{{ mcpError }}</span>
        </div>
        <div v-if="mcpStatus" class="alert alert-success py-2">
          <span>{{ mcpStatus }}</span>
        </div>

      </div>
    </div>

    <!-- Appearance -->
    <div class="card bg-base-200">
      <div class="card-body">
        <h3 class="card-title">Appearance</h3>

        <div class="rounded-xl border border-base-300 bg-base-100 p-4 shadow-sm">
          <label class="label p-0 pb-1">
            <span class="label-text text-sm font-semibold uppercase tracking-[0.18em] text-base-content/70">Theme</span>
          </label>
          <p class="mb-3 text-xs text-base-content/60">
            Visual theme for the local UI only.
          </p>
          <select
            v-model="selectedTheme"
            class="select select-bordered w-full"
            @change="applyTheme"
          >
            <option value="light">Light</option>
            <option value="dark">Dark</option>
            <option value="cupcake">Cupcake</option>
            <option value="dracula">Dracula</option>
            <option value="nord">Nord</option>
          </select>
        </div>

        <!-- Theme Preview -->
        <div class="mt-4 p-4 rounded-xl border border-base-300 bg-base-100 shadow-sm">
          <div class="flex flex-wrap gap-2">
            <button class="btn btn-primary btn-sm">Primary</button>
            <button class="btn btn-secondary btn-sm">Secondary</button>
            <button class="btn btn-accent btn-sm">Accent</button>
          </div>
        </div>
      </div>
    </div>

    <!-- Danger Zone -->
    <div class="card bg-error/10 border border-error">
      <div class="card-body">
        <h3 class="card-title text-error">Danger Zone</h3>
        <p class="text-sm">Permanently delete all sources and indexes in the current collection.</p>

        <div v-if="clearSuccess" class="alert alert-success">
          <span>All data has been cleared successfully!</span>
        </div>

        <div v-if="clearError" class="alert alert-error">
          <span>{{ clearError }}</span>
        </div>

        <div class="card-actions justify-end">
          <button class="btn btn-error" @click="confirmClearAll" :disabled="clearing">
            <span v-if="clearing" class="loading loading-spinner"></span>
            {{ clearing ? 'Clearing...' : 'Clear All Data' }}
          </button>
        </div>
      </div>
    </div>

    <!-- Clear All Confirmation Modal -->
    <dialog ref="clearModal" class="modal">
      <div class="modal-box">
        <h3 class="font-bold text-lg text-error">Clear All Data</h3>
        <p class="py-4">
          This will <strong>permanently delete</strong> all sources and indexes in <strong>{{ collectionStore.currentCollection?.name || 'Default' }}</strong>.
        </p>
        <p class="text-sm text-error font-semibold">
          This action cannot be undone.
        </p>
        <div class="modal-action">
          <button class="btn" @click="closeClearModal" :disabled="clearing">Cancel</button>
          <button class="btn btn-error" @click="clearAllData" :disabled="clearing">
            <span v-if="clearing" class="loading loading-spinner"></span>
            {{ clearing ? 'Clearing...' : 'Delete Everything' }}
          </button>
        </div>
      </div>
      <form method="dialog" class="modal-backdrop">
        <button @click="closeClearModal">close</button>
      </form>
    </dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import axios from 'axios'
import { useCollectionStore } from '../stores/collectionStore'

const emit = defineEmits(['data-cleared', 'stats-updated', 'switch-tab'])

const collectionStore = useCollectionStore()

// Theme
const selectedTheme = ref('light')

// Clear data
const clearing = ref(false)
const clearSuccess = ref(false)
const clearError = ref('')
const clearModal = ref(null)

// AI integration state
const apiKey = ref('')
const showKey = ref(false)
const validatingKey = ref(false)
const apiKeyStatus = ref('')
const apiKeyDirty = ref(false)
const hasStoredKey = ref(false)
const aiSettings = ref({
  provider: 'anthropic',
  rerank: false,
  synthesize: false,
})

// Ollama state
const checkingOllama = ref(false)
const ollamaAvailable = ref(false)
const ollamaModels = ref([])
const selectedOllamaModel = ref('')

// INL HPC state
const checkingInlHpc = ref(false)
const inlHpcModels = ref([])
const selectedInlHpcModel = ref('')

// Anthropic model state
const ANTHROPIC_MODELS = [
  { id: 'claude-sonnet-4-5-20250929', label: 'Claude Sonnet 4.5 (default)' },
  { id: 'claude-haiku-4-5-20251001', label: 'Claude Haiku 4.5 (fast)' },
  { id: 'claude-opus-4-6', label: 'Claude Opus 4.6' },
  { id: 'claude-3-5-sonnet-20241022', label: 'Claude 3.5 Sonnet' },
  { id: 'claude-3-5-haiku-20241022', label: 'Claude 3.5 Haiku' },
  { id: 'claude-3-opus-20240229', label: 'Claude 3 Opus' },
  { id: 'claude-3-haiku-20240307', label: 'Claude 3 Haiku' },
]
const selectedAnthropicModel = ref(localStorage.getItem('anthropic_model') || '')

// OpenAI model state
const OPENAI_MODELS = [
  { id: 'gpt-4o', label: 'GPT-4o (default quality)' },
  { id: 'gpt-4o-mini', label: 'GPT-4o Mini (default fast)' },
  { id: 'gpt-4-turbo', label: 'GPT-4 Turbo' },
  { id: 'gpt-4', label: 'GPT-4' },
  { id: 'gpt-3.5-turbo', label: 'GPT-3.5 Turbo' },
]
const selectedOpenAIModel = ref(localStorage.getItem('openai_model') || '')

// MCP state
const mcpLoading = ref(false)
const mcpSaving = ref(false)
const mcpError = ref('')
const mcpStatus = ref('')
const mcpCollections = ref([])
const mcpResources = ref([])
const addResourceModal = ref(null)
const newResource = ref({ name: '', collection_id: 'default', repo_url: '' })
const mcpSettings = ref({
  enable_mcp: true,
  mcp_default_collection: 'default',
  mcp_top_k: 5,
  mcp_mode: 'semantic',
  mcp_semantic_weight: 0.7,
  mcp_include_sources: true,
  mcp_max_source_length: 500,
})

// Computed
const hasAnthropicKey = computed(() => !!localStorage.getItem('ai_api_key_anthropic'))
const hasOpenAIKey = computed(() => !!localStorage.getItem('ai_api_key_openai'))
const hasINLHpcKey = computed(() => !!localStorage.getItem('ai_api_key_inl_hpc'))
const providerDisplayName = computed(() => {
  if (aiSettings.value.provider === 'openai') return 'OpenAI'
  if (aiSettings.value.provider === 'inl_hpc') return 'INL HPC'
  return 'Anthropic'
})
const providerPlaceholder = computed(() => {
  if (aiSettings.value.provider === 'openai') return 'sk-...'
  if (aiSettings.value.provider === 'inl_hpc') return 'api_...'
  return 'sk-ant-...'
})

// AI Methods
const setProvider = (provider) => {
  aiSettings.value.provider = provider
  localStorage.setItem('ai_settings', JSON.stringify(aiSettings.value))
  loadProviderKey()
  if (provider === 'ollama') {
    checkOllamaStatus()
  } else if (provider === 'inl_hpc') {
    loadInlHpcModelsFromStorage()
    if (hasStoredKey.value) {
      checkInlHpcStatus()
    }
  }
}

const validateAndSaveKey = async () => {
  if (!apiKey.value.trim()) return
  validatingKey.value = true
  apiKeyStatus.value = ''

  try {
    const response = await axios.post('/api/ai/validate-key', null, {
      headers: {
        'X-AI-Key': apiKey.value.trim(),
        'X-AI-Provider': aiSettings.value.provider,
      }
    })
    if (response.data.valid) {
      const keyName = `ai_api_key_${aiSettings.value.provider}`
      localStorage.setItem(keyName, apiKey.value.trim())
      if (aiSettings.value.provider === 'inl_hpc') {
        applyInlHpcModels(Array.isArray(response.data.models) ? response.data.models : [])
      }
      localStorage.setItem('ai_settings', JSON.stringify(aiSettings.value))
      hasStoredKey.value = true
      apiKeyDirty.value = false
      apiKeyStatus.value = 'valid'
    } else {
      if (aiSettings.value.provider === 'inl_hpc') {
        inlHpcModels.value = []
        selectedInlHpcModel.value = ''
        localStorage.removeItem('inl_hpc_models')
        localStorage.removeItem('inl_hpc_model')
      }
      apiKeyStatus.value = 'invalid'
    }
  } catch {
    if (aiSettings.value.provider === 'inl_hpc') {
      inlHpcModels.value = []
      selectedInlHpcModel.value = ''
      localStorage.removeItem('inl_hpc_models')
      localStorage.removeItem('inl_hpc_model')
    }
    apiKeyStatus.value = 'invalid'
  } finally {
    validatingKey.value = false
  }
}

const removeKey = () => {
  const keyName = `ai_api_key_${aiSettings.value.provider}`
  localStorage.removeItem(keyName)
  if (aiSettings.value.provider === 'inl_hpc') {
    localStorage.removeItem('inl_hpc_models')
    localStorage.removeItem('inl_hpc_model')
    inlHpcModels.value = []
    selectedInlHpcModel.value = ''
  } else if (aiSettings.value.provider === 'anthropic') {
    localStorage.removeItem('anthropic_model')
    selectedAnthropicModel.value = ''
  } else if (aiSettings.value.provider === 'openai') {
    localStorage.removeItem('openai_model')
    selectedOpenAIModel.value = ''
  }
  apiKey.value = ''
  hasStoredKey.value = false
  apiKeyStatus.value = ''
  apiKeyDirty.value = false
  localStorage.setItem('ai_settings', JSON.stringify(aiSettings.value))
}

const toggleAI = (feature) => {
  aiSettings.value[feature] = !aiSettings.value[feature]
  localStorage.setItem('ai_settings', JSON.stringify(aiSettings.value))
}

const loadProviderKey = () => {
  const keyName = `ai_api_key_${aiSettings.value.provider}`
  const storedKey = localStorage.getItem(keyName)
  if (storedKey) {
    apiKey.value = storedKey
    hasStoredKey.value = true
    apiKeyDirty.value = false
    apiKeyStatus.value = 'saved'
  } else {
    apiKey.value = ''
    hasStoredKey.value = false
    apiKeyDirty.value = false
    apiKeyStatus.value = ''
  }
}

const loadAISettings = () => {
  const storedSettings = localStorage.getItem('ai_settings')
  if (storedSettings) {
    try {
      const parsed = JSON.parse(storedSettings)
      const validProviders = ['anthropic', 'openai', 'inl_hpc', 'ollama']
      aiSettings.value = {
        provider: validProviders.includes(parsed.provider) ? parsed.provider : 'anthropic',
        rerank: !!parsed.rerank,
        synthesize: !!parsed.synthesize,
      }
    } catch { /* use defaults */ }
  }
  loadProviderKey()
  if (aiSettings.value.provider === 'inl_hpc') {
    loadInlHpcModelsFromStorage()
  }
}

// Ollama Methods
const checkOllamaStatus = async () => {
  checkingOllama.value = true
  try {
    const response = await axios.get('/api/ollama/status')
    ollamaAvailable.value = response.data.available
    ollamaModels.value = response.data.models || []

    const savedModel = localStorage.getItem('ollama_model')
    if (savedModel && ollamaModels.value.find(m => m.name === savedModel)) {
      selectedOllamaModel.value = savedModel
    } else if (ollamaModels.value.length > 0) {
      selectedOllamaModel.value = ollamaModels.value[0].name
      localStorage.setItem('ollama_model', selectedOllamaModel.value)
    }
  } catch {
    ollamaAvailable.value = false
    ollamaModels.value = []
  } finally {
    checkingOllama.value = false
  }
}

const saveOllamaModel = () => {
  localStorage.setItem('ollama_model', selectedOllamaModel.value)
}

const saveAnthropicModel = () => {
  if (selectedAnthropicModel.value) {
    localStorage.setItem('anthropic_model', selectedAnthropicModel.value)
  } else {
    localStorage.removeItem('anthropic_model')
  }
}

const saveOpenAIModel = () => {
  if (selectedOpenAIModel.value) {
    localStorage.setItem('openai_model', selectedOpenAIModel.value)
  } else {
    localStorage.removeItem('openai_model')
  }
}

const normalizeInlHpcModels = (models) => {
  if (!Array.isArray(models)) return []
  return models
    .map((model) => {
      if (typeof model === 'string') return { id: model }
      if (model && typeof model === 'object' && model.id) return model
      return null
    })
    .filter(Boolean)
}

const applyInlHpcModels = (models) => {
  const normalized = normalizeInlHpcModels(models)
  inlHpcModels.value = normalized
  localStorage.setItem('inl_hpc_models', JSON.stringify(normalized))

  const savedModel = localStorage.getItem('inl_hpc_model')
  if (savedModel && normalized.find((m) => m.id === savedModel)) {
    selectedInlHpcModel.value = savedModel
  } else if (normalized.length > 0) {
    selectedInlHpcModel.value = normalized[0].id
    localStorage.setItem('inl_hpc_model', selectedInlHpcModel.value)
  } else {
    selectedInlHpcModel.value = ''
    localStorage.removeItem('inl_hpc_model')
  }
}

const loadInlHpcModelsFromStorage = () => {
  try {
    const raw = localStorage.getItem('inl_hpc_models')
    const parsed = raw ? JSON.parse(raw) : []
    applyInlHpcModels(parsed)
  } catch {
    inlHpcModels.value = []
    selectedInlHpcModel.value = ''
  }
}

const checkInlHpcStatus = async () => {
  const key = apiKey.value.trim() || localStorage.getItem('ai_api_key_inl_hpc')
  if (!key) return

  checkingInlHpc.value = true
  try {
    const response = await axios.post('/api/ai/validate-key', null, {
      headers: {
        'X-AI-Key': key,
        'X-AI-Provider': 'inl_hpc',
      }
    })

    if (response.data.valid) {
      applyInlHpcModels(response.data.models || [])
      apiKeyStatus.value = hasStoredKey.value ? 'saved' : apiKeyStatus.value
    } else {
      inlHpcModels.value = []
      selectedInlHpcModel.value = ''
      localStorage.removeItem('inl_hpc_models')
      localStorage.removeItem('inl_hpc_model')
    }
  } catch {
    inlHpcModels.value = []
    selectedInlHpcModel.value = ''
    localStorage.removeItem('inl_hpc_models')
    localStorage.removeItem('inl_hpc_model')
  } finally {
    checkingInlHpc.value = false
  }
}

const saveInlHpcModel = () => {
  localStorage.setItem('inl_hpc_model', selectedInlHpcModel.value)
}

const formatInlHpcModel = (model) => {
  if (!model || !model.id) return ''
  if (model.max_model_len) {
    const contextLen = Number(model.max_model_len)
    if (!Number.isNaN(contextLen) && contextLen > 0) {
      return `${model.id} (ctx ${contextLen.toLocaleString()})`
    }
  }
  return model.id
}

const formatBytes = (bytes) => {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i]
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
    collection_id: mcpCollections.value[0]?.id || 'default',
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
  } catch (error) {
    mcpError.value = error.response?.data?.detail || 'Failed to delete connection'
  }
}

const copyResourceText = async (value, label) => {
  if (!value) return
  try {
    await navigator.clipboard.writeText(value)
    mcpStatus.value = `${label} copied`
    mcpError.value = ''
  } catch {
    mcpError.value = `Failed to copy ${label}`
  }
}

const downloadResourceText = (value, filename) => {
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

// Theme
const applyTheme = () => {
  document.documentElement.setAttribute('data-theme', selectedTheme.value)
  localStorage.setItem('theme', selectedTheme.value)
  window.dispatchEvent(new CustomEvent('theme-changed'))
}

// Clear data
const confirmClearAll = () => {
  clearSuccess.value = false
  clearError.value = ''
  clearModal.value?.showModal()
}

const closeClearModal = () => {
  if (!clearing.value) {
    clearModal.value?.close()
  }
}

const clearAllData = async () => {
  clearing.value = true
  clearError.value = ''
  clearSuccess.value = false

  try {
    const collectionId = collectionStore.currentCollectionId
    const docsResponse = await axios.get(`/documents?collection_id=${collectionId}`)
    const documents = docsResponse.data.documents || []

    for (const doc of documents) {
      await axios.delete(`/documents/${doc.document_id}?collection_id=${collectionId}`)
    }

    clearSuccess.value = true
    clearModal.value?.close()
    emit('data-cleared')

    setTimeout(() => {
      clearSuccess.value = false
    }, 5000)
  } catch (error) {
    clearError.value = error.response?.data?.detail || 'Failed to clear data'
  } finally {
    clearing.value = false
  }
}

// Lifecycle
onMounted(() => {
  loadAISettings()

  // Load theme
  const savedTheme = localStorage.getItem('theme')
  if (savedTheme) {
    selectedTheme.value = savedTheme
  } else {
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
    selectedTheme.value = prefersDark ? 'dark' : 'light'
  }

  // Check Ollama if that's the selected provider
  if (aiSettings.value.provider === 'ollama') {
    checkOllamaStatus()
  } else if (aiSettings.value.provider === 'inl_hpc') {
    loadInlHpcModelsFromStorage()
    if (hasStoredKey.value) {
      checkInlHpcStatus()
    }
  }

  loadMcpCollections()
  loadMcpSettings()
  loadMcpResources()
})
</script>




