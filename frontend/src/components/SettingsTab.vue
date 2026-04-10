<template>
  <div class="space-y-6">
    <h2 class="text-2xl font-bold">Settings</h2>

    <!-- System Info -->
    <div class="card bg-base-200">
      <div class="card-body space-y-3">
        <div>
          <h3 class="card-title text-base">System</h3>
          <p class="text-sm text-base-content/70 mt-0.5">Database backend and multi-user configuration. These are set via environment variables.</p>
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div class="bg-base-100 rounded-lg p-3">
            <div class="text-xs text-base-content/50 mb-1">Database Backend</div>
            <div class="font-semibold text-sm flex items-center gap-1.5">
              <span class="badge badge-sm" :class="systemInfo.db_backend === 'postgresql' ? 'badge-primary' : 'badge-ghost'">
                {{ systemInfo.db_backend === 'postgresql' ? 'PostgreSQL' : 'SQLite' }}
              </span>
            </div>
          </div>
          <div class="bg-base-100 rounded-lg p-3">
            <div class="text-xs text-base-content/50 mb-1">Multi-User Mode</div>
            <div class="font-semibold text-sm flex items-center gap-1.5">
              <span class="badge badge-sm" :class="systemInfo.multi_user ? 'badge-success' : 'badge-ghost'">
                {{ systemInfo.multi_user ? 'Enabled' : 'Disabled' }}
              </span>
            </div>
          </div>
          <div class="bg-base-100 rounded-lg p-3">
            <div class="text-xs text-base-content/50 mb-1">Current User</div>
            <div class="font-semibold text-sm truncate">{{ systemInfo.user_id || 'default' }}</div>
          </div>
        </div>
        <p class="text-xs text-base-content/50">
          Set <code>DB_BACKEND=postgresql</code> and <code>ENABLE_MULTI_USER=true</code> in your .env for multi-user enterprise mode.
        </p>
      </div>
    </div>

    <!-- AI Integration -->
    <div class="card bg-base-200">
      <div class="card-body space-y-4">
        <div>
          <h3 class="card-title">AI Providers</h3>
          <p class="text-sm text-base-content/70 mt-0.5">
            Configure providers here. Select which ones to use and choose a model per-search in the Search tab. API keys are stored in your browser only, never on the server.
          </p>
        </div>

        <!-- Provider Table -->
        <div class="rounded-xl border border-base-300 bg-base-100 divide-y divide-base-300 overflow-hidden">

          <!-- Built-in Provider Rows -->
          <div v-for="def in PROVIDER_DEFS" :key="def.id">
            <!-- Row header -->
            <div
              class="flex items-center gap-3 px-4 py-3 cursor-pointer select-none hover:bg-base-200/50 transition-colors"
              @click="toggleExpand(def.id)"
            >
              <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2 flex-wrap">
                  <span class="font-medium text-sm">{{ def.name }}</span>
                  <span v-if="def.type === 'local'" class="badge badge-info badge-xs badge-outline">local</span>
                </div>
                <div v-if="getProviderModelLabel(def.id)" class="text-xs text-base-content/50 mt-0.5 truncate">{{ getProviderModelLabel(def.id) }}</div>
              </div>
              <div class="flex items-center gap-2 flex-shrink-0">
                <span v-if="isProviderConfigured(def.id)" class="badge badge-success badge-xs">Configured</span>
                <span v-else class="text-base-content/35 text-xs hidden sm:block">Not set up</span>
                <span class="text-base-content/35 text-xs">{{ expandedProvider === def.id ? '▲' : '▼' }}</span>
              </div>
            </div>

            <!-- Config panel -->
            <div v-if="expandedProvider === def.id" class="border-t border-base-300 bg-base-200/30 px-4 py-4 space-y-3">

              <!-- Ollama config -->
              <template v-if="def.id === 'ollama'">
                <div class="form-control">
                  <label class="label p-0 pb-1">
                    <span class="label-text font-medium">Base URL</span>
                  </label>
                  <input
                    v-model="editBuffer.baseUrl"
                    type="url"
                    placeholder="http://localhost:11434"
                    class="input input-bordered input-sm w-full"
                  />
                  <p class="text-xs text-base-content/50 mt-1">Change this to connect to a remote Ollama instance.</p>
                </div>

                <div v-if="ollamaCheckStatus === 'checking'" class="flex items-center gap-2 text-sm text-base-content/60">
                  <span class="loading loading-spinner loading-xs"></span> Detecting models…
                </div>
                <div v-else-if="ollamaCheckStatus === 'available'" class="space-y-2">
                  <div class="alert alert-success py-2 text-sm">
                    Ollama running · {{ detectedOllamaModels.length }} model(s) available
                  </div>
                  <div class="form-control">
                    <label class="label p-0 pb-1"><span class="label-text font-medium">Model</span></label>
                    <select v-model="editBuffer.model" class="select select-bordered select-sm w-full">
                      <option v-for="m in detectedOllamaModels" :key="m.name" :value="m.name">
                        {{ m.name }} ({{ formatBytes(m.size) }})
                      </option>
                    </select>
                  </div>
                </div>
                <div v-else-if="ollamaCheckStatus === 'unavailable'" class="alert alert-warning py-2 text-sm">
                  Ollama not detected at <code class="font-mono">{{ editBuffer.baseUrl || 'http://localhost:11434' }}</code>.
                  <a href="https://ollama.com" target="_blank" class="link link-primary ml-1">Install Ollama</a>
                </div>

                <div class="flex gap-2">
                  <button class="btn btn-sm btn-ghost" @click="detectOllama">Detect</button>
                  <button
                    v-if="ollamaCheckStatus === 'available'"
                    class="btn btn-sm btn-primary"
                    @click="saveOllamaConfig"
                    :disabled="savingProvider === 'ollama'"
                  >
                    <span v-if="savingProvider === 'ollama'" class="loading loading-spinner loading-xs"></span>
                    Save
                  </button>
                  <button
                    v-if="isProviderConfigured('ollama')"
                    class="btn btn-sm btn-ghost text-error"
                    @click="removeProvider('ollama')"
                  >Remove</button>
                </div>
              </template>

              <!-- Cloud provider config -->
              <template v-else>
                <div class="form-control">
                  <label class="label p-0 pb-1">
                    <span class="label-text font-medium">API Key</span>
                    <a v-if="def.keyLink" :href="def.keyLink" target="_blank" class="label-text-alt link link-primary text-xs">Get a key ↗</a>
                  </label>
                  <div class="join w-full">
                    <input
                      v-model="editBuffer.apiKey"
                      :type="showEditKey ? 'text' : 'password'"
                      :placeholder="def.keyPlaceholder || 'API key…'"
                      class="input input-bordered input-sm join-item flex-1"
                      @input="editBuffer.keyDirty = true; editBuffer.keyStatus = ''"
                    />
                    <button class="btn btn-sm join-item" @click="showEditKey = !showEditKey">
                      {{ showEditKey ? 'Hide' : 'Show' }}
                    </button>
                  </div>
                </div>

                <div v-if="def.models && def.models.length" class="form-control">
                  <label class="label p-0 pb-1"><span class="label-text font-medium">Model</span></label>
                  <p class="text-xs text-base-content/50 mb-1">Override the default model. Leave blank to use provider defaults.</p>
                  <select v-model="editBuffer.model" class="select select-bordered select-sm w-full">
                    <option value="">Use provider defaults</option>
                    <option v-for="m in def.models" :key="m.id" :value="m.id">{{ m.label }}</option>
                  </select>
                </div>

                <div class="flex items-center gap-2 flex-wrap">
                  <button
                    class="btn btn-sm btn-primary"
                    @click="validateAndSave(def.id)"
                    :disabled="validatingProvider === def.id || !editBuffer.apiKey?.trim()"
                  >
                    <span v-if="validatingProvider === def.id" class="loading loading-spinner loading-xs"></span>
                    {{ validatingProvider === def.id ? '' : 'Validate & Save' }}
                  </button>
                  <button
                    v-if="isProviderConfigured(def.id) && !editBuffer.keyDirty"
                    class="btn btn-sm btn-ghost text-error"
                    @click="removeProvider(def.id)"
                  >Remove</button>
                  <span v-if="editBuffer.keyStatus === 'valid'" class="text-success text-sm font-semibold">✓ Key valid</span>
                  <span v-else-if="editBuffer.keyStatus === 'invalid'" class="text-error text-sm font-semibold">✗ Invalid</span>
                  <span v-else-if="editBuffer.keyStatus === 'saved'" class="text-success text-sm font-semibold">Saved</span>
                  <span v-if="editBuffer.keyError" class="text-error text-xs">{{ editBuffer.keyError }}</span>
                </div>
              </template>
            </div>
          </div>

          <!-- Custom Provider Rows -->
          <div v-for="cp in customProvidersConfig" :key="cp.id">
            <div
              class="flex items-center gap-3 px-4 py-3 cursor-pointer select-none hover:bg-base-200/50 transition-colors"
              @click="toggleExpand(cp.id)"
            >
              <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2 flex-wrap">
                  <span class="font-medium text-sm">{{ cp.name }}</span>
                  <span class="badge badge-neutral badge-xs badge-outline">custom</span>
                </div>
                <div class="text-xs text-base-content/50 mt-0.5 truncate">{{ cp.baseUrl }}</div>
              </div>
              <div class="flex items-center gap-2 flex-shrink-0">
                <span class="badge badge-success badge-xs">Configured</span>
                <span class="text-base-content/35 text-xs">{{ expandedProvider === cp.id ? '▲' : '▼' }}</span>
              </div>
            </div>

            <div v-if="expandedProvider === cp.id" class="border-t border-base-300 bg-base-200/30 px-4 py-4 space-y-3">
              <div class="grid gap-3 sm:grid-cols-2">
                <div class="form-control">
                  <label class="label p-0 pb-1"><span class="label-text font-medium">Name</span></label>
                  <input v-model="editBuffer.name" type="text" placeholder="My vLLM Server" class="input input-bordered input-sm w-full" />
                </div>
                <div class="form-control">
                  <label class="label p-0 pb-1"><span class="label-text font-medium">Model</span></label>
                  <input v-model="editBuffer.model" type="text" placeholder="model-name" class="input input-bordered input-sm w-full" />
                </div>
              </div>
              <div class="form-control">
                <label class="label p-0 pb-1"><span class="label-text font-medium">Base URL</span></label>
                <input v-model="editBuffer.baseUrl" type="url" placeholder="http://localhost:8000/v1" class="input input-bordered input-sm w-full" />
                <p class="text-xs text-base-content/50 mt-1">Must be an OpenAI-compatible endpoint (e.g. vLLM, LM Studio, Groq, OpenRouter).</p>
              </div>
              <div class="form-control">
                <label class="label p-0 pb-1">
                  <span class="label-text font-medium">API Key <span class="font-normal opacity-50">(optional)</span></span>
                </label>
                <input v-model="editBuffer.apiKey" type="password" placeholder="none or your key" class="input input-bordered input-sm w-full" />
              </div>
              <div class="flex gap-2">
                <button class="btn btn-sm btn-primary" @click="saveCustomProvider(cp.id)" :disabled="!editBuffer.baseUrl?.trim() || !editBuffer.name?.trim()">Save</button>
                <button class="btn btn-sm btn-ghost text-error" @click="removeProvider(cp.id)">Remove</button>
              </div>
            </div>
          </div>

          <!-- Add Custom Provider row -->
          <div v-if="showAddCustomForm" class="border-t border-base-300 bg-base-200/30 px-4 py-4 space-y-3">
            <p class="text-sm font-medium">New Custom Endpoint</p>
            <div class="grid gap-3 sm:grid-cols-2">
              <div class="form-control">
                <label class="label p-0 pb-1"><span class="label-text font-medium">Name</span></label>
                <input v-model="newCustom.name" type="text" placeholder="My vLLM Server" class="input input-bordered input-sm w-full" />
              </div>
              <div class="form-control">
                <label class="label p-0 pb-1"><span class="label-text font-medium">Model</span></label>
                <input v-model="newCustom.model" type="text" placeholder="model-name" class="input input-bordered input-sm w-full" />
              </div>
            </div>
            <div class="form-control">
              <label class="label p-0 pb-1"><span class="label-text font-medium">Base URL</span></label>
              <input v-model="newCustom.baseUrl" type="url" placeholder="http://localhost:8000/v1" class="input input-bordered input-sm w-full" />
              <p class="text-xs text-base-content/50 mt-1">OpenAI-compatible endpoint (vLLM, LM Studio, Groq, OpenRouter, etc.).</p>
            </div>
            <div class="form-control">
              <label class="label p-0 pb-1">
                <span class="label-text font-medium">API Key <span class="font-normal opacity-50">(optional)</span></span>
              </label>
              <input v-model="newCustom.apiKey" type="password" placeholder="none or your key" class="input input-bordered input-sm w-full" />
            </div>
            <div class="flex gap-2">
              <button class="btn btn-sm btn-primary" @click="addCustomProvider" :disabled="!newCustom.baseUrl?.trim() || !newCustom.name?.trim()">Add</button>
              <button class="btn btn-sm btn-ghost" @click="showAddCustomForm = false">Cancel</button>
            </div>
          </div>
        </div>

        <button v-if="!showAddCustomForm" class="btn btn-ghost btn-sm self-start gap-1" @click="showAddCustomForm = true">
          + Add Custom Endpoint
        </button>

        <!-- AI Feature Toggles -->
        <div v-if="configuredProviderIds.length > 0" class="rounded-xl border border-base-300 bg-base-100 p-4 shadow-sm space-y-3">
          <div>
            <h4 class="text-sm font-semibold uppercase tracking-[0.18em] text-base-content/70">AI Features (Defaults)</h4>
            <p class="mt-1 text-xs text-base-content/60">Default on/off state for reranking and synthesis when you run a search. You can also toggle these live in the Search tab.</p>
          </div>
          <div class="space-y-2">
            <div class="rounded-lg border border-base-300 bg-base-200/50 p-3">
              <label class="label cursor-pointer justify-start gap-4 p-0">
                <input type="checkbox" class="toggle toggle-primary toggle-sm" :checked="aiSettings.rerank" @change="toggleAIFeature('rerank')" />
                <div>
                  <span class="label-text font-medium">Result Reranking</span>
                  <p class="text-xs text-base-content/60">Use AI to judge which results actually answer your question.</p>
                </div>
              </label>
            </div>
            <div class="rounded-lg border border-base-300 bg-base-200/50 p-3">
              <label class="label cursor-pointer justify-start gap-4 p-0">
                <input type="checkbox" class="toggle toggle-primary toggle-sm" :checked="aiSettings.synthesize" @change="toggleAIFeature('synthesize')" />
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
import {
  PROVIDER_DEFS,
  getProvidersConfig,
  getProviderConfig,
  upsertProviderConfig,
  removeProviderConfig,
  getAISettings,
  getConfiguredProviderIds,
  migrateLegacySettings,
} from '../utils/aiProviders.js'

const emit = defineEmits(['data-cleared', 'stats-updated', 'switch-tab'])

const collectionStore = useCollectionStore()

// System info
const systemInfo = ref({ db_backend: 'sqlite', multi_user: false, user_id: 'default' })

async function loadSystemInfo() {
  try {
    const response = await axios.get('/api/user/me')
    systemInfo.value = response.data
  } catch (err) {
    console.error('Failed to load system info:', err)
  }
}

// Theme
const selectedTheme = ref('light')

// Clear data
const clearing = ref(false)
const clearSuccess = ref(false)
const clearError = ref('')
const clearModal = ref(null)

// AI integration state
const aiSettings = ref({ rerank: false, synthesize: false })

// Ids of all currently-configured providers (drives AI Features visibility)
const configuredProviderIds = computed(() => getConfiguredProviderIds())

// Expand/edit state
const expandedProvider = ref(null)
const editBuffer = ref({})
const showEditKey = ref(false)
const validatingProvider = ref(null)
const savingProvider = ref(null)

// Ollama detection state
const ollamaCheckStatus = ref(null) // null | 'checking' | 'available' | 'unavailable'
const detectedOllamaModels = ref([])

// Custom provider add form
const showAddCustomForm = ref(false)
const newCustom = ref({ name: '', baseUrl: '', apiKey: '', model: '' })

// Derived: all custom provider configs
const customProvidersConfig = computed(() => getProvidersConfig().filter(p => p.isCustom))

const isProviderConfigured = (id) => {
  const cfg = getProviderConfig(id)
  if (!cfg) return false
  const def = PROVIDER_DEFS.find(d => d.id === id)
  if (!def) return !!cfg.baseUrl  // custom
  if (def.type === 'local') return !!cfg.available
  return !!cfg.apiKey
}

const getProviderModelLabel = (id) => {
  const cfg = getProviderConfig(id)
  return cfg?.model || ''
}

const toggleExpand = (id) => {
  if (expandedProvider.value === id) {
    expandedProvider.value = null
    return
  }
  expandedProvider.value = id
  showEditKey.value = false
  // Init edit buffer from stored config
  const cfg = getProviderConfig(id) || {}
  editBuffer.value = {
    apiKey: cfg.apiKey || '',
    model: cfg.model || '',
    baseUrl: cfg.baseUrl || (id === 'ollama' ? 'http://localhost:11434' : ''),
    name: cfg.name || '',
    keyDirty: false,
    keyStatus: cfg.apiKey ? 'saved' : '',
    keyError: '',
  }
  if (id === 'ollama' && ollamaCheckStatus.value === null) {
    detectOllama()
  }
}

const toggleAIFeature = (feature) => {
  aiSettings.value[feature] = !aiSettings.value[feature]
  const settings = getAISettings()
  localStorage.setItem('ai_settings', JSON.stringify({ ...settings, ...aiSettings.value }))
}

const validateAndSave = async (id) => {
  const key = editBuffer.value.apiKey?.trim()
  if (!key) return
  validatingProvider.value = id
  editBuffer.value.keyStatus = ''
  editBuffer.value.keyError = ''

  try {
    const response = await axios.post('/api/ai/validate-key', null, {
      headers: {
        'X-AI-Key': key,
        'X-AI-Provider': id,
        ...(editBuffer.value.model ? { 'X-AI-Model': editBuffer.value.model } : {}),
      },
    })
    if (response.data.valid) {
      upsertProviderConfig(id, {
        apiKey: key,
        model: editBuffer.value.model || '',
      })
      editBuffer.value.keyStatus = 'valid'
      editBuffer.value.keyDirty = false
    } else {
      editBuffer.value.keyStatus = 'invalid'
      editBuffer.value.keyError = response.data.error || ''
    }
  } catch {
    editBuffer.value.keyStatus = 'invalid'
    editBuffer.value.keyError = 'Request failed'
  } finally {
    validatingProvider.value = null
  }
}

const removeProvider = (id) => {
  removeProviderConfig(id)
  expandedProvider.value = null
}

// Ollama
const detectOllama = async () => {
  ollamaCheckStatus.value = 'checking'
  const baseUrl = editBuffer.value.baseUrl?.trim() || 'http://localhost:11434'
  try {
    const response = await axios.get('/api/ollama/status', {
      params: { base_url: baseUrl },
    })
    if (response.data.available) {
      ollamaCheckStatus.value = 'available'
      detectedOllamaModels.value = response.data.models || []
      // Pre-select first model if none chosen
      const stored = getProviderConfig('ollama')?.model
      if (stored && detectedOllamaModels.value.find(m => m.name === stored)) {
        editBuffer.value.model = stored
      } else if (detectedOllamaModels.value.length > 0) {
        editBuffer.value.model = detectedOllamaModels.value[0].name
      }
    } else {
      ollamaCheckStatus.value = 'unavailable'
    }
  } catch {
    ollamaCheckStatus.value = 'unavailable'
  }
}

const saveOllamaConfig = () => {
  savingProvider.value = 'ollama'
  const baseUrl = editBuffer.value.baseUrl?.trim() || 'http://localhost:11434'
  upsertProviderConfig('ollama', {
    baseUrl,
    model: editBuffer.value.model || '',
    available: ollamaCheckStatus.value === 'available',
  })
  savingProvider.value = null
}

// Custom provider CRUD
const addCustomProvider = () => {
  if (!newCustom.value.baseUrl?.trim() || !newCustom.value.name?.trim()) return
  const id = `custom_${Date.now()}`
  upsertProviderConfig(id, {
    id,
    name: newCustom.value.name.trim(),
    baseUrl: newCustom.value.baseUrl.trim(),
    apiKey: newCustom.value.apiKey?.trim() || '',
    model: newCustom.value.model?.trim() || '',
    isCustom: true,
  })
  newCustom.value = { name: '', baseUrl: '', apiKey: '', model: '' }
  showAddCustomForm.value = false
}

const saveCustomProvider = (id) => {
  upsertProviderConfig(id, {
    name: editBuffer.value.name?.trim(),
    baseUrl: editBuffer.value.baseUrl?.trim(),
    apiKey: editBuffer.value.apiKey?.trim() || '',
    model: editBuffer.value.model?.trim() || '',
  })
  expandedProvider.value = null
}

const formatBytes = (bytes) => {
  if (!bytes) return ''
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i]
}

const loadAIState = () => {
  migrateLegacySettings()
  const settings = getAISettings()
  aiSettings.value = {
    rerank: !!settings.rerank,
    synthesize: !!settings.synthesize,
  }
}

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
  loadAIState()
  loadSystemInfo()

  // Load theme
  const savedTheme = localStorage.getItem('theme')
  if (savedTheme) {
    selectedTheme.value = savedTheme
  } else {
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
    selectedTheme.value = prefersDark ? 'dark' : 'light'
  }

  loadMcpCollections()
  loadMcpSettings()
  loadMcpResources()
})
</script>




