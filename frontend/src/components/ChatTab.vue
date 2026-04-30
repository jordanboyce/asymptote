<template>
  <div class="flex flex-col h-full min-h-0">

      <!-- Header row -->
      <div class="flex items-center justify-between flex-shrink-0 py-1.5 mb-1 border-b border-base-300/60">
        <div class="flex items-center gap-1.5 min-w-0">
          <Bot :size="14" class="text-base-content/40 flex-shrink-0" aria-hidden="true" />
          <span class="text-sm font-medium truncate text-base-content/80">{{ activeSessionTitle }}</span>
          <span v-if="messages.length > 0" class="text-xs text-base-content/40 ml-1 flex-shrink-0">
            · {{ messages.length }}
          </span>
        </div>

        <div class="flex items-center gap-0.5">
          <!-- Privacy status indicator -->
          <div class="dropdown dropdown-end mr-1">
            <label
              tabindex="0"
              class="btn btn-xs btn-ghost gap-1 px-1.5 h-5 min-h-0 font-normal"
              :class="piiRedactionEnabled ? 'text-success/80 hover:text-success' : 'text-warning hover:text-warning'"
              :title="piiRedactionEnabled ? 'Data stays local. PII redacted before AI calls.' : 'Warning: PII redaction is currently OFF'"
              :aria-label="piiRedactionEnabled ? 'Privacy: stored locally, PII redacted before AI calls' : 'Privacy warning: PII redaction is off'"
              aria-haspopup="menu"
            >
              <ShieldCheck v-if="piiRedactionEnabled" :size="11" aria-hidden="true" />
              <ShieldAlert v-else :size="11" aria-hidden="true" />
              <span class="text-xs">{{ piiRedactionEnabled ? 'Local · PII redacted' : 'PII redaction off' }}</span>
            </label>
            <div
              tabindex="0"
              class="dropdown-content z-[60] card card-compact w-80 shadow-lg bg-base-100 border border-base-300"
              role="menu"
            >
              <div class="card-body gap-3">
                <div class="flex items-start gap-2">
                  <Lock :size="14" class="text-base-content/60 flex-shrink-0 mt-0.5" aria-hidden="true" />
                  <div class="text-xs">
                    <div class="font-medium text-base-content/90">Stored locally</div>
                    <div class="text-base-content/60 mt-0.5">
                      Your documents and the derived index live on the machine running Asymptote. They are not uploaded to our servers.
                    </div>
                  </div>
                </div>
                <div class="flex items-start gap-2">
                  <ShieldCheck v-if="piiRedactionEnabled" :size="14" class="text-success flex-shrink-0 mt-0.5" aria-hidden="true" />
                  <ShieldAlert v-else :size="14" class="text-warning flex-shrink-0 mt-0.5" aria-hidden="true" />
                  <div class="text-xs">
                    <div class="font-medium text-base-content/90">
                      {{ piiRedactionEnabled ? 'PII redacted before AI calls' : 'PII redaction is disabled' }}
                    </div>
                    <div class="text-base-content/60 mt-0.5">
                      <template v-if="piiRedactionEnabled">
                        Names, account numbers, and other identifiers are detected and replaced on-device before any prompt is sent to the AI provider.
                      </template>
                      <template v-else>
                        Prompts sent to the AI provider may contain client identifiers. Enable redaction in Privacy settings.
                      </template>
                    </div>
                  </div>
                </div>
                <button
                  class="btn btn-xs btn-ghost justify-start w-full text-xs font-normal"
                  @click="$emit('switch-tab', 'settings')"
                >
                  Privacy settings →
                </button>
              </div>
            </div>
          </div>

          <!-- Session switcher -->
          <div class="dropdown dropdown-end">
            <label
              tabindex="0"
              class="btn btn-xs btn-ghost btn-square text-base-content/50 hover:text-base-content"
              title="Switch chat session"
              :aria-label="`Switch chat session. Current: ${activeSessionTitle}`"
              aria-haspopup="menu"
            >
              <History :size="13" aria-hidden="true" />
            </label>
            <ul tabindex="0" class="dropdown-content z-[60] menu p-2 shadow-lg bg-base-100 border border-base-300 rounded-box w-72 max-h-96 overflow-y-auto">
              <li class="menu-title">
                <span class="text-xs">Chat sessions ({{ sessions.length }})</span>
              </li>
              <li v-for="session in sessions" :key="session.id">
                <div
                  class="flex items-start gap-2 group"
                  :class="{ 'bg-primary/10': session.id === activeSessionId }"
                >
                  <button class="flex-1 text-left" @click="switchSession(session.id)">
                    <div class="text-sm font-medium truncate">{{ session.title || 'New chat' }}</div>
                    <div class="text-xs text-base-content/50">
                      {{ session.messages.length }} message{{ session.messages.length !== 1 ? 's' : '' }}
                      · {{ formatRelativeTime(session.updatedAt) }}
                    </div>
                  </button>
                  <button
                    class="btn btn-xs btn-ghost btn-circle opacity-0 group-hover:opacity-100 focus:opacity-100"
                    title="Delete session"
                    :aria-label="`Delete chat session: ${session.title || 'New chat'}`"
                    @click.stop="deleteSession(session.id)"
                  >
                    <Trash2 :size="12" />
                  </button>
                </div>
              </li>
            </ul>
          </div>

          <button
            class="btn btn-xs btn-ghost btn-square text-base-content/50 hover:text-base-content"
            @click="startNewChat"
            title="New chat"
            aria-label="New chat"
          >
            <Plus :size="13" />
          </button>

          <button
            v-if="messages.length > 0"
            class="btn btn-xs btn-ghost btn-square text-base-content/50 hover:text-error"
            @click="clearChat"
            title="Clear this session"
            aria-label="Clear messages"
          >
            <Trash2 :size="13" />
          </button>
        </div>
      </div>

      <!-- No providers configured notice (inline, always visible) -->
      <div v-if="!hasAnyProvider" class="flex items-center gap-3 rounded-lg bg-info/10 border border-info/30 px-3 py-2 flex-shrink-0">
        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" class="stroke-info shrink-0 w-4 h-4">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
        </svg>
        <span class="text-sm flex-1">Configure an AI provider in Settings to use Chat.</span>
        <button class="btn btn-xs btn-primary" @click="$emit('switch-tab', 'settings')">Settings</button>
      </div>

      <!-- Chat Settings Drawer -->
      <div
        v-if="settingsDrawerOpen"
        class="fixed inset-0 z-[200]"
        @click.self="settingsDrawerOpen = false"
        role="dialog"
        aria-modal="true"
        aria-labelledby="chat-settings-title"
      >
        <!-- Backdrop -->
        <div class="absolute inset-0 bg-black/30" @click="settingsDrawerOpen = false" aria-hidden="true"></div>

        <!-- Drawer Panel -->
        <div class="absolute right-0 top-0 h-full w-80 max-w-[85vw] bg-base-100 shadow-2xl flex flex-col">
          <!-- Header -->
          <div class="flex items-center justify-between p-4 border-b border-base-300">
            <h3 id="chat-settings-title" class="text-sm font-bold">Chat Settings</h3>
            <button
              class="btn btn-ghost btn-sm btn-circle"
              @click="settingsDrawerOpen = false"
              aria-label="Close chat settings"
            >
              <X :size="18" />
            </button>
          </div>

          <!-- Content -->
          <div class="flex-1 overflow-y-auto p-4 space-y-5">

            <!-- Scope -->
            <div class="space-y-2">
              <span class="text-xs font-semibold text-base-content/60 uppercase tracking-wider">Scope</span>
              <div class="flex items-center gap-1 rounded-lg border border-base-300 p-1 bg-base-200/60">
                <button
                  class="btn btn-xs gap-1 flex-1 transition-all"
                  :class="scope === 'current' ? 'btn-primary' : 'btn-ghost'"
                  @click="scope = 'current'"
                >
                  <Layers :size="12" />
                  Current
                </button>
                <button
                  class="btn btn-xs gap-1 flex-1 transition-all"
                  :class="scope === 'all' ? 'btn-secondary' : 'btn-ghost'"
                  @click="scope = 'all'"
                >
                  <Database :size="12" />
                  All
                </button>
              </div>
            </div>

            <!-- Retrieval -->
            <div class="space-y-2">
              <span class="text-xs font-semibold text-base-content/60 uppercase tracking-wider">Retrieval</span>

              <label class="flex items-center justify-between cursor-pointer select-none">
                <span class="text-sm">Rerank context</span>
                <input type="checkbox" class="toggle toggle-sm toggle-primary" v-model="rerank" />
              </label>

              <label class="flex items-center justify-between">
                <span class="text-sm">Context chunks</span>
                <input
                  v-model.number="topK"
                  type="number"
                  min="1"
                  max="20"
                  class="input input-bordered input-xs w-16 text-center"
                  aria-label="Number of context chunks"
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
            </div>

            <!-- Provider -->
            <div v-if="hasAnyProvider" class="space-y-2">
              <span class="text-xs font-semibold text-base-content/60 uppercase tracking-wider">AI Provider</span>
              <div class="space-y-1.5">
                <label
                  v-for="pid in configuredProviders"
                  :key="pid"
                  class="flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer border transition-colors text-sm"
                  :class="selectedProvider === pid ? 'bg-primary/20 border-primary font-medium' : 'bg-base-200/60 border-base-300 hover:bg-base-100'"
                >
                  <input type="radio" class="radio radio-xs radio-primary" :checked="selectedProvider === pid" @change="selectProvider(pid)" />
                  <span class="flex-1">{{ providerDisplayName(pid) }}</span>
                  <span class="badge badge-xs badge-outline">{{ isLocalProvider(pid) ? 'local' : 'cloud' }}</span>
                </label>
              </div>
            </div>

          </div>
        </div>
      </div>

      <!-- No data notice -->
      <div v-if="documentCount === 0" class="alert alert-warning flex-shrink-0 py-2">
        <svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-5 w-5" fill="none" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
        </svg>
        <span class="text-sm">No documents indexed. Upload and index sources before chatting.</span>
      </div>

      <!-- Error -->
      <div v-if="error" class="alert alert-error flex-shrink-0 py-2" role="alert">
        <svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-5 w-5" fill="none" viewBox="0 0 24 24" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        <span class="text-sm">{{ error }}</span>
        <button class="btn btn-xs btn-ghost" @click="error = ''" aria-label="Dismiss error">✕</button>
      </div>

      <!-- Message list -->
      <div ref="messagesContainer" class="flex-1 overflow-y-auto space-y-4 min-h-0 pr-1">

        <!-- Empty state -->
        <div v-if="messages.length === 0" class="flex flex-col items-center justify-center h-full text-base-content/50 gap-3 py-8">
          <div v-if="hasAnyProvider && documentCount > 0" class="flex flex-wrap gap-2 justify-center max-w-md">
            <button
              v-for="suggestion in suggestions"
              :key="suggestion"
              class="btn btn-xs btn-outline"
              @click="useSuggestion(suggestion)"
            >
              {{ suggestion }}
            </button>
          </div>
        </div>

        <!-- Messages -->
        <template v-for="(msg, index) in messages" :key="index">

          <!-- User message -->
          <div v-if="msg.role === 'user'" class="flex justify-end">
            <div class="max-w-[80%] rounded-2xl rounded-tr-sm bg-primary text-primary-content px-4 py-3 shadow-sm">
              <p class="text-sm whitespace-pre-wrap">{{ msg.content }}</p>
            </div>
          </div>

          <!-- Assistant message -->
          <div v-else class="flex flex-col gap-1">
            <div class="flex items-start gap-2 max-w-[90%]">
              <div class="flex-shrink-0 w-7 h-7 rounded-full bg-base-300 flex items-center justify-center mt-1">
                <Bot :size="14" class="text-base-content/60" />
              </div>
              <div class="rounded-2xl rounded-tl-sm bg-base-200 border border-base-300 px-4 py-3 shadow-sm flex-1">
                <div
                  v-if="msg.slashCommand"
                  class="prose prose-sm max-w-none whitespace-pre-wrap font-mono text-xs leading-snug"
                >{{ msg.content }}</div>

                <!-- Initial "Thinking…" placeholder before any content arrives -->
                <div v-else-if="msg.streaming && !msg.content && (!msg.structuredResults || msg.structuredResults.length === 0)"
                  class="flex items-center gap-2 text-xs text-base-content/50 py-0.5">
                  <span class="loading loading-dots loading-xs text-primary"></span>
                  <span>Thinking…</span>
                </div>

                <!-- Structured query / metric results (rendered BEFORE the prose answer
                     so the synthesized response lands at the bottom of the message,
                     where the auto-scroll anchor keeps it in view as it streams) -->
                <div v-if="msg.structuredResults && msg.structuredResults.length > 0" class="mt-3 space-y-2">
                  <template v-for="(sr, srIdx) in msg.structuredResults" :key="srIdx">

                  <!-- Thinking breadcrumb: prose the agent emitted between tool calls -->
                  <div
                    v-if="sr.tool === '_thinking'"
                    class="flex items-start gap-2 text-xs text-base-content/50 italic px-1 py-0.5"
                  >
                    <Sparkles :size="11" class="mt-0.5 flex-shrink-0 text-base-content/30" />
                    <span class="whitespace-pre-wrap">{{ sr.result?.text }}</span>
                  </div>

                  <!-- Tool call card -->
                  <div
                    v-else
                    class="rounded-lg border bg-base-100 overflow-hidden transition-colors"
                    :class="sr.pending ? 'border-primary/40 bg-primary/5' : 'border-base-300'"
                  >
                    <div class="flex items-center gap-2 px-3 py-1.5 border-b"
                      :class="sr.pending ? 'bg-primary/10 border-primary/20' : 'bg-base-200 border-base-300'"
                    >
                      <!-- Spinning icon while pending, normal icon when done -->
                      <span v-if="sr.pending" class="loading loading-spinner loading-xs text-primary flex-shrink-0"></span>
                      <component v-else :is="toolIcon(sr.tool)" :size="12" :class="toolIconClass(sr.tool)" />
                      <span class="text-xs font-semibold">{{ toolLabel(sr) }}</span>
                      <span v-if="toolDetail(sr)" class="text-xs text-base-content/50 font-mono truncate">{{ toolDetail(sr) }}</span>
                      <span v-if="sr.pending" class="ml-auto text-xs text-primary/60 italic">running…</span>
                      <button
                        v-else
                        class="ml-auto btn btn-ghost btn-xs p-0 h-4 min-h-0 text-base-content/40"
                        @click="toggleStructuredDetail(srIdx, index)"
                        :title="isStructuredOpen(srIdx, index) ? 'Hide details' : 'Show details'"
                      >
                        <ChevronDown
                          :size="12"
                          class="transition-transform"
                          :class="isStructuredOpen(srIdx, index) ? 'rotate-180' : ''"
                        />
                      </button>
                    </div>

                    <!-- Error -->
                    <div v-if="sr.error" class="px-3 py-2 text-xs text-error">{{ sr.error }}</div>

                    <!-- Tabular result (query / top_holdings / etc.) -->
                    <div
                      v-else-if="sr.result && sr.result.columns && sr.result.rows"
                      class="overflow-x-auto max-h-80"
                    >
                      <table class="table table-xs">
                        <thead>
                          <tr>
                            <th v-for="col in sr.result.columns" :key="col" class="text-xs">{{ col }}</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr v-for="(row, rIdx) in sr.result.rows" :key="rIdx">
                            <td
                              v-for="(cell, cIdx) in row"
                              :key="cIdx"
                              class="text-xs font-mono"
                              :class="{ 'text-right': isNumeric(cell) }"
                            >{{ formatCell(cell) }}</td>
                          </tr>
                        </tbody>
                      </table>
                      <div v-if="sr.result.truncated" class="px-3 py-1 text-xs text-base-content/40 border-t border-base-300">
                        Showing first {{ sr.result.row_count }} rows (truncated)
                      </div>
                    </div>

                    <!-- Breakdown result -->
                    <div v-else-if="sr.result && sr.result.groups" class="overflow-x-auto max-h-80">
                      <table class="table table-xs">
                        <thead>
                          <tr>
                            <th class="text-xs">{{ sr.result.group_column || 'group' }}</th>
                            <th class="text-xs text-right">total</th>
                            <th class="text-xs text-right">count</th>
                            <th class="text-xs text-right">%</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr v-for="(g, gIdx) in sr.result.groups" :key="gIdx">
                            <td class="text-xs">{{ g.group }}</td>
                            <td class="text-xs font-mono text-right">{{ formatCell(g.total) }}</td>
                            <td class="text-xs font-mono text-right">{{ g.count }}</td>
                            <td class="text-xs font-mono text-right">{{ g.pct != null ? g.pct.toFixed(2) + '%' : '' }}</td>
                          </tr>
                        </tbody>
                      </table>
                    </div>

                    <!-- Scalar metric result -->
                    <div v-else-if="sr.result && 'value' in sr.result" class="px-3 py-2 text-sm">
                      <span class="font-mono font-bold">{{ formatCell(sr.result.value) }}</span>
                      <span v-if="sr.result.column" class="ml-2 text-xs text-base-content/50">from "{{ sr.result.column }}"</span>
                    </div>

                    <!-- Document search results -->
                    <div v-else-if="sr.tool === 'search_documents' && sr.result?.results" class="px-3 py-2 space-y-1">
                      <div class="text-xs text-base-content/50">
                        {{ sr.result.total_results }} result{{ sr.result.total_results === 1 ? '' : 's' }}
                      </div>
                      <ul class="space-y-0.5">
                        <li
                          v-for="r in sr.result.results.slice(0, 6)"
                          :key="r.rank + r.filename"
                          class="text-xs flex items-baseline gap-2"
                        >
                          <span class="text-base-content/40 font-mono">#{{ r.rank }}</span>
                          <span class="font-medium truncate">{{ r.filename }}</span>
                          <span v-if="r.page_number" class="text-base-content/40">p.{{ r.page_number }}</span>
                          <span class="text-base-content/40 ml-auto font-mono">{{ Number(r.similarity_score).toFixed(3) }}</span>
                        </li>
                      </ul>
                    </div>

                    <!-- Document context fetch -->
                    <div v-else-if="sr.tool === 'get_document_context' && sr.result" class="px-3 py-2 text-xs">
                      <div class="text-base-content/50">
                        {{ sr.result.filename }}
                        <span v-if="sr.result.total_pages">· {{ sr.result.total_pages }} pages</span>
                        · {{ sr.result.total_chars }} chars retrieved
                      </div>
                    </div>

                    <!-- Company news -->
                    <div v-else-if="sr.tool === 'get_company_news' && sr.result?.news" class="px-3 py-2 space-y-1.5">
                      <div v-if="sr.result.count === 0" class="text-xs text-base-content/50">No recent news.</div>
                      <ul v-else class="space-y-1.5">
                        <li v-for="(n, nIdx) in sr.result.news.slice(0, 8)" :key="nIdx" class="text-xs">
                          <a v-if="n.url" :href="n.url" target="_blank" rel="noopener" class="font-medium link link-hover">{{ n.title }}</a>
                          <span v-else class="font-medium">{{ n.title }}</span>
                          <div class="text-base-content/40 text-[10px]">
                            {{ n.publisher || 'unknown' }}<span v-if="n.published_at"> · {{ n.published_at }}</span>
                          </div>
                        </li>
                      </ul>
                    </div>

                    <!-- Company profile -->
                    <div v-else-if="sr.tool === 'get_company_profile' && sr.result?.name" class="px-3 py-2 space-y-1 text-xs">
                      <div class="font-semibold text-sm">{{ sr.result.name }} <span class="text-base-content/40 font-normal">· {{ sr.result.symbol }}</span></div>
                      <div v-if="sr.result.ceo" class="text-base-content/70">CEO: {{ sr.result.ceo.name }}<span v-if="sr.result.ceo.title" class="text-base-content/40"> · {{ sr.result.ceo.title }}</span></div>
                      <div class="text-base-content/50">
                        <span v-if="sr.result.sector">{{ sr.result.sector }}</span>
                        <span v-if="sr.result.industry"> · {{ sr.result.industry }}</span>
                        <span v-if="sr.result.employees"> · {{ sr.result.employees.toLocaleString() }} employees</span>
                      </div>
                    </div>

                    <!-- list_tables / list_collections / get_collection_info -->
                    <div v-else-if="sr.result?.tables" class="px-3 py-2 text-xs text-base-content/60">
                      {{ sr.result.tables.length }} table{{ sr.result.tables.length === 1 ? '' : 's' }}
                    </div>
                    <div v-else-if="sr.result?.collections" class="px-3 py-2 text-xs text-base-content/60">
                      {{ sr.result.collections.length }} collection{{ sr.result.collections.length === 1 ? '' : 's' }}
                    </div>

                    <!-- Price history summary -->
                    <div
                      v-else-if="sr.tool === 'get_price_history' && sr.result?.points"
                      class="px-3 py-2 text-xs space-y-1.5"
                    >
                      <div class="flex flex-wrap gap-x-3 gap-y-1 text-base-content/70">
                        <span class="font-mono">{{ sr.result.symbol }}</span>
                        <span class="text-base-content/40">·</span>
                        <span>{{ sr.result.interval }} bars</span>
                        <span class="text-base-content/40">·</span>
                        <span>{{ sr.result.start }} → {{ sr.result.end }}</span>
                        <span class="text-base-content/40">·</span>
                        <span>{{ sr.result.point_count }} points</span>
                        <span v-if="sr.result.currency" class="text-base-content/40">·</span>
                        <span v-if="sr.result.currency" class="font-mono">{{ sr.result.currency }}</span>
                      </div>
                      <div v-if="priceHistorySummary(sr.result)" class="flex flex-wrap gap-x-4 gap-y-1 font-mono">
                        <span>
                          <span class="text-base-content/50">open:</span>
                          {{ formatCell(priceHistorySummary(sr.result).firstClose) }}
                        </span>
                        <span>
                          <span class="text-base-content/50">close:</span>
                          {{ formatCell(priceHistorySummary(sr.result).lastClose) }}
                        </span>
                        <span
                          v-if="priceHistorySummary(sr.result).pctChange != null"
                          :class="priceHistorySummary(sr.result).pctChange >= 0 ? 'text-success' : 'text-error'"
                        >
                          {{ priceHistorySummary(sr.result).pctChange >= 0 ? '+' : '' }}{{ priceHistorySummary(sr.result).pctChange.toFixed(2) }}%
                        </span>
                        <span>
                          <span class="text-base-content/50">high:</span>
                          {{ formatCell(priceHistorySummary(sr.result).high) }}
                        </span>
                        <span>
                          <span class="text-base-content/50">low:</span>
                          {{ formatCell(priceHistorySummary(sr.result).low) }}
                        </span>
                      </div>
                      <div v-if="sr.result.cached" class="text-base-content/40">
                        cached · source: {{ sr.result.source }}
                      </div>
                    </div>

                    <!-- Generic key/value fallback -->
                    <div v-else-if="sr.result" class="px-3 py-2 text-xs font-mono space-y-0.5 max-h-48 overflow-y-auto">
                      <div v-for="(val, key) in sr.result" :key="key" class="flex gap-2">
                        <span class="text-base-content/50">{{ key }}:</span>
                        <span class="truncate">{{ formatCell(val) }}</span>
                      </div>
                    </div>

                    <!-- Details toggle (SQL, raw args) -->
                    <div v-if="isStructuredOpen(srIdx, index)" class="px-3 py-2 border-t border-base-300 bg-base-200/50">
                      <div v-if="sr.args?.sql" class="text-xs font-mono break-all text-base-content/60">
                        <span class="font-semibold">SQL:</span> {{ sr.args.sql }}
                      </div>
                      <div v-else-if="sr.args" class="text-xs font-mono text-base-content/60">
                        {{ JSON.stringify(sr.args) }}
                      </div>
                    </div>
                  </div>
                  </template>
                </div>

                <!-- Synthesized prose answer — streams in below the tool cards so
                     auto-scroll keeps the final response visible. -->
                <div v-if="!msg.slashCommand && (msg.content || msg.streaming)" class="relative"
                  :class="{ 'mt-3': msg.structuredResults && msg.structuredResults.length > 0 }">
                  <div
                    class="prose prose-sm max-w-none text-sm chat-markdown"
                    v-html="renderAssistantMarkdown(msg.content)"
                  ></div>
                  <!-- Blinking cursor while streaming -->
                  <span
                    v-if="msg.streaming && msg.content"
                    class="inline-block w-0.5 h-4 bg-primary align-middle ml-0.5 animate-pulse"
                    aria-hidden="true"
                  ></span>
                  <!-- Post-tool "Synthesizing…" hint: tools finished, prose not started -->
                  <div
                    v-if="msg.streaming && !msg.content && msg.structuredResults?.length > 0 && !msg.structuredResults.some(sr => sr.pending)"
                    class="flex items-center gap-2 text-xs text-base-content/50 py-0.5"
                  >
                    <span class="loading loading-dots loading-xs text-primary"></span>
                    <span>Synthesizing answer…</span>
                  </div>
                </div>

                <!-- Footer row: provider badges + copy button -->
                <div
                  v-if="!msg.streaming && (msg.aiUsage || msg.content)"
                  class="flex items-center gap-2 mt-2 flex-wrap"
                >
                  <template v-if="msg.aiUsage">
                    <span class="badge badge-xs" :class="providerBadgeClass(msg.provider || selectedProvider)">
                      {{ providerDisplayName(msg.provider || selectedProvider) }}
                    </span>
                    <span class="text-xs text-base-content/40">
                      {{ msg.aiUsage.total_input_tokens + msg.aiUsage.total_output_tokens }} tokens
                    </span>
                    <span v-if="msg.aiUsage.features_used?.includes('reranking')" class="badge badge-xs badge-outline">reranked</span>
                    <span v-if="msg.aiUsage.features_used?.includes('structured_tools')" class="badge badge-xs badge-success gap-0.5">
                      <Table2 :size="9" /> sql
                    </span>
                    <span v-if="msg.scope === 'all'" class="badge badge-xs badge-secondary badge-outline">all collections</span>
                  </template>
                  <button
                    v-if="msg.content"
                    class="btn btn-ghost btn-xs h-5 min-h-0 px-1.5 ml-auto text-base-content/50 hover:text-base-content gap-1"
                    :title="copiedMessageIndex === index ? 'Copied!' : 'Copy answer'"
                    :aria-label="copiedMessageIndex === index ? 'Copied to clipboard' : 'Copy answer to clipboard'"
                    @click="copyMessage(msg, index)"
                  >
                    <Check v-if="copiedMessageIndex === index" :size="11" class="text-success" />
                    <Copy v-else :size="11" />
                    <span class="text-xs">{{ copiedMessageIndex === index ? 'Copied' : 'Copy' }}</span>
                  </button>
                </div>
              </div>
            </div>

            <!-- Per-message source count -->
            <div v-if="msg.sources && msg.sources.length > 0" class="ml-9">
              <span class="flex items-center gap-1 text-xs text-base-content/40">
                <FileText :size="12" />
                {{ msg.sources.length }} source{{ msg.sources.length !== 1 ? 's' : '' }} retrieved
              </span>
            </div>
          </div>

        </template>

        <!-- Typing indicator — only show while waiting for the first SSE event -->
        <div v-if="loading && !hasStreamingMessage" class="flex items-start gap-2">
          <div class="flex-shrink-0 w-7 h-7 rounded-full bg-base-300 flex items-center justify-center">
            <Bot :size="14" class="text-base-content/60" />
          </div>
          <div class="rounded-2xl rounded-tl-sm bg-base-200 border border-base-300 px-4 py-3 shadow-sm">
            <div class="flex items-center gap-2">
              <span class="loading loading-dots loading-xs text-primary"></span>
              <span class="text-xs text-base-content/50">Connecting…</span>
            </div>
          </div>
        </div>

        <div ref="messagesEnd"></div>
      </div>

      <!-- Input area -->
      <div class="flex-shrink-0 pt-3">

        <!-- Quick-action chips (only shown when chat is empty + provider configured) -->
        <div v-if="messages.length === 0 && hasAnyProvider && props.documentCount > 0" class="flex flex-wrap gap-1.5 mb-2">
          <button
            class="btn btn-xs btn-outline btn-primary gap-1 rounded-full"
            :disabled="loading"
            @click="runBriefCommand"
            title="Generate pre-meeting portfolio brief"
          >
            <FileText :size="11" />
            Generate Meeting Brief
          </button>
        </div>

        <div
          class="relative rounded-2xl border border-base-300 bg-base-200/60 focus-within:border-primary/50 focus-within:ring-1 focus-within:ring-primary/20 transition-all"
        >
          <!-- Slash command picker (floats above the input) -->
          <SlashCommandPicker
            ref="slashPickerRef"
            :show="slashPickerOpen"
            :model-value="inputMessage"
            @select="onSlashSelect"
            @close="slashPickerOpen = false"
          />

          <!-- Textarea -->
          <label for="chat-input" class="sr-only">Ask a question about your documents</label>
          <textarea
            id="chat-input"
            ref="chatInputRef"
            v-model="inputMessage"
            class="w-full bg-transparent border-none outline-none resize-none text-sm px-4 pt-3 pb-2 placeholder:text-base-content/30"
            rows="2"
            placeholder="Ask a question about your documents..."
            :disabled="loading"
            @input="onInputChange"
            @keydown="onKeydown"
            @blur="onInputBlur"
          ></textarea>

          <!-- Bottom toolbar -->
          <div class="flex items-center justify-between px-3 pb-2">
            <!-- Left: inline controls (expert-only) -->
            <div class="flex items-center gap-1.5">
              <template v-if="isExpertMode">
                <button
                  class="btn btn-ghost btn-xs btn-circle"
                  @click="settingsDrawerOpen = !settingsDrawerOpen"
                  title="Chat settings"
                  aria-label="Open chat settings"
                  :aria-expanded="settingsDrawerOpen"
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
                <span v-if="selectedProvider" class="badge badge-xs" :class="providerBadgeClass(selectedProvider)">{{ providerDisplayName(selectedProvider) }}</span>
                <span v-if="rerank" class="badge badge-xs badge-outline badge-primary">Rerank</span>
              </template>
            </div>

            <!-- Right: send button -->
            <button
              class="btn btn-circle btn-sm btn-primary transition-all"
              :class="{ 'btn-disabled opacity-40': sendDisabled }"
              :disabled="sendDisabled"
              @click="sendMessage"
              title="Send message"
              :aria-label="loading ? 'Sending message' : 'Send message'"
            >
              <span v-if="loading" class="loading loading-spinner loading-xs" aria-hidden="true"></span>
              <ArrowUp v-else :size="16" aria-hidden="true" />
            </button>
          </div>
        </div>
        <p v-if="isExpertMode" class="text-xs text-base-content/30 mt-1.5 text-center">Enter to send · Shift+Enter for new line · Try /brief, /tools, /stats, /docs, /help</p>
        <p v-else class="text-xs text-base-content/30 mt-1.5 text-center">Enter to send · Shift+Enter for new line</p>
      </div>

  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, nextTick, watch } from 'vue'
import axios from 'axios'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

marked.setOptions({ gfm: true, breaks: true })

const renderMarkdown = (text) => {
  if (!text) return ''
  const html = marked.parse(String(text))
  return DOMPurify.sanitize(html, { ADD_ATTR: ['target', 'rel'] })
}

const renderAssistantMarkdown = (text) => {
  const html = renderMarkdown(text)
  return html.replace(/<a /g, '<a target="_blank" rel="noopener noreferrer" ')
}
import { Bot, FileText, ArrowUp, Trash2, Layers, Database, Plus, History, ChevronDown, SlidersHorizontal, X, Table2, Search, BookOpen, ListTree, LineChart, Tag, Wrench, Sparkles, Building2, Newspaper, Copy, Check, ShieldCheck, ShieldAlert, Lock } from 'lucide-vue-next'
import { useChatStore } from '../stores/chatStore'
import { useCollectionStore } from '../stores/collectionStore'
import SlashCommandPicker from './SlashCommandPicker.vue'
import { runSlashCommand, isSlashCommand } from '../utils/slashCommands'
import {
  getConfiguredProviderIds,
  buildProviderHeaders,
  getAPIProviderName,
  getProviderDisplayName,
  isLocalProvider,
} from '../utils/aiProviders.js'
import { isExpertMode } from '../utils/expertMode.js'

const props = defineProps({
  chunkCount: { type: Number, default: 0 },
  // documentCount is the real "is there anything indexed" signal — CSV/XLSX
  // files live entirely in the structured SQL store and produce zero chunks,
  // so chunkCount alone would falsely trigger the "no data" banner for
  // advisors whose only sources are tabular.
  documentCount: { type: Number, default: 0 },
})
const emit = defineEmits(['switch-tab'])

const chatStore = useChatStore()
const collectionStore = useCollectionStore()

// UI state
const loading = ref(false)
const error = ref('')
const inputMessage = ref('')
const messagesEnd = ref(null)
const settingsDrawerOpen = ref(false)

// Chat options (persisted)
const topK = ref(parseInt(localStorage.getItem('chat_top_k') || '5'))
const searchMode = ref(localStorage.getItem('chat_search_mode') || 'semantic')
const scope = ref(localStorage.getItem('chat_scope') || 'current')
const rerank = ref(localStorage.getItem('chat_rerank') === 'true')

// Provider state
const configuredProviders = computed(() => getConfiguredProviderIds())
const selectedProvider = ref(localStorage.getItem('chat_provider') || '')
const hasAnyProvider = computed(() => configuredProviders.value.length > 0)

// Privacy status (for the header indicator)
const piiRedactionEnabled = ref(true)
const loadPrivacyStatus = async () => {
  try {
    const response = await axios.get('/api/config')
    piiRedactionEnabled.value = response.data.enable_pii_redaction ?? true
  } catch {
    // Keep optimistic default — the badge falls back to "on" if the config
    // endpoint is unreachable, which matches the server-side default.
  }
}

const sendDisabled = computed(() => {
  if (!inputMessage.value.trim() || loading.value) return true
  // Slash commands don't need a provider or indexed content —
  // they read collection metadata directly.
  if (isSlashCommand(inputMessage.value)) return false
  return !hasAnyProvider.value || props.documentCount === 0
})

// True while an SSE streaming message is in-flight (has been added to store but not finalized)
const hasStreamingMessage = computed(() =>
  messages.value.some(m => m.streaming === true)
)

const messages = computed(() => chatStore.getMessages(collectionStore.currentCollectionId))
const sessions = computed(() => chatStore.getSessions(collectionStore.currentCollectionId))
const activeSessionId = computed(() => chatStore.getActiveSessionId(collectionStore.currentCollectionId))
const activeSessionTitle = computed(() => {
  const s = sessions.value.find((x) => x.id === activeSessionId.value)
  return s?.title || 'New chat'
})

const formatRelativeTime = (ts) => {
  if (!ts) return ''
  const diff = Date.now() - ts
  const sec = Math.floor(diff / 1000)
  if (sec < 60) return 'just now'
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min}m ago`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr}h ago`
  const day = Math.floor(hr / 24)
  if (day < 7) return `${day}d ago`
  return new Date(ts).toLocaleDateString()
}

const startNewChat = () => {
  chatStore.newSession(collectionStore.currentCollectionId)
}

const switchSession = (sessionId) => {
  chatStore.selectSession(collectionStore.currentCollectionId, sessionId)
  // Close dropdown by blurring active element
  if (document.activeElement instanceof HTMLElement) document.activeElement.blur()
  scrollToBottom()
}

const deleteSession = (sessionId) => {
  if (confirm('Delete this chat session? This cannot be undone.')) {
    chatStore.deleteSession(collectionStore.currentCollectionId, sessionId)
  }
}

const suggestions = [
  'Top 10 holdings by market value',
  'Portfolio concentration — top 5 positions',
  'Allocation breakdown by sector',
  'Largest unrealized gains and losses',
]

// Expanded-details state for structured result cards: Set of "msgIdx:srIdx"
const openStructuredDetails = ref(new Set())
const structuredKey = (srIdx, msgIdx) => `${msgIdx}:${srIdx}`
const toggleStructuredDetail = (srIdx, msgIdx) => {
  const key = structuredKey(srIdx, msgIdx)
  const next = new Set(openStructuredDetails.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  openStructuredDetails.value = next
}
const isStructuredOpen = (srIdx, msgIdx) =>
  openStructuredDetails.value.has(structuredKey(srIdx, msgIdx))

const isNumeric = (v) => typeof v === 'number' || (typeof v === 'string' && v !== '' && !isNaN(Number(v)))

// Map agent tool names to a renderer label, icon, and primary detail.
const TOOL_META = {
  search_documents:        { label: 'Searching documents',  icon: Search,   color: 'text-info' },
  get_document_context:    { label: 'Reading document',     icon: BookOpen, color: 'text-info' },
  list_tables:             { label: 'Listing tables',       icon: ListTree, color: 'text-base-content/60' },
  get_table_schema:        { label: 'Inspecting schema',    icon: ListTree, color: 'text-base-content/60' },
  get_table_rows:          { label: 'Reading table rows',   icon: Table2,   color: 'text-success' },
  query_table:             { label: 'SQL query',            icon: Table2,   color: 'text-success' },
  aggregate_table:         { label: 'Aggregating table',    icon: Table2,   color: 'text-success' },
  compute_portfolio_metric:{ label: 'Portfolio metric',     icon: Table2,   color: 'text-success' },
  list_collections:        { label: 'Listing collections',  icon: Database, color: 'text-base-content/60' },
  get_collection_info:     { label: 'Collection info',      icon: Database, color: 'text-base-content/60' },
  get_price_history:       { label: 'Price history',        icon: LineChart,color: 'text-warning' },
  get_security_classification: { label: 'Security info',    icon: Tag,      color: 'text-warning' },
  get_company_profile:     { label: 'Company profile',      icon: Building2,color: 'text-warning' },
  get_company_news:        { label: 'Company news',         icon: Newspaper,color: 'text-warning' },
}

const toolMeta = (name) => TOOL_META[name] || { label: name || 'tool', icon: Wrench, color: 'text-base-content/50' }
const toolIcon = (name) => toolMeta(name).icon
const toolIconClass = (name) => toolMeta(name).color
const toolLabel = (sr) => {
  const meta = toolMeta(sr.tool)
  if (sr.tool === 'compute_portfolio_metric') return sr.args?.metric || meta.label
  return meta.label
}
const toolDetail = (sr) => {
  const a = sr.args || {}
  return a.query || a.identifier || a.symbol || a.table || a.filename || a.document_id || ''
}

const formatCell = (v) => {
  if (v == null) return ''
  if (typeof v === 'number') {
    if (!isFinite(v)) return String(v)
    // Large numbers: group with commas and trim to 2 decimals for floats
    if (Number.isInteger(v)) return v.toLocaleString()
    return v.toLocaleString(undefined, { maximumFractionDigits: 4 })
  }
  if (typeof v === 'boolean') return v ? 'true' : 'false'
  if (Array.isArray(v)) return `[${v.length} item${v.length === 1 ? '' : 's'}]`
  if (typeof v === 'object') {
    // Avoid the default "[object Object]" by showing compact JSON, trimmed.
    try {
      const s = JSON.stringify(v)
      return s.length > 200 ? s.slice(0, 200) + '…' : s
    } catch {
      return '[object]'
    }
  }
  const s = String(v)
  return s.length > 200 ? s.slice(0, 200) + '…' : s
}

const priceHistorySummary = (result) => {
  const pts = Array.isArray(result?.points) ? result.points : []
  if (pts.length === 0) return null
  const first = pts[0]
  const last = pts[pts.length - 1]
  const firstClose = typeof first.close === 'number' ? first.close : null
  const lastClose = typeof last.close === 'number' ? last.close : null
  let pctChange = null
  if (firstClose != null && lastClose != null && firstClose !== 0) {
    pctChange = ((lastClose - firstClose) / firstClose) * 100
  }
  let hi = -Infinity, lo = Infinity
  for (const p of pts) {
    if (typeof p.high === 'number' && p.high > hi) hi = p.high
    if (typeof p.low === 'number' && p.low < lo) lo = p.low
  }
  return {
    firstDate: first.date,
    lastDate: last.date,
    firstClose,
    lastClose,
    pctChange,
    high: isFinite(hi) ? hi : null,
    low: isFinite(lo) ? lo : null,
  }
}

const providerDisplayName = getProviderDisplayName

const providerBadgeClass = (provider) => ({
  'badge-primary': provider === 'anthropic',
  'badge-success': provider === 'openai',
  'badge-info': provider === 'ollama',
  'badge-secondary': provider === 'ollama_cloud',
  'badge-warning': provider === 'grok',
  'badge-accent': provider === 'google',
  'badge-neutral': !['anthropic','openai','ollama','ollama_cloud','grok','google'].includes(provider),
})

const selectProvider = (provider) => {
  selectedProvider.value = provider
  localStorage.setItem('chat_provider', provider)
}

const ensureValidProvider = () => {
  if (!selectedProvider.value || !configuredProviders.value.includes(selectedProvider.value)) {
    const first = configuredProviders.value[0]
    if (first) selectProvider(first)
  }
}

const scrollToBottom = async () => {
  await nextTick()
  messagesEnd.value?.scrollIntoView({ behavior: 'smooth' })
}

// ── Copy answer to clipboard ─────────────────────────────────────────────
const copiedMessageIndex = ref(null)
let copyResetTimer = null

const copyMessage = async (msg, index) => {
  if (!msg?.content) return
  try {
    await navigator.clipboard.writeText(msg.content)
    copiedMessageIndex.value = index
    if (copyResetTimer) clearTimeout(copyResetTimer)
    copyResetTimer = setTimeout(() => { copiedMessageIndex.value = null }, 1600)
  } catch (err) {
    console.warn('Clipboard write failed:', err)
  }
}

const useSuggestion = (suggestion) => {
  inputMessage.value = suggestion
}

// Slash command picker state
const slashPickerOpen = ref(false)
const slashPickerRef = ref(null)
const chatInputRef = ref(null)

const toggleSlashPicker = () => {
  slashPickerOpen.value = !slashPickerOpen.value
  if (slashPickerOpen.value) {
    nextTick(() => chatInputRef.value?.focus())
  }
}

const onInputChange = () => {
  // Open the picker as soon as the input starts with "/" so suggestions
  // appear while the user is typing; close it again if they erase the slash.
  // Basic mode hides the slash-command surface entirely.
  if (!isExpertMode.value) {
    slashPickerOpen.value = false
    return
  }
  slashPickerOpen.value = inputMessage.value.trimStart().startsWith('/')
}

const onInputBlur = () => {
  // Delay so click/mousedown on picker items still registers.
  setTimeout(() => {
    slashPickerOpen.value = false
  }, 150)
}

const onKeydown = (e) => {
  // Let the picker handle navigation keys first when it's open.
  if (slashPickerOpen.value && slashPickerRef.value?.handleKeydown(e)) return

  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    sendMessage()
  }
}

const onSlashSelect = (cmd) => {
  // Auto-run on pick — the commands take no arguments today.
  inputMessage.value = cmd
  slashPickerOpen.value = false
  sendMessage()
}

const runInlineSlashCommand = async (input) => {
  chatStore.addMessage(collectionStore.currentCollectionId, { role: 'user', content: input })
  await scrollToBottom()

  const result = await runSlashCommand(input, {
    collectionId: collectionStore.currentCollectionId,
    collection: collectionStore.currentCollection,
    groupId: collectionStore.currentGroupId,
    messages: messages.value,
    providerHeaders: buildProviderHeaders(selectedProvider.value),
  })

  chatStore.addAssistantMessage(
    collectionStore.currentCollectionId,
    { role: 'assistant', content: result.content, slashCommand: result.cmd },
    [],
    null,
  )
  await scrollToBottom()
}

const sendMessage = async () => {
  if (!inputMessage.value.trim() || loading.value) return

  const userContent = inputMessage.value.trim()

  inputMessage.value = ''
  error.value = ''
  slashPickerOpen.value = false

  // Intercept slash commands before hitting the LLM — zero tokens, instant.
  if (isSlashCommand(userContent)) {
    await runInlineSlashCommand(userContent)
    return
  }

  // Refuse to send if no provider is configured.
  if (!hasAnyProvider.value) {
    error.value = 'Configure an AI provider in Settings to chat.'
    return
  }

  const collectionId = collectionStore.currentCollectionId
  chatStore.addMessage(collectionId, { role: 'user', content: userContent })
  await scrollToBottom()

  loading.value = true

  // Add the in-flight placeholder message immediately so the UI shows it.
  chatStore.addStreamingMessage(collectionId)
  await scrollToBottom()

  try {
    const providerHeaders = buildProviderHeaders(selectedProvider.value)
    // Pass only role+content to the API (strip UI-only fields like timestamps).
    const apiMessages = messages.value
      .filter(m => !m.streaming)
      .map(m => ({ role: m.role, content: m.content }))

    const response = await fetch(
      `/api/chat/stream?collection_id=${collectionId}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...providerHeaders },
        body: JSON.stringify({
          messages: apiMessages,
          provider: selectedProvider.value,
          top_k: topK.value,
          mode: searchMode.value,
          scope: collectionStore.currentGroupId ? `group:${collectionStore.currentGroupId}` : scope.value,
          rerank: rerank.value,
        }),
      }
    )

    if (!response.ok) {
      const errBody = await response.json().catch(() => ({}))
      throw new Error(errBody.detail || `HTTP ${response.status}`)
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      // SSE lines end with \n\n — split and process complete events.
      const parts = buffer.split('\n\n')
      buffer = parts.pop() // last part may be incomplete

      for (const part of parts) {
        const line = part.trim()
        if (!line.startsWith('data:')) continue
        const raw = line.slice(5).trim()
        if (!raw || raw === '[DONE]') continue

        let event
        try { event = JSON.parse(raw) } catch { continue }

        if (event.type === 'tool_start') {
          chatStore.addStreamingToolCall(collectionId, event.tool, event.args || {})
          await scrollToBottom()
        } else if (event.type === 'tool_end') {
          chatStore.resolveStreamingToolCall(collectionId, event.tool, event.result || {})
        } else if (event.type === 'thinking') {
          chatStore.addStreamingThinking(collectionId, event.text || '')
          await scrollToBottom()
        } else if (event.type === 'text_delta') {
          chatStore.appendStreamingText(collectionId, event.delta || '')
          await scrollToBottom()
        } else if (event.type === 'sources') {
          // Sources will be committed in 'done'
        } else if (event.type === 'done') {
          chatStore.finalizeStreamingMessage(collectionId, {
            sources: event.sources || [],
            usage: event.usage || {},
            structuredResults: event.structured_results || [],
          })
          // Stamp the last assistant message with provider/scope for the badge
          const msgs = messages.value
          for (let i = msgs.length - 1; i >= 0; i--) {
            if (msgs[i].role === 'assistant') {
              msgs[i].provider = selectedProvider.value
              msgs[i].scope = scope.value
              break
            }
          }
          await scrollToBottom()
        } else if (event.type === 'error') {
          chatStore.removeLastStreamingMessage(collectionId)
          error.value = event.message || 'Chat failed. Please try again.'
        }
      }
    }
  } catch (err) {
    chatStore.removeLastStreamingMessage(collectionId)
    error.value = err.message || 'Chat failed. Please try again.'
  } finally {
    loading.value = false
  }
}

const clearChat = () => {
  if (confirm('Clear this conversation? This cannot be undone.')) {
    chatStore.clearMessages(collectionStore.currentCollectionId)
  }
}

// One-click brief button handler — same as typing `/brief` and hitting Enter
const runBriefCommand = async () => {
  if (loading.value) return
  inputMessage.value = '/brief'
  await runInlineSlashCommand('/brief')
  inputMessage.value = ''
}

// Persist options
watch(topK, (v) => localStorage.setItem('chat_top_k', String(v)))
watch(searchMode, (v) => localStorage.setItem('chat_search_mode', v))
watch(scope, (v) => localStorage.setItem('chat_scope', v))
watch(rerank, (v) => localStorage.setItem('chat_rerank', String(v)))

// Scroll-on-new-message only. Streaming events (text_delta/thinking/tool_start)
// already call scrollToBottom() inline as content arrives, so a deep watch on
// messages would just duplicate work — and on a long conversation it walks the
// full array on every token.
watch(() => messages.value.length, async () => { await scrollToBottom() })

const handlePrefill = (e) => {
  const prompt = e?.detail?.prompt
  if (!prompt) return
  inputMessage.value = prompt
  nextTick(() => {
    const ta = document.querySelector('textarea[placeholder*="message" i], textarea')
    if (ta) ta.focus()
  })
}

onMounted(() => {
  ensureValidProvider()
  scrollToBottom()
  loadPrivacyStatus()
  window.addEventListener('asymptote:prefill-chat', handlePrefill)
})

onBeforeUnmount(() => {
  window.removeEventListener('asymptote:prefill-chat', handlePrefill)
  if (copyResetTimer) clearTimeout(copyResetTimer)
})
</script>
