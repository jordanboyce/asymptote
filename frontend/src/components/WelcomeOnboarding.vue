<template>
  <div
    v-if="show"
    class="fixed inset-0 z-[300] bg-base-200 flex items-center justify-center p-6 overflow-y-auto"
    role="dialog"
    aria-modal="true"
    aria-labelledby="welcome-title"
  >
    <div class="max-w-md w-full my-auto">
      <!-- Step indicator -->
      <div
        v-if="stage !== 'welcome'"
        class="flex items-center justify-center gap-1.5 mb-6"
        aria-label="Setup progress"
      >
        <span
          v-for="(s, i) in STAGES"
          :key="s"
          class="h-1 rounded-full transition-all"
          :class="[
            stageIndex >= i ? 'bg-primary' : 'bg-base-content/20',
            stageIndex === i ? 'w-8' : 'w-4',
          ]"
          aria-hidden="true"
        ></span>
      </div>

      <!-- Logo + heading (welcome stage only) -->
      <div v-if="stage === 'welcome'" class="text-center mb-8">
        <img :src="logoSrc" alt="" class="w-14 h-14 mx-auto mb-5 opacity-90" />
        <h1 id="welcome-title" class="text-2xl font-semibold tracking-tight">
          Welcome to Finn
        </h1>
        <p class="text-sm text-base-content/60 mt-3 max-w-sm mx-auto leading-relaxed">
          {{ hasManagedProvider ? "Let's get you set up in two short steps." : "Let's get you set up in three short steps." }}
        </p>
      </div>

      <!-- ── Stage 1: welcome / privacy ──────────────────────────────────── -->
      <div v-if="stage === 'welcome'" class="space-y-5">
        <div class="bg-base-100 rounded-lg p-5 space-y-4 text-sm leading-relaxed">
          <p class="font-medium text-base">How Finn handles your data.</p>
          <ul class="space-y-2 text-base-content/75">
            <li class="flex gap-2">
              <span class="text-primary font-semibold">·</span>
              <span>Brokerage exports, transcripts, and notes are uploaded so Finn can index them — originals can be discarded once chunks and embeddings are stored.</span>
            </li>
            <li class="flex gap-2">
              <span class="text-primary font-semibold">·</span>
              <span>Names, account numbers, and other identifiers are redacted before any AI call leaves Finn.</span>
            </li>
            <li class="flex gap-2">
              <span class="text-primary font-semibold">·</span>
              <span>Your AI provider only ever sees redacted text — never the raw client data.</span>
            </li>
          </ul>
        </div>
        <div
          v-if="hasManagedProvider"
          class="rounded-lg border border-info/30 bg-info/5 px-4 py-3 text-sm flex items-start gap-2"
        >
          <span class="text-info text-base leading-none mt-0.5" aria-hidden="true">●</span>
          <div class="flex-1">
            <div class="font-medium">AI provider already set up</div>
            <div class="text-xs text-base-content/65 mt-0.5">
              Finn has provisioned Ollama Cloud for you — no API key needed. You can switch providers later in Settings.
            </div>
          </div>
        </div>
        <button class="btn btn-primary w-full" @click="advance(hasManagedProvider ? 'collection' : 'provider')">
          Continue
        </button>
      </div>

      <!-- ── Stage 2: provider + key ─────────────────────────────────────── -->
      <div v-else-if="stage === 'provider'" class="space-y-5">
        <div class="text-center">
          <h2 class="text-xl font-semibold">Choose an AI provider</h2>
          <p class="text-sm text-base-content/60 mt-2">
            We recommend Anthropic Claude — deepest reasoning and the richest tool-use. You can change this later in Settings.
          </p>
        </div>

        <!-- Provider radio cards -->
        <div role="radiogroup" aria-label="AI provider" class="space-y-2">
          <label
            v-for="p in PROVIDER_OPTIONS"
            :key="p.id"
            class="flex items-start gap-3 p-3 border rounded-lg cursor-pointer transition"
            :class="providerId === p.id ? 'border-primary bg-primary/5' : 'border-base-300 hover:border-base-content/30'"
          >
            <input
              type="radio"
              :value="p.id"
              v-model="providerId"
              class="radio radio-primary radio-sm mt-0.5"
              :disabled="validating"
            />
            <div class="flex-1 text-sm">
              <div class="flex items-center gap-2">
                <span class="font-medium">{{ p.name }}</span>
                <span
                  v-if="p.recommended"
                  class="badge badge-primary badge-sm font-medium"
                >Recommended</span>
              </div>
              <div class="text-xs text-base-content/55 mt-0.5">{{ p.tagline }}</div>
            </div>
          </label>
        </div>

        <!-- Key input -->
        <div class="form-control w-full">
          <label class="label py-1" for="welcome-api-key">
            <span class="label-text text-sm">API key</span>
            <a
              v-if="activeProvider?.keyLink"
              :href="activeProvider.keyLink"
              target="_blank"
              rel="noopener noreferrer"
              class="label-text-alt link link-hover text-xs"
            >
              Get a key →
            </a>
          </label>
          <input
            id="welcome-api-key"
            v-model="apiKey"
            type="password"
            autocomplete="off"
            spellcheck="false"
            :placeholder="activeProvider?.keyPlaceholder || 'Paste your key'"
            class="input input-bordered w-full font-mono text-sm"
            :disabled="validating"
            @keyup.enter="handleProviderSubmit"
            ref="keyInputRef"
          />
        </div>

        <!-- Typed error -->
        <div v-if="errorCode" class="alert alert-error text-sm py-2" role="alert">
          <span>{{ errorMessage }}</span>
        </div>

        <div class="flex gap-2">
          <button class="btn btn-ghost flex-1" @click="goBack" :disabled="validating">
            Back
          </button>
          <button
            class="btn btn-primary flex-1"
            :disabled="!apiKey.trim() || validating"
            @click="handleProviderSubmit"
          >
            <span v-if="validating" class="loading loading-spinner loading-sm"></span>
            {{ validating ? 'Verifying…' : 'Continue' }}
          </button>
        </div>

        <div class="text-center text-xs text-base-content/55">
          <button class="link link-hover" @click="handleSkip" :disabled="validating">
            Skip — set up later
          </button>
        </div>
      </div>

      <!-- ── Stage 3: first collection ───────────────────────────────────── -->
      <div v-else-if="stage === 'collection'" class="space-y-5">
        <div class="text-center">
          <h2 class="text-xl font-semibold">Create your first collection</h2>
          <p class="text-sm text-base-content/60 mt-2">
            One collection per client household — usually the household name.
          </p>
        </div>

        <div class="form-control w-full">
          <label class="label py-1" for="welcome-collection-name">
            <span class="label-text text-sm">Client / household name</span>
          </label>
          <input
            id="welcome-collection-name"
            v-model="collectionName"
            type="text"
            autocomplete="off"
            placeholder="e.g. Henderson Household"
            class="input input-bordered w-full text-sm"
            :disabled="creating"
            @keyup.enter="handleCollectionSubmit"
            ref="collectionInputRef"
          />
        </div>

        <div v-if="collectionError" class="alert alert-error text-sm py-2" role="alert">
          <span>{{ collectionError }}</span>
        </div>

        <div class="flex gap-2">
          <button class="btn btn-ghost flex-1" @click="goBack" :disabled="creating">
            Back
          </button>
          <button
            class="btn btn-primary flex-1"
            :disabled="!collectionName.trim() || creating"
            @click="handleCollectionSubmit"
          >
            <span v-if="creating" class="loading loading-spinner loading-sm"></span>
            {{ creating ? 'Creating…' : 'Create' }}
          </button>
        </div>

        <div class="text-center text-xs text-base-content/55">
          <button class="link link-hover" @click="advance('done')" :disabled="creating">
            I'll do this later
          </button>
        </div>
      </div>

      <!-- ── Stage 4: done ───────────────────────────────────────────────── -->
      <div v-else-if="stage === 'done'" class="space-y-5 text-center">
        <div class="w-14 h-14 rounded-full bg-success/15 text-success mx-auto flex items-center justify-center">
          <svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M5 12l5 5L20 7" />
          </svg>
        </div>
        <div>
          <h2 class="text-xl font-semibold">You're all set</h2>
          <p class="text-sm text-base-content/60 mt-2">
            <span v-if="createdCollection">
              Upload a brokerage export to <span class="font-medium">{{ createdCollection.name }}</span>, then ask Finn about the portfolio.
            </span>
            <span v-else>
              Create a collection from the Collections tab and upload your first brokerage export.
            </span>
          </p>
        </div>
        <button class="btn btn-primary w-full" @click="handleFinish">
          Open chat
        </button>
      </div>

      <!-- Trust copy footer -->
      <div class="mt-10 text-center text-[11px] text-base-content/45 leading-relaxed">
        Documents are uploaded for indexing only; originals can be discarded after indexing. PII is redacted before any AI call.
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, watch } from 'vue'
import { upsertProviderConfig, setActiveProviderLS, isManagedProvider, getConfiguredProviderIds } from '../utils/aiProviders.js'
import { validateProviderKey, validateErrorMessage } from '../utils/validateKey.js'
import { useCollectionStore } from '../stores/collectionStore.js'
import { useThemeIcon } from '../composables/useThemeIcon.js'

const { src: logoSrc } = useThemeIcon('/icon_light.svg', '/icon_dark.svg')

const props = defineProps({
  show: { type: Boolean, default: false },
})

const emit = defineEmits(['complete', 'skip'])

const collectionStore = useCollectionStore()

// True when the backend has provisioned a managed Ollama Cloud provider for
// the advisor (FALLBACK_API_KEY set on the server). In that case we skip the
// provider-key stage — the advisor doesn't need to paste anything to chat.
const hasManagedProvider = computed(() =>
  getConfiguredProviderIds().some(id => isManagedProvider(id)),
)

// Step machine. The provider stage is conditionally elided when the server
// has bootstrapped a managed provider, so the displayed STAGES are computed.
const STAGES = computed(() =>
  hasManagedProvider.value
    ? ['welcome', 'collection', 'done']
    : ['welcome', 'provider', 'collection', 'done'],
)
const stage = ref('welcome')
const stageIndex = computed(() => STAGES.value.indexOf(stage.value))

// Provider stage state
const PROVIDER_OPTIONS = [
  {
    id: 'anthropic',
    name: 'Anthropic Claude',
    tagline: 'Best reasoning, biggest context, richest tool-use — the most capable experience in Finn. Bring your own key from console.anthropic.com.',
    keyPlaceholder: 'sk-ant-...',
    keyLink: 'https://console.anthropic.com/settings/keys',
    defaultModel: 'claude-sonnet-4-6',
    recommended: true,
  },
  {
    id: 'openai',
    name: 'OpenAI',
    tagline: 'GPT-4o and o-series. Bring your own key from platform.openai.com.',
    keyPlaceholder: 'sk-...',
    keyLink: 'https://platform.openai.com/api-keys',
    defaultModel: 'gpt-4o',
  },
  {
    id: 'ollama_cloud',
    name: 'Ollama Cloud',
    tagline: 'Free tier — no credit card. Open-weight models; defaults to Gemma 4 31B.',
    keyPlaceholder: 'your Ollama API key',
    keyLink: 'https://ollama.com/settings/keys',
    defaultModel: 'gemma4:31b',
  },
]
const providerId = ref('anthropic')
const apiKey = ref('')
const validating = ref(false)
const errorCode = ref('') // 'invalid_key' | 'network_error' | 'unsupported_provider' | ''

const activeProvider = computed(() =>
  PROVIDER_OPTIONS.find(p => p.id === providerId.value),
)

const errorMessage = computed(() => validateErrorMessage(errorCode.value))

// Collection stage state
const collectionName = ref('')
const creating = ref(false)
const collectionError = ref('')
const createdCollection = ref(null)

// Refs for autofocus
const keyInputRef = ref(null)
const collectionInputRef = ref(null)

// Reset to welcome stage every time the dialog opens (e.g. via Settings reset).
watch(
  () => props.show,
  (isShown) => {
    if (isShown) {
      stage.value = 'welcome'
      apiKey.value = ''
      collectionName.value = ''
      errorCode.value = ''
      collectionError.value = ''
      createdCollection.value = null
    }
  },
)

// Autofocus the right input as we advance.
watch(stage, async (s) => {
  await nextTick()
  if (s === 'provider') keyInputRef.value?.focus()
  if (s === 'collection') collectionInputRef.value?.focus()
})

function advance(next) {
  errorCode.value = ''
  collectionError.value = ''
  stage.value = next
}

function goBack() {
  const i = stageIndex.value
  if (i <= 0) return
  stage.value = STAGES.value[i - 1]
}

async function handleProviderSubmit() {
  const key = apiKey.value.trim()
  if (!key || validating.value) return
  validating.value = true
  errorCode.value = ''

  const def = activeProvider.value
  const result = await validateProviderKey({
    provider: def.id,
    apiKey: key,
    model: def.defaultModel,
  })
  validating.value = false

  if (!result.valid) {
    errorCode.value = result.code || 'invalid_key'
    return
  }

  // Persist locally so chat requests pick it up. We deliberately don't call
  // /api/agent/config here — that path is for the MCP-only key store. The
  // primary chat surface reads from localStorage via buildProviderHeaders.
  upsertProviderConfig(def.id, {
    apiKey: key,
    model: def.defaultModel,
  })
  setActiveProviderLS(def.id)
  advance('collection')
}

async function handleCollectionSubmit() {
  const name = collectionName.value.trim()
  if (!name || creating.value) return
  creating.value = true
  collectionError.value = ''
  try {
    const created = await collectionStore.createCollection({ name })
    createdCollection.value = created
    if (created?.id) {
      collectionStore.setCurrentCollection(created.id)
    }
    advance('done')
  } catch (err) {
    collectionError.value =
      err?.response?.data?.detail || 'Could not create the collection. Please try again.'
  } finally {
    creating.value = false
  }
}

function handleFinish() {
  // Mark onboarding complete (Pass 1: localStorage; Pass 2 will move to app DB
  // alongside other key/value flags introduced for §12.3).
  try {
    localStorage.setItem('finn_onboarding_completed_at', new Date().toISOString())
  } catch { /* localStorage disabled — non-fatal */ }
  emit('complete', {
    providerConfigured: true,
    collectionId: createdCollection.value?.id || null,
  })
}

function handleSkip() {
  // Stamp the timestamp even on skip so we don't re-show the takeover. The
  // App-level banner reads `getConfiguredProviderIds().length` to decide
  // whether to nudge the advisor back into Settings.
  try {
    localStorage.setItem('finn_onboarding_completed_at', new Date().toISOString())
  } catch { /* localStorage disabled — non-fatal */ }
  emit('skip')
}
</script>
