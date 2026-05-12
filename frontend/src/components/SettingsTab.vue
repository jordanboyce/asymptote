<template>
  <div class="space-y-6">

    <!-- Basic / Expert Mode Toggle -->
    <div class="card bg-base-100 border border-base-300">
      <div class="card-body py-4 px-5">
        <div class="flex items-center justify-between gap-4 flex-wrap">
          <div class="min-w-0">
            <h3 class="font-semibold text-sm">Interface Mode</h3>
            <p class="text-xs text-base-content/55 mt-0.5">
              {{ isExpertMode
                ? 'Expert mode — all settings and tools are visible.'
                : 'Basic mode — showing only the essentials. Switch to Expert for advanced configuration.' }}
            </p>
          </div>
          <label class="flex items-center gap-3 flex-shrink-0 cursor-pointer select-none">
            <span class="text-sm font-medium" :class="!isExpertMode ? 'text-base-content' : 'text-base-content/40'">Basic</span>
            <input
              type="checkbox"
              class="toggle toggle-primary"
              :checked="isExpertMode"
              @change="toggleExpertMode"
              aria-label="Toggle expert mode"
            />
            <span class="text-sm font-medium" :class="isExpertMode ? 'text-base-content' : 'text-base-content/40'">Expert</span>
          </label>
        </div>
      </div>
    </div>

    <!-- System Info (expert only) -->
    <div v-if="isExpertMode" class="card bg-base-200">
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

    <!-- UI Feature Flags (expert only) -->
    <div v-if="isExpertMode" class="card bg-base-200">
      <div class="card-body space-y-3">
        <div>
          <h3 class="card-title text-base">UI Features</h3>
          <p class="text-sm text-base-content/70 mt-0.5">Toggle optional interface panels.</p>
        </div>
        <div class="rounded-lg border border-base-300 bg-base-100 p-3">
          <label class="flex cursor-pointer items-start gap-4">
            <input type="checkbox" class="toggle toggle-primary toggle-sm" v-model="searchTabEnabled" @change="saveSearchTabSetting" />
            <div>
              <span class="label-text font-medium">Search Tab</span>
              <p class="text-xs text-base-content/60">Standalone retrieval surface for ad-hoc queries against indexed sources. Chat is the primary advisor surface; turn this off if you only work through chat.</p>
            </div>
          </label>
        </div>
      </div>
    </div>

    <!-- AI Integration -->
    <div class="card bg-base-200">
      <div class="card-body space-y-4">
        <div>
          <h3 class="card-title text-base">AI Providers</h3>
          <p class="text-sm text-base-content/70 mt-0.5">
            Configure providers and select models per-search in the Search tab. API keys are stored in your browser only, never on the server.
          </p>
        </div>

        <!-- Provider Table -->
        <div class="rounded-xl border border-base-300 bg-base-100 divide-y divide-base-300 overflow-hidden">

          <!-- Built-in Provider Rows -->
          <div v-for="def in visibleProviderDefs()" :key="def.id">
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
                <span
                  v-if="isProviderConfigured(def.id) && activeProviderId === def.id"
                  class="badge badge-primary badge-xs"
                  title="This is the provider used for chat right now"
                >Active</span>
                <span
                  v-else-if="isProviderConfigured(def.id)"
                  class="badge badge-success badge-xs"
                >Configured</span>
                <span v-else class="text-base-content/35 text-xs hidden sm:block">Not set up</span>
                <span class="text-base-content/35 text-xs">{{ expandedProvider === def.id ? '▲' : '▼' }}</span>
              </div>
            </div>

            <!-- Config panel -->
            <div v-if="expandedProvider === def.id" class="border-t border-base-300 bg-base-200/30 px-4 py-4 space-y-3">

              <!-- Ollama config -->
              <template v-if="def.id === 'ollama'">
                <div class="form-control">
                  <label class="label p-0 pb-1" for="ollama-base-url">
                    <span class="label-text font-medium">Base URL</span>
                  </label>
                  <input
                    id="ollama-base-url"
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
                    <label class="label p-0 pb-1" for="ollama-model"><span class="label-text font-medium">Model</span></label>
                    <select id="ollama-model" v-model="editBuffer.model" class="select select-bordered select-sm w-full">
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
                  <label class="label p-0 pb-1" :for="`provider-apikey-${def.id}`">
                    <span class="label-text font-medium">API Key</span>
                    <a v-if="def.keyLink" :href="def.keyLink" target="_blank" rel="noopener" class="label-text-alt link link-primary text-xs">Get a key ↗</a>
                  </label>
                  <div class="join w-full">
                    <input
                      :id="`provider-apikey-${def.id}`"
                      v-model="editBuffer.apiKey"
                      :type="showEditKey ? 'text' : 'password'"
                      :placeholder="def.keyPlaceholder || 'API key…'"
                      class="input input-bordered input-sm join-item flex-1"
                      @input="editBuffer.keyDirty = true; editBuffer.keyStatus = ''; editBuffer.errorCode = ''"
                    />
                    <button
                      class="btn btn-sm join-item"
                      @click="showEditKey = !showEditKey"
                      :aria-label="showEditKey ? 'Hide API key' : 'Show API key'"
                    >
                      {{ showEditKey ? 'Hide' : 'Show' }}
                    </button>
                  </div>
                </div>

                <div v-if="def.models && def.models.length" class="form-control">
                  <label class="label p-0 pb-1" :for="`provider-model-${def.id}`"><span class="label-text font-medium">Model</span></label>
                  <p class="text-xs text-base-content/50 mb-1">Override the default model. Leave blank to use provider defaults.</p>
                  <select :id="`provider-model-${def.id}`" v-model="editBuffer.model" class="select select-bordered select-sm w-full">
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
                    {{ validatingProvider === def.id ? 'Testing…' : 'Test connection' }}
                  </button>
                  <button
                    v-if="isProviderConfigured(def.id) && activeProviderId !== def.id"
                    class="btn btn-sm btn-ghost"
                    @click="setActiveProvider(def.id)"
                    title="Use this provider for chat"
                  >Make active</button>
                  <button
                    v-if="isProviderConfigured(def.id) && !editBuffer.keyDirty"
                    class="btn btn-sm btn-ghost text-error"
                    @click="removeProvider(def.id)"
                  >Remove</button>
                  <span v-if="editBuffer.keyStatus === 'valid'" class="text-success text-sm font-semibold">✓ Connection OK · saved</span>
                  <span v-else-if="editBuffer.keyStatus === 'invalid'" class="text-error text-sm font-semibold">✗ {{ validateErrorMessage(editBuffer.errorCode) || 'Invalid' }}</span>
                  <span v-else-if="editBuffer.keyStatus === 'saved'" class="text-success text-sm font-semibold">Saved</span>
                </div>

                <div v-if="editBuffer.capabilities" class="mt-2 rounded border border-base-300 bg-base-100 px-3 py-2 text-xs space-y-1">
                  <div class="flex items-center gap-2 flex-wrap">
                    <span class="font-semibold">Model capabilities:</span>
                    <span class="text-base-content/60">{{ editBuffer.capabilities.model || '(none)' }}</span>
                    <span v-if="editBuffer.capabilities.probed" class="badge badge-xs badge-success">probed</span>
                    <span v-else class="badge badge-xs badge-ghost">declared</span>
                  </div>
                  <div class="flex items-center gap-3 flex-wrap">
                    <span :class="capabilityClass(editBuffer.capabilities.tools)">
                      {{ capabilityIcon(editBuffer.capabilities.tools) }} Tool calling
                    </span>
                    <span :class="capabilityClass(editBuffer.capabilities.vision)">
                      {{ capabilityIcon(editBuffer.capabilities.vision) }} Vision
                    </span>
                    <span :class="capabilityClass(editBuffer.capabilities.streaming)">
                      {{ capabilityIcon(editBuffer.capabilities.streaming) }} Streaming
                    </span>
                    <span v-if="editBuffer.capabilities.context_window" class="text-base-content/60">
                      ctx {{ Math.round(editBuffer.capabilities.context_window / 1000) }}k
                    </span>
                  </div>
                  <div v-if="editBuffer.capabilities.tools === false" class="text-warning">
                    Chat will fall back to a text-based ReAct loop. Slower and less reliable than native tool calls — pick a tool-capable model for the best experience.
                  </div>
                  <div v-for="(note, i) in (editBuffer.capabilities.notes || [])" :key="i" class="text-base-content/50">
                    {{ note }}
                  </div>
                </div>
              </template>
            </div>
          </div>

          <!-- Custom Provider Rows (expert only) -->
          <template v-if="isExpertMode">
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
                    <label class="label p-0 pb-1" :for="`custom-name-${cp.id}`"><span class="label-text font-medium">Name</span></label>
                    <input :id="`custom-name-${cp.id}`" v-model="editBuffer.name" type="text" placeholder="My vLLM Server" class="input input-bordered input-sm w-full" />
                  </div>
                  <div class="form-control">
                    <label class="label p-0 pb-1" :for="`custom-model-${cp.id}`"><span class="label-text font-medium">Model</span></label>
                    <input :id="`custom-model-${cp.id}`" v-model="editBuffer.model" type="text" placeholder="model-name" class="input input-bordered input-sm w-full" />
                  </div>
                </div>
                <div class="form-control">
                  <label class="label p-0 pb-1" :for="`custom-baseurl-${cp.id}`"><span class="label-text font-medium">Base URL</span></label>
                  <input :id="`custom-baseurl-${cp.id}`" v-model="editBuffer.baseUrl" type="url" placeholder="http://localhost:8000/v1" class="input input-bordered input-sm w-full" />
                  <p class="text-xs text-base-content/50 mt-1">Must be an OpenAI-compatible endpoint (e.g. vLLM, LM Studio, Groq, OpenRouter).</p>
                </div>
                <div class="form-control">
                  <label class="label p-0 pb-1" :for="`custom-apikey-${cp.id}`">
                    <span class="label-text font-medium">API Key <span class="font-normal opacity-50">(optional)</span></span>
                  </label>
                  <input :id="`custom-apikey-${cp.id}`" v-model="editBuffer.apiKey" type="password" placeholder="none or your key" class="input input-bordered input-sm w-full" />
                </div>
                <div class="flex gap-2">
                  <button class="btn btn-sm btn-primary" @click="saveCustomProvider(cp.id)" :disabled="!editBuffer.baseUrl?.trim() || !editBuffer.name?.trim()">Save</button>
                  <button class="btn btn-sm btn-ghost text-error" @click="removeProvider(cp.id)">Remove</button>
                </div>
              </div>
            </div>
          </template>

          <!-- Add Custom Provider row (expert only) -->
          <div v-if="showAddCustomForm && isExpertMode" class="border-t border-base-300 bg-base-200/30 px-4 py-4 space-y-3">
            <p class="text-sm font-medium">New Custom Endpoint</p>
            <div class="grid gap-3 sm:grid-cols-2">
              <div class="form-control">
                <label class="label p-0 pb-1" for="new-custom-name"><span class="label-text font-medium">Name</span></label>
                <input id="new-custom-name" v-model="newCustom.name" type="text" placeholder="My vLLM Server" class="input input-bordered input-sm w-full" />
              </div>
              <div class="form-control">
                <label class="label p-0 pb-1" for="new-custom-model"><span class="label-text font-medium">Model</span></label>
                <input id="new-custom-model" v-model="newCustom.model" type="text" placeholder="model-name" class="input input-bordered input-sm w-full" />
              </div>
            </div>
            <div class="form-control">
              <label class="label p-0 pb-1" for="new-custom-baseurl"><span class="label-text font-medium">Base URL</span></label>
              <input id="new-custom-baseurl" v-model="newCustom.baseUrl" type="url" placeholder="http://localhost:8000/v1" class="input input-bordered input-sm w-full" />
              <p class="text-xs text-base-content/50 mt-1">OpenAI-compatible endpoint (vLLM, LM Studio, Groq, OpenRouter, etc.).</p>
            </div>
            <div class="form-control">
              <label class="label p-0 pb-1" for="new-custom-apikey">
                <span class="label-text font-medium">API Key <span class="font-normal opacity-50">(optional)</span></span>
              </label>
              <input id="new-custom-apikey" v-model="newCustom.apiKey" type="password" placeholder="none or your key" class="input input-bordered input-sm w-full" />
            </div>
            <div class="flex gap-2">
              <button class="btn btn-sm btn-primary" @click="addCustomProvider" :disabled="!newCustom.baseUrl?.trim() || !newCustom.name?.trim()">Add</button>
              <button class="btn btn-sm btn-ghost" @click="showAddCustomForm = false">Cancel</button>
            </div>
          </div>
        </div>

        <button v-if="!showAddCustomForm && isExpertMode" class="btn btn-ghost btn-sm self-start gap-1" @click="showAddCustomForm = true">
          + Add Custom Endpoint
        </button>

        <!-- AI Feature Toggles (expert only — these are Search-tab defaults
             and not part of the basic-mode chat surface) -->
        <div v-if="isExpertMode && configuredProviderIds.length > 0" class="rounded-xl border border-base-300 bg-base-100 p-4 shadow-sm space-y-3">
          <div>
            <h4 class="text-sm font-semibold uppercase tracking-[0.18em] text-base-content/70">AI Features (Defaults)</h4>
            <p class="mt-1 text-xs text-base-content/60">Default on/off state for reranking and synthesis when you run a search. You can also toggle these live in the Search tab.</p>
          </div>
          <div class="space-y-2">
            <div class="rounded-lg border border-base-300 bg-base-200/50 p-3">
              <label class="flex cursor-pointer items-start gap-4">
                <input type="checkbox" class="toggle toggle-primary toggle-sm" :checked="aiSettings.rerank" @change="toggleAIFeature('rerank')" />
                <div>
                  <span class="label-text font-medium">Result Reranking</span>
                  <p class="text-xs text-base-content/60">Use AI to judge which results actually answer your question.</p>
                </div>
              </label>
            </div>
            <div class="rounded-lg border border-base-300 bg-base-200/50 p-3">
              <label class="flex cursor-pointer items-start gap-4">
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


    <!-- OCR Settings (expert only) -->
    <div v-if="isExpertMode" class="card bg-base-200">
      <div class="card-body space-y-4">
        <div>
          <h3 class="card-title text-base">OCR Settings</h3>
          <p class="text-sm text-base-content/70 mt-0.5">
            Configure OCR for scanned PDFs. Applies to document indexing and the OCR preview playground.
          </p>
        </div>

        <div class="rounded-lg border border-base-300 bg-base-100 p-3">
          <label class="flex cursor-pointer items-start gap-4">
            <input
              type="checkbox"
              class="toggle toggle-primary toggle-sm flex-shrink-0"
              v-model="ocrEnabled"
              @change="saveOCRSettings"
            />
            <div class="min-w-0">
              <span class="label-text font-medium">Enable OCR for Scanned PDFs</span>
              <p class="text-xs text-base-content/60">
                Scanned PDFs with little or no native text will be processed with OCR during indexing.
              </p>
            </div>
          </label>
        </div>

        <div v-if="ocrEnabled" class="space-y-6">
          <!-- Vision AI Provider -->
          <section class="rounded-xl border border-base-300 bg-base-100 p-4 shadow-sm">
            <div class="mb-4">
              <h4 class="text-sm font-semibold uppercase tracking-[0.18em] text-base-content/70">Vision AI Provider</h4>
              <p class="mt-1 text-xs text-base-content/60">
                Vision AI sends each PDF page as an image to a language model for text extraction.
                More accurate than traditional OCR for complex layouts and degraded scans.
                Set provider to "None" to fall back to local OCR (Tesseract / pdfplumber).
              </p>
            </div>

            <div class="grid gap-4 grid-cols-1 sm:grid-cols-2 xl:grid-cols-3">
              <div class="form-control">
                <label class="label pb-1" for="vision-provider">
                  <span class="label-text font-medium">Provider</span>
                </label>
                <select id="vision-provider" v-model="visionProvider" class="select select-bordered w-full" @change="onOcrProviderChange">
                  <option value="none">None (local OCR fallback)</option>
                  <option value="openai">OpenAI</option>
                  <option value="anthropic">Anthropic</option>
                  <option value="ollama">Ollama (local)</option>
                </select>
              </div>

              <!-- OpenAI model -->
              <div class="form-control" v-if="visionProvider === 'openai'">
                <label class="label pb-1" for="vision-model-openai"><span class="label-text font-medium">Model</span></label>
                <select id="vision-model-openai" v-model="visionModel" class="select select-bordered w-full" @change="saveOCRSettings">
                  <option value="gpt-4o">gpt-4o (best quality)</option>
                  <option value="gpt-4o-mini">gpt-4o-mini (faster, cheaper)</option>
                </select>
              </div>

              <!-- Anthropic model -->
              <div class="form-control" v-else-if="visionProvider === 'anthropic'">
                <label class="label pb-1" for="vision-model-anthropic"><span class="label-text font-medium">Model</span></label>
                <select id="vision-model-anthropic" v-model="visionModel" class="select select-bordered w-full" @change="saveOCRSettings">
                  <option value="claude-opus-4-7">claude-opus-4-7 (best quality)</option>
                  <option value="claude-sonnet-4-6">claude-sonnet-4-6 (balanced)</option>
                  <option value="claude-haiku-4-5-20251001">claude-haiku-4-5 (fastest)</option>
                </select>
              </div>

              <!-- Ollama model -->
              <div class="form-control" v-else-if="visionProvider === 'ollama'">
                <label class="label pb-1" for="vision-model-ollama">
                  <span class="label-text font-medium">Model</span>
                  <button
                    class="label-text-alt btn btn-xs btn-ghost"
                    @click="refreshOcrOllamaVisionModels"
                    :disabled="ocrOllamaVisionLoading"
                    aria-label="Refresh Ollama vision models"
                  >
                    {{ ocrOllamaVisionLoading ? '...' : 'Refresh' }}
                  </button>
                </label>
                <select id="vision-model-ollama" v-if="ocrOllamaVisionModels.length" v-model="visionModel" class="select select-bordered w-full" @change="saveOCRSettings">
                  <option v-for="m in ocrOllamaVisionModels" :key="m.name" :value="m.name">{{ m.name }}</option>
                </select>
                <div v-else-if="ocrOllamaVisionLoading" class="text-xs text-base-content/60 py-2">Checking models...</div>
                <div v-else class="alert alert-warning py-2 text-xs">
                  <span v-if="ocrOllamaVisionTotal > 0">
                    {{ ocrOllamaVisionTotal }} model(s) installed but none support vision. Try: <code>ollama pull qwen2.5-vl</code>
                  </span>
                  <span v-else>Ollama not running or no models installed. <code>ollama pull qwen2.5-vl</code></span>
                </div>
              </div>

              <!-- API Key (cloud providers) -->
              <div class="form-control" v-if="visionProvider !== 'none' && visionProvider !== 'ollama'">
                <label class="label pb-1" for="vision-apikey">
                  <span class="label-text font-medium">API Key (stored on server for indexing)</span>
                  <span v-if="visionKeyFromStorage" class="label-text-alt text-success">loaded from settings</span>
                </label>
                <input
                  id="vision-apikey"
                  v-model="visionApiKey"
                  type="password"
                  class="input input-bordered w-full"
                  :placeholder="visionKeyFromStorage ? '(using saved key)' : 'sk-... or sk-ant-...'"
                  @change="saveOCRSettings"
                />
              </div>

              <!-- Ollama URL -->
              <div class="form-control" v-if="visionProvider === 'ollama'">
                <label class="label pb-1" for="vision-ollama-url"><span class="label-text font-medium">Ollama URL</span></label>
                <input
                  id="vision-ollama-url"
                  v-model="visionOllamaUrl"
                  class="input input-bordered w-full"
                  placeholder="http://localhost:11434"
                  @change="saveOCRSettings"
                />
              </div>

              <!-- DPI -->
              <div class="form-control" v-if="visionProvider !== 'none'">
                <label class="label pb-1" for="vision-dpi"><span class="label-text font-medium">Render DPI</span></label>
                <input id="vision-dpi" v-model.number="visionDpi" type="number" min="72" max="400" class="input input-bordered w-full" @change="saveOCRSettings" aria-describedby="vision-dpi-help" />
                <p id="vision-dpi-help" class="text-xs text-base-content/50 mt-1">150 is usually sufficient. Higher = better quality, slower.</p>
              </div>
            </div>

            <div v-if="visionProvider !== 'none'" class="mt-4 grid gap-3 grid-cols-1 sm:grid-cols-3">
              <div class="rounded-xl border border-base-300 bg-base-200/50 p-3 min-w-0">
                <label class="flex cursor-pointer items-start gap-3">
                  <input type="checkbox" class="toggle toggle-primary toggle-sm flex-shrink-0" v-model="visionEnhanceImage" @change="saveOCRSettings" />
                  <div class="min-w-0">
                    <span class="label-text font-medium">Enhance Image</span>
                    <p class="text-xs text-base-content/60">Boost contrast and sharpness for degraded scans.</p>
                  </div>
                </label>
              </div>

              <div class="rounded-xl border border-base-300 bg-base-200/50 p-3 min-w-0">
                <label class="flex cursor-pointer items-start gap-3">
                  <input type="checkbox" class="toggle toggle-primary toggle-sm flex-shrink-0" v-model="visionCleanupPass" @change="saveOCRSettings" />
                  <div class="min-w-0">
                    <span class="label-text font-medium">LLM Cleanup Pass</span>
                    <p class="text-xs text-base-content/60">Second LLM call to fix OCR errors. Uses extra tokens.</p>
                  </div>
                </label>
              </div>

              <div class="rounded-xl border border-base-300 bg-base-200/50 p-3 min-w-0">
                <label class="flex cursor-pointer items-start gap-3">
                  <input type="checkbox" class="toggle toggle-secondary toggle-sm flex-shrink-0" v-model="visionFormMode" @change="saveOCRSettings" />
                  <div class="min-w-0">
                    <span class="label-text font-medium">Form Mode</span>
                    <p class="text-xs text-base-content/60">Form-aware prompt for scanned government/regulatory forms.</p>
                  </div>
                </label>
              </div>
            </div>

            <!-- Cleanup model -->
            <div v-if="visionProvider !== 'none' && visionCleanupPass" class="mt-4 rounded-xl border border-base-300 bg-base-100 p-4">
              <h4 id="cleanup-model-heading" class="text-sm font-semibold uppercase tracking-[0.18em] text-base-content/70 mb-2">Cleanup Model</h4>
              <p class="text-xs text-base-content/60 mb-3">Text-only model for the cleanup pass. Leave blank to reuse the vision model.</p>
              <div class="form-control max-w-xs">
                <select v-if="visionProvider === 'openai'" v-model="visionCleanupModel" class="select select-bordered w-full" @change="saveOCRSettings" aria-labelledby="cleanup-model-heading">
                  <option value="">(same as vision model)</option>
                  <option value="gpt-4o">gpt-4o</option>
                  <option value="gpt-4o-mini">gpt-4o-mini</option>
                </select>
                <select v-else-if="visionProvider === 'anthropic'" v-model="visionCleanupModel" class="select select-bordered w-full" @change="saveOCRSettings" aria-labelledby="cleanup-model-heading">
                  <option value="">(same as vision model)</option>
                  <option value="claude-opus-4-7">claude-opus-4-7</option>
                  <option value="claude-sonnet-4-6">claude-sonnet-4-6</option>
                  <option value="claude-haiku-4-5-20251001">claude-haiku-4-5</option>
                </select>
                <select v-else-if="visionProvider === 'ollama' && ocrOllamaAllModels.length" v-model="visionCleanupModel" class="select select-bordered w-full" @change="saveOCRSettings" aria-labelledby="cleanup-model-heading">
                  <option value="">(same as vision model)</option>
                  <option v-for="m in ocrOllamaAllModels" :key="m.name" :value="m.name">{{ m.name }}</option>
                </select>
              </div>
            </div>
          </section>

          <!-- Guardrails -->
          <section class="rounded-xl border border-base-300 bg-base-100 p-4 shadow-sm">
            <div class="mb-4">
              <h4 class="text-sm font-semibold uppercase tracking-[0.18em] text-base-content/70">Guardrails</h4>
              <p class="mt-1 text-xs text-base-content/60">Limit OCR cost and processing time on large documents.</p>
            </div>
            <div class="grid gap-3 md:grid-cols-2">
              <div class="form-control">
                <label class="label pb-1" for="ocr-max-pages"><span class="label-text font-medium">Max Pages</span></label>
                <input id="ocr-max-pages" v-model.number="ocrMaxPages" type="number" min="0" class="input input-bordered w-full" @change="saveOCRSettings" aria-describedby="ocr-max-pages-help" />
                <p id="ocr-max-pages-help" class="text-xs text-base-content/50 mt-1">0 = no limit.</p>
              </div>
              <div class="form-control">
                <label class="label pb-1" for="ocr-max-filesize"><span class="label-text font-medium">Max File Size (MB)</span></label>
                <input id="ocr-max-filesize" v-model.number="ocrMaxFileMb" type="number" min="0" class="input input-bordered w-full" @change="saveOCRSettings" aria-describedby="ocr-max-filesize-help" />
                <p id="ocr-max-filesize-help" class="text-xs text-base-content/50 mt-1">0 = no limit.</p>
              </div>
            </div>
          </section>
        </div>

        <div v-if="ocrSettingsSaved" class="alert alert-success py-2 mt-3">
          <span>Settings saved. Applies to new uploads.</span>
        </div>
      </div>
    </div>

    <!-- Appearance (expert only — basic-mode keeps the OS-resolved theme;
         the picker is a power-user knob the advisor doesn't need on day one) -->
    <div v-if="isExpertMode" class="card bg-base-200">
      <div class="card-body space-y-4">
        <div>
          <h3 class="card-title text-base">Appearance</h3>
          <p class="text-sm text-base-content/70 mt-0.5">Visual theme for the local UI.</p>
        </div>

        <div class="rounded-xl border border-base-300 bg-base-100 p-4 shadow-sm">
          <label class="label p-0 pb-2" for="theme-select">
            <span class="label-text text-sm font-semibold uppercase tracking-[0.18em] text-base-content/70">Theme</span>
          </label>
          <select
            id="theme-select"
            v-model="selectedTheme"
            class="select select-bordered w-full"
            @change="applyTheme"
          >
            <option value="corporate">Light</option>
            <option value="business">Dark</option>
          </select>
        </div>

        <!-- Theme Preview -->
        <div class="rounded-xl border border-base-300 bg-base-100 p-4 shadow-sm">
          <div class="text-xs font-semibold uppercase tracking-[0.18em] text-base-content/70 mb-3">Preview</div>
          <div class="flex flex-wrap gap-2">
            <button class="btn btn-primary btn-sm">Primary</button>
            <button class="btn btn-secondary btn-sm">Secondary</button>
            <button class="btn btn-accent btn-sm">Accent</button>
          </div>
        </div>
      </div>
    </div>

    <!-- Privacy / PII Redaction (expert only — the chat-tab PII pill (§9.2)
         already exposes the live posture in basic mode; the on/off toggle and
         tuning live here for power users) -->
    <div v-if="isExpertMode" class="card bg-base-200">
      <div class="card-body space-y-4">
        <div>
          <h3 class="card-title text-base">Privacy</h3>
          <p class="text-sm text-base-content/70 mt-0.5">
            PII redaction strips personally identifiable information from MCP tool responses before they reach an external LLM. Powered by Microsoft Presidio, runs 100% locally.
          </p>
        </div>

        <div class="rounded-lg border border-base-300 bg-base-100 p-3">
          <label class="flex cursor-pointer items-start gap-4">
            <input
              type="checkbox"
              class="toggle toggle-primary toggle-sm flex-shrink-0"
              v-model="piiRedactionEnabled"
              @change="savePrivacySettings"
            />
            <div class="min-w-0">
              <span class="label-text font-medium">Enable PII Redaction</span>
              <p class="text-xs text-base-content/60">
                All data leaving Finn through MCP is scanned and redacted. Original data remains intact in local storage.
              </p>
            </div>
          </label>
        </div>

        <div v-if="piiRedactionEnabled && isExpertMode" class="space-y-4">
          <section class="rounded-xl border border-base-300 bg-base-100 p-4 shadow-sm">
            <div class="mb-4">
              <h4 class="text-sm font-semibold uppercase tracking-[0.18em] text-base-content/70">Redaction Style</h4>
              <p class="mt-1 text-xs text-base-content/60">
                Controls how redacted entities appear in LLM-facing output.
              </p>
            </div>

            <div class="grid gap-4 md:grid-cols-2">
              <div class="form-control">
                <label class="label pb-1">
                  <span class="label-text font-medium">Replacement Style</span>
                </label>
                <select v-model="piiRedactionStyle" class="select select-bordered w-full" @change="savePrivacySettings">
                  <option value="entity_type">[ENTITY_TYPE] &mdash; e.g. [PERSON], [ACCOUNT_NUMBER]</option>
                  <option value="redacted">[REDACTED] &mdash; opaque, maximum anonymity</option>
                  <option value="consistent_pseudonym">Consistent Pseudonym &mdash; fake but stable names</option>
                  <option value="partial_mask">Partial Mask &mdash; e.g. ****1234</option>
                  <option value="synthetic_placeholder">Synthetic Placeholder &mdash; realistic fake values</option>
                </select>
              </div>

              <div class="form-control">
                <label class="label pb-1">
                  <span class="label-text font-medium">Confidence Threshold</span>
                </label>
                <input
                  type="number"
                  class="input input-bordered w-full"
                  v-model.number="piiScoreThreshold"
                  min="0.1"
                  max="1.0"
                  step="0.05"
                  @change="savePrivacySettings"
                />
                <p class="text-xs text-base-content/50 mt-1">Lower = more aggressive (0.4 recommended for financial data). Range: 0.1 &ndash; 1.0</p>
              </div>
            </div>
          </section>

          <div class="text-xs text-base-content/50 flex items-center gap-1.5">
            <svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
            Detects: names, SSN, email, phone, credit cards, account numbers, routing numbers, addresses, dates of birth, and more.
            All detection runs locally via Presidio &mdash; no data is sent to any external service for PII scanning.
          </div>
        </div>

        <div v-if="privacySettingsSaved" class="alert alert-success mt-3">
          <span>Privacy settings saved.</span>
        </div>
      </div>
    </div>

    <!-- LLM Schema Inference (expert only) -->
    <div v-if="isExpertMode" class="card bg-base-200">
      <div class="card-body space-y-4">
        <div>
          <h3 class="card-title text-base">LLM Column Role Inference</h3>
          <p class="text-sm text-base-content/70 mt-0.5">
            When columns can't be mapped by vendor profiles or heuristics, send column names and redacted samples to an LLM for role assignment. Requires an AI provider above.
          </p>
        </div>

        <div class="rounded-lg border border-base-300 bg-base-100 p-3">
          <label class="flex cursor-pointer items-start gap-4">
            <input
              type="checkbox"
              class="toggle toggle-primary toggle-sm flex-shrink-0"
              v-model="llmSchemaInferenceEnabled"
              @change="saveInferenceSettings"
            />
            <div class="min-w-0">
              <span class="label-text font-medium">Enable LLM Schema Inference</span>
              <p class="text-xs text-base-content/60">
                Sample values are redacted via Presidio before being sent to the LLM provider.
              </p>
            </div>
          </label>
        </div>

        <div v-if="llmSchemaInferenceEnabled" class="space-y-4">
          <div class="form-control">
            <label class="label">
              <span class="label-text">Unmapped Column Threshold</span>
              <span class="label-text-alt">{{ Math.round(llmSchemaInferenceThreshold * 100) }}%</span>
            </label>
            <input
              type="range"
              min="0.1"
              max="1.0"
              step="0.1"
              class="range range-primary range-sm"
              v-model.number="llmSchemaInferenceThreshold"
              @change="saveInferenceSettings"
            />
            <p class="text-xs text-base-content/50 mt-1">
              LLM inference triggers when this percentage or more of columns have no role after heuristic detection.
            </p>
          </div>
        </div>

        <div v-if="inferenceSettingsSaved" class="alert alert-success mt-3">
          <span>Inference settings saved.</span>
        </div>
      </div>
    </div>

    <!-- Re-index Collection (expert only — re-running indexing is a power-user
         operation; basic-mode advisors never need this surface) -->
    <div v-if="isExpertMode" class="card bg-base-200">
      <div class="card-body space-y-3">
        <div>
          <h3 class="card-title text-base">Re-index Collection</h3>
          <p class="text-sm text-base-content/70 mt-0.5">
            Re-process all documents using current settings. Useful after changing indexing, OCR, or vendor profile configuration.
          </p>
        </div>

        <!-- Chunk settings (expert only) -->
        <template v-if="isExpertMode">
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div class="form-control">
              <label class="label py-1">
                <span class="label-text text-sm font-medium">Chunk size</span>
                <span class="label-text-alt text-xs text-base-content/50">characters</span>
              </label>
              <input
                type="number"
                min="100"
                max="4000"
                step="50"
                class="input input-sm input-bordered"
                v-model.number="chunkSize"
                @change="saveChunkSettings"
              />
              <p class="text-xs text-base-content/50 mt-1">
                Smaller = more precise matches. Larger = more context per chunk.
              </p>
            </div>
            <div class="form-control">
              <label class="label py-1">
                <span class="label-text text-sm font-medium">Chunk overlap</span>
                <span class="label-text-alt text-xs text-base-content/50">characters</span>
              </label>
              <input
                type="number"
                min="0"
                max="1000"
                step="25"
                class="input input-sm input-bordered"
                v-model.number="chunkOverlap"
                @change="saveChunkSettings"
              />
              <p class="text-xs text-base-content/50 mt-1">
                Overlap between adjacent chunks to preserve context across boundaries.
              </p>
            </div>
          </div>
          <p class="text-xs text-base-content/60">
            Changes take effect on the next re-index.
          </p>

          <div v-if="chunkSettingsSaved" class="alert alert-success py-2 text-sm">
            <span>Chunk settings saved. Re-index to apply.</span>
          </div>
          <div v-if="chunkSettingsError" class="alert alert-error py-2 text-sm">
            <span>{{ chunkSettingsError }}</span>
          </div>
        </template>

        <div v-if="reindexSuccess" class="alert alert-success">
          <span>Re-indexing started. Progress is shown in the status bar.</span>
        </div>

        <div v-if="reindexCompleted" class="alert alert-success">
          <span>Re-index complete. Sources have been refreshed.</span>
        </div>

        <div v-if="reindexError" class="alert alert-error">
          <span>{{ reindexError }}</span>
        </div>

        <!-- When an upload/index is running we can't safely start a reindex
             (both write to the same FAISS index + metadata SQLite). Surface
             *why* the button is disabled so the user isn't left guessing. -->
        <div v-if="blockedByUpload" class="alert alert-info text-sm">
          <span>An upload or indexing job is currently running. Wait for it to finish (or cancel it from the status bar) before re-indexing.</span>
        </div>

        <!-- In-progress reindex: live counter + cancel control. -->
        <div v-if="reindexInProgress" class="text-xs text-base-content/60 flex items-center justify-end gap-3">
          <span v-if="reindexProgressLabel">{{ reindexProgressLabel }}</span>
          <span v-if="reindexCancelling" class="text-warning">Cancelling…</span>
        </div>

        <div class="card-actions justify-end gap-2">
          <button
            v-if="reindexInProgress"
            class="btn btn-ghost btn-sm"
            @click="cancelReindex"
            :disabled="reindexCancelling"
          >
            {{ reindexCancelling ? 'Cancelling…' : 'Cancel Re-indexing' }}
          </button>
          <button
            class="btn btn-warning"
            @click="startReindex"
            :disabled="reindexing || reindexInProgress || blockedByUpload"
          >
            <span v-if="reindexing || reindexInProgress" class="loading loading-spinner"></span>
            {{ reindexing
              ? 'Starting...'
              : (reindexInProgress ? 'Re-indexing...' : 'Re-index All Documents') }}
          </button>
        </div>
      </div>
    </div>

    <!-- Danger Zone (expert only — destructive op behind the mode toggle) -->
    <div v-if="isExpertMode" class="card bg-error/10 border border-error">
      <div class="card-body space-y-3">
        <div>
          <h3 class="card-title text-base text-error">Danger Zone</h3>
          <p class="text-sm text-base-content/70 mt-0.5">Permanently delete all sources and indexes in the current collection.</p>
        </div>

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
    <dialog ref="clearModal" class="modal" aria-labelledby="clear-all-title">
      <div class="modal-box">
        <h3 id="clear-all-title" class="font-bold text-lg text-error">Clear All Data</h3>
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
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import axios from 'axios'
import { useCollectionStore } from '../stores/collectionStore'
import { useBackgroundJobsStore } from '../stores/backgroundJobsStore'
import { isExpertMode, toggleExpertMode } from '../utils/expertMode.js'
import {
  PROVIDER_DEFS,
  visibleProviderDefs,
  getProvidersConfig,
  getProviderConfig,
  upsertProviderConfig,
  removeProviderConfig,
  getAISettings,
  getActiveProvider,
  setActiveProviderLS,
  getConfiguredProviderIds,
  migrateLegacySettings,
  bootstrapAIDefaultsOnFirstProvider,
} from '../utils/aiProviders.js'
import {
  validateProviderKey,
  validateErrorMessage,
} from '../utils/validateKey.js'

const emit = defineEmits(['data-cleared', 'stats-updated', 'switch-tab', 'search-tab-toggled'])

const collectionStore = useCollectionStore()
const backgroundJobsStore = useBackgroundJobsStore()

// Track auto-reset timers (clear-feedback-after-Ns) so they don't fire on
// an unmounted component when the user navigates away mid-countdown.
const pendingTimers = new Set()
const trackTimeout = (fn, ms) => {
  const id = setTimeout(() => {
    pendingTimers.delete(id)
    fn()
  }, ms)
  pendingTimers.add(id)
  return id
}

// UI feature flags
const searchTabEnabled = ref(true)

// OCR settings state
const ocrEnabled = ref(false)
const ocrMaxPages = ref(25)
const ocrMaxFileMb = ref(50)
const ocrSettingsSaved = ref(false)

const visionProvider = ref('none')
const visionModel = ref('')
const visionApiKey = ref('')
const visionKeyFromStorage = ref(false)
const visionOllamaUrl = ref('http://localhost:11434')
const visionDpi = ref(150)
const visionEnhanceImage = ref(true)
const visionCleanupPass = ref(false)
const visionCleanupModel = ref('')
const visionFormMode = ref(false)

const ocrOllamaVisionModels = ref([])
const ocrOllamaVisionLoading = ref(false)
const ocrOllamaVisionTotal = ref(0)
const ocrOllamaAllModels = ref([])

const OCR_DEFAULT_MODELS = {
  openai: 'gpt-4o',
  anthropic: 'claude-opus-4-7',
  ollama: '',
  none: '',
}

const OCR_STORAGE_KEY_MAP = {
  openai: 'ai_api_key_openai',
  anthropic: 'ai_api_key_anthropic',
}

const loadVisionKeyFromStorage = (provider) => {
  const storageKey = OCR_STORAGE_KEY_MAP[provider]
  if (!storageKey) { visionKeyFromStorage.value = false; return }
  const stored = localStorage.getItem(storageKey)
  if (stored) {
    visionApiKey.value = stored
    visionKeyFromStorage.value = true
  } else {
    visionApiKey.value = ''
    visionKeyFromStorage.value = false
  }
}

const onOcrProviderChange = () => {
  visionModel.value = OCR_DEFAULT_MODELS[visionProvider.value] || ''
  visionCleanupModel.value = ''
  loadVisionKeyFromStorage(visionProvider.value)
  if (visionProvider.value === 'ollama') {
    refreshOcrOllamaVisionModels()
    fetchOcrAllOllamaModels()
  }
  saveOCRSettings()
}

const refreshOcrOllamaVisionModels = async () => {
  ocrOllamaVisionLoading.value = true
  try {
    const response = await axios.get('/api/ollama/vision-models')
    ocrOllamaVisionModels.value = response.data.models || []
    ocrOllamaVisionTotal.value = response.data.total_models || 0
    if (ocrOllamaVisionModels.value.length > 0 && !ocrOllamaVisionModels.value.find(m => m.name === visionModel.value)) {
      visionModel.value = ocrOllamaVisionModels.value[0].name
    }
  } catch {
    ocrOllamaVisionModels.value = []
    ocrOllamaVisionTotal.value = 0
  } finally {
    ocrOllamaVisionLoading.value = false
  }
}

const fetchOcrAllOllamaModels = async () => {
  try {
    const response = await axios.get('/api/ollama/status')
    ocrOllamaAllModels.value = response.data.models || []
  } catch {
    ocrOllamaAllModels.value = []
  }
}

const saveSearchTabSetting = async () => {
  try {
    await axios.post('/api/config', { enable_search_tab: searchTabEnabled.value })
    emit('search-tab-toggled', searchTabEnabled.value)
  } catch {
    // revert on failure
    searchTabEnabled.value = !searchTabEnabled.value
  }
}

const loadOCRSettings = async () => {
  try {
    const response = await axios.get('/api/config')
    searchTabEnabled.value = response.data.enable_search_tab ?? true
    ocrEnabled.value = response.data.enable_ocr || false
    ocrMaxPages.value = response.data.ocr_max_pages ?? 25
    ocrMaxFileMb.value = response.data.ocr_max_file_mb ?? 50
    visionProvider.value = response.data.vision_ocr_provider || 'none'
    visionModel.value = response.data.vision_ocr_model || ''
    visionApiKey.value = response.data.vision_ocr_api_key || ''
    visionOllamaUrl.value = response.data.vision_ocr_ollama_url || 'http://localhost:11434'
    visionDpi.value = response.data.vision_ocr_dpi ?? 150
    visionEnhanceImage.value = response.data.vision_ocr_enhance_image ?? true
    visionCleanupPass.value = response.data.vision_ocr_cleanup_pass ?? true
    visionCleanupModel.value = response.data.vision_ocr_cleanup_model || ''
    visionFormMode.value = response.data.vision_ocr_form_mode ?? false

    if (!visionApiKey.value) {
      loadVisionKeyFromStorage(visionProvider.value)
    } else {
      visionKeyFromStorage.value = false
    }

    if (visionProvider.value === 'ollama') {
      refreshOcrOllamaVisionModels()
      fetchOcrAllOllamaModels()
    }
  } catch {
    // Use defaults
  }
}

const saveOCRSettings = async () => {
  try {
    await axios.post('/api/config', {
      enable_ocr: ocrEnabled.value,
      ocr_max_pages: Number(ocrMaxPages.value),
      ocr_max_file_mb: Number(ocrMaxFileMb.value),
      vision_ocr_provider: visionProvider.value,
      vision_ocr_model: visionModel.value,
      vision_ocr_api_key: visionApiKey.value,
      vision_ocr_dpi: Number(visionDpi.value),
      vision_ocr_enhance_image: Boolean(visionEnhanceImage.value),
      vision_ocr_cleanup_pass: Boolean(visionCleanupPass.value),
      vision_ocr_cleanup_model: visionCleanupModel.value,
      vision_ocr_ollama_url: visionOllamaUrl.value,
      vision_ocr_form_mode: Boolean(visionFormMode.value),
    })
    ocrSettingsSaved.value = true
    trackTimeout(() => { ocrSettingsSaved.value = false }, 5000)
  } catch (error) {
    console.error('Failed to save OCR settings:', error.response?.data?.detail || error)
  }
}

// Privacy / PII redaction settings state
const piiRedactionEnabled = ref(true)
const piiRedactionStyle = ref('entity_type')
const piiScoreThreshold = ref(0.4)
const privacySettingsSaved = ref(false)

const loadPrivacySettings = async () => {
  try {
    const response = await axios.get('/api/config')
    piiRedactionEnabled.value = response.data.enable_pii_redaction ?? true
    piiRedactionStyle.value = response.data.pii_redaction_style || 'entity_type'
    piiScoreThreshold.value = response.data.pii_score_threshold ?? 0.4
  } catch {
    // Use defaults
  }
}

const savePrivacySettings = async () => {
  try {
    await axios.post('/api/config', {
      enable_pii_redaction: Boolean(piiRedactionEnabled.value),
      pii_redaction_style: piiRedactionStyle.value,
      pii_score_threshold: Number(piiScoreThreshold.value),
    })
    privacySettingsSaved.value = true
    trackTimeout(() => { privacySettingsSaved.value = false }, 5000)
  } catch (error) {
    console.error('Failed to save privacy settings:', error.response?.data?.detail || error)
  }
}

// LLM schema inference settings state
const llmSchemaInferenceEnabled = ref(false)
const llmSchemaInferenceThreshold = ref(0.5)
const inferenceSettingsSaved = ref(false)

const loadInferenceSettings = async () => {
  try {
    const response = await axios.get('/api/config')
    llmSchemaInferenceEnabled.value = response.data.enable_llm_schema_inference ?? false
    llmSchemaInferenceThreshold.value = response.data.llm_schema_inference_threshold ?? 0.5
  } catch {
    // Use defaults
  }
}

const saveInferenceSettings = async () => {
  try {
    await axios.post('/api/config', {
      enable_llm_schema_inference: Boolean(llmSchemaInferenceEnabled.value),
      llm_schema_inference_threshold: Number(llmSchemaInferenceThreshold.value),
    })
    inferenceSettingsSaved.value = true
    trackTimeout(() => { inferenceSettingsSaved.value = false }, 5000)
  } catch (error) {
    console.error('Failed to save inference settings:', error.response?.data?.detail || error)
  }
}

// Re-index collection
const reindexing = ref(false)
const reindexSuccess = ref(false)
const reindexCompleted = ref(false)
const reindexError = ref('')
const reindexCancelling = ref(false)

const reindexInProgress = computed(() => {
  const job = backgroundJobsStore.reindexJob
  return job && (job.status === 'pending' || job.status === 'running')
})

// Mutual exclusion: uploads and reindexes share the same on-disk index, so
// either kind running blocks the other. The reindex button needs to know
// whether the active job is an *upload* specifically (so we can tell the
// user why it's disabled — "Re-indexing..." is wrong if a CSV upload is the
// thing blocking it).
const blockedByUpload = computed(() => {
  if (reindexInProgress.value) return false
  return backgroundJobsStore.uploadJobs.some(
    j => j.status === 'pending' || j.status === 'running'
  )
})

const reindexProgressLabel = computed(() => {
  const job = backgroundJobsStore.reindexJob
  if (!job) return ''
  const total = job.total_documents ?? 0
  const done = job.processed_documents ?? 0
  if (total > 0) {
    return job.current_file
      ? `${done}/${total} — ${job.current_file}`
      : `${done}/${total} documents`
  }
  return job.current_file || 'Preparing…'
})

const cancelReindex = async () => {
  const job = backgroundJobsStore.reindexJob
  if (!job?.id) return
  reindexCancelling.value = true
  try {
    await backgroundJobsStore.cancelReindexJob(job.id)
  } catch (err) {
    reindexError.value = err.response?.data?.detail || 'Failed to cancel re-indexing'
    trackTimeout(() => { reindexError.value = '' }, 8000)
  } finally {
    // Leave the cancelling flag on briefly so the button stays in the
    // "Cancelling…" state until the polling loop catches the new status.
    trackTimeout(() => { reindexCancelling.value = false }, 2000)
  }
}

// Chunk settings (per-collection)
const chunkSize = ref(600)
const chunkOverlap = ref(100)
const chunkSettingsSaved = ref(false)
const chunkSettingsError = ref('')

const loadChunkSettings = () => {
  const c = collectionStore.currentCollection
  if (c) {
    chunkSize.value = c.chunk_size ?? 600
    chunkOverlap.value = c.chunk_overlap ?? 100
  }
}

const saveChunkSettings = async () => {
  const collectionId = collectionStore.currentCollection?.id
  if (!collectionId) return
  const size = Number(chunkSize.value)
  const overlap = Number(chunkOverlap.value)
  if (!Number.isFinite(size) || size < 100 || size > 4000) {
    chunkSettingsError.value = 'Chunk size must be between 100 and 4000.'
    trackTimeout(() => { chunkSettingsError.value = '' }, 5000)
    return
  }
  if (!Number.isFinite(overlap) || overlap < 0 || overlap >= size) {
    chunkSettingsError.value = 'Overlap must be 0 or greater and less than chunk size.'
    trackTimeout(() => { chunkSettingsError.value = '' }, 5000)
    return
  }
  try {
    await collectionStore.updateCollection(collectionId, {
      chunk_size: size,
      chunk_overlap: overlap,
    })
    chunkSettingsSaved.value = true
    trackTimeout(() => { chunkSettingsSaved.value = false }, 4000)
  } catch (err) {
    chunkSettingsError.value = err.response?.data?.detail || 'Failed to save chunk settings.'
    trackTimeout(() => { chunkSettingsError.value = '' }, 5000)
  }
}

watch(() => collectionStore.currentCollectionId, loadChunkSettings, { immediate: true })
watch(() => collectionStore.currentCollection, loadChunkSettings)

// Surface reindex completion inline (sidebar refreshes independently)
watch(() => backgroundJobsStore.reindexJob?.status, (newStatus, oldStatus) => {
  if (newStatus === 'completed' && oldStatus && oldStatus !== 'completed') {
    reindexCompleted.value = true
    trackTimeout(() => { reindexCompleted.value = false }, 8000)
  }
  if (newStatus === 'cancelled' && oldStatus && oldStatus !== 'cancelled') {
    reindexCancelling.value = false
    reindexError.value = 'Re-indexing was cancelled. Already-processed documents stayed indexed.'
    trackTimeout(() => { reindexError.value = '' }, 8000)
  }
  if (newStatus === 'failed' && oldStatus && oldStatus !== 'failed') {
    reindexCancelling.value = false
  }
})

const startReindex = async () => {
  const collectionId = collectionStore.currentCollection?.id || 'default'
  reindexing.value = true
  reindexError.value = ''
  reindexSuccess.value = false
  try {
    const response = await axios.post(`/api/collections/${collectionId}/reindex`)
    backgroundJobsStore.setReindexJob({
      id: response.data.job_id,
      collection_id: collectionId,
      status: 'pending',
      total_documents: 0,
      processed_documents: 0,
      current_file: null,
      started_at: new Date().toISOString(),
    })
    backgroundJobsStore.startPolling()
    reindexSuccess.value = true
    trackTimeout(() => { reindexSuccess.value = false }, 8000)
  } catch (error) {
    reindexError.value = error.response?.data?.detail || 'Failed to start re-indexing'
    trackTimeout(() => { reindexError.value = '' }, 8000)
  } finally {
    reindexing.value = false
  }
}

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
const selectedTheme = ref('corporate')

// Clear data
const clearing = ref(false)
const clearSuccess = ref(false)
const clearError = ref('')
const clearModal = ref(null)

// AI integration state
const aiSettings = ref({ rerank: true, synthesize: true })

// Ids of all currently-configured providers (drives AI Features visibility)
const configuredProviderIds = computed(() => getConfiguredProviderIds())

// Currently-active provider id; surfaces as the "Active" badge and gates the
// "Make active" button. Kept as a plain ref (not a computed over localStorage)
// because localStorage reads aren't reactive — we update it explicitly when
// the user toggles via setActiveProvider() and on mount.
const activeProviderId = ref(getActiveProvider() || '')

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
  const def = PROVIDER_DEFS.find(d => d.id === id)
  editBuffer.value = {
    apiKey: cfg.apiKey || '',
    model: cfg.model || def?.defaultModel || '',
    baseUrl: cfg.baseUrl || (id === 'ollama' ? 'http://localhost:11434' : ''),
    name: cfg.name || '',
    keyDirty: false,
    keyStatus: cfg.apiKey ? 'saved' : '',
    errorCode: '',
    capabilities: null,
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
  editBuffer.value.errorCode = ''

  const result = await validateProviderKey({
    provider: id,
    apiKey: key,
    model: editBuffer.value.model || undefined,
  })

  if (result.valid) {
    upsertProviderConfig(id, {
      apiKey: key,
      model: editBuffer.value.model || '',
    })
    // Auto-promote the first-configured provider to active so chat works
    // immediately after the very first Test connection in Settings.
    if (!activeProviderId.value) {
      setActiveProviderLS(id)
      activeProviderId.value = id
      // Flip AI defaults ON before notifying listeners so SearchTab/ChatTab
      // re-read the post-bootstrap values when they handle the event.
      bootstrapAIDefaultsOnFirstProvider()
      // Pull the just-flipped defaults into the live AI Features card too.
      const refreshedAI = getAISettings()
      aiSettings.value = {
        rerank: refreshedAI.rerank ?? true,
        synthesize: refreshedAI.synthesize ?? true,
      }
      window.dispatchEvent(new CustomEvent('finn:providers-changed'))
    }
    editBuffer.value.keyStatus = 'valid'
    editBuffer.value.keyDirty = false
    editBuffer.value.capabilities = result.capabilities || null
  } else {
    editBuffer.value.keyStatus = 'invalid'
    editBuffer.value.errorCode = result.code || 'invalid_key'
    editBuffer.value.capabilities = null
  }
  validatingProvider.value = null
}

const setActiveProvider = (id) => {
  setActiveProviderLS(id)
  activeProviderId.value = id
  window.dispatchEvent(new CustomEvent('finn:providers-changed'))
}

const capabilityIcon = (value) => {
  if (value === true) return '✓'
  if (value === false) return '✗'
  return '?'
}

const capabilityClass = (value) => {
  if (value === true) return 'text-success font-semibold'
  if (value === false) return 'text-error font-semibold'
  return 'text-base-content/50'
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
    rerank: settings.rerank ?? true,
    synthesize: settings.synthesize ?? true,
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

    trackTimeout(() => {
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

  // Load theme. App.vue's bootstrap migrates legacy values (light/dark/cupcake/...)
  // to corporate/business and rewrites localStorage, so by the time we read
  // here it's always one of the two we ship — but we still defend against a
  // direct first visit to Settings before App.vue's onMounted fires.
  const savedTheme = localStorage.getItem('theme')
  if (savedTheme === 'corporate' || savedTheme === 'business') {
    selectedTheme.value = savedTheme
  } else {
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
    selectedTheme.value = prefersDark ? 'business' : 'corporate'
  }

  loadOCRSettings()
  loadPrivacySettings()
  loadInferenceSettings()
})

onBeforeUnmount(() => {
  pendingTimers.forEach(clearTimeout)
  pendingTimers.clear()
})
</script>




