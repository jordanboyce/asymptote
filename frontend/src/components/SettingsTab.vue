<template>
  <div class="space-y-6">

    <!-- Appearance -->
    <div class="card bg-base-200">
      <div class="card-body space-y-4">
        <div>
          <h3 class="card-title text-base">Appearance</h3>
          <p class="text-sm text-base-content/70 mt-0.5">Visual theme and interface options.</p>
        </div>

        <div class="form-control">
          <label class="label p-0 pb-1" for="theme-select">
            <span class="label-text font-medium">Theme</span>
          </label>
          <select
            id="theme-select"
            v-model="selectedTheme"
            class="select select-bordered w-full max-w-xs"
            @change="applyTheme"
          >
            <option value="light">Light</option>
            <option value="dark">Dark</option>
            <option value="cupcake">Cupcake</option>
            <option value="dracula">Dracula</option>
            <option value="nord">Nord</option>
          </select>
        </div>

        <div class="rounded-lg border border-base-300 bg-base-100 p-3">
          <label class="flex cursor-pointer items-start gap-4">
            <input type="checkbox" class="toggle toggle-primary toggle-sm" v-model="chatTabEnabled" @change="saveChatTabSetting" />
            <div>
              <span class="label-text font-medium">Chat Tab</span>
              <p class="text-xs text-base-content/60">Built-in chat interface. Disable if you use Claude Desktop or another MCP client instead.</p>
            </div>
          </label>
        </div>
      </div>
    </div>

    <!-- AI Providers -->
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
          <div v-for="def in PROVIDER_DEFS" :key="def.id">
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

                <div class="form-control">
                  <label class="label p-0 pb-1" for="ollama-num-ctx">
                    <span class="label-text font-medium">Context window (num_ctx)</span>
                  </label>
                  <input
                    id="ollama-num-ctx"
                    v-model.number="ollamaNumCtx"
                    type="number"
                    min="512"
                    step="512"
                    placeholder="8192"
                    class="input input-bordered input-sm w-full"
                    @change="saveOllamaNumCtx"
                  />
                  <p class="text-xs text-base-content/50 mt-1">
                    Tokens of context sent to local Ollama models. Ollama's default is small (~2048) and
                    silently truncates longer prompts — raise this so the model sees your full documents
                    (higher uses more memory). Applies to chat, search, and OCR cleanup.
                  </p>
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
                      @input="editBuffer.keyDirty = true; editBuffer.keyStatus = ''"
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

          <!-- Add Custom Provider row -->
          <div v-if="showAddCustomForm" class="border-t border-base-300 bg-base-200/30 px-4 py-4 space-y-3">
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

        <button v-if="!showAddCustomForm" class="btn btn-ghost btn-sm self-start gap-1" @click="showAddCustomForm = true">
          + Add Custom Endpoint
        </button>

      </div>
    </div>

    <!-- OCR Settings -->
    <div class="card bg-base-200">
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
                OCR reuses a provider you've already set up above in <strong>AI Providers</strong> —
                pick one here and its key is reused automatically. Choose <strong>None</strong> to use
                Docling (free, local) when available.
              </p>
            </div>

            <div class="grid gap-4 grid-cols-1 sm:grid-cols-2 xl:grid-cols-3">
              <div class="form-control">
                <label class="label pb-1" for="vision-provider">
                  <span class="label-text font-medium">Provider</span>
                </label>
                <select id="vision-provider" v-model="visionProvider" class="select select-bordered w-full" @change="onOcrProviderChange">
                  <option value="none">None — Docling (free, local)</option>
                  <option v-for="opt in ocrConfiguredProviders" :key="opt.value" :value="opt.value">
                    {{ opt.label }}
                  </option>
                </select>
                <p v-if="!ocrConfiguredProviders.length" class="text-xs text-warning mt-1">
                  No AI providers configured yet. Add one in AI Providers above, or use None (Docling).
                </p>
                <p v-else class="text-xs text-base-content/55 mt-1">
                  The chosen model must support image input (vision).
                </p>
              </div>

              <!-- Ollama (local): detect vision-capable models -->
              <div class="form-control" v-if="visionProvider === 'ollama'">
                <label class="label pb-1" for="vision-model-ollama">
                  <span class="label-text font-medium">Vision model</span>
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

              <!-- Cloud / hosted providers: vision model -->
              <div class="form-control" v-else-if="visionIsCloud">
                <label class="label pb-1" for="vision-model-cloud"><span class="label-text font-medium">Vision model</span></label>
                <input
                  id="vision-model-cloud"
                  v-model="visionModel"
                  class="input input-bordered w-full"
                  placeholder="e.g. gpt-4o, claude-sonnet-4-5, qwen2.5-vl"
                  @change="saveOCRSettings"
                />
                <p class="text-xs text-base-content/50 mt-1">
                  Reused from your {{ visionProviderLabel }} setup. Override here if it isn't vision-capable.
                </p>
              </div>

              <!-- API Key (cloud/hosted) -->
              <div class="form-control" v-if="visionIsCloud">
                <label class="label pb-1" for="vision-apikey">
                  <span class="label-text font-medium">API Key</span>
                  <span v-if="visionApiKey" class="label-text-alt text-success">reused from {{ visionProviderLabel }}</span>
                </label>
                <input
                  id="vision-apikey"
                  v-model="visionApiKey"
                  type="password"
                  class="input input-bordered w-full"
                  :placeholder="visionApiKey ? '(using saved key)' : 'paste a key to override'"
                  @change="saveOCRSettings"
                />
                <p class="text-xs text-base-content/50 mt-1">Stored on the server so background indexing can use it.</p>
              </div>

              <!-- Ollama URL (local only) -->
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
              <p class="text-xs text-base-content/60 mb-3">Optional text-only model for the cleanup pass, on the same provider. Leave blank to reuse the vision model.</p>
              <div class="form-control max-w-xs">
                <select v-if="visionProvider === 'ollama' && ocrOllamaAllModels.length" v-model="visionCleanupModel" class="select select-bordered w-full" @change="saveOCRSettings" aria-labelledby="cleanup-model-heading">
                  <option value="">(same as vision model)</option>
                  <option v-for="m in ocrOllamaAllModels" :key="m.name" :value="m.name">{{ m.name }}</option>
                </select>
                <input
                  v-else
                  v-model="visionCleanupModel"
                  class="input input-bordered w-full"
                  placeholder="(same as vision model)"
                  @change="saveOCRSettings"
                  aria-labelledby="cleanup-model-heading"
                />
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

    <!-- Embedding Settings -->
    <div class="card bg-base-200">
      <div class="card-body space-y-4">
        <div>
          <h3 class="card-title text-base">Embedding</h3>
          <p class="text-sm text-base-content/70 mt-0.5">
            How document chunks and search queries are converted into vectors.
            Changing the provider or model requires re-indexing all collections.
          </p>
        </div>

        <!-- Provider selector -->
        <div class="form-control">
          <label class="label p-0 pb-1"><span class="label-text font-medium">Provider</span></label>
          <div class="flex gap-3 flex-wrap">
            <label class="flex items-center gap-2 cursor-pointer">
              <input type="radio" class="radio radio-primary radio-sm" value="local" v-model="embeddingProvider" />
              <span class="text-sm">Local <span class="text-xs text-base-content/50">(sentence-transformers)</span></span>
            </label>
            <label class="flex items-center gap-2 cursor-pointer">
              <input type="radio" class="radio radio-primary radio-sm" value="ollama" v-model="embeddingProvider" @change="detectEmbeddingOllama" />
              <span class="text-sm">Ollama <span class="text-xs text-base-content/50">(fully local, no HF download)</span></span>
            </label>
          </div>
        </div>

        <!-- Local: model name -->
        <div v-if="embeddingProvider === 'local'" class="form-control">
          <label class="label p-0 pb-1" for="embedding-model-local"><span class="label-text font-medium">Model</span></label>
          <input
            id="embedding-model-local"
            v-model="embeddingModel"
            type="text"
            placeholder="all-MiniLM-L6-v2"
            class="input input-bordered input-sm w-full max-w-sm"
          />
          <p class="text-xs text-base-content/50 mt-1">
            Any <a href="https://www.sbert.net/docs/pretrained_models.html" target="_blank" class="link link-primary">sentence-transformers</a> model.
            Downloaded from HuggingFace on first use.
          </p>
        </div>

        <!-- Ollama: base URL + model -->
        <div v-if="embeddingProvider === 'ollama'" class="space-y-3">
          <div class="form-control">
            <label class="label p-0 pb-1" for="embedding-ollama-url"><span class="label-text font-medium">Ollama URL</span></label>
            <input
              id="embedding-ollama-url"
              v-model="ollamaBaseUrl"
              type="url"
              placeholder="http://localhost:11434"
              class="input input-bordered input-sm w-full max-w-sm"
            />
          </div>

          <div class="form-control">
            <label class="label p-0 pb-1" for="embedding-ollama-model"><span class="label-text font-medium">Embedding Model</span></label>
            <div class="flex gap-2 items-start flex-wrap">
              <div class="flex-1 min-w-[180px] max-w-xs">
                <select
                  v-if="embeddingOllamaModels.length"
                  id="embedding-ollama-model"
                  v-model="ollamaEmbeddingModel"
                  class="select select-bordered select-sm w-full"
                >
                  <option v-for="m in embeddingOllamaModels" :key="m.name" :value="m.name">{{ m.name }}</option>
                </select>
                <input
                  v-else
                  id="embedding-ollama-model"
                  v-model="ollamaEmbeddingModel"
                  type="text"
                  placeholder="nomic-embed-text"
                  class="input input-bordered input-sm w-full"
                />
              </div>
              <button class="btn btn-sm btn-ghost flex-shrink-0" @click="detectEmbeddingOllama" :disabled="embeddingOllamaStatus === 'checking'">
                <span v-if="embeddingOllamaStatus === 'checking'" class="loading loading-spinner loading-xs"></span>
                <span v-else>Detect</span>
              </button>
            </div>
            <p v-if="embeddingOllamaStatus === 'error'" class="text-xs text-warning mt-1">
              Ollama not reachable at <code>{{ ollamaBaseUrl }}</code>. Check that Ollama is running.
              <span v-if="ollamaBaseUrl.includes('localhost') || ollamaBaseUrl.includes('127.0.0.1')">
                Running in Docker? Use <code>http://host.docker.internal:11434</code> instead.
              </span>
            </p>
            <p v-else class="text-xs text-base-content/50 mt-1">
              Recommended: <code>nomic-embed-text</code> or <code>mxbai-embed-large</code>.
              Pull first: <code>ollama pull nomic-embed-text</code>
            </p>
          </div>
        </div>

        <!-- Save row -->
        <div class="flex items-center gap-3 flex-wrap">
          <button class="btn btn-sm btn-primary" @click="saveEmbeddingSettings">Save</button>
          <span v-if="embeddingSettingsSaved && !embeddingNeedsReindex" class="text-success text-sm">Saved</span>
          <span v-if="embeddingSettingsSaved && embeddingNeedsReindex" class="text-warning text-sm font-medium">
            Saved — restart the server and re-index all collections to apply changes.
          </span>
        </div>
      </div>
    </div>

    <!-- Re-index Collection -->
    <div class="card bg-base-200">
      <div class="card-body space-y-3">
        <div>
          <h3 class="card-title text-base">Re-index Collection</h3>
          <p class="text-sm text-base-content/70 mt-0.5">
            Re-process all documents using current settings. Useful after changing indexing or OCR configuration.
          </p>
        </div>

        <!-- Chunk settings -->
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

        <div v-if="chunkSettingsSaved" class="alert alert-success py-2 text-sm">
          <span>Chunk settings saved. Re-index to apply.</span>
        </div>
        <div v-if="chunkSettingsError" class="alert alert-error py-2 text-sm">
          <span>{{ chunkSettingsError }}</span>
        </div>

        <div v-if="reindexSuccess" class="alert alert-success">
          <span>Re-indexing started. Progress is shown in the status bar.</span>
        </div>
        <div v-if="reindexCompleted" class="alert alert-success">
          <span>Re-index complete. Sources have been refreshed.</span>
        </div>
        <div v-if="reindexError" class="alert alert-error">
          <span>{{ reindexError }}</span>
        </div>

        <div class="card-actions justify-end">
          <button class="btn btn-warning" @click="startReindex" :disabled="reindexing || reindexInProgress">
            <span v-if="reindexing || reindexInProgress" class="loading loading-spinner"></span>
            {{ reindexing ? 'Starting...' : (reindexInProgress ? 'Re-indexing...' : 'Re-index All Documents') }}
          </button>
        </div>
      </div>
    </div>

    <!-- LLM Column Inference -->
    <div class="card bg-base-200">
      <div class="card-body space-y-3">
        <div>
          <h3 class="card-title text-base">Table Column Inference</h3>
          <p class="text-sm text-base-content/70 mt-0.5">
            When importing CSV or Excel files, use AI to identify column roles that heuristics can't determine automatically. Requires an AI provider configured above.
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
              <span class="label-text font-medium">Enable AI Column Detection</span>
              <p class="text-xs text-base-content/60">
                Column names and sample values are sent to your AI provider for role assignment.
              </p>
            </div>
          </label>
        </div>

        <div v-if="inferenceSettingsSaved" class="alert alert-success py-2">
          <span>Saved.</span>
        </div>
      </div>
    </div>

    <!-- System Info -->
    <div class="rounded-xl border border-base-300 bg-base-100 px-4 py-3 flex flex-wrap gap-x-6 gap-y-1 items-center text-sm">
      <span class="text-xs font-semibold uppercase tracking-widest text-base-content/40">System</span>
      <span class="text-base-content/70">
        <span class="text-base-content/40 text-xs mr-1">DB</span>
        <span class="font-medium">{{ systemInfo.db_backend === 'postgresql' ? 'PostgreSQL' : 'SQLite' }}</span>
      </span>
      <span class="text-base-content/70">
        <span class="text-base-content/40 text-xs mr-1">Multi-user</span>
        <span class="font-medium">{{ systemInfo.multi_user ? 'On' : 'Off' }}</span>
      </span>
      <span class="text-base-content/70">
        <span class="text-base-content/40 text-xs mr-1">User</span>
        <span class="font-medium">{{ systemInfo.user_id || 'default' }}</span>
      </span>
      <span class="text-base-content/30 text-xs ml-auto hidden sm:block">Configure via .env</span>
    </div>

    <!-- Danger Zone -->
    <div class="card bg-error/10 border border-error">
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
import { ref, computed, onMounted, watch } from 'vue'
import axios from 'axios'
import { useCollectionStore } from '../stores/collectionStore'
import { useBackgroundJobsStore } from '../stores/backgroundJobsStore'
import {
  PROVIDER_DEFS,
  getProvidersConfig,
  getProviderConfig,
  upsertProviderConfig,
  removeProviderConfig,
  getConfiguredProviderIds,
  getProviderDisplayName,
  migrateLegacySettings,
} from '../utils/aiProviders.js'

const emit = defineEmits(['data-cleared', 'stats-updated', 'switch-tab', 'chat-tab-toggled'])

const collectionStore = useCollectionStore()
const backgroundJobsStore = useBackgroundJobsStore()

// UI feature flags
const chatTabEnabled = ref(true)

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

// Local Ollama context window (server-side setting; applies to all Ollama calls)
const ollamaNumCtx = ref(8192)

// Embedding settings
const embeddingProvider = ref('local')  // 'local' | 'ollama'
const embeddingModel = ref('all-MiniLM-L6-v2')  // local sentence-transformers model
const ollamaEmbeddingModel = ref('nomic-embed-text')  // ollama model for embeddings
const ollamaBaseUrl = ref('http://localhost:11434')
const embeddingSettingsSaved = ref(false)
const embeddingNeedsReindex = ref(false)
const embeddingOllamaModels = ref([])
const embeddingOllamaStatus = ref(null)  // null | 'checking' | 'ok' | 'error'

// Preferred *vision-capable* model per provider for OCR. OCR needs image input,
// so we default to a known multimodal model rather than blindly reusing the
// provider's chat model (which may be text-only, e.g. gpt-oss). The user can
// still override in the model field.
const OCR_DEFAULT_MODELS = {
  openai: 'gpt-4o',
  anthropic: 'claude-opus-4-6',
  google: 'gemini-2.0-flash',
  grok: 'grok-2-vision-1212',
  github: 'openai/gpt-4o',
  openrouter: 'openai/gpt-4o',
  ollama_cloud: 'gemma4:31b',
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

// Providers OCR can reuse: everything the user configured for chat, minus
// custom OpenAI-compatible endpoints (those need a base_url OCR doesn't carry yet).
// Local Ollama is handled with its own vision-model detection.
const ocrConfiguredProviders = computed(() =>
  configuredProviderIds.value
    .filter((id) => !(getProviderConfig(id)?.isCustom))
    .map((id) => ({ value: id, label: getProviderDisplayName(id) }))
)

const visionIsCloud = computed(
  () => visionProvider.value !== 'none' && visionProvider.value !== 'ollama'
)

const visionProviderLabel = computed(() =>
  visionProvider.value && visionProvider.value !== 'none'
    ? getProviderDisplayName(visionProvider.value)
    : ''
)

const onOcrProviderChange = () => {
  const id = visionProvider.value
  visionCleanupModel.value = ''

  if (id === 'none') {
    visionModel.value = ''
    visionApiKey.value = ''
    saveOCRSettings()
    return
  }

  if (id === 'ollama') {
    visionModel.value = ''
    visionApiKey.value = ''
    const cfg = getProviderConfig('ollama')
    if (cfg?.baseUrl) visionOllamaUrl.value = cfg.baseUrl
    refreshOcrOllamaVisionModels()
    fetchOcrAllOllamaModels()
    saveOCRSettings()
    return
  }

  // Cloud / hosted provider: reuse the key from its saved config so the user
  // doesn't re-enter anything. For the model, prefer the provider's known
  // vision-capable default (OCR needs image input) over its chat model, which
  // may be text-only. The user can still override below.
  const cfg = getProviderConfig(id) || {}
  visionApiKey.value = cfg.apiKey || ''
  visionModel.value = OCR_DEFAULT_MODELS[id] || cfg.model || ''
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

const saveOllamaNumCtx = async () => {
  let v = Number(ollamaNumCtx.value)
  if (!Number.isFinite(v) || v < 512) v = 8192
  v = Math.round(v)
  ollamaNumCtx.value = v
  try {
    await axios.post('/api/config', { ollama_num_ctx: v })
  } catch {
    // non-fatal; keep the entered value
  }
}

const saveChatTabSetting = async () => {
  try {
    await axios.post('/api/config', { enable_chat_tab: chatTabEnabled.value })
    emit('chat-tab-toggled', chatTabEnabled.value)
  } catch {
    // revert on failure
    chatTabEnabled.value = !chatTabEnabled.value
  }
}

const loadEmbeddingSettings = async () => {
  try {
    const response = await axios.get('/api/config')
    embeddingProvider.value = response.data.embedding_provider || 'local'
    embeddingModel.value = response.data.embedding_model || 'all-MiniLM-L6-v2'
    ollamaEmbeddingModel.value = response.data.ollama_embedding_model || 'nomic-embed-text'
    ollamaBaseUrl.value = response.data.ollama_base_url || 'http://localhost:11434'
  } catch {
    // use defaults
  }
}

const saveEmbeddingSettings = async () => {
  try {
    const result = await axios.post('/api/config', {
      embedding_provider: embeddingProvider.value,
      embedding_model: embeddingModel.value,
      ollama_embedding_model: ollamaEmbeddingModel.value,
      ollama_base_url: ollamaBaseUrl.value,
    })
    embeddingSettingsSaved.value = true
    embeddingNeedsReindex.value = result.data.requires_reindex || false
    setTimeout(() => { embeddingSettingsSaved.value = false }, 5000)
  } catch (error) {
    console.error('Failed to save embedding settings:', error.response?.data?.detail || error)
  }
}

const detectEmbeddingOllama = async () => {
  embeddingOllamaStatus.value = 'checking'
  embeddingOllamaModels.value = []
  try {
    const resp = await axios.get('/api/ollama/status')
    if (resp.data.available) {
      embeddingOllamaModels.value = resp.data.models || []
      embeddingOllamaStatus.value = 'ok'
    } else {
      embeddingOllamaStatus.value = 'error'
    }
  } catch {
    embeddingOllamaStatus.value = 'error'
  }
}

const loadOCRSettings = async () => {
  try {
    const response = await axios.get('/api/config')
    chatTabEnabled.value = response.data.enable_chat_tab ?? true
    ollamaNumCtx.value = response.data.ollama_num_ctx ?? 8192
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

    // If no key is stored server-side, inherit it from the matching provider's
    // saved config so OCR works without re-entering credentials.
    if (!visionApiKey.value && visionProvider.value !== 'none' && visionProvider.value !== 'ollama') {
      const cfg = getProviderConfig(visionProvider.value)
      if (cfg?.apiKey) {
        visionApiKey.value = cfg.apiKey
        visionKeyFromStorage.value = true
      } else {
        loadVisionKeyFromStorage(visionProvider.value)
      }
      if (!visionModel.value) {
        visionModel.value = OCR_DEFAULT_MODELS[visionProvider.value] || cfg?.model || ''
      }
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
    setTimeout(() => { ocrSettingsSaved.value = false }, 5000)
  } catch (error) {
    console.error('Failed to save OCR settings:', error.response?.data?.detail || error)
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
    setTimeout(() => { inferenceSettingsSaved.value = false }, 5000)
  } catch (error) {
    console.error('Failed to save inference settings:', error.response?.data?.detail || error)
  }
}

// Re-index collection
const reindexing = ref(false)
const reindexSuccess = ref(false)
const reindexCompleted = ref(false)
const reindexError = ref('')

const reindexInProgress = computed(() => {
  const job = backgroundJobsStore.reindexJob
  return job && (job.status === 'pending' || job.status === 'running')
})

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
    setTimeout(() => { chunkSettingsError.value = '' }, 5000)
    return
  }
  if (!Number.isFinite(overlap) || overlap < 0 || overlap >= size) {
    chunkSettingsError.value = 'Overlap must be 0 or greater and less than chunk size.'
    setTimeout(() => { chunkSettingsError.value = '' }, 5000)
    return
  }
  try {
    await collectionStore.updateCollection(collectionId, {
      chunk_size: size,
      chunk_overlap: overlap,
    })
    chunkSettingsSaved.value = true
    setTimeout(() => { chunkSettingsSaved.value = false }, 4000)
  } catch (err) {
    chunkSettingsError.value = err.response?.data?.detail || 'Failed to save chunk settings.'
    setTimeout(() => { chunkSettingsError.value = '' }, 5000)
  }
}

watch(() => collectionStore.currentCollectionId, loadChunkSettings, { immediate: true })
watch(() => collectionStore.currentCollection, loadChunkSettings)

// Surface reindex completion inline (sidebar refreshes independently)
watch(() => backgroundJobsStore.reindexJob?.status, (newStatus, oldStatus) => {
  if (newStatus === 'completed' && oldStatus && oldStatus !== 'completed') {
    reindexCompleted.value = true
    setTimeout(() => { reindexCompleted.value = false }, 8000)
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
    setTimeout(() => { reindexSuccess.value = false }, 8000)
  } catch (error) {
    reindexError.value = error.response?.data?.detail || 'Failed to start re-indexing'
    setTimeout(() => { reindexError.value = '' }, 8000)
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
const selectedTheme = ref('light')

// Clear data
const clearing = ref(false)
const clearSuccess = ref(false)
const clearError = ref('')
const clearModal = ref(null)

// Ids of all currently-configured providers
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

  loadEmbeddingSettings()
  loadOCRSettings()
  loadInferenceSettings()
})
</script>
