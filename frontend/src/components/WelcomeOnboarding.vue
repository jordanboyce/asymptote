<template>
  <div
    v-if="show"
    class="fixed inset-0 z-[300] bg-base-200 flex items-center justify-center p-6 overflow-y-auto"
    role="dialog"
    aria-modal="true"
    aria-labelledby="welcome-title"
  >
    <div class="max-w-md w-full my-auto">
      <!-- Logo + welcome -->
      <div class="text-center mb-8">
        <img src="/icon_black.svg" alt="" class="w-14 h-14 mx-auto mb-5 opacity-90" />
        <h1 id="welcome-title" class="text-2xl font-semibold tracking-tight">
          Welcome to Asymptote
        </h1>
        <p class="text-sm text-base-content/60 mt-2 max-w-sm mx-auto leading-relaxed">
          Paste your Ollama Cloud API key to get started. Free tier — no credit card required.
        </p>
      </div>

      <!-- Form -->
      <div class="space-y-3">
        <div class="form-control w-full">
          <label class="label py-1" for="welcome-api-key">
            <span class="label-text text-sm">Ollama Cloud API Key</span>
          </label>
          <input
            id="welcome-api-key"
            v-model="apiKey"
            type="password"
            autocomplete="off"
            spellcheck="false"
            placeholder="Paste your key"
            class="input input-bordered w-full font-mono text-sm"
            :disabled="saving"
            @keyup.enter="handleSave"
            ref="keyInputRef"
          />
        </div>

        <!-- Validation error -->
        <div v-if="error" class="alert alert-error text-sm py-2" role="alert">
          <span>{{ error }}</span>
        </div>

        <button
          class="btn btn-primary w-full"
          :disabled="!apiKey.trim() || saving"
          @click="handleSave"
        >
          <span v-if="saving" class="loading loading-spinner loading-sm"></span>
          {{ saving ? 'Verifying…' : 'Get Started' }}
        </button>

        <div class="text-center text-xs text-base-content/55 pt-3 space-x-2">
          <a
            href="https://ollama.com/settings/keys"
            target="_blank"
            rel="noopener noreferrer"
            class="link link-hover"
          >
            Get your key →
          </a>
          <span class="text-base-content/30" aria-hidden="true">·</span>
          <button class="link link-hover" @click="handleSkip">
            Use a different provider
          </button>
        </div>
      </div>

      <!-- Trust copy footer -->
      <div class="mt-10 text-center text-[11px] text-base-content/45 leading-relaxed">
        Your documents stay on this device.
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, nextTick, watch } from 'vue'
import axios from 'axios'
import { upsertProviderConfig, setActiveProviderLS } from '../utils/aiProviders.js'

const props = defineProps({
  show: { type: Boolean, default: false },
})

const emit = defineEmits(['complete', 'skip'])

const apiKey = ref('')
const saving = ref(false)
const error = ref('')
const keyInputRef = ref(null)

// Focus the input whenever the onboarding becomes visible so the advisor can
// paste immediately without tabbing in.
watch(
  () => props.show,
  (isShown) => {
    if (isShown) {
      nextTick(() => keyInputRef.value?.focus())
    }
  },
)

const handleSave = async () => {
  const key = apiKey.value.trim()
  if (!key || saving.value) return

  saving.value = true
  error.value = ''
  try {
    // Server-side: validates the key and stores it in the agent_api_keys table
    // so the MCP chat path can use it without per-request headers.
    await axios.post('/api/agent/config', null, {
      params: { provider: 'ollama_cloud', api_key: key },
    })

    // Client-side: persist for chat requests (X-AI-Key header on each call)
    // and set as the default selected provider so the chat UI picks it up.
    upsertProviderConfig('ollama_cloud', {
      apiKey: key,
      model: 'gpt-oss:120b',
    })
    setActiveProviderLS('ollama_cloud')

    emit('complete')
  } catch (err) {
    const detail = err?.response?.data?.detail
    error.value =
      detail ||
      'Could not verify that key. Double-check you copied it correctly from ollama.com/settings/keys.'
  } finally {
    saving.value = false
  }
}

const handleSkip = () => {
  emit('skip')
}
</script>
