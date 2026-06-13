/**
 * Shared AI provider utilities.
 * Used by SettingsTab, ChatTab, and SearchTab to read/write provider configs
 * and build API request headers in a consistent way.
 */

export const PROVIDER_DEFS = [
  {
    id: 'anthropic',
    name: 'Anthropic',
    type: 'cloud',
    badge: 'cloud',
    keyPlaceholder: 'sk-ant-...',
    keyLink: 'https://console.anthropic.com/settings/keys',
    defaultModel: 'claude-sonnet-4-6',
    models: [
      { id: 'claude-opus-4-7', label: 'Claude Opus 4.7 (best quality)' },
      { id: 'claude-sonnet-4-6', label: 'Claude Sonnet 4.6 (quality)' },
      { id: 'claude-haiku-4-5-20251001', label: 'Claude Haiku 4.5 (fast)' },
      { id: 'claude-sonnet-4-5-20250929', label: 'Claude Sonnet 4.5' },
      { id: 'claude-3-5-sonnet-20241022', label: 'Claude 3.5 Sonnet' },
      { id: 'claude-3-5-haiku-20241022', label: 'Claude 3.5 Haiku' },
      { id: 'claude-3-opus-20240229', label: 'Claude 3 Opus' },
    ],
  },
  {
    id: 'openai',
    name: 'OpenAI',
    type: 'cloud',
    badge: 'cloud',
    keyPlaceholder: 'sk-...',
    keyLink: 'https://platform.openai.com/api-keys',
    models: [
      { id: 'gpt-4o', label: 'GPT-4o (quality)' },
      { id: 'gpt-4o-mini', label: 'GPT-4o Mini (fast)' },
      { id: 'o3', label: 'o3' },
      { id: 'o3-mini', label: 'o3-mini' },
      { id: 'o1', label: 'o1' },
      { id: 'gpt-4-turbo', label: 'GPT-4 Turbo' },
      { id: 'gpt-4', label: 'GPT-4' },
      { id: 'gpt-3.5-turbo', label: 'GPT-3.5 Turbo' },
    ],
  },
  {
    id: 'grok',
    name: 'Grok (xAI)',
    type: 'cloud',
    badge: 'cloud',
    keyPlaceholder: 'xai-...',
    keyLink: 'https://console.x.ai/',
    models: [
      { id: 'grok-3', label: 'Grok-3 (quality)' },
      { id: 'grok-3-mini', label: 'Grok-3 Mini (fast)' },
      { id: 'grok-2-1212', label: 'Grok-2' },
      { id: 'grok-2-vision-1212', label: 'Grok-2 Vision' },
    ],
  },
  {
    id: 'google',
    name: 'Google Gemini',
    type: 'cloud',
    badge: 'cloud',
    keyPlaceholder: 'AIza...',
    keyLink: 'https://aistudio.google.com/app/apikey',
    models: [
      { id: 'gemini-2.5-pro-preview-03-25', label: 'Gemini 2.5 Pro (quality)' },
      { id: 'gemini-2.0-flash', label: 'Gemini 2.0 Flash (fast)' },
      { id: 'gemini-2.0-flash-lite', label: 'Gemini 2.0 Flash Lite' },
      { id: 'gemini-1.5-pro', label: 'Gemini 1.5 Pro' },
      { id: 'gemini-1.5-flash', label: 'Gemini 1.5 Flash' },
    ],
  },
  {
    id: 'github',
    name: 'GitHub Models',
    type: 'cloud',
    badge: 'cloud',
    keyPlaceholder: 'github_pat_... or ghp_...',
    keyLink: 'https://github.com/settings/tokens?type=beta',
    models: [
      { id: 'openai/gpt-4o', label: 'GPT-4o (quality)' },
      { id: 'openai/gpt-4o-mini', label: 'GPT-4o Mini (fast)' },
      { id: 'openai/o1', label: 'o1' },
      { id: 'openai/o1-mini', label: 'o1-mini' },
      { id: 'openai/o3-mini', label: 'o3-mini' },
      { id: 'meta/Llama-3.3-70B-Instruct', label: 'Llama 3.3 70B' },
      { id: 'meta/Meta-Llama-3.1-405B-Instruct', label: 'Llama 3.1 405B' },
      { id: 'mistral-ai/Mistral-Large-2411', label: 'Mistral Large' },
      { id: 'mistral-ai/Mistral-Nemo', label: 'Mistral Nemo' },
      { id: 'microsoft/Phi-3.5-MoE-instruct', label: 'Phi 3.5 MoE' },
      { id: 'microsoft/Phi-3.5-mini-instruct', label: 'Phi 3.5 Mini' },
      { id: 'deepseek/DeepSeek-R1', label: 'DeepSeek R1' },
      { id: 'cohere/Cohere-command-r-plus-08-2024', label: 'Command R+' },
      { id: 'ai21-labs/AI21-Jamba-1.5-Large', label: 'Jamba 1.5 Large' },
    ],
  },
  {
    id: 'ollama',
    name: 'Ollama',
    type: 'local',
    badge: 'local',
    hasBaseUrl: true,
    defaultBaseUrl: 'http://localhost:11434',
    models: [], // Populated dynamically
  },
  {
    id: 'ollama_cloud',
    name: 'Ollama Cloud',
    type: 'cloud',
    badge: 'cloud',
    keyPlaceholder: 'your Ollama API key',
    keyLink: 'https://ollama.com/settings/keys',
    defaultModel: 'gemma4:31b',
    models: [
      { id: 'gpt-oss:120b', label: 'GPT-OSS 120B (quality)' },
      { id: 'gpt-oss:20b', label: 'GPT-OSS 20B (fast)' },
      { id: 'gemma4:31b', label: 'Gemma 4 31B' },
      { id: 'qwen3-coder-next', label: 'Qwen3 Coder Next' },
      { id: 'kimi-k2.6', label: 'Kimi K2.6' },
      { id: 'deepseek-v3.2', label: 'DeepSeek v3.2' },
      { id: 'glm-4.7', label: 'GLM 4.7' },
      { id: 'glm-5.1', label: 'GLM 5.1' },
      { id: 'minimax-m2.7', label: 'MiniMax M2.7' },
    ],
  },
]

const CONFIG_KEY = 'ai_providers_config'
const SETTINGS_KEY = 'ai_settings'

/**
 * Hosted/browser mode: window.finn.apiUrl is only injected by the Electron
 * preload script. Its absence means we're running in a plain browser
 * (Docker, Railway, Render, etc.), where local providers like Ollama have
 * nothing serving them inside the container.
 */
function isHostedMode() {
  if (typeof window === 'undefined') return true
  return !window.finn?.apiUrl
}

/**
 * Provider definitions that should be offered to the user for new setup.
 * In hosted mode this drops `type: 'local'` providers (Ollama) since they
 * can never resolve on a shared cloud host. Existing local configs are still
 * recognized by the lookup helpers below — we just stop showing them as
 * an option in pickers.
 */
export function visibleProviderDefs() {
  if (isHostedMode()) {
    return PROVIDER_DEFS.filter(def => def.type !== 'local')
  }
  return PROVIDER_DEFS
}

/** Return all provider configs (built-in + custom) from localStorage. */
export function getProvidersConfig() {
  try {
    const raw = localStorage.getItem(CONFIG_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

export function saveProvidersConfig(configs) {
  localStorage.setItem(CONFIG_KEY, JSON.stringify(configs))
}

/** Get config for one provider by id. */
export function getProviderConfig(id) {
  return getProvidersConfig().find(p => p.id === id) || null
}

/**
 * Bootstrap a server-managed provider config in localStorage. Called once on
 * app mount when GET /api/managed-provider reports `available: true`. The
 * resulting entry has `managed: true` and no `apiKey` — the server fills the
 * key in when the request arrives (see `_build_ai_provider_from_headers` in
 * main.py). Idempotent: re-running won't clobber an advisor's own key if
 * they later paste one through Settings.
 */
export function bootstrapManagedProvider({ provider, model }) {
  if (!provider) return
  const existing = getProviderConfig(provider)
  // Don't overwrite an advisor's BYO key if one is already stored.
  if (existing && existing.apiKey) return
  upsertProviderConfig(provider, {
    managed: true,
    apiKey: '',
    model: model || '',
  })
  // Make managed provider the active one if no other provider is active yet.
  if (!getActiveProvider()) {
    setActiveProviderLS(provider)
  }
  bootstrapAIDefaultsOnFirstProvider()
}

/**
 * Returns true when a provider entry was set up by `bootstrapManagedProvider`
 * — i.e. it's the server-side fallback and the advisor hasn't supplied their
 * own key. Used to render the Settings card as read-only and to skip sending
 * the X-AI-Key header.
 */
export function isManagedProvider(id) {
  const cfg = getProviderConfig(id)
  return !!(cfg && cfg.managed && !cfg.apiKey)
}

/** Create or update a provider config entry. */
export function upsertProviderConfig(id, data) {
  const configs = getProvidersConfig()
  const idx = configs.findIndex(p => p.id === id)
  if (idx >= 0) {
    configs[idx] = { ...configs[idx], ...data }
  } else {
    configs.push({ id, ...data })
  }
  saveProvidersConfig(configs)
}

/** Remove a provider config (used for custom providers or key removal). */
export function removeProviderConfig(id) {
  saveProvidersConfig(getProvidersConfig().filter(p => p.id !== id))
}

/** Get the current ai_settings object. */
export function getAISettings() {
  try {
    return JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}')
  } catch {
    return {}
  }
}

/**
 * On the first time a provider is configured, flip rerank + synthesize ON
 * by default — without an AI provider these toggles do nothing, but with
 * one the experience is meaningfully better, and a new advisor shouldn't
 * have to discover them in Settings to see the value. Idempotent: only
 * writes when the localStorage keys are missing, so an advisor who has
 * explicitly toggled either off keeps their preference.
 *
 * Returns true if defaults were written this call (useful for testing).
 */
export function bootstrapAIDefaultsOnFirstProvider() {
  if (getConfiguredProviderIds().length === 0) return false
  let wrote = false
  if (localStorage.getItem(SETTINGS_KEY) === null) {
    localStorage.setItem(
      SETTINGS_KEY,
      JSON.stringify({ rerank: true, synthesize: true }),
    )
    wrote = true
  }
  // ChatTab uses its own key (so chat-only rerank can be toggled
  // independently of the search-tab AI defaults). Flip it the same way.
  if (localStorage.getItem('chat_rerank') === null) {
    localStorage.setItem('chat_rerank', 'true')
    wrote = true
  }
  return wrote
}

/** Get the active provider id. */
export function getActiveProvider() {
  return getAISettings().provider || null
}

/** Set the active provider. */
export function setActiveProviderLS(id) {
  const settings = getAISettings()
  localStorage.setItem(SETTINGS_KEY, JSON.stringify({ ...settings, provider: id }))
}

/**
 * Returns ids of all providers that are considered "configured":
 *  - cloud provider: has a non-empty apiKey OR is server-managed (managed=true)
 *  - ollama: marked available=true
 *  - custom: has a non-empty baseUrl
 */
export function getConfiguredProviderIds() {
  const configs = getProvidersConfig()
  const result = []

  for (const def of PROVIDER_DEFS) {
    const cfg = configs.find(c => c.id === def.id)
    if (!cfg) continue
    if (def.type === 'local') {
      if (cfg.available) result.push(def.id)
    } else {
      if (cfg.apiKey || cfg.managed) result.push(def.id)
    }
  }

  // Custom providers
  for (const cfg of configs) {
    if (cfg.isCustom && cfg.baseUrl) result.push(cfg.id)
  }

  return result
}

/**
 * Build HTTP headers for a provider API call.
 * Returns object suitable for axios `headers` option.
 * Pass modelOverride to use a different model than the saved default.
 */
export function buildProviderHeaders(providerId, modelOverride = null) {
  const cfg = getProviderConfig(providerId)
  const headers = {}
  if (!cfg) return headers

  const resolvedModel = (modelOverride && modelOverride.trim()) ? modelOverride.trim() : (cfg.model || '')

  if (cfg.isCustom) {
    if (cfg.apiKey && cfg.apiKey !== 'none') headers['X-AI-Key'] = cfg.apiKey
    if (cfg.baseUrl) headers['X-AI-Base-URL'] = cfg.baseUrl
    if (resolvedModel) headers['X-AI-Model'] = resolvedModel
  } else if (providerId === 'ollama') {
    const baseUrl = cfg.baseUrl || 'http://localhost:11434'
    if (baseUrl !== 'http://localhost:11434') headers['X-AI-Base-URL'] = baseUrl
    const model = resolvedModel || 'llama3.2'
    headers['X-Ollama-Model'] = model
    headers['X-AI-Model'] = model
  } else {
    // Server-managed providers send no X-AI-Key — the backend fills it in
    // from settings.fallback_api_key. Sending an empty header would short-
    // circuit that fallback, so we omit the header entirely.
    if (cfg.apiKey) headers['X-AI-Key'] = cfg.apiKey
    if (resolvedModel) headers['X-AI-Model'] = resolvedModel
  }

  return headers
}

/**
 * Returns the backend provider name for a given frontend provider id.
 * Custom providers map to 'openai_compatible'.
 */
export function getAPIProviderName(providerId) {
  const cfg = getProviderConfig(providerId)
  if (cfg?.isCustom) return 'openai_compatible'
  return providerId
}

/** Human-readable display name for a provider id. */
export function getProviderDisplayName(providerId) {
  const def = PROVIDER_DEFS.find(d => d.id === providerId)
  if (def) return def.name
  const cfg = getProviderConfig(providerId)
  return cfg?.name || providerId
}

/** Available model list for a provider id. Empty array for custom/unknown. */
export function getProviderModels(providerId) {
  return PROVIDER_DEFS.find(d => d.id === providerId)?.models ?? []
}

/** Whether a provider is local/private (not cloud). */
export function isLocalProvider(providerId) {
  const cfg = getProviderConfig(providerId)
  if (cfg?.isCustom) return false // custom could be remote
  return PROVIDER_DEFS.find(d => d.id === providerId)?.type === 'local'
}

/**
 * Migrate legacy localStorage keys (ai_api_key_anthropic etc.) to the
 * unified ai_providers_config format. Safe to call on every mount.
 */
export function migrateLegacySettings() {
  const configs = getProvidersConfig()
  let changed = false

  const legacyMap = [
    { id: 'anthropic', keyLS: 'ai_api_key_anthropic', modelLS: 'anthropic_model' },
    { id: 'openai', keyLS: 'ai_api_key_openai', modelLS: 'openai_model' },
  ]

  for (const { id, keyLS, modelLS } of legacyMap) {
    const apiKey = localStorage.getItem(keyLS)
    const model = localStorage.getItem(modelLS) || ''
    if (apiKey && !configs.find(c => c.id === id)) {
      configs.push({ id, apiKey, model })
      changed = true
    }
  }

  // Skip the local-Ollama placeholder in hosted mode — there's nothing on
  // localhost:11434 inside a cloud container, and surfacing a "detecting…"
  // entry just confuses users into a dead end.
  if (!isHostedMode()) {
    const ollamaModel = localStorage.getItem('ollama_model')
    if (!configs.find(c => c.id === 'ollama')) {
      configs.push({
        id: 'ollama',
        model: ollamaModel || 'llama3.2',
        baseUrl: 'http://localhost:11434',
        available: false,
      })
      changed = true
    }
  }

  if (changed) saveProvidersConfig(configs)
}

export async function fetchDynamicModels(providerId, apiKey, baseUrl = null) {
  try {
    const response = await fetch('/api/providers/models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        provider_id: providerId,
        api_key: apiKey,
        base_url: baseUrl,
      }),
    });
    if (!response.ok) throw new Error(`Failed to fetch models: ${response.statusText}`);
    return await response.json();
  } catch (e) {
    console.error('Error fetching dynamic models:', e);
    return [];
  }
}
