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
    models: [], // populated dynamically via detection
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

// Provider ids the server has pre-provisioned a key for on behalf of this
// user (closed-beta path — see services.beta_keys on the backend). Populated
// once on app boot via fetchServerManagedProviders(); empty in any deployment
// where beta_keys_file is unset, which is the default.
let _serverManagedProviders = []

/**
 * Fetch the list of providers the server has a key for on this user's
 * behalf, cache it module-locally, and bootstrap an active provider if the
 * user has none configured. Idempotent — safe to call again to refresh.
 *
 * Failures are swallowed silently because the legacy BYO-key flow is still
 * the fallback; if the discovery endpoint is unreachable, Settings still
 * lets the user paste a key.
 */
export async function fetchServerManagedProviders() {
  try {
    const res = await fetch('/api/user/ai-providers', { credentials: 'same-origin' })
    if (!res.ok) {
      _serverManagedProviders = []
      return _serverManagedProviders
    }
    const data = await res.json()
    _serverManagedProviders = (data?.providers || [])
      .map(p => p?.provider)
      .filter(Boolean)
  } catch {
    _serverManagedProviders = []
  }

  // Auto-pick an active provider when the user has nothing in localStorage
  // yet — beta users land in the app without ever touching Settings, so chat
  // would otherwise have no provider id to send.
  if (_serverManagedProviders.length > 0 && !getActiveProvider()) {
    setActiveProviderLS(_serverManagedProviders[0])
  }

  return _serverManagedProviders
}

export function getServerManagedProviders() {
  return _serverManagedProviders.slice()
}

export function isServerManaged(providerId) {
  return _serverManagedProviders.includes(providerId)
}

/**
 * Provider definitions that should be offered to the user for new setup.
 * In hosted mode this drops `type: 'local'` providers (Ollama) since they
 * can never resolve on a shared cloud host. Server-managed providers are
 * also dropped — the operator owns those keys; users shouldn't see a card
 * inviting them to enter or replace one.
 *
 * Existing local configs and server-managed providers are still recognized
 * by the lookup helpers below; we just stop offering them in pickers.
 */
export function visibleProviderDefs() {
  let defs = PROVIDER_DEFS
  if (isHostedMode()) {
    defs = defs.filter(def => def.type !== 'local')
  }
  if (_serverManagedProviders.length > 0) {
    defs = defs.filter(def => !_serverManagedProviders.includes(def.id))
  }
  return defs
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
 *  - cloud provider: has a non-empty apiKey
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
      if (cfg.apiKey) result.push(def.id)
    }
  }

  // Custom providers
  for (const cfg of configs) {
    if (cfg.isCustom && cfg.baseUrl) result.push(cfg.id)
  }

  // Server-managed providers (beta-seeded). Surfaced here so the rest of the
  // UI — chat picker, AI-feature gates, etc. — treats them as configured
  // even though there's nothing in localStorage.
  for (const id of _serverManagedProviders) {
    if (!result.includes(id)) result.push(id)
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
  const serverManaged = isServerManaged(providerId)

  // Server-managed providers may have no localStorage entry at all — the
  // backend resolves the key from user_api_keys. Forward only the model
  // hint when there's one to forward.
  if (!cfg && !serverManaged) return headers

  const resolvedModel = (modelOverride && modelOverride.trim())
    ? modelOverride.trim()
    : (cfg?.model || '')

  if (cfg?.isCustom) {
    if (cfg.apiKey && cfg.apiKey !== 'none') headers['X-AI-Key'] = cfg.apiKey
    if (cfg.baseUrl) headers['X-AI-Base-URL'] = cfg.baseUrl
    if (resolvedModel) headers['X-AI-Model'] = resolvedModel
  } else if (providerId === 'ollama') {
    const baseUrl = cfg?.baseUrl || 'http://localhost:11434'
    if (baseUrl !== 'http://localhost:11434') headers['X-AI-Base-URL'] = baseUrl
    const model = resolvedModel || 'llama3.2'
    headers['X-Ollama-Model'] = model
    headers['X-AI-Model'] = model
  } else {
    // For server-managed providers we intentionally omit X-AI-Key so the
    // backend falls back to the user's row in user_api_keys. Sending an
    // empty/placeholder key would short-circuit that lookup.
    if (cfg?.apiKey && !serverManaged) headers['X-AI-Key'] = cfg.apiKey
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
