<template>
  <div class="space-y-6 max-w-5xl mx-auto">

    <!-- Token Visualizer -->
    <div>
      <p class="text-base-content/60 mb-5">
        See exactly how AI models read your text. Each colored block is one token — the fundamental unit AI works with.
      </p>

      <!-- Input row -->
      <div class="flex items-center justify-between mb-1.5">
        <label for="tokenizer-input" class="text-sm font-semibold">Enter text to tokenize</label>
        <div class="flex items-center gap-1 flex-wrap justify-end">
          <span class="text-xs text-base-content/40 mr-1">Try:</span>
          <button
            v-for="ex in examples" :key="ex.label"
            class="btn btn-xs btn-ghost"
            @click="inputText = ex.text"
            :aria-label="`Load example: ${ex.label}`"
          >{{ ex.label }}</button>
          <button class="btn btn-xs btn-ghost text-error" @click="inputText = ''" aria-label="Clear tokenizer input">Clear</button>
        </div>
      </div>
      <textarea
        id="tokenizer-input"
        v-model="inputText"
        class="textarea textarea-bordered w-full h-36 font-mono text-sm resize-none mb-4"
        placeholder="Type or paste any text here…"
        spellcheck="false"
      ></textarea>

      <!-- Stats row -->
      <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
        <div class="stat bg-base-200 rounded-box p-4">
          <div class="stat-title text-xs">Tokens</div>
          <div class="stat-value text-primary text-3xl">{{ tokenCount }}</div>
        </div>
        <div class="stat bg-base-200 rounded-box p-4">
          <div class="stat-title text-xs">Characters</div>
          <div class="stat-value text-3xl">{{ charCount }}</div>
        </div>
        <div class="stat bg-base-200 rounded-box p-4">
          <div class="stat-title text-xs">Words</div>
          <div class="stat-value text-3xl">{{ wordCount }}</div>
        </div>
        <div class="stat bg-base-200 rounded-box p-4">
          <div class="stat-title text-xs">Chars / Token</div>
          <div class="stat-value text-3xl">{{ ratio }}</div>
        </div>
      </div>

      <!-- Efficiency bar -->
      <div v-if="tokenCount > 0" class="bg-base-200 rounded-box px-4 py-3 mb-4">
        <div class="flex items-center gap-3">
          <span class="text-xs text-base-content/50 w-32 flex-shrink-0">Encoding efficiency</span>
          <progress class="progress progress-primary flex-1" :value="Math.min(efficiencyPct, 100)" max="100"></progress>
          <span class="text-xs font-mono w-10 text-right">{{ efficiencyPct }}%</span>
          <span class="text-xs text-base-content/40 hidden sm:inline">{{ efficiencyLabel }}</span>
        </div>
      </div>

      <!-- Token breakdown -->
      <div class="flex items-center justify-between mb-2">
        <span class="text-sm font-semibold">Token breakdown</span>
        <div class="flex items-center gap-3">
          <label class="flex items-center gap-1.5 cursor-pointer">
            <span class="text-xs text-base-content/60">Show IDs</span>
            <input type="checkbox" class="toggle toggle-xs toggle-primary" v-model="showIds" />
          </label>
          <label class="flex items-center gap-1.5 cursor-pointer">
            <span class="text-xs text-base-content/60">Show whitespace</span>
            <input type="checkbox" class="toggle toggle-xs toggle-primary" v-model="showWhitespace" />
          </label>
        </div>
      </div>
      <div v-if="tokenChunks.length > 0" class="bg-base-200 rounded-box p-4 min-h-16 leading-loose font-mono text-sm">
        <span
          v-for="(chunk, i) in tokenChunks" :key="i"
          class="inline-block rounded px-1 py-0.5 m-0.5 cursor-default hover:brightness-90 transition-all"
          :style="tokenStyle(i)"
          :title="`Token ID: ${chunk.id}  |  '${chunk.raw}'  |  ${chunk.raw.length} char${chunk.raw.length !== 1 ? 's' : ''}`"
        >
          <span v-if="showWhitespace">{{ visualizeWhitespace(chunk.text) }}</span>
          <span v-else>{{ chunk.text }}</span>
          <sup v-if="showIds" class="text-[0.6rem] opacity-60 ml-0.5">{{ chunk.id }}</sup>
        </span>
      </div>
      <div v-else class="bg-base-200 rounded-box p-8 text-center text-base-content/40 font-mono text-sm">
        Tokens will appear here as you type…
      </div>
    </div>

    <div class="divider"></div>

    <!-- How AI Works -->
    <div>
      <div class="mb-6">
        <h2 class="text-2xl font-bold mb-1">How AI Conversations Work</h2>
        <p class="text-base-content/60">
          Understanding tokens helps explain the mechanics — and limits — of AI assistants.
        </p>
      </div>

      <div class="space-y-4">

        <!-- Stateless + context -->
        <div class="card bg-base-200">
          <div class="card-body p-5">
            <h3 class="card-title text-base"><Server :size="16" class="text-primary" /> AI has no memory between requests</h3>
            <p class="text-sm text-base-content/70 mt-1">
              Each time you send a message, your entire conversation history is bundled up and sent to the model from scratch.
              There is no persistent memory living on a server — the model is completely stateless. Your chat app is responsible
              for tracking history and re-sending it every turn.
            </p>
          </div>
        </div>

        <!-- Different machine each time -->
        <div class="card bg-base-200">
          <div class="card-body p-5">
            <h3 class="card-title text-base"><Cpu :size="16" class="text-warning" /> A different machine handles every request</h3>
            <p class="text-sm text-base-content/70 mt-1">
              AI models run on large clusters of GPUs. When you send turn 1, it might be processed by server A.
              Turn 2 goes to server B. Because any machine could handle any request, the full context must be included
              every time — the receiving GPU has never seen your conversation before.
            </p>
          </div>
        </div>

        <!-- Tokens = cost + limits -->
        <div class="card bg-base-200">
          <div class="card-body p-5">
            <h3 class="card-title text-base"><DollarSign :size="16" class="text-success" /> Tokens are the unit of cost and capacity</h3>
            <p class="text-sm text-base-content/70 mt-1">
              AI APIs charge per token — both the tokens you send (input) and the tokens the model generates (output).
              Every model also has a <span class="font-semibold">context window</span>: a hard cap on the total tokens
              it can process in one request. Typical limits range from 8K to 200K+ tokens.
              A 1,000-word document is roughly 1,300–1,500 tokens.
            </p>
          </div>
        </div>

        <!-- Why context grows -->
        <div class="card bg-base-200">
          <div class="card-body p-5">
            <h3 class="card-title text-base"><MessageSquare :size="16" class="text-info" /> Context grows with every turn</h3>
            <p class="text-sm text-base-content/70 mt-1">
              Turn 1 sends: <span class="font-mono text-xs bg-base-300 px-1 rounded">system prompt + your message</span>.<br>
              Turn 2 sends: <span class="font-mono text-xs bg-base-300 px-1 rounded">system prompt + turn 1 (user + AI) + your new message</span>.<br>
              Each exchange adds more tokens. Long enough conversations eventually hit the context limit and older
              turns must be summarized or dropped.
            </p>
          </div>
        </div>

        <!-- Efficiency tips -->
        <div class="card bg-base-200">
          <div class="card-body p-5">
            <h3 class="card-title text-base"><Lightbulb :size="16" class="text-secondary" /> Tips for efficient prompting</h3>
            <ul class="text-sm text-base-content/70 mt-1 space-y-1 list-disc list-inside">
              <li>English prose averages ~4 characters per token — code, URLs, and numbers use more tokens per character.</li>
              <li>System prompts are re-sent every turn — keep them concise.</li>
              <li>Use retrieval (like Asymptote's search) instead of pasting entire documents into context.</li>
              <li>For long sessions, start a new conversation with a brief summary rather than letting context fill up.</li>
            </ul>
          </div>
        </div>

      </div>
    </div>

  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { encode, decode } from 'gpt-tokenizer'
import { DollarSign, Lightbulb, MessageSquare, Server, Cpu } from 'lucide-vue-next'

// ─── Token Visualizer ─────────────────────────────────────────────────────────

const inputText = ref('')
const showIds = ref(false)
const showWhitespace = ref(true)

const TOKEN_PALETTE = [
  { bg: '#fde68a', color: '#78350f' },
  { bg: '#bbf7d0', color: '#14532d' },
  { bg: '#bfdbfe', color: '#1e3a8a' },
  { bg: '#fecaca', color: '#7f1d1d' },
  { bg: '#e9d5ff', color: '#4c1d95' },
  { bg: '#fed7aa', color: '#7c2d12' },
  { bg: '#cffafe', color: '#164e63' },
  { bg: '#fce7f3', color: '#831843' },
]

const tokenChunks = computed(() => {
  if (!inputText.value) return []
  try {
    const ids = encode(inputText.value)
    return ids.map(id => ({ id, raw: decode([id]), text: decode([id]) }))
  } catch { return [] }
})

const tokenCount = computed(() => tokenChunks.value.length)
const charCount = computed(() => inputText.value.length)
const wordCount = computed(() => {
  const t = inputText.value.trim()
  return t ? t.split(/\s+/).length : 0
})
const ratio = computed(() => tokenCount.value ? (charCount.value / tokenCount.value).toFixed(1) : '—')
const efficiencyPct = computed(() => {
  if (!tokenCount.value) return 0
  return Math.round(Math.min((charCount.value / tokenCount.value / 4) * 100, 100))
})
const efficiencyLabel = computed(() => {
  const p = efficiencyPct.value
  if (p >= 90) return 'Excellent — dense English prose'
  if (p >= 70) return 'Good — typical English text'
  if (p >= 50) return 'Moderate — mixed content or code'
  return 'Low — numbers, URLs, or non-English characters'
})
const tokenStyle = (i) => {
  const p = TOKEN_PALETTE[i % TOKEN_PALETTE.length]
  return { backgroundColor: p.bg, color: p.color }
}
const visualizeWhitespace = (text) =>
  text.replace(/ /g, '·').replace(/\n/g, '↵\n').replace(/\t/g, '→')

const examples = [
  { label: 'English prose', text: 'The quick brown fox jumps over the lazy dog.' },
  { label: 'Common words',  text: 'I am happy to help you today!' },
  { label: 'Punctuation',   text: "Wait... really?! That's—unexpected." },
  { label: 'Numbers',       text: '3.14159265358979 + 2.71828182845904 = 6.859...' },
  { label: 'Code',          text: 'const greet = (name) => `Hello, ${name}!`;' },
  { label: 'URL',           text: 'https://www.example.com/path/to/resource?query=value&foo=bar' },
  { label: 'Non-English',   text: '日本語のテキストはどのようにトークン化されますか？' },
]
</script>
