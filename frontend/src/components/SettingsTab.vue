<template>
  <div class="max-w-4xl mx-auto">

    <!-- ── Page header: title + basic/advanced depth switch ── -->
    <header class="flex items-end justify-between gap-6 flex-wrap pb-5 mb-6 border-b border-base-300/60">
      <div class="min-w-0">
        <h1 class="text-[22px] leading-none font-semibold tracking-tight">Settings</h1>
        <p class="mt-2 text-xs text-base-content/50">
          {{ advanced ? 'Everything, including expert tuning' : 'The essentials — switch to Advanced for expert tuning' }}
        </p>
      </div>

      <div class="join" role="group" aria-label="Settings depth">
        <button
          class="btn btn-xs join-item px-3"
          :class="!advanced ? 'btn-active' : 'btn-ghost'"
          @click="settingsMode = 'basic'"
          :aria-pressed="!advanced"
        >Basic</button>
        <button
          class="btn btn-xs join-item px-3"
          :class="advanced ? 'btn-active' : 'btn-ghost'"
          @click="settingsMode = 'advanced'"
          :aria-pressed="advanced"
        >Advanced</button>
      </div>
    </header>

    <div class="flex gap-10 items-start">

      <!-- ── Section rail ── -->
      <nav class="w-40 flex-shrink-0 sticky top-2 hidden sm:block" aria-label="Settings sections">
        <ul class="space-y-0.5">
          <li v-for="s in SECTIONS" :key="s.id">
            <button
              class="w-full text-left px-2.5 py-1.5 rounded-md text-[13px] transition-colors"
              :class="activeSection === s.id
                ? 'bg-base-200 font-semibold text-base-content'
                : 'text-base-content/55 hover:text-base-content hover:bg-base-200/50'"
              @click="activeSection = s.id"
              :aria-current="activeSection === s.id ? 'true' : undefined"
            >{{ s.label }}</button>
          </li>
        </ul>
      </nav>

      <!-- Mobile: section picker replaces the rail -->
      <div class="sm:hidden fixed bottom-8 left-0 right-0 px-6 z-20 pointer-events-none">
        <select
          v-model="activeSection"
          class="select select-bordered select-sm w-full shadow-lg pointer-events-auto"
          aria-label="Settings section"
        >
          <option v-for="s in SECTIONS" :key="s.id" :value="s.id">{{ s.label }}</option>
        </select>
      </div>

      <!-- ── Content column ── -->
      <div class="flex-1 min-w-0 max-w-2xl pb-20">

        <!-- ═══ General ═══ -->
        <section v-show="activeSection === 'general'" aria-labelledby="settings-general">
          <h2 id="settings-general" class="sr-only">General</h2>

          <!-- Theme -->
          <div class="pb-8">
            <div class="text-sm font-medium">Theme</div>
            <p class="text-xs text-base-content/55 mt-1 mb-4">Follows your system until you choose one.</p>
            <div class="grid gap-2" style="grid-template-columns: repeat(auto-fill, minmax(104px, 1fr)); max-width: 36rem;">
              <button
                v-for="t in THEMES"
                :key="t.id"
                type="button"
                class="rounded-lg p-0 overflow-hidden text-left border transition-all"
                :class="selectedTheme === t.id
                  ? 'border-base-content/50 ring-1 ring-base-content/25'
                  : 'border-base-300 hover:border-base-content/30'"
                @click="selectedTheme = t.id; applyTheme()"
                :aria-pressed="selectedTheme === t.id"
                :aria-label="`Theme: ${t.label}`"
              >
                <!-- The inner div renders IN the candidate theme, so the
                     swatch is a live preview, not a hard-coded palette. -->
                <div :data-theme="t.id" class="bg-base-100 px-3 pt-3 pb-2.5">
                  <div class="flex gap-1" aria-hidden="true">
                    <span class="w-3.5 h-3.5 rounded-full bg-primary"></span>
                    <span class="w-3.5 h-3.5 rounded-full bg-secondary"></span>
                    <span class="w-3.5 h-3.5 rounded-full bg-accent"></span>
                  </div>
                  <div class="mt-2 text-[11px] font-medium text-base-content flex items-center justify-between gap-1">
                    {{ t.label }}
                    <Check v-if="selectedTheme === t.id" :size="11" aria-hidden="true" />
                  </div>
                </div>
              </button>
            </div>
          </div>

          <!-- Interface (advanced) -->
          <div v-if="advanced" class="pt-6 border-t border-base-300/60">
            <p class="text-[10px] uppercase tracking-[0.18em] font-semibold text-base-content/35 mb-4">Advanced</p>
            <div class="flex items-start justify-between gap-8">
              <div class="min-w-0">
                <div class="text-sm font-medium">Chat tab</div>
                <p class="text-xs text-base-content/55 mt-1 leading-relaxed max-w-[52ch]">
                  The built-in chat interface. Turn off if you work through Claude Desktop or another MCP client instead.
                </p>
              </div>
              <input
                type="checkbox"
                class="toggle toggle-primary toggle-sm flex-shrink-0 mt-0.5"
                v-model="chatTabEnabled"
                @change="saveChatTabSetting"
                aria-label="Enable chat tab"
              />
            </div>
          </div>
        </section>

        <!-- ═══ AI Providers ═══ -->
        <section v-show="activeSection === 'providers'" aria-labelledby="settings-providers">
          <h2 id="settings-providers" class="sr-only">AI Providers</h2>

          <p v-if="!systemInfo.offline_mode" class="text-[13px] text-base-content/55 leading-relaxed max-w-[60ch] mb-5">
            Connect the AI you trust — a cloud key, a local model, or your own OpenAI-compatible
            endpoint. Keys live in your browser; on connect they're also stored on your self-hosted
            server so background features can use them.
          </p>
          <p v-else class="text-[13px] text-base-content/55 leading-relaxed max-w-[60ch] mb-5">
            <ShieldCheck :size="13" class="inline -mt-0.5 mr-1 text-success" aria-hidden="true" />This
            deployment runs air-gapped — cloud AI providers are disabled. Connect a local Ollama
            instance or a self-hosted OpenAI-compatible server on your network.
          </p>

          <!-- Empty state: nothing connected yet -->
          <div v-if="!hasAnyProvider" class="rounded-xl border border-dashed border-base-300 px-4 py-3 space-y-1 mb-4">
            <template v-if="!systemInfo.offline_mode">
              <p class="text-sm font-medium">Nothing connected yet — three quick ways to start:</p>
              <ul class="text-xs text-base-content/60 list-disc list-inside space-y-0.5">
                <li>Paste a cloud API key (Anthropic, OpenAI, …) into a provider below and hit Connect.</li>
                <li>Run <a href="https://ollama.com" target="_blank" rel="noopener" class="link link-primary">Ollama</a> locally, then open the Ollama row.</li>
                <li>Connect LM Studio, vLLM, or any OpenAI-compatible server via "Add your own endpoint" below.</li>
              </ul>
            </template>
            <template v-else>
              <p class="text-sm font-medium">Nothing connected yet — two ways to start:</p>
              <ul class="text-xs text-base-content/60 list-disc list-inside space-y-0.5">
                <li>Run Ollama on this machine or your network, then open the Ollama row.</li>
                <li>Connect LM Studio, vLLM, or any OpenAI-compatible server via "Add your own endpoint" below.</li>
              </ul>
            </template>
          </div>

          <!-- Provider table -->
          <div class="rounded-xl border border-base-300 bg-base-100 divide-y divide-base-300 overflow-hidden">

            <!-- Built-in provider rows -->
            <div v-for="def in visibleProviderDefs" :key="def.id">
              <div
                class="flex items-center gap-3 px-4 py-3 cursor-pointer select-none hover:bg-base-200/50 transition-colors"
                @click="toggleExpand(def.id)"
                role="button"
                :aria-expanded="expandedProvider === def.id"
              >
                <div class="flex-1 min-w-0">
                  <div class="flex items-center gap-2 flex-wrap">
                    <span class="font-medium text-sm">{{ def.name }}</span>
                    <span v-if="def.type === 'local'" class="badge badge-info badge-xs badge-outline">local</span>
                  </div>
                  <div v-if="getProviderModelLabel(def.id)" class="text-xs text-base-content/50 mt-0.5 truncate">{{ getProviderModelLabel(def.id) }}</div>
                </div>
                <div class="flex items-center gap-2 flex-shrink-0">
                  <span v-if="isServerProvider(def.id) && !hasLocalKey(def.id)" class="badge badge-success badge-xs">Team key</span>
                  <span v-else-if="isProviderConfigured(def.id)" class="badge badge-success badge-xs">Connected</span>
                  <span v-else class="text-base-content/35 text-xs hidden sm:block">Not connected</span>
                  <ChevronDown
                    :size="14"
                    class="text-base-content/35 transition-transform"
                    :class="{ 'rotate-180': expandedProvider === def.id }"
                    aria-hidden="true"
                  />
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

                  <div v-if="ollamaCheckStatus === 'checking'" class="flex items-center gap-2 text-sm text-base-content/60">
                    <span class="loading loading-spinner loading-xs"></span> Detecting models…
                  </div>
                  <div v-else-if="ollamaCheckStatus === 'available'" class="space-y-2">
                    <div class="alert alert-success py-2 text-sm">
                      Ollama running · {{ modelsFor('ollama').length }} model(s) available
                    </div>
                    <div class="form-control">
                      <label class="label p-0 pb-1" for="ollama-model">
                        <span class="label-text font-medium">Model</span>
                        <button
                          class="label-text-alt btn btn-xs btn-ghost"
                          @click="detectOllama"
                          aria-label="Refresh Ollama model list"
                        >Refresh</button>
                      </label>
                      <select
                        id="ollama-model"
                        class="select select-bordered select-sm w-full"
                        :value="editBuffer.model"
                        @change="onModelSelect('ollama', $event.target.value)"
                      >
                        <option v-for="m in modelsFor('ollama')" :key="m.id" :value="m.id">{{ m.label }}</option>
                      </select>
                    </div>
                  </div>
                  <div v-else-if="ollamaCheckStatus === 'unavailable'" class="alert alert-warning py-2 text-sm">
                    Ollama not detected at <code class="font-mono">{{ editBuffer.baseUrl || 'http://localhost:11434' }}</code>.
                    <a href="https://ollama.com" target="_blank" class="link link-primary ml-1">Install Ollama</a>
                  </div>
                  <p v-if="ollamaError && ollamaCheckStatus === 'unavailable'" class="text-error text-sm">{{ ollamaError }}</p>

                  <div class="flex gap-2">
                    <button
                      class="btn btn-sm btn-primary"
                      @click="connectOllama"
                      :disabled="savingProvider === 'ollama' || ollamaCheckStatus === 'checking'"
                    >
                      <span v-if="savingProvider === 'ollama'" class="loading loading-spinner loading-xs"></span>
                      Connect
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
                  <p v-if="isServerProvider(def.id)" class="text-xs text-base-content/50">
                    This server already has a team key for {{ def.name }}. Add your own key only if you want to override it.
                  </p>
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
                        @input="editBuffer.keyDirty = true; editBuffer.keyStatus = ''; editBuffer.keyError = ''"
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

                  <div class="form-control">
                    <label class="label p-0 pb-1" :for="`provider-model-${def.id}`">
                      <span class="label-text font-medium">Model</span>
                      <button
                        class="label-text-alt btn btn-xs btn-ghost"
                        @click="loadModels(def.id, { force: true })"
                        :disabled="modelState[def.id]?.loading"
                        aria-label="Refresh model list"
                      >{{ modelState[def.id]?.loading ? '…' : 'Refresh' }}</button>
                    </label>
                    <p class="text-xs text-base-content/50 mb-1">Leave on provider defaults, pick a model, or type any model id.</p>
                    <select
                      :id="`provider-model-${def.id}`"
                      class="select select-bordered select-sm w-full"
                      :value="modelSelectValue()"
                      @change="onModelSelect(def.id, $event.target.value)"
                    >
                      <option value="">Use provider defaults</option>
                      <optgroup v-if="recommendedModels(def.id).length" label="Recommended">
                        <option v-for="m in recommendedModels(def.id)" :key="m.id" :value="m.id">{{ m.label }}</option>
                      </optgroup>
                      <optgroup v-if="otherModels(def.id).length" label="All models">
                        <option v-for="m in otherModels(def.id)" :key="m.id" :value="m.id">{{ m.label }}</option>
                      </optgroup>
                      <option value="__custom__">Custom model id…</option>
                    </select>
                    <input
                      v-if="editBuffer.customModelMode"
                      v-model="editBuffer.model"
                      type="text"
                      placeholder="exact model id"
                      class="input input-bordered input-sm w-full mt-2"
                      @change="saveModelIfConfigured(def.id)"
                    />
                    <p v-if="modelState[def.id]?.error" class="text-error text-sm mt-1">{{ modelState[def.id].error }}</p>
                  </div>

                  <div class="flex items-center gap-2 flex-wrap">
                    <button
                      class="btn btn-sm btn-primary"
                      @click="connectProvider(def.id)"
                      :disabled="connectingProvider === def.id || !editBuffer.apiKey?.trim()"
                    >
                      <span v-if="connectingProvider === def.id" class="loading loading-spinner loading-xs"></span>
                      {{ connectingProvider === def.id ? '' : 'Connect' }}
                    </button>
                    <button
                      v-if="isProviderConfigured(def.id) && !editBuffer.keyDirty"
                      class="btn btn-sm btn-ghost text-error"
                      @click="removeProvider(def.id)"
                    >Remove</button>
                    <span v-if="editBuffer.keyStatus === 'valid'" class="text-success text-sm font-semibold">✓ Connected</span>
                    <span v-if="editBuffer.keyError" class="text-error text-sm">{{ editBuffer.keyError }}</span>
                  </div>
                </template>
              </div>
            </div>

            <!-- Custom endpoint rows (added via "Add your own endpoint") -->
            <div v-for="cp in customProvidersConfig" :key="cp.id">
              <div
                class="flex items-center gap-3 px-4 py-3 cursor-pointer select-none hover:bg-base-200/50 transition-colors"
                @click="toggleExpand(cp.id)"
                role="button"
                :aria-expanded="expandedProvider === cp.id"
              >
                <div class="flex-1 min-w-0">
                  <div class="flex items-center gap-2 flex-wrap">
                    <span class="font-medium text-sm">{{ cp.name }}</span>
                    <span class="badge badge-neutral badge-xs badge-outline">custom</span>
                  </div>
                  <div class="text-xs text-base-content/50 mt-0.5 truncate">{{ cp.baseUrl }}</div>
                </div>
                <div class="flex items-center gap-2 flex-shrink-0">
                  <span class="badge badge-success badge-xs">Connected</span>
                  <ChevronDown
                    :size="14"
                    class="text-base-content/35 transition-transform"
                    :class="{ 'rotate-180': expandedProvider === cp.id }"
                    aria-hidden="true"
                  />
                </div>
              </div>

              <div v-if="expandedProvider === cp.id" class="border-t border-base-300 bg-base-200/30 px-4 py-4 space-y-3">
                <p class="text-xs text-base-content/50">
                  Endpoint <code class="font-mono">{{ cp.baseUrl }}</code>
                </p>
                <div class="form-control">
                  <label class="label p-0 pb-1" :for="`custom-model-${cp.id}`">
                    <span class="label-text font-medium">Model</span>
                    <button
                      class="label-text-alt btn btn-xs btn-ghost"
                      @click="loadModels(cp.id, { force: true })"
                      :disabled="modelState[cp.id]?.loading"
                      aria-label="Refresh model list"
                    >{{ modelState[cp.id]?.loading ? '…' : 'Refresh' }}</button>
                  </label>
                  <select
                    :id="`custom-model-${cp.id}`"
                    class="select select-bordered select-sm w-full"
                    :value="modelSelectValue()"
                    @change="onModelSelect(cp.id, $event.target.value)"
                  >
                    <option value="">Use endpoint default</option>
                    <optgroup v-if="recommendedModels(cp.id).length" label="Recommended">
                      <option v-for="m in recommendedModels(cp.id)" :key="m.id" :value="m.id">{{ m.label }}</option>
                    </optgroup>
                    <optgroup v-if="otherModels(cp.id).length" label="All models">
                      <option v-for="m in otherModels(cp.id)" :key="m.id" :value="m.id">{{ m.label }}</option>
                    </optgroup>
                    <option value="__custom__">Custom model id…</option>
                  </select>
                  <input
                    v-if="editBuffer.customModelMode"
                    v-model="editBuffer.model"
                    type="text"
                    placeholder="exact model id"
                    class="input input-bordered input-sm w-full mt-2"
                    @change="saveModelIfConfigured(cp.id)"
                  />
                  <p v-if="modelState[cp.id]?.error" class="text-error text-sm mt-1">{{ modelState[cp.id].error }}</p>
                </div>
                <div class="flex gap-2">
                  <button class="btn btn-sm btn-ghost text-error" @click="removeProvider(cp.id)">Remove</button>
                </div>
              </div>
            </div>
          </div>

          <!-- Add your own endpoint -->
          <div v-if="showAddEndpoint" class="rounded-xl border border-base-300 bg-base-100 px-4 py-4 space-y-3 mt-3">
            <p class="text-sm font-medium">Add your own endpoint</p>
            <div class="form-control">
              <label class="label p-0 pb-1" for="endpoint-preset">
                <span class="label-text font-medium">Preset</span>
                <a v-if="selectedPreset?.keyLink" :href="selectedPreset.keyLink" target="_blank" rel="noopener" class="label-text-alt link link-primary text-xs">Get a key ↗</a>
              </label>
              <select id="endpoint-preset" v-model="newEndpoint.presetId" class="select select-bordered select-sm w-full max-w-xs" @change="onPresetChange">
                <option v-for="p in availableEndpointPresets" :key="p.id" :value="p.id">{{ p.name }}</option>
              </select>
            </div>
            <div class="grid gap-3 sm:grid-cols-2">
              <div class="form-control">
                <label class="label p-0 pb-1" for="endpoint-name"><span class="label-text font-medium">Display name</span></label>
                <input id="endpoint-name" v-model="newEndpoint.name" type="text" placeholder="My vLLM Server" class="input input-bordered input-sm w-full" />
              </div>
              <div class="form-control">
                <label class="label p-0 pb-1" for="endpoint-apikey">
                  <span class="label-text font-medium">API Key <span v-if="!selectedPreset?.needsKey" class="font-normal opacity-50">(optional)</span></span>
                </label>
                <input id="endpoint-apikey" v-model="newEndpoint.apiKey" type="password" placeholder="none or your key" class="input input-bordered input-sm w-full" />
              </div>
            </div>
            <div class="form-control">
              <label class="label p-0 pb-1" for="endpoint-baseurl"><span class="label-text font-medium">Base URL</span></label>
              <input id="endpoint-baseurl" v-model="newEndpoint.baseUrl" type="url" placeholder="http://localhost:8000/v1" class="input input-bordered input-sm w-full" />
              <p class="text-xs text-base-content/50 mt-1">Any OpenAI-compatible endpoint works (vLLM, LM Studio, LiteLLM, Groq, OpenRouter…).</p>
            </div>
            <p v-if="addEndpointError" class="text-error text-sm">{{ addEndpointError }}</p>
            <div class="flex gap-2">
              <button
                class="btn btn-sm btn-primary"
                @click="testAndAddEndpoint"
                :disabled="addingEndpoint || !newEndpoint.baseUrl?.trim() || !newEndpoint.name?.trim()"
              >
                <span v-if="addingEndpoint" class="loading loading-spinner loading-xs"></span>
                {{ addingEndpoint ? 'Testing…' : 'Test & Add' }}
              </button>
              <button class="btn btn-sm btn-ghost" @click="showAddEndpoint = false">Cancel</button>
            </div>
          </div>
          <button v-else class="btn btn-ghost btn-sm gap-1 mt-3" @click="showAddEndpoint = true">
            + Add your own endpoint
          </button>

          <!-- Ollama context window (advanced) -->
          <div v-if="advanced" class="mt-8 pt-6 border-t border-base-300/60">
            <p class="text-[10px] uppercase tracking-[0.18em] font-semibold text-base-content/35 mb-4">Advanced</p>
            <div class="flex items-start justify-between gap-8">
              <div class="min-w-0">
                <div class="text-sm font-medium">Ollama context window</div>
                <p class="text-xs text-base-content/55 mt-1 leading-relaxed max-w-[52ch]">
                  Tokens of context sent to local Ollama models. Ollama's default is small (~2048)
                  and silently truncates longer prompts — raise this so the model sees your full
                  documents. Higher uses more memory. Applies to chat, search, and OCR cleanup.
                </p>
              </div>
              <input
                id="ollama-num-ctx"
                v-model.number="ollamaNumCtx"
                type="number"
                min="512"
                step="512"
                placeholder="8192"
                class="input input-bordered input-sm w-28 flex-shrink-0 tabular-nums"
                @change="saveOllamaNumCtx"
                aria-label="Ollama context window (tokens)"
              />
            </div>
          </div>
        </section>

        <!-- ═══ Indexing ═══ -->
        <section v-show="activeSection === 'indexing'" aria-labelledby="settings-indexing">
          <h2 id="settings-indexing" class="sr-only">Indexing</h2>

          <p class="text-[13px] text-base-content/55 leading-relaxed max-w-[60ch] mb-5">
            How documents in <span class="font-medium text-base-content/75">{{ collectionStore.currentCollection?.name || 'Default' }}</span>
            are processed and indexed.
          </p>

          <!-- OCR toggle -->
          <div class="flex items-start justify-between gap-8 pb-6">
            <div class="min-w-0">
              <div class="text-sm font-medium">OCR scanned PDFs</div>
              <p class="text-xs text-base-content/55 mt-1 leading-relaxed max-w-[52ch]">
                Pages with little or no native text are read automatically. Applies to new uploads
                and re-indexing.<span v-if="!advanced"> Engine selection is under Advanced.</span>
              </p>
              <p v-if="ocrSettingsSaved" class="text-success text-xs mt-1.5" role="status">Saved — applies to new uploads</p>
            </div>
            <input
              type="checkbox"
              class="toggle toggle-primary toggle-sm flex-shrink-0 mt-0.5"
              v-model="ocrEnabled"
              @change="saveOCRSettings"
              aria-label="OCR scanned PDFs"
            />
          </div>

          <!-- Re-index -->
          <div class="flex items-start justify-between gap-8 py-6 border-t border-base-300/50">
            <div class="min-w-0">
              <div class="text-sm font-medium">Re-index this collection</div>
              <p class="text-xs text-base-content/55 mt-1 leading-relaxed max-w-[52ch]">
                Rebuilds the search index from the original files, applying any changed settings.
                Runs in the background; progress is shown in the status bar.
              </p>
              <p v-if="reindexSuccess" class="text-success text-xs mt-1.5" role="status">Re-indexing started</p>
              <p v-if="reindexCompleted" class="text-success text-xs mt-1.5" role="status">Re-index complete — sources refreshed</p>
              <p v-if="reindexError" class="text-error text-xs mt-1.5" role="alert">{{ reindexError }}</p>
            </div>
            <button
              class="btn btn-sm btn-outline flex-shrink-0"
              @click="startReindex"
              :disabled="reindexing || reindexInProgress"
            >
              <span v-if="reindexing || reindexInProgress" class="loading loading-spinner loading-xs"></span>
              {{ reindexing ? 'Starting…' : (reindexInProgress ? 'Re-indexing…' : 'Re-index') }}
            </button>
          </div>

          <!-- Advanced indexing -->
          <div v-if="advanced" class="pt-6 border-t border-base-300/60 space-y-8">
            <p class="text-[10px] uppercase tracking-[0.18em] font-semibold text-base-content/35">Advanced</p>

            <!-- OCR engine -->
            <div>
              <div class="text-sm font-medium">OCR engine</div>
              <p class="text-xs text-base-content/55 mt-1 leading-relaxed max-w-[56ch]">
                Used when OCR is on. Vision AI reads each page with a multimodal model — most
                accurate for complex layouts and degraded scans. Providers come from
                <button class="link link-primary" @click="activeSection = 'providers'">AI Providers</button>;
                keys and a vision-capable model are reused automatically.
              </p>

              <div class="grid gap-4 grid-cols-1 sm:grid-cols-2 mt-3">
                <div class="form-control">
                  <label class="label p-0 pb-1" for="vision-provider">
                    <span class="label-text text-xs font-medium">Engine</span>
                  </label>
                  <select id="vision-provider" v-model="visionProvider" class="select select-bordered select-sm w-full" @change="onOcrProviderChange">
                    <option value="none">Local — Docling / Tesseract (free, offline)</option>
                    <option v-for="opt in ocrConfiguredProviders" :key="opt.value" :value="opt.value">
                      Vision AI — {{ opt.label }}
                    </option>
                  </select>
                </div>

                <!-- Ollama (local): detect vision-capable models -->
                <div class="form-control" v-if="visionProvider === 'ollama'">
                  <label class="label p-0 pb-1" for="vision-model-ollama">
                    <span class="label-text text-xs font-medium">Vision model</span>
                    <button
                      class="label-text-alt btn btn-xs btn-ghost"
                      @click="refreshOcrOllamaVisionModels"
                      :disabled="ocrOllamaVisionLoading"
                      aria-label="Refresh Ollama vision models"
                    >
                      {{ ocrOllamaVisionLoading ? '…' : 'Refresh' }}
                    </button>
                  </label>
                  <select id="vision-model-ollama" v-if="ocrOllamaVisionModels.length" v-model="visionModel" class="select select-bordered select-sm w-full" @change="saveOCRSettings">
                    <option v-for="m in ocrOllamaVisionModels" :key="m.name" :value="m.name">{{ m.name }}</option>
                  </select>
                  <div v-else-if="ocrOllamaVisionLoading" class="text-xs text-base-content/60 py-2">Checking models…</div>
                  <div v-else class="alert alert-warning py-2 text-xs">
                    <span v-if="ocrOllamaVisionTotal > 0">
                      {{ ocrOllamaVisionTotal }} model(s) installed but none support vision. Try: <code>ollama pull qwen2.5-vl</code>
                    </span>
                    <span v-else>Ollama not running or no models installed. <code>ollama pull qwen2.5-vl</code></span>
                  </div>
                </div>
              </div>
            </div>

            <!-- Embedding -->
            <div class="pt-6 border-t border-base-300/50">
              <div class="text-sm font-medium">Embedding</div>
              <p class="text-xs text-base-content/55 mt-1 leading-relaxed max-w-[56ch]">
                How document chunks and search queries are converted into vectors.
                Changing the provider or model requires re-indexing all collections.
              </p>

              <div class="form-control mt-3">
                <div class="flex gap-4 flex-wrap">
                  <label class="flex items-center gap-2 cursor-pointer">
                    <input type="radio" class="radio radio-primary radio-sm" value="local" v-model="embeddingProvider" />
                    <span class="text-sm">Local <span class="text-xs text-base-content/50">(sentence-transformers)</span></span>
                  </label>
                  <label v-if="!isRemoteDeployment() || embeddingProvider === 'ollama'" class="flex items-center gap-2 cursor-pointer">
                    <input type="radio" class="radio radio-primary radio-sm" value="ollama" v-model="embeddingProvider" @change="detectEmbeddingOllama" />
                    <span class="text-sm">Ollama <span class="text-xs text-base-content/50">(server-side Ollama instance)</span></span>
                  </label>
                </div>
              </div>

              <!-- Local: model name -->
              <div v-if="embeddingProvider === 'local'" class="form-control mt-3">
                <label class="label p-0 pb-1" for="embedding-model-local"><span class="label-text text-xs font-medium">Model</span></label>
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
              <div v-if="embeddingProvider === 'ollama'" class="space-y-3 mt-3">
                <div class="form-control">
                  <label class="label p-0 pb-1" for="embedding-ollama-url"><span class="label-text text-xs font-medium">Ollama URL</span></label>
                  <input
                    id="embedding-ollama-url"
                    v-model="ollamaBaseUrl"
                    type="url"
                    placeholder="http://localhost:11434"
                    class="input input-bordered input-sm w-full max-w-sm"
                  />
                </div>

                <div class="form-control">
                  <label class="label p-0 pb-1" for="embedding-ollama-model"><span class="label-text text-xs font-medium">Embedding model</span></label>
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

              <div class="flex items-center gap-3 flex-wrap mt-3">
                <button class="btn btn-sm btn-primary" @click="saveEmbeddingSettings">Save</button>
                <span v-if="embeddingSettingsSaved && !embeddingNeedsReindex" class="text-success text-sm" role="status">Saved</span>
                <span v-if="embeddingSettingsSaved && embeddingNeedsReindex" class="text-warning text-sm font-medium" role="status">
                  Saved — restart the server and re-index all collections to apply changes.
                </span>
              </div>
            </div>

            <!-- Chunking -->
            <div class="pt-6 border-t border-base-300/50">
              <div class="text-sm font-medium">Chunking</div>
              <p class="text-xs text-base-content/55 mt-1 leading-relaxed max-w-[56ch]">
                Per-collection chunking for search. Re-index to apply changes.
              </p>

              <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-3">
                <div class="form-control">
                  <label class="label py-1">
                    <span class="label-text text-xs font-medium">Chunk size</span>
                    <span class="label-text-alt text-xs text-base-content/50">characters</span>
                  </label>
                  <input
                    type="number"
                    min="100"
                    max="4000"
                    step="50"
                    class="input input-sm input-bordered tabular-nums"
                    v-model.number="chunkSize"
                    @change="saveChunkSettings"
                    aria-label="Chunk size (characters)"
                  />
                  <p class="text-xs text-base-content/50 mt-1">
                    Smaller = more precise matches. Larger = more context per chunk.
                  </p>
                </div>
                <div class="form-control">
                  <label class="label py-1">
                    <span class="label-text text-xs font-medium">Chunk overlap</span>
                    <span class="label-text-alt text-xs text-base-content/50">characters</span>
                  </label>
                  <input
                    type="number"
                    min="0"
                    max="1000"
                    step="25"
                    class="input input-sm input-bordered tabular-nums"
                    v-model.number="chunkOverlap"
                    @change="saveChunkSettings"
                    aria-label="Chunk overlap (characters)"
                  />
                  <p class="text-xs text-base-content/50 mt-1">
                    Overlap between adjacent chunks to preserve context across boundaries.
                  </p>
                </div>
              </div>

              <p v-if="chunkSettingsSaved" class="text-success text-xs mt-2" role="status">Saved — re-index to apply</p>
              <p v-if="chunkSettingsError" class="text-error text-xs mt-2" role="alert">{{ chunkSettingsError }}</p>
            </div>

            <!-- Table column inference -->
            <div class="pt-6 border-t border-base-300/50">
              <div class="flex items-start justify-between gap-8">
                <div class="min-w-0">
                  <div class="text-sm font-medium">AI column detection</div>
                  <p class="text-xs text-base-content/55 mt-1 leading-relaxed max-w-[52ch]">
                    When importing CSV or Excel files, column names and sample values are sent to
                    your AI provider to identify roles that heuristics can't determine.
                  </p>
                  <p v-if="inferenceSettingsSaved" class="text-success text-xs mt-1.5" role="status">Saved</p>
                </div>
                <input
                  type="checkbox"
                  class="toggle toggle-primary toggle-sm flex-shrink-0 mt-0.5"
                  v-model="llmSchemaInferenceEnabled"
                  @change="saveInferenceSettings"
                  aria-label="Enable AI column detection"
                />
              </div>
            </div>
          </div>
        </section>

        <!-- ═══ Data ═══ -->
        <section v-show="activeSection === 'data'" aria-labelledby="settings-data">
          <h2 id="settings-data" class="sr-only">Data</h2>

          <div class="flex items-start justify-between gap-8">
            <div class="min-w-0">
              <div class="text-sm font-medium text-error">Clear collection data</div>
              <p class="text-xs text-base-content/55 mt-1 leading-relaxed max-w-[52ch]">
                Permanently deletes every source and index in
                <span class="font-medium text-base-content/75">{{ collectionStore.currentCollection?.name || 'Default' }}</span>.
                The collection itself remains. This cannot be undone.
              </p>
              <p v-if="clearSuccess" class="text-success text-xs mt-1.5" role="status">All data cleared</p>
              <p v-if="clearError" class="text-error text-xs mt-1.5" role="alert">{{ clearError }}</p>
            </div>
            <button class="btn btn-sm btn-outline btn-error flex-shrink-0" @click="confirmClearAll" :disabled="clearing">
              <span v-if="clearing" class="loading loading-spinner loading-xs"></span>
              {{ clearing ? 'Clearing…' : 'Clear data' }}
            </button>
          </div>
        </section>

        <!-- ═══ System ═══ -->
        <section v-show="activeSection === 'system'" aria-labelledby="settings-system">
          <h2 id="settings-system" class="sr-only">System</h2>

          <p class="text-[13px] text-base-content/55 leading-relaxed mb-5">
            Server configuration — set via <code class="text-xs">.env</code> or the environment, read-only here.
          </p>

          <dl class="divide-y divide-base-300/50 text-sm">
            <div class="flex items-center justify-between gap-8 py-3">
              <dt class="text-base-content/55">Deployment</dt>
              <dd class="font-medium flex items-center gap-1.5">
                <ShieldCheck v-if="systemInfo.offline_mode" :size="14" class="text-success" aria-hidden="true" />
                {{ systemInfo.offline_mode ? 'Air-gapped (offline mode)' : 'Standard' }}
              </dd>
            </div>
            <div class="flex items-center justify-between gap-8 py-3">
              <dt class="text-base-content/55">Database</dt>
              <dd class="font-medium">{{ systemInfo.db_backend === 'postgresql' ? 'PostgreSQL' : 'SQLite' }}</dd>
            </div>
            <div class="flex items-center justify-between gap-8 py-3">
              <dt class="text-base-content/55">Multi-user</dt>
              <dd class="font-medium">{{ systemInfo.multi_user ? 'On' : 'Off' }}</dd>
            </div>
            <div class="flex items-center justify-between gap-8 py-3">
              <dt class="text-base-content/55">User</dt>
              <dd class="font-medium">{{ systemInfo.user_id || 'default' }}</dd>
            </div>
          </dl>

          <p v-if="systemInfo.offline_mode" class="text-xs text-base-content/50 mt-4 leading-relaxed max-w-[56ch]">
            Cloud AI providers are disabled and models load from the local cache only.
            This deployment makes no outbound connections beyond the endpoints configured on your network.
          </p>
        </section>

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
import { ChevronDown, Check, ShieldCheck } from 'lucide-vue-next'
import { useCollectionStore } from '../stores/collectionStore'
import { useBackgroundJobsStore } from '../stores/backgroundJobsStore'
import {
  PROVIDER_DEFS,
  CUSTOM_ENDPOINT_PRESETS,
  getProvidersConfig,
  getProviderConfig,
  upsertProviderConfig,
  removeProviderConfig,
  getConfiguredProviderIds,
  getProviderDisplayName,
  fetchProviderModels,
  invalidateModelCache,
  testProviderConnection,
  fetchServerProviderIds,
  migrateLegacySettings,
  isRemoteDeployment,
} from '../utils/aiProviders.js'

const emit = defineEmits(['data-cleared', 'stats-updated', 'switch-tab', 'chat-tab-toggled'])

const collectionStore = useCollectionStore()
const backgroundJobsStore = useBackgroundJobsStore()

// ── Settings shell: section rail + basic/advanced depth ──
const SECTIONS = [
  { id: 'general', label: 'General' },
  { id: 'providers', label: 'AI Providers' },
  { id: 'indexing', label: 'Indexing' },
  { id: 'data', label: 'Data' },
  { id: 'system', label: 'System' },
]

const THEMES = [
  { id: 'light', label: 'Light' },
  { id: 'dark', label: 'Dark' },
  { id: 'cupcake', label: 'Cupcake' },
  { id: 'nord', label: 'Nord' },
  { id: 'dracula', label: 'Dracula' },
]

const settingsMode = ref(localStorage.getItem('settings_mode') || 'basic')
watch(settingsMode, v => localStorage.setItem('settings_mode', v))
const advanced = computed(() => settingsMode.value === 'advanced')

const savedSection = localStorage.getItem('settings_section')
const activeSection = ref(SECTIONS.some(s => s.id === savedSection) ? savedSection : 'general')
watch(activeSection, v => localStorage.setItem('settings_section', v))

// UI feature flags
const chatTabEnabled = ref(true)

// OCR settings state — deliberately minimal: on/off + engine (+ auto-picked
// model/key when the engine is a vision provider). Rendering details are fixed
// server-side; page/size cost guards are env-only.
const ocrEnabled = ref(false)
const ocrSettingsSaved = ref(false)

const visionProvider = ref('none')
const visionModel = ref('')
// The key is inherited from the provider's saved config (AI Providers) and
// POSTed to the server so background indexing can use it — no separate input.
const visionApiKey = ref('')

const ocrOllamaVisionModels = ref([])
const ocrOllamaVisionLoading = ref(false)
const ocrOllamaVisionTotal = ref(0)

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
// provider's chat model (which may be text-only, e.g. gpt-oss).
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

// Providers OCR can reuse: everything the user configured for chat, minus
// custom OpenAI-compatible endpoints (those need a base_url OCR doesn't carry yet).
// Local Ollama is handled with its own vision-model detection.
const ocrConfiguredProviders = computed(() =>
  configuredProviderIds.value
    .filter((id) => !(getProviderConfig(id)?.isCustom))
    .map((id) => ({ value: id, label: getProviderDisplayName(id) }))
)

const onOcrProviderChange = () => {
  const id = visionProvider.value

  if (id === 'none') {
    visionModel.value = ''
    visionApiKey.value = ''
    saveOCRSettings()
    return
  }

  if (id === 'ollama') {
    visionModel.value = ''
    visionApiKey.value = ''
    refreshOcrOllamaVisionModels()
    saveOCRSettings()
    return
  }

  // Cloud / hosted provider: reuse the key from its saved config so the user
  // doesn't re-enter anything. For the model, use the provider's known
  // vision-capable default (OCR needs image input) over its chat model, which
  // may be text-only.
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

const loadEmbeddingSettings = (data) => {
  embeddingProvider.value = data.embedding_provider || 'local'
  embeddingModel.value = data.embedding_model || 'all-MiniLM-L6-v2'
  ollamaEmbeddingModel.value = data.ollama_embedding_model || 'nomic-embed-text'
  ollamaBaseUrl.value = data.ollama_base_url || 'http://localhost:11434'
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

const loadOCRSettings = (data) => {
  chatTabEnabled.value = data.enable_chat_tab ?? true
  ollamaNumCtx.value = data.ollama_num_ctx ?? 8192
  ocrEnabled.value = data.enable_ocr || false
  visionProvider.value = data.vision_ocr_provider || 'none'
  visionModel.value = data.vision_ocr_model || ''
  visionApiKey.value = data.vision_ocr_api_key || ''

  // If no key is stored server-side, inherit it from the matching provider's
  // saved config so OCR works without re-entering credentials.
  if (!visionApiKey.value && visionProvider.value !== 'none' && visionProvider.value !== 'ollama') {
    const cfg = getProviderConfig(visionProvider.value)
    if (cfg?.apiKey) {
      visionApiKey.value = cfg.apiKey
    }
    if (!visionModel.value) {
      visionModel.value = OCR_DEFAULT_MODELS[visionProvider.value] || cfg?.model || ''
    }
  }

  if (visionProvider.value === 'ollama') {
    refreshOcrOllamaVisionModels()
  }
}

const saveOCRSettings = async () => {
  try {
    await axios.post('/api/config', {
      enable_ocr: ocrEnabled.value,
      vision_ocr_provider: visionProvider.value,
      vision_ocr_model: visionModel.value,
      vision_ocr_api_key: visionApiKey.value,
    })
    ocrSettingsSaved.value = true
    setTimeout(() => { ocrSettingsSaved.value = false }, 5000)
  } catch (error) {
    console.error('Failed to save OCR settings:', error.response?.data?.detail || error)
  }
}

// LLM schema inference settings state.
// Note: the inference *threshold* (llm_schema_inference_threshold) is
// server-configured (config.py default 0.5, override via .env) and no longer
// sent from the UI.
const llmSchemaInferenceEnabled = ref(false)
const inferenceSettingsSaved = ref(false)

const loadInferenceSettings = (data) => {
  llmSchemaInferenceEnabled.value = data.enable_llm_schema_inference ?? false
}

const saveInferenceSettings = async () => {
  try {
    await axios.post('/api/config', {
      enable_llm_schema_inference: Boolean(llmSchemaInferenceEnabled.value),
    })
    inferenceSettingsSaved.value = true
    setTimeout(() => { inferenceSettingsSaved.value = false }, 5000)
  } catch (error) {
    console.error('Failed to save inference settings:', error.response?.data?.detail || error)
  }
}

// Single /api/config fetch on mount, fanned out to the per-domain loaders.
const loadServerConfig = async () => {
  try {
    const response = await axios.get('/api/config')
    const data = response.data || {}
    loadEmbeddingSettings(data)
    loadOCRSettings(data)
    loadInferenceSettings(data)
  } catch {
    // Use defaults
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

// System info (read-only; offline flag comes from /health)
const systemInfo = ref({ db_backend: 'sqlite', multi_user: false, user_id: 'default', offline_mode: false })

// Air-gapped deployments: the backend rejects cloud providers, so don't
// offer them. Only local Ollama and self-hosted endpoints remain.
const visibleProviderDefs = computed(() => {
  if (systemInfo.value.offline_mode) return PROVIDER_DEFS.filter(d => d.type === 'local')
  // Hosted: hide "local machine" providers — localhost is the server there,
  // not the machine the user is browsing from. Server-side .env still works.
  if (isRemoteDeployment()) return PROVIDER_DEFS.filter(d => d.type !== 'local')
  return PROVIDER_DEFS
})
const availableEndpointPresets = computed(() => {
  let presets = CUSTOM_ENDPOINT_PRESETS
  if (systemInfo.value.offline_mode) presets = presets.filter(p => !p.baseUrl || !p.baseUrl.startsWith('https://'))
  if (isRemoteDeployment()) presets = presets.filter(p => !p.baseUrl || !p.baseUrl.includes('localhost'))
  return presets
})

async function loadSystemInfo() {
  try {
    const response = await axios.get('/api/user/me')
    systemInfo.value = { ...systemInfo.value, ...response.data }
  } catch (err) {
    console.error('Failed to load system info:', err)
  }
  try {
    const health = await axios.get('/health')
    systemInfo.value.offline_mode = !!health.data.offline_mode
  } catch {
    // leave default
  }
}

// Theme
const selectedTheme = ref('light')

// Clear data
const clearing = ref(false)
const clearSuccess = ref(false)
const clearError = ref('')
const clearModal = ref(null)

// Bumped after every provider-config write so computeds/template re-read
// localStorage (which is not reactive by itself).
const configVersion = ref(0)
const bumpConfig = () => { configVersion.value++ }

// Providers with a server-stored team key (hosted/multi-user instances).
const serverProviderIds = ref([])
const isServerProvider = (id) => serverProviderIds.value.includes(id)

// Ids of all currently-configured providers
const configuredProviderIds = computed(() => {
  configVersion.value // reactivity hook
  return getConfiguredProviderIds()
})
const hasAnyProvider = computed(() => configuredProviderIds.value.length > 0)

// Expand/edit state
const expandedProvider = ref(null)
const editBuffer = ref({})
const showEditKey = ref(false)
const connectingProvider = ref(null)
const savingProvider = ref(null)

// Live model lists, keyed by provider id: { loading, models, error }
const modelState = ref({})

// Ollama detection state
const ollamaCheckStatus = ref(null) // null | 'checking' | 'available' | 'unavailable'
const ollamaError = ref('')

// "Add your own endpoint" form
const showAddEndpoint = ref(false)
const addingEndpoint = ref(false)
const addEndpointError = ref('')
const newEndpoint = ref({ presetId: 'lmstudio', name: 'LM Studio', baseUrl: 'http://localhost:1234/v1', apiKey: '' })
const selectedPreset = computed(() => CUSTOM_ENDPOINT_PRESETS.find(p => p.id === newEndpoint.value.presetId) || null)

const onPresetChange = () => {
  const p = selectedPreset.value
  addEndpointError.value = ''
  if (!p) return
  newEndpoint.value.name = p.id === 'other' ? '' : p.name
  newEndpoint.value.baseUrl = p.baseUrl || ''
}

// Derived: all custom provider configs
const customProvidersConfig = computed(() => {
  configVersion.value // reactivity hook
  return getProvidersConfig().filter(p => p.isCustom)
})

const hasLocalKey = (id) => !!getProviderConfig(id)?.apiKey

const isProviderConfigured = (id) => {
  const cfg = getProviderConfig(id)
  const def = PROVIDER_DEFS.find(d => d.id === id)
  if (!def) return !!cfg?.baseUrl  // custom
  if (def.type === 'local') return !!cfg?.available
  return !!cfg?.apiKey || isServerProvider(id)
}

const getProviderModelLabel = (id) => {
  const cfg = getProviderConfig(id)
  return cfg?.model || ''
}

// ── Live model lists ──
// Curated recommendations render immediately; the full list arrives from
// fetchProviderModels (backend POST /api/ai/models) once credentials exist.

const modelsFor = (id) => {
  const st = modelState.value[id]
  if (st?.models?.length) return st.models
  const def = PROVIDER_DEFS.find(d => d.id === id)
  return (def?.models || []).map(m => ({ ...m, recommended: true }))
}
const recommendedModels = (id) => modelsFor(id).filter(m => m.recommended)
const otherModels = (id) => modelsFor(id).filter(m => !m.recommended)

const loadModels = async (id, { force = false } = {}) => {
  modelState.value = { ...modelState.value, [id]: { ...(modelState.value[id] || {}), loading: true } }
  const { models, error } = await fetchProviderModels(id, force ? { force: true } : {})
  modelState.value = { ...modelState.value, [id]: { loading: false, models, error } }
  // A stored model the provider doesn't list is shown as a custom id.
  if (expandedProvider.value === id && editBuffer.value.model && !models.some(m => m.id === editBuffer.value.model)) {
    editBuffer.value.customModelMode = true
  }
}

const modelSelectValue = () =>
  editBuffer.value.customModelMode ? '__custom__' : (editBuffer.value.model || '')

const onModelSelect = (id, value) => {
  if (value === '__custom__') {
    editBuffer.value.customModelMode = true
    return
  }
  editBuffer.value.customModelMode = false
  editBuffer.value.model = value
  saveModelIfConfigured(id)
}

const saveModelIfConfigured = (id) => {
  if (!isProviderConfigured(id)) return
  upsertProviderConfig(id, { model: editBuffer.value.model?.trim() || '' })
  bumpConfig()
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
    keyStatus: '',
    keyError: '',
    customModelMode: false,
  }
  if (id === 'ollama') {
    if (ollamaCheckStatus.value === null) detectOllama()
  } else if (isProviderConfigured(id) && !modelState.value[id]) {
    loadModels(id)
  }
}

const connectProvider = async (id) => {
  const key = editBuffer.value.apiKey?.trim()
  if (!key) return
  connectingProvider.value = id
  editBuffer.value.keyStatus = ''
  editBuffer.value.keyError = ''

  const model = editBuffer.value.model?.trim() || ''
  const { valid, error } = await testProviderConnection(id, { apiKey: key, model })
  if (!valid) {
    editBuffer.value.keyStatus = 'invalid'
    editBuffer.value.keyError = error || 'Connection failed'
    connectingProvider.value = null
    return
  }

  upsertProviderConfig(id, { apiKey: key, model })
  bumpConfig()
  editBuffer.value.keyStatus = 'valid'
  editBuffer.value.keyDirty = false

  // Best-effort: also store the key server-side so background features
  // (OCR, indexing) can use it. Failure is non-fatal — browser key still works.
  try {
    await axios.post(`/api/agent/config?provider=${encodeURIComponent(id)}&api_key=${encodeURIComponent(key)}`)
  } catch (err) {
    console.warn('Could not store key server-side (browser key still works):', err?.message || err)
  }

  invalidateModelCache(id)
  await loadModels(id, { force: true })
  connectingProvider.value = null
}

const removeProvider = (id) => {
  removeProviderConfig(id)
  invalidateModelCache(id)
  const next = { ...modelState.value }
  delete next[id]
  modelState.value = next
  bumpConfig()
  expandedProvider.value = null
}

// Ollama: probe via the shared model-list helper — non-empty list = running.
const detectOllama = async () => {
  ollamaCheckStatus.value = 'checking'
  ollamaError.value = ''
  const baseUrl = editBuffer.value.baseUrl?.trim() || 'http://localhost:11434'
  const { models, error } = await fetchProviderModels('ollama', { baseUrl, force: true })
  if (models.length > 0) {
    ollamaCheckStatus.value = 'available'
    modelState.value = { ...modelState.value, ollama: { loading: false, models, error: null } }
    // Pre-select the stored model if still present, else the first detected one
    const stored = getProviderConfig('ollama')?.model
    if (stored && models.some(m => m.id === stored)) {
      editBuffer.value.model = stored
    } else if (!models.some(m => m.id === editBuffer.value.model)) {
      editBuffer.value.model = models[0].id
    }
    return true
  }
  ollamaCheckStatus.value = 'unavailable'
  ollamaError.value = error || ''
  return false
}

const connectOllama = async () => {
  savingProvider.value = 'ollama'
  const ok = await detectOllama()
  if (ok) {
    upsertProviderConfig('ollama', {
      baseUrl: editBuffer.value.baseUrl?.trim() || 'http://localhost:11434',
      model: editBuffer.value.model || '',
      available: true,
    })
    bumpConfig()
    invalidateModelCache('ollama')
  }
  savingProvider.value = null
}

// Add a custom OpenAI-compatible endpoint: test first, save only on success.
const testAndAddEndpoint = async () => {
  const name = newEndpoint.value.name?.trim()
  const baseUrl = newEndpoint.value.baseUrl?.trim()
  if (!name || !baseUrl) return
  addingEndpoint.value = true
  addEndpointError.value = ''
  const apiKey = newEndpoint.value.apiKey?.trim() || ''
  const id = `custom_${Date.now()}`

  const { valid, error } = await testProviderConnection(id, { apiKey, baseUrl })
  if (!valid) {
    addEndpointError.value = error || 'Could not reach the endpoint'
    addingEndpoint.value = false
    return
  }

  // Same stored shape as before (ChatTab/SearchTab rely on it)
  upsertProviderConfig(id, { id, name, baseUrl, apiKey, model: '', isCustom: true })
  bumpConfig()

  const { models } = await fetchProviderModels(id, { force: true })
  modelState.value = { ...modelState.value, [id]: { loading: false, models, error: null } }
  if (models.length > 0) {
    upsertProviderConfig(id, { model: models[0].id })
    bumpConfig()
  }

  newEndpoint.value = { presetId: 'lmstudio', name: 'LM Studio', baseUrl: 'http://localhost:1234/v1', apiKey: '' }
  showAddEndpoint.value = false
  addingEndpoint.value = false
  toggleExpand(id)
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

  // Which providers have a server-stored team key ("Team key" badge)
  fetchServerProviderIds().then((ids) => {
    serverProviderIds.value = ids
    bumpConfig()
  })

  // Load theme
  const savedTheme = localStorage.getItem('theme')
  if (savedTheme) {
    selectedTheme.value = savedTheme
  } else {
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
    selectedTheme.value = prefersDark ? 'dark' : 'light'
  }

  loadServerConfig()
})
</script>

