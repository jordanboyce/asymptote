<template>
  <div
    v-if="show"
    class="fixed inset-0 z-[290] bg-base-200/80 backdrop-blur-sm flex items-center justify-center p-4 sm:p-6"
    role="dialog"
    aria-modal="true"
    aria-labelledby="help-panel-title"
    @click.self="$emit('close')"
    @keydown.esc="$emit('close')"
  >
    <div class="max-w-2xl w-full max-h-[90vh] bg-base-100 border border-base-300 rounded-lg shadow-lg flex flex-col">

      <!-- Header -->
      <header class="flex items-center justify-between gap-3 px-5 py-4 border-b border-base-300 flex-shrink-0">
        <div class="flex items-center gap-2.5">
          <CircleHelp :size="18" class="text-primary" aria-hidden="true" />
          <h2 id="help-panel-title" class="text-lg font-semibold tracking-tight">
            Quick start &amp; help
          </h2>
        </div>
        <button
          class="btn btn-ghost btn-sm btn-circle"
          @click="$emit('close')"
          aria-label="Close help"
          ref="closeBtnRef"
        >
          <X :size="16" />
        </button>
      </header>

      <!-- Section nav (jump links) -->
      <nav
        class="flex flex-wrap gap-1 px-5 py-2 border-b border-base-300 flex-shrink-0 text-xs"
        aria-label="Help sections"
      >
        <button
          v-for="s in SECTIONS"
          :key="s.id"
          class="btn btn-ghost btn-xs gap-1.5 normal-case font-normal"
          @click="scrollTo(s.id)"
        >
          <component :is="s.icon" :size="12" />
          {{ s.label }}
        </button>
      </nav>

      <!-- Scrollable body -->
      <div ref="bodyRef" class="overflow-y-auto px-5 py-5 space-y-7 text-sm leading-relaxed">

        <!-- ── Getting started ─────────────────────────────────────────── -->
        <section id="help-start" class="space-y-3">
          <h3 class="font-semibold flex items-center gap-2 text-base">
            <Sparkles :size="15" class="text-primary" aria-hidden="true" />
            Getting started
          </h3>
          <ol class="space-y-2.5 ml-1">
            <li class="flex gap-3">
              <span class="flex-shrink-0 w-5 h-5 rounded-full bg-primary/15 text-primary text-xs font-semibold flex items-center justify-center mt-0.5">1</span>
              <div>
                <div class="font-medium">Create a collection</div>
                <p class="text-base-content/65 text-xs mt-0.5">
                  One per client household — name it after the family or relationship (e.g. <span class="font-mono text-[11px]">Henderson Household</span>). Create from the <span class="font-medium">Collections</span> tab.
                </p>
              </div>
            </li>
            <li class="flex gap-3">
              <span class="flex-shrink-0 w-5 h-5 rounded-full bg-primary/15 text-primary text-xs font-semibold flex items-center justify-center mt-0.5">2</span>
              <div>
                <div class="font-medium">Add sources</div>
                <p class="text-base-content/65 text-xs mt-0.5">
                  Drag brokerage exports, statements, or notes into the <span class="font-medium">Sources</span> sidebar on the left. Finn indexes them on upload.
                </p>
              </div>
            </li>
            <li class="flex gap-3">
              <span class="flex-shrink-0 w-5 h-5 rounded-full bg-primary/15 text-primary text-xs font-semibold flex items-center justify-center mt-0.5">3</span>
              <div>
                <div class="font-medium">Ask in chat</div>
                <p class="text-base-content/65 text-xs mt-0.5">
                  Plain language works — <span class="font-mono text-[11px]">"Show top 10 holdings"</span>, <span class="font-mono text-[11px]">"What's the cash position?"</span>. Or type <span class="font-mono text-[11px]">/</span> to see slash commands.
                </p>
              </div>
            </li>
          </ol>
        </section>

        <!-- ── What you can upload ─────────────────────────────────────── -->
        <section id="help-uploads" class="space-y-3">
          <h3 class="font-semibold flex items-center gap-2 text-base">
            <Upload :size="15" class="text-primary" aria-hidden="true" />
            What you can upload
          </h3>

          <div class="bg-base-200/60 rounded-md p-3.5 space-y-1.5">
            <div class="text-xs font-medium uppercase tracking-wider text-base-content/55">
              Brokerage statements (auto-detected)
            </div>
            <p class="text-xs text-base-content/70">
              CSV or XLSX exports — Finn recognizes the format and maps columns automatically.
            </p>
            <div class="flex flex-wrap gap-1.5 pt-1">
              <span
                v-for="b in BROKERAGES"
                :key="b"
                class="badge badge-sm badge-ghost border-base-300 text-[11px]"
              >{{ b }}</span>
            </div>
          </div>

          <div class="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
            <div
              v-for="g in FILE_GROUPS"
              :key="g.label"
              class="border border-base-300 rounded-md p-3"
            >
              <div class="flex items-center gap-2 mb-1.5">
                <component :is="g.icon" :size="14" class="text-base-content/60" aria-hidden="true" />
                <span class="text-xs font-medium">{{ g.label }}</span>
              </div>
              <div class="text-[11px] text-base-content/55 font-mono">{{ g.exts }}</div>
              <div v-if="g.note" class="text-[11px] text-base-content/55 mt-1">{{ g.note }}</div>
            </div>
          </div>
        </section>

        <!-- ── Sources & collections ───────────────────────────────────── -->
        <section id="help-collections" class="space-y-3">
          <h3 class="font-semibold flex items-center gap-2 text-base">
            <FolderOpen :size="15" class="text-primary" aria-hidden="true" />
            Sources &amp; collections
          </h3>
          <ul class="space-y-2 ml-1">
            <li class="flex gap-2">
              <span class="text-primary mt-1.5 flex-shrink-0">·</span>
              <div>
                A <span class="font-medium">collection</span> is a workspace — typically one client household. Documents you add are its <span class="font-medium">sources</span>.
              </div>
            </li>
            <li class="flex gap-2">
              <span class="text-primary mt-1.5 flex-shrink-0">·</span>
              <div>
                When you chat, Finn searches across <span class="font-medium">all sources in the active collection</span>. Switch collections from the badge in the top-left.
              </div>
            </li>
            <li class="flex gap-2">
              <span class="text-primary mt-1.5 flex-shrink-0">·</span>
              <div>
                Group related collections into a <span class="font-medium">household</span> for cross-account analysis (e.g. spouse + joint + trust accounts).
              </div>
            </li>
            <li class="flex gap-2">
              <span class="text-primary mt-1.5 flex-shrink-0">·</span>
              <div>
                Originals can be discarded after indexing — Finn keeps only the chunks and embeddings it needs to answer questions.
              </div>
            </li>
          </ul>
        </section>

        <!-- ── AI providers ────────────────────────────────────────────── -->
        <section id="help-providers" class="space-y-3">
          <h3 class="font-semibold flex items-center gap-2 text-base">
            <Cpu :size="15" class="text-primary" aria-hidden="true" />
            AI providers
          </h3>
          <p class="text-xs text-base-content/65">
            Bring your own API key — Finn stores it locally and never proxies through us. Change providers anytime in <span class="font-medium">Settings → AI providers</span>.
          </p>
          <ul class="divide-y divide-base-300 border border-base-300 rounded-md overflow-hidden">
            <li
              v-for="p in visibleProviders"
              :key="p.id"
              class="flex items-start gap-3 px-3 py-2.5"
            >
              <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2 flex-wrap">
                  <span class="text-sm font-medium">{{ p.name }}</span>
                  <span
                    v-if="p.id === 'anthropic'"
                    class="badge badge-primary badge-sm font-medium text-[10px]"
                  >Recommended</span>
                  <span
                    v-if="p.badge === 'local'"
                    class="badge badge-ghost badge-sm border-base-300 text-[10px]"
                  >Local</span>
                </div>
                <div class="text-[11px] text-base-content/55 mt-0.5">{{ providerTagline(p.id) }}</div>
              </div>
              <a
                v-if="p.keyLink"
                :href="p.keyLink"
                target="_blank"
                rel="noopener noreferrer"
                class="link link-hover text-[11px] flex-shrink-0 mt-1"
              >
                Get key →
              </a>
            </li>
          </ul>
          <div class="flex items-start gap-2 text-[11px] text-base-content/65 bg-base-200/60 rounded-md p-3">
            <Shield :size="13" class="text-success flex-shrink-0 mt-0.5" aria-hidden="true" />
            <span>Names, account numbers, and other identifiers are redacted from prompts before any AI call leaves Finn.</span>
          </div>
        </section>

        <!-- ── What Finn can do ────────────────────────────────────────── -->
        <section id="help-commands" class="space-y-3">
          <h3 class="font-semibold flex items-center gap-2 text-base">
            <Zap :size="15" class="text-primary" aria-hidden="true" />
            What you can ask Finn
          </h3>

          <div>
            <div class="text-xs font-medium uppercase tracking-wider text-base-content/55 mb-2">
              Slash commands
            </div>
            <ul class="space-y-1.5">
              <li
                v-for="[cmd, desc] in slashCommandEntries"
                :key="cmd"
                class="flex items-start gap-3"
              >
                <code class="text-[11px] font-mono bg-base-200 text-primary px-1.5 py-0.5 rounded flex-shrink-0 min-w-[4.5rem] text-center">{{ cmd }}</code>
                <span class="text-xs text-base-content/75">{{ desc }}</span>
              </li>
            </ul>
          </div>

          <div>
            <div class="text-xs font-medium uppercase tracking-wider text-base-content/55 mb-2">
              Or just ask in plain language
            </div>
            <ul class="space-y-1.5">
              <li
                v-for="ex in EXAMPLES"
                :key="ex"
                class="text-xs text-base-content/75 font-mono bg-base-200/50 rounded px-2.5 py-1.5"
              >"{{ ex }}"</li>
            </ul>
          </div>

          <div>
            <div class="text-xs font-medium uppercase tracking-wider text-base-content/55 mb-2">
              Under the hood
            </div>
            <div class="grid grid-cols-1 sm:grid-cols-3 gap-2">
              <div
                v-for="cap in CAPABILITIES"
                :key="cap.title"
                class="border border-base-300 rounded-md p-2.5"
              >
                <div class="text-[11px] font-semibold uppercase tracking-wider text-base-content/65 mb-1">{{ cap.title }}</div>
                <div class="text-[11px] text-base-content/60 leading-snug">{{ cap.summary }}</div>
              </div>
            </div>
          </div>
        </section>
      </div>

      <!-- Footer -->
      <footer class="px-5 py-3 border-t border-base-300 flex-shrink-0 flex items-center justify-between text-[11px] text-base-content/55">
        <span>Press <kbd class="kbd kbd-xs">Esc</kbd> to close</span>
        <button class="link link-hover" @click="$emit('close')">Got it</button>
      </footer>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import {
  CircleHelp, X, Sparkles, Upload, FolderOpen, Cpu, Zap, Shield,
  FileText, Table, Mic, Image, Code, Database,
} from 'lucide-vue-next'
import { SLASH_COMMANDS } from '../utils/slashCommands.js'
import { visibleProviderDefs } from '../utils/aiProviders.js'

const props = defineProps({
  show: { type: Boolean, default: false },
})

defineEmits(['close'])

const closeBtnRef = ref(null)
const bodyRef = ref(null)

// Autofocus close on open; reset scroll so panel always opens at the top.
watch(
  () => props.show,
  async (isShown) => {
    if (!isShown) return
    await nextTick()
    closeBtnRef.value?.focus()
    if (bodyRef.value) bodyRef.value.scrollTop = 0
  },
)

const SECTIONS = [
  { id: 'help-start', label: 'Start', icon: Sparkles },
  { id: 'help-uploads', label: 'Uploads', icon: Upload },
  { id: 'help-collections', label: 'Collections', icon: FolderOpen },
  { id: 'help-providers', label: 'AI', icon: Cpu },
  { id: 'help-commands', label: 'Commands', icon: Zap },
]

const BROKERAGES = ['Pershing', 'Schwab', 'Fidelity', 'Vanguard', 'NetX360']

const FILE_GROUPS = [
  { label: 'Documents', icon: FileText, exts: 'PDF, DOCX, TXT, MD' },
  { label: 'Spreadsheets', icon: Table, exts: 'CSV, XLSX, XLS' },
  { label: 'Meeting recordings', icon: Mic, exts: 'MP3, WAV, M4A, OGG, FLAC', note: 'Auto-transcribed via Whisper' },
  { label: 'Images', icon: Image, exts: 'PNG, JPG, JPEG', note: 'OCR\'d when enabled' },
  { label: 'Structured data', icon: Database, exts: 'JSON, JSONL' },
  { label: 'Code & text', icon: Code, exts: 'Most plain-text source files' },
]

// Per-provider one-liner. Order in the UI follows visibleProviderDefs(), so
// hosted deployments will already have local Ollama filtered out.
const PROVIDER_TAGLINES = {
  anthropic: 'Best reasoning, biggest context — the most capable experience in Finn.',
  openai: 'GPT-4o and o-series. Solid all-rounder.',
  grok: 'xAI Grok-3 / Grok-3 Mini.',
  google: 'Gemini 2.5 Pro and Flash variants.',
  github: 'GitHub Models marketplace — GPT, Llama, Mistral, DeepSeek, and more under one key.',
  ollama_cloud: 'Open-weight models (GPT-OSS, Gemma, Qwen, DeepSeek). Free tier available.',
  ollama: 'Local LLMs running on your machine — fully offline. Desktop app only.',
}

function providerTagline(id) {
  return PROVIDER_TAGLINES[id] || ''
}

const visibleProviders = computed(() => visibleProviderDefs())

const slashCommandEntries = computed(() => Object.entries(SLASH_COMMANDS))

const EXAMPLES = [
  'Show top 10 holdings by market value',
  'What\'s the cash position across all accounts?',
  'Generate a meeting brief',
  'Find tax-loss candidates above $1,000',
  'Summarize the last meeting transcript',
  'What sector is most concentrated in this portfolio?',
]

const CAPABILITIES = [
  { title: 'Documents', summary: 'Keyword, semantic, and hybrid search across every indexed source.' },
  { title: 'Portfolio tables', summary: 'SQL-style queries, aggregations, and metrics on imported CSV/XLSX holdings.' },
  { title: 'Market data', summary: 'Prices, sector, classification, company profile, and recent news (Yahoo Finance).' },
]

function scrollTo(id) {
  const el = bodyRef.value?.querySelector(`#${id}`)
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
</script>
