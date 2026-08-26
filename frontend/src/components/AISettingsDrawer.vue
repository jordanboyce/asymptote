<template>
  <div
    v-if="open"
    class="fixed inset-0 z-[200]"
    @click.self="close"
    role="dialog"
    aria-modal="true"
    :aria-labelledby="titleId"
  >
    <!-- Backdrop -->
    <div class="absolute inset-0 bg-base-content/20" @click="close" aria-hidden="true"></div>

    <!-- Drawer Panel -->
    <div class="absolute right-0 top-0 h-full w-80 max-w-[85vw] bg-base-100 shadow-2xl flex flex-col">
      <!-- Header -->
      <div class="flex items-center justify-between p-4 border-b border-base-300">
        <h3 :id="titleId" class="text-sm font-bold">{{ title }}</h3>
        <button
          class="btn btn-ghost btn-sm btn-circle"
          @click="close"
          :aria-label="`Close ${title.toLowerCase()}`"
        >
          <X :size="18" />
        </button>
      </div>

      <!-- Content -->
      <div class="flex-1 overflow-y-auto p-4 space-y-5">

        <!-- Section 1: Provider(s) -->
        <div v-if="hasAnyProvider" class="space-y-2">
          <span class="text-xs font-semibold text-base-content/60 uppercase tracking-wider">
            {{ mode === 'multi' ? 'Providers' : 'AI Provider' }}
          </span>

          <!-- Single mode: radio list -->
          <div v-if="mode === 'single'" class="space-y-1.5">
            <!-- "Follow the app-wide default" option (provider = '') -->
            <label
              v-if="allowGlobal"
              class="flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer border transition-colors text-sm"
              :class="provider === '' ? 'bg-primary/20 border-primary font-medium' : 'bg-base-200/60 border-base-300 hover:bg-base-100'"
            >
              <input
                type="radio"
                class="radio radio-xs radio-primary"
                :checked="provider === ''"
                @change="provider = ''"
              />
              <span class="flex-1">Global<span v-if="globalProviderId" class="text-base-content/50"> ({{ displayName(globalProviderId) }})</span></span>
              <span class="badge badge-xs badge-primary badge-outline">default</span>
            </label>

            <label
              v-for="pid in configuredProviders"
              :key="pid"
              class="flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer border transition-colors text-sm"
              :class="provider === pid ? 'bg-primary/20 border-primary font-medium' : 'bg-base-200/60 border-base-300 hover:bg-base-100'"
            >
              <input
                type="radio"
                class="radio radio-xs radio-primary"
                :checked="provider === pid"
                @change="provider = pid"
              />
              <span class="flex-1">{{ displayName(pid) }}</span>
              <span v-if="allowGlobal && provider === pid" class="badge badge-xs badge-warning">override</span>
              <span class="badge badge-xs badge-outline">{{ isLocalProvider(pid) ? 'local' : 'cloud' }}</span>
            </label>

            <p v-if="allowGlobal && provider" class="text-xs text-warning/80 px-1">
              Overriding the global default<template v-if="globalProviderId"> ({{ displayName(globalProviderId) }})</template> for this surface.
            </p>
          </div>

          <!-- Multi mode: checkbox list -->
          <div v-else class="space-y-1.5">
            <label
              v-for="pid in configuredProviders"
              :key="pid"
              class="flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer border transition-colors text-sm"
              :class="selectedProviders.includes(pid) ? 'bg-primary/20 border-primary font-medium' : 'bg-base-200/60 border-base-300 hover:bg-base-100'"
            >
              <input
                type="checkbox"
                class="checkbox checkbox-xs checkbox-primary"
                :checked="selectedProviders.includes(pid)"
                @change="toggleProvider(pid)"
              />
              <span class="flex-1">{{ displayName(pid) }}</span>
              <span class="badge badge-xs badge-outline" :class="isLocalProvider(pid) ? 'badge-info' : 'badge-warning'">{{ isLocalProvider(pid) ? 'local' : 'cloud' }}</span>
            </label>
          </div>

          <!-- Single mode: compact model select for the effective provider -->
          <div v-if="mode === 'single' && effectiveProvider && hasModelOverrides" class="flex items-center justify-between gap-2 px-1">
            <span class="text-xs text-base-content/50">Model</span>
            <select
              class="select select-xs select-bordered max-w-[180px]"
              :value="overrideFor(effectiveProvider)"
              :aria-label="`Model for ${displayName(effectiveProvider)}`"
              @change="setOverride(effectiveProvider, $event.target.value)"
            >
              <option value="">Provider default</option>
              <option v-if="missingOverride" :value="missingOverride">{{ missingOverride }}</option>
              <optgroup v-if="recommendedModels.length > 0" label="Recommended">
                <option v-for="m in recommendedModels" :key="m.id" :value="m.id">{{ m.label }}</option>
              </optgroup>
              <optgroup v-if="otherModels.length > 0" label="All models">
                <option v-for="m in otherModels" :key="m.id" :value="m.id">{{ m.label }}</option>
              </optgroup>
            </select>
          </div>
          <p v-if="mode === 'single' && modelsLoading" class="text-xs text-base-content/40 px-1">Loading models…</p>

          <!-- Multi mode: per-provider model overrides (curated list, as before) -->
          <template v-if="mode === 'multi' && hasModelOverrides">
            <div v-for="pid in selectedProviders" :key="'model-' + pid">
              <div v-if="curatedModels(pid).length > 1" class="flex items-center justify-between mt-1 px-1">
                <span class="text-xs text-base-content/50">{{ displayName(pid) }} model</span>
                <select
                  class="select select-xs select-bordered max-w-[160px]"
                  :value="overrideFor(pid)"
                  :aria-label="`Model for ${displayName(pid)}`"
                  @change="setOverride(pid, $event.target.value)"
                >
                  <option value="">Default ({{ defaultModelLabel(pid) }})</option>
                  <option v-for="m in curatedModels(pid)" :key="m.id" :value="m.id">{{ m.label }}</option>
                </select>
              </div>
            </div>
          </template>

          <p v-if="mode === 'multi' && selectedExternalCount > 0" class="text-xs text-warning/80">Cloud providers will receive your query.</p>
        </div>

        <!-- No-provider nudge -->
        <div v-else class="flex items-center gap-3 rounded-lg border border-info/30 bg-info/5 px-3 py-2.5">
          <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" class="stroke-info shrink-0 w-4 h-4" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
          </svg>
          <p class="text-sm flex-1">Add an AI provider in Settings to enable AI features.</p>
          <button class="btn btn-xs btn-primary flex-shrink-0" @click="$emit('switch-tab', 'settings')">Settings</button>
        </div>

        <!-- Section 2: Answer depth (presets) -->
        <div class="space-y-2">
          <span class="text-xs font-semibold text-base-content/60 uppercase tracking-wider">Answer depth</span>
          <div class="join w-full" role="group" aria-label="Answer depth preset">
            <button
              v-for="p in PRESETS"
              :key="p.key"
              type="button"
              class="btn btn-xs join-item flex-1"
              :class="activePreset === p.key ? 'btn-primary' : 'btn-ghost border border-base-300'"
              :aria-pressed="activePreset === p.key"
              @click="applyPreset(p.key)"
            >{{ p.label }}</button>
          </div>
          <p v-if="!activePreset" class="text-xs text-base-content/40">Custom — adjust in Advanced below.</p>
        </div>

        <!-- Section 3: Advanced (raw knobs) -->
        <details class="collapse collapse-arrow border border-base-300 bg-base-200/40 rounded-lg">
          <summary class="collapse-title min-h-0 py-2.5 px-3 text-xs font-semibold text-base-content/60 uppercase tracking-wider">
            Advanced
          </summary>
          <div class="collapse-content space-y-3 px-3">

            <label class="flex items-center justify-between">
              <span class="text-sm">{{ topKLabel }}</span>
              <input
                v-model.number="topK"
                type="number"
                :min="topKMin"
                :max="topKMax"
                class="input input-bordered input-xs w-16 text-center"
                :aria-label="topKLabel"
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

            <label v-if="hasWeight && searchMode === 'hybrid'" class="space-y-1 block">
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

            <label class="flex items-center justify-between cursor-pointer select-none">
              <span class="text-sm">Rerank</span>
              <input type="checkbox" class="toggle toggle-sm toggle-primary" v-model="rerank" />
            </label>

            <label v-if="hasSynthesize" class="flex items-center justify-between cursor-pointer select-none">
              <span class="text-sm">Synthesize</span>
              <input type="checkbox" class="toggle toggle-sm toggle-secondary" v-model="synthesize" />
            </label>

            <!-- Surface-specific extras (e.g. Chat's collection scope) -->
            <slot name="advanced" />
          </div>
        </details>

      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { X } from 'lucide-vue-next'
import {
  getProviderDisplayName,
  getProviderModels,
  getProviderConfig,
  isLocalProvider,
  fetchProviderModels,
} from '../utils/aiProviders.js'

const props = defineProps({
  /** 'single' (radio, one provider — Chat) or 'multi' (checkboxes — Search). */
  mode: { type: String, default: 'single' },
  title: { type: String, default: 'AI Settings' },
  /** Configured provider ids to offer. */
  configuredProviders: { type: Array, default: () => [] },
  topKLabel: { type: String, default: 'Top K results' },
  topKMin: { type: Number, default: 1 },
  topKMax: { type: Number, default: 20 },
  /**
   * Single mode only: offer a "Global (provider)" option that clears the
   * surface's override (provider v-model = ''). globalProviderId is what the
   * shared resolution chain currently yields for this surface without an
   * override, shown so users know what "Global" means right now.
   */
  allowGlobal: { type: Boolean, default: false },
  globalProviderId: { type: String, default: '' },
})

const emit = defineEmits(['switch-tab'])

// v-model bindings. semanticWeight / synthesize / modelOverrides are optional:
// when the parent doesn't bind them the corresponding control is hidden.
const open = defineModel('open', { type: Boolean, default: false })
const provider = defineModel('provider', { type: String, default: '' })
const selectedProviders = defineModel('selectedProviders', { type: Array, default: () => [] })
const topK = defineModel('topK', { type: Number, default: 10 })
const searchMode = defineModel('searchMode', { type: String, default: 'hybrid' })
const semanticWeight = defineModel('semanticWeight', { type: Number, default: undefined })
const rerank = defineModel('rerank', { type: Boolean, default: false })
const synthesize = defineModel('synthesize', { type: Boolean, default: undefined })
const modelOverrides = defineModel('modelOverrides', { type: Object, default: undefined })

const titleId = computed(() => `ai-settings-title-${props.mode}`)
const hasAnyProvider = computed(() => props.configuredProviders.length > 0)
const hasWeight = computed(() => typeof semanticWeight.value === 'number')
const hasSynthesize = computed(() => typeof synthesize.value === 'boolean')
const hasModelOverrides = computed(() => modelOverrides.value != null)

const close = () => { open.value = false }

const displayName = getProviderDisplayName

// Single mode: the provider actually in effect — the explicit selection, or
// the globally resolved one while following "Global".
const effectiveProvider = computed(() =>
  provider.value || (props.allowGlobal ? props.globalProviderId : '')
)

// ── Provider selection ─────────────────────────────────────────────────────

const toggleProvider = (pid) => {
  const next = [...selectedProviders.value]
  const idx = next.indexOf(pid)
  if (idx > -1) next.splice(idx, 1)
  else next.push(pid)
  selectedProviders.value = next
}

const selectedExternalCount = computed(() =>
  selectedProviders.value.filter(p => !isLocalProvider(p)).length
)

// ── Model overrides ────────────────────────────────────────────────────────
// Stored as { providerId: modelId | '' }; '' / absent means provider default.

const overrideFor = (pid) => (modelOverrides.value && modelOverrides.value[pid]) || ''

const setOverride = (pid, val) => {
  modelOverrides.value = { ...(modelOverrides.value || {}), [pid]: val }
}

// Multi mode keeps the curated recommendation list (as Search did before).
const curatedModels = (pid) => getProviderModels(pid)

const defaultModelLabel = (pid) => {
  const cfg = getProviderConfig(pid)
  if (!cfg?.model) return ''
  const found = getProviderModels(pid).find(m => m.id === cfg.model)
  return found ? found.label : cfg.model
}

// Single mode fetches the live model list (recommended first) lazily on open.
const liveModels = ref([])
const modelsLoading = ref(false)
let modelFetchSeq = 0

const loadModels = async () => {
  if (props.mode !== 'single' || !effectiveProvider.value || !hasModelOverrides.value) return
  const seq = ++modelFetchSeq
  modelsLoading.value = true
  try {
    const { models } = await fetchProviderModels(effectiveProvider.value)
    if (seq === modelFetchSeq) liveModels.value = models || []
  } finally {
    if (seq === modelFetchSeq) modelsLoading.value = false
  }
}

watch([open, effectiveProvider], ([isOpen]) => {
  if (isOpen) loadModels()
})

const recommendedModels = computed(() => liveModels.value.filter(m => m.recommended))
const otherModels = computed(() => liveModels.value.filter(m => !m.recommended))

// A previously saved override that the live list doesn't contain — keep it
// selectable so the stored value still displays.
const missingOverride = computed(() => {
  const cur = overrideFor(effectiveProvider.value)
  if (!cur) return null
  return liveModels.value.some(m => m.id === cur) ? null : cur
})

// ── Answer depth presets ───────────────────────────────────────────────────
// Selecting a preset writes the underlying values through the same v-models
// (and therefore the same persistence) as the Advanced knobs.

const PRESETS = [
  { key: 'fast',     label: 'Fast',     values: { topK: 5,  mode: 'semantic', semanticWeight: 0.7, rerank: false } },
  { key: 'balanced', label: 'Balanced', values: { topK: 10, mode: 'hybrid',   semanticWeight: 0.7, rerank: true } },
  { key: 'thorough', label: 'Thorough', values: { topK: 20, mode: 'hybrid',   semanticWeight: 0.6, rerank: true } },
]

const activePreset = computed(() => {
  for (const p of PRESETS) {
    const v = p.values
    if (
      topK.value === v.topK &&
      searchMode.value === v.mode &&
      rerank.value === v.rerank &&
      (!hasWeight.value || Math.abs(semanticWeight.value - v.semanticWeight) < 0.001)
    ) return p.key
  }
  return null
})

const applyPreset = (key) => {
  const v = PRESETS.find(p => p.key === key).values
  topK.value = v.topK
  searchMode.value = v.mode
  rerank.value = v.rerank
  if (hasWeight.value) semanticWeight.value = v.semanticWeight
}
</script>
