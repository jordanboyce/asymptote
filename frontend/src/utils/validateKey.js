/**
 * Shared helpers for validating an AI provider key against
 * `POST /api/ai/validate-key` and rendering the result with
 * a small, predictable error vocabulary.
 *
 * The backend currently returns free-form `error` strings; we map them to a
 * stable code on the client so every UI (onboarding, Settings, ChatTab banner)
 * speaks the same language. When the backend grows a real `error_code`
 * discriminator, prefer it over the substring match.
 */

import axios from 'axios'

/** @typedef {'invalid_key' | 'network_error' | 'unsupported_provider' | ''} ValidateErrorCode */

/**
 * Map a /api/ai/validate-key response or an axios error to one of three
 * stable error codes. Empty string means "no error / valid".
 *
 * @param {{ response?: any, axiosError?: any }} args
 * @returns {ValidateErrorCode}
 */
export function classifyValidateError({ response, axiosError } = {}) {
  // Network-level failure (no HTTP response at all)
  if (axiosError && !axiosError.response) return 'network_error'

  // Prefer a stable backend code if present
  const code = response?.error_code || axiosError?.response?.data?.error_code
  if (code === 'invalid_key' || code === 'network_error' || code === 'unsupported_provider') {
    return code
  }

  if (response?.valid) return ''

  const msg = (
    response?.error ||
    axiosError?.response?.data?.detail ||
    axiosError?.message ||
    ''
  ).toLowerCase()

  if (msg.includes('invalid provider') || msg.includes('unsupported')) {
    return 'unsupported_provider'
  }
  if (
    msg.includes('could not reach') ||
    msg.includes('network') ||
    msg.includes('connection') ||
    msg.includes('timeout')
  ) {
    return 'network_error'
  }
  return 'invalid_key'
}

/**
 * Plain-language copy for each typed error code. Centralized so onboarding,
 * Settings, and the chat banner all read the same way.
 *
 * @param {ValidateErrorCode} code
 * @returns {string}
 */
export function validateErrorMessage(code) {
  switch (code) {
    case 'invalid_key':
      return "That key didn't validate. Double-check you copied the whole key from your provider's dashboard."
    case 'network_error':
      return "Couldn't reach the provider. Check your internet connection and try again."
    case 'unsupported_provider':
      return "This provider isn't supported yet. Pick another option."
    default:
      return ''
  }
}

/**
 * Run `/api/ai/validate-key` for a (provider, apiKey, model?) tuple and
 * return a normalized `{ valid, code, capabilities }` shape so callers
 * never have to think about the request mechanics.
 *
 * @param {{ provider: string, apiKey: string, model?: string, baseUrl?: string }} args
 */
export async function validateProviderKey({ provider, apiKey, model, baseUrl }) {
  const headers = {
    'X-AI-Provider': provider,
  }
  if (apiKey) headers['X-AI-Key'] = apiKey
  if (model) headers['X-AI-Model'] = model
  if (baseUrl) headers['X-AI-Base-URL'] = baseUrl

  try {
    const resp = await axios.post('/api/ai/validate-key', null, { headers })
    return {
      valid: !!resp.data?.valid,
      code: classifyValidateError({ response: resp.data }),
      capabilities: resp.data?.capabilities || null,
      rawError: resp.data?.error || '',
    }
  } catch (err) {
    return {
      valid: false,
      code: classifyValidateError({ axiosError: err }),
      capabilities: null,
      rawError: err?.response?.data?.detail || err?.message || '',
    }
  }
}
