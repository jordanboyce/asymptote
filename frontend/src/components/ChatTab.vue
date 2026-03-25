<template>
  <div class="flex flex-col h-full min-h-0">

      <!-- Header row -->
      <div class="flex items-center justify-between flex-shrink-0">
        <div>
          <h2 class="text-2xl font-bold">Chat</h2>
          <p class="text-sm text-base-content/60">Converse with your indexed documents</p>
        </div>
        <div class="flex items-center gap-2">
          <button
            v-if="messages.length > 0"
            class="btn btn-sm btn-ghost gap-1"
            @click="clearChat"
          >
            <Trash2 :size="14" />
            Clear
          </button>
        </div>
      </div>

      <!-- Options bar (collapsible) -->
      <div class="flex-shrink-0 card border border-base-300 bg-base-100/80 shadow-sm">
        <button class="flex w-full items-center justify-between gap-3 px-4 py-3 text-left" @click="settingsCollapsed = !settingsCollapsed">
          <div>
            <div class="text-sm font-semibold">Chat Settings</div>
            <p class="text-xs text-base-content/60">Provider, scope, context options</p>
          </div>
          <div class="flex items-center gap-2">
            <span v-if="selectedProvider" class="badge badge-xs" :class="providerBadgeClass(selectedProvider)">{{ providerDisplayName(selectedProvider) }}</span>
            <span class="badge badge-xs badge-outline">{{ scope === 'all' ? 'All collections' : 'Current' }}</span>
            <span v-if="rerank" class="badge badge-xs badge-outline badge-primary">Rerank</span>
            <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 transition-transform" :class="settingsCollapsed ? '' : 'rotate-180'" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
            </svg>
          </div>
        </button>
        <div v-show="!settingsCollapsed" class="border-t border-base-300 px-4 py-4 space-y-3">

          <!-- Row 1: Scope + Rerank + Context count -->
          <div class="flex flex-wrap items-center gap-3">

            <!-- Scope toggle -->
            <div class="flex items-center gap-1 rounded-lg border border-base-300 p-1 bg-base-200/60">
              <button
                class="btn btn-xs gap-1 transition-all"
                :class="scope === 'current' ? 'btn-primary' : 'btn-ghost'"
                @click="scope = 'current'"
                title="Search only the current collection"
              >
                <Layers :size="12" />
                Current
              </button>
              <button
                class="btn btn-xs gap-1 transition-all"
                :class="scope === 'all' ? 'btn-secondary' : 'btn-ghost'"
                @click="scope = 'all'"
                title="Search across all collections"
              >
                <Database :size="12" />
                All Collections
              </button>
            </div>

            <!-- Rerank toggle -->
            <label class="flex items-center gap-2 cursor-pointer select-none">
              <input type="checkbox" class="checkbox checkbox-xs checkbox-primary" v-model="rerank" />
              <span class="text-sm">Rerank context</span>
              <div class="tooltip tooltip-right" data-tip="Use AI to reorder retrieved chunks by relevance before generating a response. Improves quality but uses extra tokens.">
                <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5 text-base-content/40" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              </div>
            </label>

            <!-- Context chunks -->
            <div class="flex items-center gap-2 ml-auto">
              <span class="text-xs text-base-content/50">Context chunks:</span>
              <input
                v-model.number="topK"
                type="number"
                min="1"
                max="20"
                class="input input-bordered input-xs w-16 text-center"
              />
              <select v-model="searchMode" class="select select-bordered select-xs">
                <option value="semantic">Semantic</option>
                <option value="keyword">Keyword</option>
                <option value="hybrid">Hybrid</option>
              </select>
            </div>
          </div>

          <!-- Row 2: Provider selection -->
          <div v-if="hasAnyProvider">
            <div class="flex items-center gap-2 mb-2">
              <span class="text-xs font-semibold text-base-content/70">AI Provider</span>
              <span
                v-if="selectedProvider"
                class="badge badge-xs"
                :class="providerBadgeClass(selectedProvider)"
              >{{ providerDisplayName(selectedProvider) }}</span>
            </div>
            <div class="flex flex-wrap gap-2">
              <!-- Private/local -->
              <label
                v-if="hasINLHpcKey"
                class="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg cursor-pointer border transition-colors text-sm"
                :class="selectedProvider === 'inl_hpc' ? 'bg-warning/20 border-warning font-medium' : 'bg-base-200/60 border-base-300 hover:bg-base-100'"
              >
                <input type="radio" class="radio radio-xs radio-warning" :checked="selectedProvider === 'inl_hpc'" @change="selectProvider('inl_hpc')" />
                INL HPC
                <span class="badge badge-xs badge-outline">private</span>
              </label>
              <label
                v-if="ollamaAvailable"
                class="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg cursor-pointer border transition-colors text-sm"
                :class="selectedProvider === 'ollama' ? 'bg-info/20 border-info font-medium' : 'bg-base-200/60 border-base-300 hover:bg-base-100'"
              >
                <input type="radio" class="radio radio-xs radio-info" :checked="selectedProvider === 'ollama'" @change="selectProvider('ollama')" />
                Ollama
                <span class="badge badge-xs badge-outline">local</span>
              </label>
              <!-- Cloud -->
              <label
                v-if="hasAnthropicKey"
                class="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg cursor-pointer border transition-colors text-sm"
                :class="selectedProvider === 'anthropic' ? 'bg-primary/20 border-primary font-medium' : 'bg-base-200/60 border-base-300 hover:bg-base-100'"
              >
                <input type="radio" class="radio radio-xs radio-primary" :checked="selectedProvider === 'anthropic'" @change="selectProvider('anthropic')" />
                Anthropic
                <span class="badge badge-xs badge-outline">cloud</span>
              </label>
              <label
                v-if="hasOpenAIKey"
                class="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg cursor-pointer border transition-colors text-sm"
                :class="selectedProvider === 'openai' ? 'bg-success/20 border-success font-medium' : 'bg-base-200/60 border-base-300 hover:bg-base-100'"
              >
                <input type="radio" class="radio radio-xs radio-success" :checked="selectedProvider === 'openai'" @change="selectProvider('openai')" />
                OpenAI
                <span class="badge badge-xs badge-outline">cloud</span>
              </label>
            </div>
          </div>

          <!-- No providers configured notice -->
          <div v-if="!hasAnyProvider" class="flex items-center gap-3 rounded-lg bg-info/10 border border-info/30 px-3 py-2">
            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" class="stroke-info shrink-0 w-4 h-4">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
            </svg>
            <span class="text-sm flex-1">Configure an AI provider in Settings to use Chat.</span>
            <button class="btn btn-xs btn-primary" @click="$emit('switch-tab', 'settings')">Settings</button>
          </div>

        </div>
      </div>

      <!-- No data notice -->
      <div v-if="chunkCount === 0" class="alert alert-warning flex-shrink-0 py-2">
        <svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-5 w-5" fill="none" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
        </svg>
        <span class="text-sm">No documents indexed. Upload and index sources before chatting.</span>
      </div>

      <!-- Error -->
      <div v-if="error" class="alert alert-error flex-shrink-0 py-2">
        <svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-5 w-5" fill="none" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        <span class="text-sm">{{ error }}</span>
        <button class="btn btn-xs btn-ghost" @click="error = ''">✕</button>
      </div>

      <!-- Message list -->
      <div ref="messagesContainer" class="flex-1 overflow-y-auto space-y-4 min-h-0 pr-1">

        <!-- Empty state -->
        <div v-if="messages.length === 0" class="flex flex-col items-center justify-center h-full text-base-content/40 gap-3 py-8">
          <MessageSquare :size="48" class="opacity-30" />
          <div class="text-center">
            <p class="font-semibold">Start a conversation</p>
            <p class="text-sm mt-1">Ask questions about your indexed documents</p>
          </div>
          <div v-if="hasAnyProvider && chunkCount > 0" class="flex flex-wrap gap-2 justify-center mt-2 max-w-md">
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
                <div class="prose prose-sm max-w-none text-sm whitespace-pre-wrap">{{ msg.content }}</div>

                <!-- Token + provider badge -->
                <div v-if="msg.aiUsage" class="flex items-center gap-2 mt-2 flex-wrap">
                  <span class="badge badge-xs" :class="providerBadgeClass(msg.provider || selectedProvider)">
                    {{ providerDisplayName(msg.provider || selectedProvider) }}
                  </span>
                  <span class="text-xs text-base-content/40">
                    {{ msg.aiUsage.total_input_tokens + msg.aiUsage.total_output_tokens }} tokens
                  </span>
                  <span v-if="msg.aiUsage.features_used?.includes('reranking')" class="badge badge-xs badge-outline">reranked</span>
                  <span v-if="msg.scope === 'all'" class="badge badge-xs badge-secondary badge-outline">all collections</span>
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

        <!-- Typing indicator -->
        <div v-if="loading" class="flex items-start gap-2">
          <div class="flex-shrink-0 w-7 h-7 rounded-full bg-base-300 flex items-center justify-center">
            <Bot :size="14" class="text-base-content/60" />
          </div>
          <div class="rounded-2xl rounded-tl-sm bg-base-200 border border-base-300 px-4 py-3 shadow-sm">
            <div class="flex items-center gap-2">
              <span class="loading loading-dots loading-xs text-primary"></span>
              <span class="text-xs text-base-content/50">
                {{ rerank ? 'Retrieving & reranking context…' : 'Retrieving context & generating response…' }}
              </span>
            </div>
          </div>
        </div>

        <div ref="messagesEnd"></div>
      </div>

      <!-- Input area -->
      <div class="flex-shrink-0 border-t border-base-300 pt-3">
        <div class="join w-full">
          <textarea
            v-model="inputMessage"
            class="textarea textarea-bordered join-item flex-1 resize-none text-sm"
            rows="2"
            placeholder="Ask a question about your documents…"
            :disabled="loading || !hasAnyProvider || chunkCount === 0"
            @keydown.enter.exact.prevent="sendMessage"
            @keydown.enter.shift.exact="inputMessage += '\n'"
          ></textarea>
          <button
            class="btn btn-primary join-item px-4 self-stretch"
            :disabled="!inputMessage.trim() || loading || !hasAnyProvider || chunkCount === 0"
            @click="sendMessage"
          >
            <span v-if="loading" class="loading loading-spinner loading-xs"></span>
            <Send v-else :size="16" />
          </button>
        </div>
        <p class="text-xs text-base-content/40 mt-1">Enter to send · Shift+Enter for new line</p>
      </div>

  </div>
</template>

<script setup>
import { ref, computed, onMounted, nextTick, watch } from 'vue'
import axios from 'axios'
import { Bot, FileText, Send, Trash2, MessageSquare, Layers, Database } from 'lucide-vue-next'
import { useChatStore } from '../stores/chatStore'
import { useCollectionStore } from '../stores/collectionStore'

const props = defineProps({
  chunkCount: { type: Number, default: 0 }
})
const emit = defineEmits(['switch-tab'])

const chatStore = useChatStore()
const collectionStore = useCollectionStore()

// UI state
const loading = ref(false)
const error = ref('')
const inputMessage = ref('')
const messagesEnd = ref(null)
const settingsCollapsed = ref(localStorage.getItem('chat_settings_collapsed') !== 'false')

// Chat options (persisted)
const topK = ref(parseInt(localStorage.getItem('chat_top_k') || '5'))
const searchMode = ref(localStorage.getItem('chat_search_mode') || 'semantic')
const scope = ref(localStorage.getItem('chat_scope') || 'current')
const rerank = ref(localStorage.getItem('chat_rerank') === 'true')

// Provider state
const hasAnthropicKey = computed(() => !!localStorage.getItem('ai_api_key_anthropic'))
const hasOpenAIKey = computed(() => !!localStorage.getItem('ai_api_key_openai'))
const hasINLHpcKey = computed(() => !!localStorage.getItem('ai_api_key_inl_hpc'))
const ollamaAvailable = ref(false)
const selectedProvider = ref(localStorage.getItem('chat_provider') || '')

const hasAnyProvider = computed(() =>
  hasAnthropicKey.value || hasOpenAIKey.value || hasINLHpcKey.value || ollamaAvailable.value
)

const messages = computed(() => chatStore.getMessages(collectionStore.currentCollectionId))

const suggestions = [
  'Summarize the key topics in these documents',
  'What are the main findings?',
  'List the most important conclusions',
]

const providerDisplayName = (provider) => {
  const names = { anthropic: 'Anthropic', openai: 'OpenAI', inl_hpc: 'INL HPC', ollama: 'Ollama' }
  return names[provider] || provider
}

const providerBadgeClass = (provider) => ({
  'badge-primary': provider === 'anthropic',
  'badge-success': provider === 'openai',
  'badge-warning': provider === 'inl_hpc',
  'badge-info': provider === 'ollama',
})

const selectProvider = (provider) => {
  selectedProvider.value = provider
  localStorage.setItem('chat_provider', provider)
}

const isProviderAvailable = (p) => {
  if (p === 'anthropic') return hasAnthropicKey.value
  if (p === 'openai') return hasOpenAIKey.value
  if (p === 'inl_hpc') return hasINLHpcKey.value
  if (p === 'ollama') return ollamaAvailable.value
  return false
}

const ensureValidProvider = () => {
  if (!selectedProvider.value || !isProviderAvailable(selectedProvider.value)) {
    const available = ['anthropic', 'openai', 'inl_hpc', 'ollama'].find(p => isProviderAvailable(p))
    if (available) selectProvider(available)
  }
}

const checkOllama = async () => {
  try {
    const response = await axios.get('/api/ollama/status')
    ollamaAvailable.value = response.data.available === true
  } catch {
    ollamaAvailable.value = false
  }
  ensureValidProvider()
}

const scrollToBottom = async () => {
  await nextTick()
  messagesEnd.value?.scrollIntoView({ behavior: 'smooth' })
}

const useSuggestion = (suggestion) => {
  inputMessage.value = suggestion
}

const sendMessage = async () => {
  if (!inputMessage.value.trim() || loading.value) return

  const userContent = inputMessage.value.trim()
  inputMessage.value = ''
  error.value = ''

  chatStore.addMessage(collectionStore.currentCollectionId, { role: 'user', content: userContent })
  await scrollToBottom()

  loading.value = true

  try {
    const headers = {}
    if (selectedProvider.value === 'ollama') {
      headers['X-Ollama-Model'] = localStorage.getItem('ollama_model') || 'llama3.2'
    } else {
      const key = localStorage.getItem(`ai_api_key_${selectedProvider.value}`)
      if (key) headers['X-AI-Key'] = key
      if (selectedProvider.value === 'inl_hpc') {
        headers['X-INL-HPC-Model'] = localStorage.getItem('inl_hpc_model') || 'gpt-oss-120b'
      } else if (selectedProvider.value === 'anthropic') {
        const m = localStorage.getItem('anthropic_model')
        if (m) headers['X-Anthropic-Model'] = m
      } else if (selectedProvider.value === 'openai') {
        const m = localStorage.getItem('openai_model')
        if (m) headers['X-OpenAI-Model'] = m
      }
    }

    // Pass only role+content to the API (strip UI-only fields)
    const apiMessages = messages.value.map(m => ({ role: m.role, content: m.content }))

    const response = await axios.post(
      `/api/chat?collection_id=${collectionStore.currentCollectionId}`,
      {
        messages: apiMessages,
        provider: selectedProvider.value,
        top_k: topK.value,
        mode: searchMode.value,
        scope: scope.value,
        rerank: rerank.value,
      },
      { headers }
    )

    chatStore.addAssistantMessage(
      collectionStore.currentCollectionId,
      { ...response.data.message, provider: selectedProvider.value, scope: scope.value },
      response.data.sources,
      response.data.ai_usage,
    )

    await scrollToBottom()
  } catch (err) {
    error.value = err.response?.data?.detail || 'Chat failed. Please try again.'
  } finally {
    loading.value = false
  }
}

const clearChat = () => {
  if (confirm('Clear this conversation? This cannot be undone.')) {
    chatStore.clearMessages(collectionStore.currentCollectionId)
  }
}

// Persist options
watch(topK, (v) => localStorage.setItem('chat_top_k', String(v)))
watch(searchMode, (v) => localStorage.setItem('chat_search_mode', v))
watch(scope, (v) => localStorage.setItem('chat_scope', v))
watch(rerank, (v) => localStorage.setItem('chat_rerank', String(v)))
watch(settingsCollapsed, (v) => localStorage.setItem('chat_settings_collapsed', String(v)))

watch(messages, async () => { await scrollToBottom() }, { deep: true })

onMounted(() => {
  checkOllama()
  scrollToBottom()
})
</script>
