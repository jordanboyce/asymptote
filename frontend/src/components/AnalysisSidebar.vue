<template>
  <div class="flex flex-col h-full">

    <!-- Header bar -->
    <div class="flex items-center gap-2 px-3 py-2.5 border-b border-base-300 flex-shrink-0 bg-base-100" role="region" aria-label="Analysis">
      <FlaskConical :size="15" class="text-base-content/60 flex-shrink-0" aria-hidden="true" />
      <span class="font-semibold text-sm flex-1">Analysis</span>
      <button
        class="btn btn-ghost btn-xs btn-circle"
        @click="$emit('close')"
        title="Close analysis sidebar"
        aria-label="Close analysis sidebar"
      >
        <X :size="13" />
      </button>
    </div>

    <!-- Scrollable body -->
    <div class="flex-1 overflow-y-auto">

      <!-- Portfolio Metrics -->
      <section class="border-b border-base-300">
        <button
          class="w-full flex items-center gap-2 px-3 py-2.5 hover:bg-base-200 transition-colors text-left"
          @click="open.metrics = !open.metrics"
          :aria-expanded="open.metrics"
        >
          <TrendingUp :size="13" class="text-primary flex-shrink-0" />
          <span class="text-xs font-semibold flex-1">Portfolio Metrics</span>
          <ChevronDown :size="12" class="text-base-content/40 transition-transform" :class="open.metrics ? 'rotate-180' : ''" />
        </button>
        <div v-show="open.metrics" class="px-3 pb-3 space-y-1">
          <p class="text-[11px] text-base-content/50 leading-snug pb-1">
            Run a canned metric on a structured table in this collection.
          </p>
          <button
            v-for="m in portfolioMetrics"
            :key="m.id"
            class="w-full text-left px-2 py-1.5 rounded hover:bg-base-200 transition-colors group"
            @click="runMetric(m)"
          >
            <div class="flex items-center gap-1.5">
              <component :is="m.icon" :size="12" class="text-base-content/50 group-hover:text-primary" />
              <span class="text-xs font-medium flex-1">{{ m.label }}</span>
              <ChevronRight :size="11" class="text-base-content/30 group-hover:text-base-content/60" />
            </div>
            <p class="text-[10px] text-base-content/45 ml-[18px]">{{ m.hint }}</p>
          </button>
        </div>
      </section>

      <!-- Structured Tables -->
      <section class="border-b border-base-300">
        <button
          class="w-full flex items-center gap-2 px-3 py-2.5 hover:bg-base-200 transition-colors text-left"
          @click="open.tables = !open.tables"
          :aria-expanded="open.tables"
        >
          <Table2 :size="13" class="text-primary flex-shrink-0" />
          <span class="text-xs font-semibold flex-1">Structured Tables</span>
          <ChevronDown :size="12" class="text-base-content/40 transition-transform" :class="open.tables ? 'rotate-180' : ''" />
        </button>
        <div v-show="open.tables" class="px-3 pb-3 space-y-1.5">
          <p class="text-[11px] text-base-content/50 leading-snug">
            Query a CSV/Excel table loaded into this collection in plain English.
          </p>
          <div class="flex gap-1.5">
            <button class="btn btn-outline btn-xs flex-1 gap-1" @click="sendToChat('List all structured tables in this collection and describe their schemas.')">
              <List :size="11" />
              List tables
            </button>
            <button class="btn btn-outline btn-xs flex-1 gap-1" @click="sendToChat('Show me a 10-row sample from each structured table in this collection.')">
              <Eye :size="11" />
              Preview
            </button>
          </div>
        </div>
      </section>

      <!-- Monte Carlo Simulation -->
      <section class="border-b border-base-300">
        <button
          class="w-full flex items-center gap-2 px-3 py-2.5 hover:bg-base-200 transition-colors text-left"
          @click="open.monte = !open.monte"
          :aria-expanded="open.monte"
        >
          <Dices :size="13" class="text-primary flex-shrink-0" />
          <span class="text-xs font-semibold flex-1">Monte Carlo Simulation</span>
          <ChevronDown :size="12" class="text-base-content/40 transition-transform" :class="open.monte ? 'rotate-180' : ''" />
        </button>
        <div v-show="open.monte" class="px-3 pb-3 space-y-2">
          <p class="text-[11px] text-base-content/50 leading-snug">
            Project portfolio outcomes from historical statistics.
          </p>

          <label class="block">
            <span class="text-[11px] text-base-content/60">Time horizon (years)</span>
            <input
              v-model.number="monte.years"
              type="number"
              min="1"
              max="50"
              class="input input-bordered input-xs w-full mt-0.5"
            />
          </label>

          <label class="block">
            <span class="text-[11px] text-base-content/60">Simulations</span>
            <input
              v-model.number="monte.runs"
              type="number"
              min="100"
              max="50000"
              step="100"
              class="input input-bordered input-xs w-full mt-0.5"
            />
          </label>

          <label class="block">
            <span class="text-[11px] text-base-content/60">Initial value ($)</span>
            <input
              v-model.number="monte.initial"
              type="number"
              min="0"
              step="1000"
              class="input input-bordered input-xs w-full mt-0.5"
            />
          </label>

          <label class="block">
            <span class="text-[11px] text-base-content/60">Annual contribution ($)</span>
            <input
              v-model.number="monte.contribution"
              type="number"
              min="0"
              step="500"
              class="input input-bordered input-xs w-full mt-0.5"
            />
          </label>

          <button
            class="btn btn-primary btn-xs w-full gap-1"
            @click="runMonteCarlo"
          >
            <Sparkles :size="11" />
            Run simulation
          </button>
        </div>
      </section>

      <!-- Notes -->
      <section class="border-b border-base-300">
        <button
          class="w-full flex items-center gap-2 px-3 py-2.5 hover:bg-base-200 transition-colors text-left"
          @click="open.notes = !open.notes"
          :aria-expanded="open.notes"
        >
          <StickyNote :size="13" class="text-primary flex-shrink-0" />
          <span class="text-xs font-semibold flex-1">Notes</span>
          <span v-if="notes.trim()" class="badge badge-xs badge-neutral">{{ noteCount }}</span>
          <ChevronDown :size="12" class="text-base-content/40 transition-transform" :class="open.notes ? 'rotate-180' : ''" />
        </button>
        <div v-show="open.notes" class="px-3 pb-3 space-y-1.5">
          <p class="text-[11px] text-base-content/50 leading-snug">
            Scratch pad scoped to this collection. Saved locally.
          </p>
          <textarea
            v-model="notes"
            class="textarea textarea-bordered textarea-xs w-full text-xs leading-snug min-h-[120px]"
            placeholder="Jot down findings, hypotheses, follow-ups…"
          ></textarea>
        </div>
      </section>

      <!-- Export -->
      <section>
        <button
          class="w-full flex items-center gap-2 px-3 py-2.5 hover:bg-base-200 transition-colors text-left"
          @click="open.export = !open.export"
          :aria-expanded="open.export"
        >
          <Download :size="13" class="text-primary flex-shrink-0" />
          <span class="text-xs font-semibold flex-1">Export</span>
          <ChevronDown :size="12" class="text-base-content/40 transition-transform" :class="open.export ? 'rotate-180' : ''" />
        </button>
        <div v-show="open.export" class="px-3 pb-3 space-y-1.5">
          <button class="btn btn-outline btn-xs w-full gap-1" @click="exportNotes">
            <FileText :size="11" />
            Notes (.md)
          </button>
          <button class="btn btn-outline btn-xs w-full gap-1" @click="sendToChat('Summarize the most important findings from our chat into a concise client-ready brief.')">
            <Sparkles :size="11" />
            Generate brief
          </button>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch } from 'vue'
import {
  X, ChevronDown, ChevronRight, TrendingUp, Table2, Dices, StickyNote,
  Download, Sparkles, FlaskConical, FileText, List, Eye,
  BarChart3, PieChart, Layers, Target, Activity, DollarSign
} from 'lucide-vue-next'
import { useCollectionStore } from '../stores/collectionStore'

const emit = defineEmits(['close', 'send-to-chat'])
const collectionStore = useCollectionStore()

const open = reactive({
  metrics: true,
  tables: false,
  monte: false,
  notes: false,
  export: false,
})

const portfolioMetrics = [
  { id: 'total_market_value', label: 'Total market value', icon: DollarSign, hint: 'Sum of current holdings' },
  { id: 'total_pnl',          label: 'Total P&L',          icon: TrendingUp, hint: 'Unrealized gains and losses' },
  { id: 'top_holdings',       label: 'Top holdings',       icon: BarChart3, hint: 'Largest positions by value' },
  { id: 'concentration',      label: 'Concentration',      icon: Target,    hint: 'Share held by top N positions' },
  { id: 'breakdown_by_sector',     label: 'By sector',     icon: PieChart,  hint: 'Allocation across sectors' },
  { id: 'breakdown_by_asset_class',label: 'By asset class',icon: Layers,    hint: 'Allocation across asset classes' },
  { id: 'weighted_return',    label: 'Weighted return',    icon: Activity,  hint: 'Value-weighted portfolio return' },
]

const sendToChat = (prompt) => {
  emit('send-to-chat', prompt)
}

const runMetric = (m) => {
  sendToChat(
    `Run the canned portfolio metric "${m.id}" on the structured table in this collection and explain the result in plain English.`
  )
}

// Monte Carlo
const monte = reactive({
  years: 20,
  runs: 1000,
  initial: 100000,
  contribution: 12000,
})

const runMonteCarlo = () => {
  const prompt =
    `Run a Monte Carlo simulation on the portfolio in this collection with these inputs:\n` +
    `- Time horizon: ${monte.years} years\n` +
    `- Simulations: ${monte.runs}\n` +
    `- Initial value: $${monte.initial.toLocaleString()}\n` +
    `- Annual contribution: $${monte.contribution.toLocaleString()}\n\n` +
    `Use historical mean/volatility from the structured table when available. ` +
    `Report the median, 10th, and 90th percentile ending values and the probability of beating the initial value.`
  sendToChat(prompt)
}

// Notes (per-collection localStorage)
const notesKey = computed(() => `analysis_notes:${collectionStore.currentCollectionId || 'default'}`)
const notes = ref(localStorage.getItem(notesKey.value) || '')
const noteCount = computed(() => notes.value.trim().split(/\s+/).filter(Boolean).length)

watch(notesKey, (key) => {
  notes.value = localStorage.getItem(key) || ''
})
watch(notes, (v) => {
  localStorage.setItem(notesKey.value, v)
})

const exportNotes = () => {
  const name = collectionStore.currentCollection?.name || 'collection'
  const blob = new Blob([notes.value || ''], { type: 'text/markdown' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${name.replace(/[^a-z0-9-_]+/gi, '_')}-notes.md`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}
</script>
