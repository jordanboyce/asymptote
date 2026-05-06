<template>
  <div class="flex flex-col h-full min-h-0">

    <!-- Header row -->
    <div class="flex items-center justify-end flex-shrink-0">
      <div class="flex items-center gap-2">
        <button v-if="searchStore.searched" class="btn btn-sm btn-primary gap-1" @click="startNewSearch">
          <Plus :size="14" />
          New
        </button>
        <button v-if="cacheStats.count > 0" class="btn btn-sm btn-ghost gap-1" @click="showHistoryModal = true">
          <History :size="14" />
          History ({{ cacheStats.count }})
        </button>
      </div>
    </div>

    <!-- Search Settings Drawer -->
    <div
      v-if="searchSettingsOpen"
      class="fixed inset-0 z-[200]"
      @click.self="searchSettingsOpen = false"
      role="dialog"
      aria-modal="true"
      aria-labelledby="search-settings-title"
    >
      <div class="absolute inset-0 bg-black/30" @click="searchSettingsOpen = false" aria-hidden="true"></div>
      <div class="absolute right-0 top-0 h-full w-80 max-w-[85vw] bg-base-100 shadow-2xl flex flex-col">
        <div class="flex items-center justify-between p-4 border-b border-base-300">
          <h3 id="search-settings-title" class="text-sm font-bold">Search Settings</h3>
          <button
            class="btn btn-ghost btn-sm btn-circle"
            @click="searchSettingsOpen = false"
            aria-label="Close search settings"
          >
            <X :size="18" />
          </button>
        </div>
        <div class="flex-1 overflow-y-auto p-4 space-y-5">

          <!-- Retrieval -->
          <div class="space-y-3">
            <span class="text-xs font-semibold text-base-content/60 uppercase tracking-wider">Retrieval</span>

            <label class="flex items-center justify-between">
              <span class="text-sm">Max results</span>
              <input
                v-model.number="searchStore.topK"
                type="number"
                min="1"
                max="50"
                class="input input-bordered input-xs w-16 text-center"
                aria-label="Maximum number of results"
              />
            </label>

            <label class="flex items-center justify-between">
              <span class="text-sm">Search mode</span>
              <select v-model="searchMode" class="select select-bordered select-xs" aria-label="Search mode">
                <option value="semantic">Semantic</option>
                <option value="keyword">Keyword</option>
                <option value="hybrid">Hybrid</option>
              </select>
            </label>

            <label v-if="searchMode === 'hybrid'" class="space-y-1 block">
              <span class="text-sm">Semantic weight: {{ Math.round(semanticWeight * 100) }}%</span>
              <input
                v-model.number="semanticWeight"
                type="range"
                min="0"
                max="1"
                step="0.1"
                class="range range-primary range-xs"
                :aria-label="`Semantic weight: ${Math.round(semanticWeight * 100)} percent`"
              />
              <div class="w-full flex justify-between text-xs px-1 text-base-content/40" aria-hidden="true">
                <span>Keywords</span><span>Balanced</span><span>Semantic</span>
              </div>
            </label>
          </div>

          <!-- AI Enhancements -->
          <template v-if="hasAnyProvider">
            <div class="space-y-3">
              <span class="text-xs font-semibold text-base-content/60 uppercase tracking-wider">AI Enhancements</span>

              <label class="flex items-center justify-between cursor-pointer select-none">
                <span class="text-sm">Rerank</span>
                <input type="checkbox" class="toggle toggle-sm toggle-primary" v-model="localRerank" />
              </label>

              <label class="flex items-center justify-between cursor-pointer select-none">
                <span class="text-sm">Synthesize</span>
                <input type="checkbox" class="toggle toggle-sm toggle-secondary" v-model="localSynthesize" />
              </label>

              <p v-if="selectedExternalCount > 0" class="text-xs text-warning/80">Cloud providers will receive your query.</p>
            </div>

            <!-- Providers -->
            <div class="space-y-2">
              <span class="text-xs font-semibold text-base-content/60 uppercase tracking-wider">Providers</span>
              <div class="space-y-1.5">
                <label
                  v-for="pid in configuredProviderIds"
                  :key="pid"
                  class="flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer border transition-colors text-sm"
                  :class="selectedProviders.includes(pid) ? 'bg-primary/20 border-primary font-medium' : 'bg-base-200/60 border-base-300 hover:bg-base-100'"
                >
                  <input type="checkbox" class="checkbox checkbox-xs checkbox-primary" :checked="selectedProviders.includes(pid)" @change="toggleProvider(pid)" />
                  <span class="flex-1">{{ getProviderDisplayNameLocal(pid) }}</span>
                  <span class="badge badge-xs badge-outline" :class="isLocalProvider(pid) ? 'badge-info' : 'badge-warning'">{{ isLocalProvider(pid) ? 'local' : 'cloud' }}</span>
                </label>
              </div>

              <!-- Model overrides -->
              <div v-for="pid in selectedProviders" :key="'model-' + pid">
                <div v-if="getModelsForProvider(pid).length > 1" class="flex items-center justify-between mt-1 px-1">
                  <span class="text-xs text-base-content/50">{{ getProviderDisplayNameLocal(pid) }} model</span>
                  <select v-model="providerModelOverrides[pid]" class="select select-xs select-bordered max-w-[160px]">
                    <option value="">Default ({{ getDefaultModelLabel(pid) }})</option>
                    <option v-for="m in getModelsForProvider(pid)" :key="m.id" :value="m.id">{{ m.label }}</option>
                  </select>
                </div>
              </div>
            </div>
          </template>

          <!-- No-provider nudge -->
          <div v-if="!hasAnyProvider" class="flex items-center gap-3 rounded-lg border border-info/30 bg-info/5 px-3 py-2.5">
            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" class="stroke-info shrink-0 w-4 h-4">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
            </svg>
            <p class="text-sm flex-1">Add an AI provider in Settings to enable reranking and synthesis.</p>
            <button class="btn btn-xs btn-primary flex-shrink-0" @click="$emit('switch-tab', 'settings')">Settings</button>
          </div>
        </div>
      </div>
    </div>

    <!-- Search input area -->
    <div class="flex-shrink-0 pt-3">
      <div
        class="relative rounded-2xl border border-base-300 bg-base-200/60 focus-within:border-primary/50 focus-within:ring-1 focus-within:ring-primary/20 transition-all"
      >
        <!-- Slash command picker (floats above the input) -->
        <SlashCommandPicker
          ref="slashPickerRef"
          :show="slashPickerOpen"
          :model-value="searchStore.query"
          @select="onSlashSelect"
          @close="slashPickerOpen = false"
        />

        <label for="search-query" class="sr-only">Search your indexed documents</label>
        <input
          id="search-query"
          ref="searchInputRef"
          v-model="searchStore.query"
          type="text"
          class="w-full bg-transparent border-none outline-none text-sm px-4 pt-3 pb-2 placeholder:text-base-content/30"
          placeholder="Search your indexed documents..."
          :disabled="loading"
          @input="onInputChange"
          @keydown="onKeydown"
          @blur="onInputBlur"
        />

        <!-- Bottom toolbar -->
        <div class="flex items-center justify-between px-3 pb-2">
          <!-- Left: inline controls -->
          <div class="flex items-center gap-1.5">
            <button
              v-if="speechSupported"
              class="btn btn-ghost btn-xs btn-circle"
              :class="{ 'text-error animate-pulse': speechListening }"
              @click="toggleDictation"
              :title="speechListening ? 'Stop dictation' : 'Dictate (uses your browser\'s speech recognition)'"
              :aria-label="speechListening ? 'Stop dictation' : 'Start dictation'"
              :aria-pressed="speechListening"
              :disabled="loading"
            >
              <Mic :size="14" />
            </button>
            <template v-if="isExpertMode">
              <button
                class="btn btn-ghost btn-xs btn-circle"
                @click="searchSettingsOpen = !searchSettingsOpen"
                title="Search settings"
                aria-label="Open search settings"
                :aria-expanded="searchSettingsOpen"
              >
                <SlidersHorizontal :size="14" />
              </button>
              <button
                class="btn btn-ghost btn-xs btn-circle font-mono"
                @mousedown.prevent="toggleSlashPicker"
                title="Slash commands"
                aria-label="Show slash commands"
                :aria-expanded="slashPickerOpen"
              >
                /
              </button>
              <span class="badge badge-xs badge-ghost">{{ searchModeLabel }}</span>
              <span class="badge badge-xs badge-ghost">Top {{ searchStore.topK }}</span>
              <template v-if="hasAnyProvider">
                <span v-if="localRerank" class="badge badge-xs badge-outline badge-primary">Rerank</span>
                <span v-if="localSynthesize" class="badge badge-xs badge-outline badge-secondary">Synth</span>
              </template>
            </template>
          </div>

          <!-- Right: search/cancel button -->
          <button
            v-if="!loading"
            class="btn btn-circle btn-sm btn-primary transition-all"
            :class="{ 'btn-disabled opacity-40': searchButtonDisabled }"
            :disabled="searchButtonDisabled"
            @click="search"
            title="Search"
            aria-label="Run search"
          >
            <SearchIcon :size="16" aria-hidden="true" />
          </button>
          <button
            v-else
            class="btn btn-circle btn-sm btn-error transition-all"
            @click="cancelSearch"
            title="Cancel search"
            aria-label="Cancel search"
          >
            <X :size="16" aria-hidden="true" />
          </button>
        </div>
      </div>

      <!-- Empty collection notice -->
      <p v-if="searchDisabled" class="text-xs text-warning mt-1.5 text-center">
        No data indexed in this collection. Add sources first.
      </p>
      <p v-else class="text-xs text-base-content/30 mt-1.5 text-center">Enter to search</p>
    </div>

    <!-- Loading indicator -->
    <div v-if="loading" class="flex-shrink-0 mt-3 rounded-xl border border-primary/30 bg-primary/5 p-3">
      <div class="flex items-center gap-2">
        <span class="loading loading-spinner loading-sm text-primary"></span>
        <span class="text-sm font-semibold">{{ loadingHeadline }}</span>
        <span class="loading loading-dots loading-xs text-primary"></span>
      </div>
      <p class="text-xs text-base-content/70 mt-1">{{ loadingPhaseMessage }}</p>
      <div class="mt-2 flex flex-wrap gap-2">
        <span class="badge badge-sm badge-outline">{{ searchModeLabel }}</span>
        <span class="badge badge-sm badge-outline">Top {{ searchStore.topK }}</span>
        <span v-if="aiActive" class="badge badge-sm badge-outline badge-primary">
          {{ selectedProviders.length }} AI provider{{ selectedProviders.length > 1 ? 's' : '' }}
        </span>
      </div>
      <progress class="progress progress-primary w-full mt-2"></progress>
    </div>

    <!-- Cache Prompt Dialog -->
    <div v-if="showCachePrompt && cachedData" class="flex-shrink-0 mt-3 alert shadow-lg"
      :class="cacheMatchType === 'semantic' ? 'alert-warning' : 'alert-success'">
      <svg v-if="cacheMatchType === 'exact'" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" class="stroke-info shrink-0 w-6 h-6">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
          d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
      </svg>
      <svg v-else xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" class="stroke-current shrink-0 w-6 h-6">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
          d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"></path>
      </svg>
      <div class="flex-1">
        <h4 class="font-semibold">
          <span v-if="cacheMatchType === 'exact'">Cached results found!</span>
          <span v-else>Semantically similar search found <span class="badge badge-sm badge-warning ml-1">{{ (cacheSimilarityScore * 100).toFixed(0) }}% match</span></span>
        </h4>
        <p class="text-sm">
          <span v-if="cacheMatchType === 'semantic'">
            Similar to: <em>"{{ cachedData.query }}"</em> &middot; searched {{ formatTimeAgo(cachedData.timestamp) }}.
            Reuse the cached AI results to save tokens, or run a fresh search for your exact query.
          </span>
          <span v-else>
            This search was performed {{ formatTimeAgo(cachedData.timestamp) }}.
            You can use the cached results instantly (no tokens used) or perform a fresh search.
          </span>
        </p>
        <p class="text-xs text-base-content/70 mt-1">
          Cached: {{ cachedData.results?.length || 0 }} results
          <span v-if="cachedData.aiResponses && cachedData.aiResponses.length > 0">
            &middot; {{ cachedData.aiResponses.length }} AI response(s)
          </span>
        </p>
      </div>
      <div class="flex gap-2">
        <button class="btn btn-sm btn-ghost" @click="useCachedResults">
          Use Cached
        </button>
        <button class="btn btn-sm btn-primary" @click="performFreshSearch">
          Fresh Search
        </button>
      </div>
    </div>

    <!-- Error Alert -->
    <div v-if="error" class="flex-shrink-0 mt-3 alert alert-error">
      <svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-6 w-6" fill="none" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
          d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
      <span>{{ error }}</span>
    </div>

    <!-- Results area (scrollable) -->
    <div class="flex-1 overflow-y-auto min-h-0 space-y-4 mt-4 pr-1">

    <!-- AI Synthesis (Multiple Providers) -->
    <div v-if="searchStore.aiResponses && searchStore.aiResponses.length > 0" class="space-y-4">
      <div v-for="(aiResponse, index) in searchStore.aiResponses" :key="index"
        class="card bg-primary/5 border border-primary/20">
        <div class="card-body">
          <h3 class="card-title text-base">
            <span class="badge badge-sm" :class="{
              'badge-success': aiResponse.provider === 'openai',
              'badge-primary': aiResponse.provider === 'anthropic',
              'badge-info': aiResponse.provider === 'ollama'
            }">
              AI
            </span>
            {{ getProviderDisplayNameLocal(aiResponse.provider) }} Answer
          </h3>
          <div v-if="aiResponse.synthesis" class="prose prose-sm max-w-none whitespace-pre-wrap">{{ aiResponse.synthesis
          }}</div>
          <div v-if="aiResponse.aiUsage" class="text-xs text-base-content/40 mt-1">
            {{ aiResponse.aiUsage.features_used.join(', ') }}
            <span v-if="aiResponse.aiUsage.total_input_tokens !== undefined">
              &middot; {{ aiResponse.aiUsage.total_input_tokens + aiResponse.aiUsage.total_output_tokens }} tokens
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- AI Synthesis (Legacy Single Provider) -->
    <div v-else-if="searchStore.synthesis" class="card bg-primary/5 border border-primary/20">
      <div class="card-body">
        <h3 class="card-title text-base">
          <span class="badge badge-primary badge-sm">AI</span>
          Answer
        </h3>
        <div class="prose prose-sm max-w-none whitespace-pre-wrap">{{ searchStore.synthesis }}</div>
        <div v-if="searchStore.aiUsage" class="text-xs text-base-content/40 mt-1">
          {{ searchStore.aiUsage.features_used.join(', ') }}
          &middot; {{ searchStore.aiUsage.total_input_tokens + searchStore.aiUsage.total_output_tokens }} tokens
        </div>
      </div>
    </div>

    <!-- Slash command output (takes the place of results when a / command was run) -->
    <div
      v-if="slashOutput"
      class="mt-4 rounded-xl border border-base-300 bg-base-200 shadow-sm"
    >
      <div class="flex items-center justify-between px-4 py-2 border-b border-base-300 bg-base-200/60">
        <div class="flex items-center gap-2">
          <code class="font-mono text-xs px-1.5 py-0.5 rounded bg-base-100 border border-base-300">{{ slashOutput.cmd }}</code>
          <span v-if="slashOutput.error" class="badge badge-xs badge-error">error</span>
        </div>
        <button
          class="btn btn-ghost btn-xs btn-circle"
          title="Dismiss"
          aria-label="Dismiss slash command output"
          @click="dismissSlashOutput"
        >
          <X :size="14" />
        </button>
      </div>
      <pre class="p-4 font-mono text-xs leading-snug whitespace-pre-wrap overflow-x-auto">{{ slashOutput.content }}</pre>
    </div>

    <!-- Results -->
    <div v-if="searchStore.results.length > 0" class="space-y-4">
      <div class="flex justify-between items-center">
        <h3 class="text-xl font-semibold">Results ({{ searchStore.results.length }})</h3>
        <p class="text-sm text-base-content/70">Query: "{{ searchStore.lastQuery }}"</p>
      </div>

      <div v-for="(result, index) in searchStore.results" :key="index" class="card bg-base-200 shadow-md">
        <div class="card-body">
          <div class="flex justify-between items-start">
            <div class="flex-1">
              <h4 class="card-title text-lg">
                <!-- Format-aware icon -->
                <svg v-if="result.source_format === 'csv'" class="w-5 h-5 text-success" fill="none"
                  stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                    d="M3 10h18M3 14h18m-9-4v8m-7 0h14a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
                </svg>
                <svg v-else-if="result.source_format === 'pdf'" class="w-5 h-5 text-error" fill="none"
                  stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                    d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
                <svg v-else-if="result.source_format === 'md'" class="w-5 h-5 text-info" fill="none"
                  stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                    d="M7 8h10M7 12h4m1 8l-4-4H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-3l-4 4z" />
                </svg>
                <!-- Code file icon -->
                <svg v-else-if="isCodeFile(result.filename)" class="w-5 h-5 text-primary" fill="none"
                  stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                    d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" />
                </svg>
                <svg v-else class="w-5 h-5 text-base-content/50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                    d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
                {{ result.filename }}
              </h4>
              <div class="flex flex-wrap gap-1 mt-1">
                <!-- Page/Row/Line indicator -->
                <div v-if="result.source_format === 'csv' && result.csv_row_number"
                  class="badge badge-success badge-sm">
                  Row {{ result.csv_row_number }}
                </div>
                <div v-else-if="isCodeFile(result.filename) && result.line_start" class="badge badge-primary badge-sm">
                  Lines {{ result.line_start }}<span v-if="result.line_end && result.line_end !== result.line_start">-{{
                    result.line_end }}</span>
                </div>
                <div v-else-if="isCodeFile(result.filename) && result.symbol_name" class="badge badge-primary badge-sm">
                  {{ result.symbol_type || 'Symbol' }}: {{ result.symbol_name }}
                </div>
                <div v-else-if="isCodeFile(result.filename)" class="badge badge-primary badge-sm">
                  Section {{ result.page_number }}
                </div>
                <div v-else class="badge badge-primary badge-sm">
                  Page {{ result.page_number }}
                </div>
                <!-- Similarity score -->
                <div class="badge badge-ghost badge-sm">
                  Similarity: {{ (result.similarity_score * 100).toFixed(1) }}%
                </div>
                <!-- Extraction method indicator (for OCR) -->
                <div v-if="result.extraction_method === 'ocr'" class="badge badge-warning badge-sm">
                  OCR
                </div>
                <div v-else-if="result.extraction_method === 'hybrid'" class="badge badge-info badge-sm">
                  Hybrid
                </div>
                <!-- Format badge -->
                <div v-if="result.source_format" class="badge badge-outline badge-sm">
                  {{ result.source_format.toUpperCase() }}
                </div>
                <!-- Local reference indicator -->
                <div v-if="result.source_type === 'local_reference'" class="badge badge-ghost badge-sm"
                  title="Indexed in-place from local file">
                  local
                </div>
              </div>
            </div>
          </div>

          <!-- CSV Table View (v3.0 feature) -->
          <div v-if="result.source_format === 'csv' && result.csv_columns && result.csv_values"
            class="mt-3 overflow-x-auto">
            <table class="table table-xs table-zebra">
              <thead>
                <tr>
                  <th v-for="col in result.csv_columns" :key="col" class="bg-base-300 text-xs font-semibold">
                    {{ col }}
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td v-for="col in result.csv_columns" :key="col" class="text-sm">
                    <span v-html="highlightText(String(result.csv_values[col] || ''), searchStore.query)"></span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <!-- Standard text snippet (for non-CSV or CSV without row data) -->
          <p v-else class="text-sm mt-2" v-html="highlightText(result.text_snippet, searchStore.query)"></p>

          <div class="card-actions justify-end mt-4">
            <!-- Open Page button - only for PDFs (page_url uses #page=N which only works in PDF viewers) -->
            <a v-if="result.source_format === 'pdf'" :href="result.page_url" target="_blank"
              class="btn btn-sm btn-primary">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                  d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
              </svg>
              Open Page {{ result.page_number }}
            </a>
            <!-- View button - for non-PDF files (no page anchor support) -->
            <a v-else :href="result.pdf_url" target="_blank" class="btn btn-sm btn-primary">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                  d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                  d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
              </svg>
              View File
            </a>
            <a :href="result.pdf_url" target="_blank" class="btn btn-sm btn-ghost" :download="result.filename">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                  d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              Download
            </a>
          </div>
        </div>
      </div>
    </div>

    <!-- No Results -->
    <div v-else-if="searchStore.searched && !loading" class="alert alert-info">
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" class="stroke-current shrink-0 w-6 h-6">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
          d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
      </svg>
      <span>No results found. Try a different query or upload more sources.</span>
    </div>

    </div><!-- end scrollable results area -->

    <!-- Search History Modal -->
    <dialog ref="historyModal" class="modal" :class="{ 'modal-open': showHistoryModal }" aria-labelledby="search-history-title">
      <div class="modal-box max-w-3xl">
        <h3 id="search-history-title" class="font-bold text-lg mb-4">Search History</h3>

        <div class="space-y-2 max-h-96 overflow-y-auto">
          <div v-for="(entry, index) in historyEntries" :key="index"
            class="card bg-base-200 hover:bg-base-300 cursor-pointer transition-colors"
            @click="loadHistoryEntry(entry)">
            <div class="card-body p-4">
              <div class="flex items-start justify-between gap-4">
                <div class="flex-1">
                  <div class="font-semibold text-sm">{{ entry.query }}</div>
                  <div class="text-xs text-base-content/60 mt-1">
                    {{ formatTimeAgo(entry.timestamp) }}
                    &middot; {{ entry.results?.length || 0 }} results
                    &middot; Top-{{ entry.topK }}
                    <span v-if="entry.aiResponses && entry.aiResponses.length > 0">
                      &middot; {{ entry.aiResponses.length }} AI response(s)
                    </span>
                  </div>
                </div>
                <button
                  class="btn btn-ghost btn-xs text-error"
                  @click.stop="deleteHistoryEntry(entry)"
                  title="Delete from history"
                  :aria-label="`Delete history entry: ${entry.query}`"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24"
                    stroke="currentColor" aria-hidden="true">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                      d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                  </svg>
                </button>
              </div>
            </div>
          </div>

          <div v-if="historyEntries.length === 0" class="text-center text-base-content/60 py-8">
            No search history yet
          </div>
        </div>

        <div class="modal-action">
          <button class="btn btn-sm btn-error" @click="clearAllHistory" :disabled="historyEntries.length === 0">
            Clear All
          </button>
          <button class="btn btn-sm" @click="showHistoryModal = false">Close</button>
        </div>
      </div>
      <form method="dialog" class="modal-backdrop">
        <button @click="showHistoryModal = false">close</button>
      </form>
    </dialog>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import axios from 'axios'
import { Plus, History, SlidersHorizontal, X, Search as SearchIcon, Mic } from 'lucide-vue-next'
import { useSpeechRecognition } from '../composables/useSpeechRecognition.js'
import { useSearchStore } from '../stores/searchStore'
import { useCollectionStore } from '../stores/collectionStore'
import SlashCommandPicker from './SlashCommandPicker.vue'
import { runSlashCommand, isSlashCommand } from '../utils/slashCommands'
import { nextTick } from 'vue'
import {
  getConfiguredProviderIds,
  buildProviderHeaders,
  getAPIProviderName,
  getProviderDisplayName,
  getProviderModels,
  getProviderConfig,
  isLocalProvider,
  getAISettings,
  migrateLegacySettings,
} from '../utils/aiProviders.js'
import { isExpertMode } from '../utils/expertMode.js'

const props = defineProps({
  chunkCount: {
    type: Number,
    default: 0
  }
})

const emit = defineEmits(['stats-updated', 'switch-tab'])

// Computed to check if search is available
const searchDisabled = computed(() => props.chunkCount === 0)

const searchButtonDisabled = computed(() => {
  if (!searchStore.query.trim()) return true
  // Slash commands bypass the vector index and don't need indexed content.
  if (isSlashCommand(searchStore.query)) return false
  return searchDisabled.value
})

// Use the search store for persistent state
const searchStore = useSearchStore()
const collectionStore = useCollectionStore()

// Local state for loading and error (not persisted)
const loading = ref(false)
const error = ref('')

// Slash command picker + inline output (rendered above results)
const slashPickerOpen = ref(false)
const slashPickerRef = ref(null)
const searchInputRef = ref(null)
const slashOutput = ref(null) // { cmd, content, error }

const toggleSlashPicker = () => {
  slashPickerOpen.value = !slashPickerOpen.value
  if (slashPickerOpen.value) {
    nextTick(() => searchInputRef.value?.focus())
  }
}

const onInputChange = () => {
  // Basic mode hides the slash-command surface entirely.
  if (!isExpertMode.value) {
    slashPickerOpen.value = false
    return
  }
  slashPickerOpen.value = (searchStore.query || '').trimStart().startsWith('/')
}

const { supported: speechSupported, listening: speechListening, toggle: toggleSpeech } = useSpeechRecognition()

const onSpeechTranscript = (text) => {
  const trimmed = (text || '').trim()
  if (!trimmed) return
  const current = searchStore.query || ''
  searchStore.query = current
    ? `${current.replace(/\s+$/, '')} ${trimmed}`
    : trimmed
  onInputChange()
}

const toggleDictation = () => {
  if (loading.value) return
  toggleSpeech(onSpeechTranscript)
}

const onInputBlur = () => {
  setTimeout(() => {
    slashPickerOpen.value = false
  }, 150)
}

const onKeydown = (e) => {
  if (slashPickerOpen.value && slashPickerRef.value?.handleKeydown(e)) return
  if (e.key === 'Enter') {
    e.preventDefault()
    search()
  }
}

const onSlashSelect = (cmd) => {
  searchStore.query = cmd
  slashPickerOpen.value = false
  search()
}

const runInlineSlashCommand = async (input) => {
  const result = await runSlashCommand(input, {
    collectionId: collectionStore.currentCollectionId,
    collection: collectionStore.currentCollection,
  })
  slashOutput.value = result
  // Clear any stale search results so the slash output is the primary view.
  searchStore.clearResults()
}

const dismissSlashOutput = () => {
  slashOutput.value = null
}

// Search mode options
const searchMode = ref('hybrid')  // 'semantic', 'keyword', or 'hybrid'
const semanticWeight = ref(0.7)     // Weight for semantic search in hybrid mode (0-1)

const searchModeLabel = computed(() => {
  if (searchMode.value === 'keyword') return 'Keyword'
  if (searchMode.value === 'hybrid') return `Hybrid ${Math.round(semanticWeight.value * 100)}%`
  return 'Semantic'
})

// AbortController for cancelling ongoing searches
let abortController = null

// Cache prompt state
const showCachePrompt = ref(false)
const cachedData = ref(null)
const cacheMatchType = ref('exact')   // 'exact' | 'semantic'
const cacheSimilarityScore = ref(0)
const pendingEmbedding = ref(null)

// History modal state
const showHistoryModal = ref(false)

// Settings drawer state
const searchSettingsOpen = ref(false)

// Collapsible settings state (legacy, kept for localStorage compat)
const SEARCH_SETTINGS_COLLAPSED_KEY = 'asymptote_search_settings_collapsed'
const searchSettingsCollapsed = ref(true)

// AI Settings - Read from Settings tab's localStorage values
// API keys and feature toggles (rerank/synthesize) are managed in Settings
// Provider selection for each search is managed here

// Check which providers are configured
const configuredProviderIds = ref([])
const hasAnyProvider = computed(() => configuredProviderIds.value.length > 0)

// Provider availability (for backwards compat)
const ollamaAvailable = computed(() => configuredProviderIds.value.includes('ollama'))

// Selected providers for this search session
const PROVIDER_SELECTION_KEY = 'asymptote_selected_providers'
const PROVIDER_MODEL_OVERRIDES_KEY = 'asymptote_search_model_overrides'
const selectedProviders = ref([])
const selectedPrivateCount = computed(() => selectedProviders.value.filter(p => isLocalProvider(p)).length)
const selectedExternalCount = computed(() => selectedProviders.value.filter(p => !isLocalProvider(p)).length)

// Per-search model overrides: { providerId: 'model-id' | '' }
const providerModelOverrides = ref({})

// Get available models for a provider (delegates to PROVIDER_DEFS)
const getModelsForProvider = (pid) => getProviderModels(pid)

// Get the display label for a provider's currently configured (default) model
const getDefaultModelLabel = (pid) => {
  const cfg = getProviderConfig(pid)
  if (!cfg?.model) return ''
  const models = getProviderModels(pid)
  const found = models.find(m => m.id === cfg.model)
  return found ? found.label : cfg.model
}

// Persist model override changes
watch(providerModelOverrides, (val) => {
  try { localStorage.setItem(PROVIDER_MODEL_OVERRIDES_KEY, JSON.stringify(val)) } catch { /* ignore */ }
}, { deep: true })

// Initialize selected providers from localStorage
const initializeProviders = () => {
  migrateLegacySettings()
  configuredProviderIds.value = getConfiguredProviderIds()

  const saved = localStorage.getItem(PROVIDER_SELECTION_KEY)
  let savedSelection = []
  if (saved) {
    try { savedSelection = JSON.parse(saved) } catch { savedSelection = [] }
  }

  // Filter to only configured providers
  const validProviders = savedSelection.filter(p => configuredProviderIds.value.includes(p))

  // Default to all configured if no valid saved selection
  if (validProviders.length > 0) {
    selectedProviders.value = validProviders
  } else {
    selectedProviders.value = [...configuredProviderIds.value]
  }
  localStorage.setItem(PROVIDER_SELECTION_KEY, JSON.stringify(selectedProviders.value))

  // Restore model overrides
  try {
    const savedOverrides = JSON.parse(localStorage.getItem(PROVIDER_MODEL_OVERRIDES_KEY) || '{}')
    // Only keep overrides for still-configured providers
    const cleaned = {}
    for (const pid of configuredProviderIds.value) {
      if (savedOverrides[pid]) cleaned[pid] = savedOverrides[pid]
    }
    providerModelOverrides.value = cleaned
  } catch {
    providerModelOverrides.value = {}
  }
}

// Toggle a provider on/off
const toggleProvider = (provider) => {
  const index = selectedProviders.value.indexOf(provider)
  if (index > -1) {
    selectedProviders.value.splice(index, 1)
  } else {
    selectedProviders.value.push(provider)
  }
  localStorage.setItem(PROVIDER_SELECTION_KEY, JSON.stringify(selectedProviders.value))
}

onMounted(() => {
  initializeProviders()
  const savedCollapsed = localStorage.getItem(SEARCH_SETTINGS_COLLAPSED_KEY)
  if (savedCollapsed !== null) {
    searchSettingsCollapsed.value = savedCollapsed === 'true'
  }
})

onBeforeUnmount(() => {
  stopLoadingPhaseAnimation()
})

// Get configured AI settings from Settings tab (features only - rerank/synthesize)
// Inline AI feature toggles (read initial value from shared ai_settings, write back on change)
const _initialAISettings = getAISettings()
const localRerank = ref(_initialAISettings.rerank ?? false)
const localSynthesize = ref(_initialAISettings.synthesize ?? false)

watch([localRerank, localSynthesize], () => {
  try {
    const current = getAISettings()
    localStorage.setItem('ai_settings', JSON.stringify({ ...current, rerank: localRerank.value, synthesize: localSynthesize.value }))
  } catch (e) { /* ignore */ }
})

const aiFeaturesEnabled = computed(() => localRerank.value || localSynthesize.value)

// Check if AI will be used for this search (features enabled + providers selected)
const aiActive = computed(() => {
  return aiFeaturesEnabled.value && selectedProviders.value.length > 0
})

const loadingPhaseIndex = ref(0)
let loadingPhaseTimer = null

const loadingHeadline = computed(() => {
  if (!aiActive.value) return 'Searching indexed sources'
  if (selectedProviders.value.includes('ollama') && selectedProviders.value.length === 1) {
    return 'Processing with local AI'
  }
  return 'Processing with AI providers'
})

const loadingPhases = computed(() => {
  const phases = ['Scanning indexed chunks', 'Scoring relevance']
  if (aiActive.value) {
    const settings = getAISettings()
    if (settings.rerank) phases.push('AI reranking in progress')
    if (settings.synthesize) phases.push('Generating synthesized answer')
    if (selectedProviders.value.includes('ollama')) phases.push('Waiting for local model response')
  }
  phases.push('Finalizing results')
  return phases
})

const loadingPhaseMessage = computed(() => {
  const phases = loadingPhases.value
  return phases[loadingPhaseIndex.value % phases.length]
})

const startLoadingPhaseAnimation = () => {
  if (loadingPhaseTimer) clearInterval(loadingPhaseTimer)
  loadingPhaseIndex.value = 0
  loadingPhaseTimer = setInterval(() => {
    loadingPhaseIndex.value = (loadingPhaseIndex.value + 1) % loadingPhases.value.length
  }, 1300)
}

const stopLoadingPhaseAnimation = () => {
  if (loadingPhaseTimer) {
    clearInterval(loadingPhaseTimer)
    loadingPhaseTimer = null
  }
}

watch(loading, (isLoading) => {
  if (isLoading) {
    startLoadingPhaseAnimation()
  } else {
    stopLoadingPhaseAnimation()
  }
})

watch(searchSettingsCollapsed, (collapsed) => {
  localStorage.setItem(SEARCH_SETTINGS_COLLAPSED_KEY, collapsed ? 'true' : 'false')
})


const activeFeaturesList = computed(() => {
  const settings = getAISettings()
  const features = []
  if (settings.rerank) features.push('Reranking')
  if (settings.synthesize) features.push('Synthesis')
  return features.length > 0 ? features.join(' + ') : 'None'
})

const cacheStats = computed(() => searchStore.getCacheStats())

const historyEntries = computed(() => {
  // Get cache for current collection (cache is collection-aware: { collectionId: { "query|topK": entry } })
  const collectionId = collectionStore.currentCollectionId || 'default'
  const collectionCache = searchStore.cache[collectionId] || {}
  return Object.values(collectionCache)
    .filter(entry => entry && entry.query && entry.timestamp) // Filter out corrupted entries
    .sort((a, b) => b.timestamp - a.timestamp)
})

const getProviderDisplayNameLocal = getProviderDisplayName

// Code file extensions for display logic
const CODE_EXTENSIONS = ['.pas', '.dpr', '.dpk', '.pp', '.inc', '.dfm', '.mod', '.def', '.mi', '.asm', '.s']

const isCodeFile = (filename) => {
  if (!filename) return false
  const ext = '.' + filename.split('.').pop().toLowerCase()
  return CODE_EXTENSIONS.includes(ext)
}

const escapeRegExp = (str) => {
  return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

const highlightText = (text, searchQuery) => {
  if (!searchQuery) return text

  const keywords = searchQuery.toLowerCase().split(/\s+/).filter(k => k.length > 2)
  let result = text

  keywords.forEach(keyword => {
    const regex = new RegExp(`(${escapeRegExp(keyword)})`, 'gi')
    result = result.replace(regex, '<mark class="bg-yellow-300 dark:bg-yellow-600 px-1 rounded">$1</mark>')
  })

  return result
}

const formatTimeAgo = (timestamp) => {
  const seconds = Math.floor((Date.now() - timestamp) / 1000)

  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} minutes ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} hours ago`
  return `${Math.floor(seconds / 86400)} days ago`
}

const useCachedResults = () => {
  if (!cachedData.value) return

  // For semantic matches, file under the current query so future exact lookups work
  const effectiveQuery = cacheMatchType.value === 'semantic'
    ? searchStore.query.toLowerCase().trim()
    : cachedData.value.query

  searchStore.setSearchResults({
    query: effectiveQuery,
    results: cachedData.value.results,
    synthesis: cachedData.value.synthesis,
    ai_usage: cachedData.value.ai_usage,
    aiResponses: cachedData.value.aiResponses,
    embedding: pendingEmbedding.value  // Store embedding for current query too
  })

  showCachePrompt.value = false
  cachedData.value = null
  pendingEmbedding.value = null
  emit('stats-updated')
}

const performFreshSearch = async () => {
  showCachePrompt.value = false
  const embedding = pendingEmbedding.value
  cachedData.value = null
  pendingEmbedding.value = null
  searchSettingsCollapsed.value = true
  await executeSearch(embedding)
}

const loadHistoryEntry = (entry) => {
  // Load the cached entry
  searchStore.setSearchResults({
    query: entry.query,
    results: entry.results,
    synthesis: entry.synthesis,
    ai_usage: entry.ai_usage,
    aiResponses: entry.aiResponses
  })

  // Update the query and topK values
  searchStore.setQuery(entry.query)
  searchStore.setTopK(entry.topK)

  showHistoryModal.value = false
  emit('stats-updated')
}

const deleteHistoryEntry = (entry) => {
  searchStore.deleteCacheEntry(entry.query, entry.topK)
}

const clearAllHistory = () => {
  if (confirm('Clear all search history? This cannot be undone.')) {
    searchStore.clearSearchCache()
    showHistoryModal.value = false
  }
}

const startNewSearch = () => {
  // Clear the current search state
  searchStore.clearResults()
  searchStore.setQuery('')
  error.value = ''
  showCachePrompt.value = false
  cachedData.value = null
  pendingEmbedding.value = null
}

const search = async () => {
  if (!searchStore.query.trim()) return

  error.value = ''
  slashPickerOpen.value = false

  // Intercept slash commands before any search machinery — they read collection
  // metadata directly and don't go through the vector index.
  if (isSlashCommand(searchStore.query)) {
    await runInlineSlashCommand(searchStore.query)
    return
  }

  // Clear any prior slash output when running a real search.
  slashOutput.value = null

  // 1. Exact cache hit — instant
  const cached = searchStore.getCachedResult(searchStore.query, searchStore.topK)
  if (cached) {
    cachedData.value = cached
    cacheMatchType.value = 'exact'
    cacheSimilarityScore.value = 1
    showCachePrompt.value = true
    return
  }

  // 2. Always fetch embedding so cache entries are always populated for future semantic checks
  try {
    const embedResponse = await axios.post('/api/embed', { text: searchStore.query.toLowerCase().trim() })
    pendingEmbedding.value = embedResponse.data.embedding
  } catch {
    pendingEmbedding.value = null
  }

  // 3. Semantic similarity check — only run if there are cached entries to compare against
  const stats = searchStore.getCacheStats()
  if (stats.count > 0 && pendingEmbedding.value) {
    const semanticMatch = searchStore.findSemanticallySimilar(pendingEmbedding.value)
    if (semanticMatch) {
      cachedData.value = semanticMatch
      cacheMatchType.value = 'semantic'
      cacheSimilarityScore.value = semanticMatch.similarityScore
      showCachePrompt.value = true
      return
    }
  }

  // 4. No match found — run fresh search
  searchSettingsCollapsed.value = true
  await executeSearch(pendingEmbedding.value)
}

const cancelSearch = () => {
  if (abortController) {
    abortController.abort()
    abortController = null
  }
  loading.value = false
  // Note: Backend/Ollama may continue processing, but we stop waiting for the response
  error.value = 'Search cancelled (backend may still be processing)'
}

const executeSearch = async (queryEmbedding = null) => {
  // Create new AbortController for this search
  abortController = new AbortController()
  const signal = abortController.signal

  loading.value = true
  error.value = ''

  try {
    const aiSettings = getAISettings()
    const useAI = aiActive.value

    if (useAI && selectedProviders.value.length > 0) {
      // Execute search with each selected provider in parallel
      const searchPromises = selectedProviders.value.map(async (provider) => {
        const body = {
          query: searchStore.query,
          top_k: searchStore.topK,
          mode: searchMode.value,
          semantic_weight: semanticWeight.value,
          ai: {
            provider: getAPIProviderName(provider),
            rerank: !!aiSettings.rerank,
            synthesize: !!aiSettings.synthesize
          }
        }

        const headers = buildProviderHeaders(provider, providerModelOverrides.value[provider] || null)

        try {
          const collectionId = collectionStore.currentCollectionId
          const response = await axios.post(`/search?collection_id=${collectionId}`, body, { headers, signal })
          return {
            provider,
            results: response.data.results,
            synthesis: response.data.synthesis,
            aiUsage: response.data.ai_usage
          }
        } catch (err) {
          // Check if this was a cancellation
          if (err.name === 'CanceledError' || err.code === 'ERR_CANCELED') {
            throw err // Re-throw to be caught by outer catch
          }
          console.error(`Search with ${provider} failed:`, err)
          return { provider, error: err.response?.data?.detail || `${provider} failed` }
        }
      })

      const providerResults = await Promise.all(searchPromises)
      const successfulResults = providerResults.filter(r => !r.error)

      if (successfulResults.length === 0) {
        throw new Error(providerResults[0]?.error || 'All AI searches failed')
      }

      // Use results from first successful provider, collect all AI responses
      const aiResponses = successfulResults
        .filter(r => r.synthesis || r.aiUsage)
        .map(r => ({ provider: r.provider, synthesis: r.synthesis, aiUsage: r.aiUsage }))

      searchStore.setSearchResults({
        query: searchStore.query,
        results: successfulResults[0].results,
        aiResponses,
        embedding: queryEmbedding
      })
    } else {
      // Regular search without AI
      const body = {
        query: searchStore.query,
        top_k: searchStore.topK,
        mode: searchMode.value,
        semantic_weight: semanticWeight.value
      }
      const collectionId = collectionStore.currentCollectionId
      const response = await axios.post(`/search?collection_id=${collectionId}`, body, { signal })
      searchStore.setSearchResults({
        query: searchStore.query,
        results: response.data.results,
        embedding: queryEmbedding
      })
    }

    emit('stats-updated')
  } catch (err) {
    // Check if this was a cancellation - don't show as error
    if (err.name === 'CanceledError' || err.code === 'ERR_CANCELED') {
      error.value = 'Search cancelled'
      return
    }
    error.value = err.response?.data?.detail || err.message || 'Search failed. Please try again.'
    searchStore.clearResults()
  } finally {
    loading.value = false
    abortController = null
    pendingEmbedding.value = null
  }
}
</script>



