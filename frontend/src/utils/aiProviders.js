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
    models: [
      { id: 'claude-sonnet-4-5-20250929', label: 'Claude Sonnet 4.5 (quality)' },
      { id: 'claude-haiku-4-5-20251001', label: 'Claude Haiku 4.5 (fast)' },
      { id: 'claude-opus-4-6', label: 'Claude Opus 4.6' },
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
]

const CONFIG_KEY = 'ai_providers_config'
const SETTINGS_KEY = 'ai_settings'

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

  if (changed) saveProvidersConfig(configs)
}
