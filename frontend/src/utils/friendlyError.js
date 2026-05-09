/**
 * Map a raw error (axios error, fetch Error, plain string, SSE error event
 * payload) to plain-language copy for non-technical advisors. R8.7.
 *
 * In Basic Mode the chat and upload toasts call this with `{ expert: false }`
 * and surface the friendly title alone. Expert Mode keeps the raw `detail`
 * string so power users can see the actual response body.
 *
 * The categories below cover the failure modes a pilot advisor actually hits:
 * provider 5xx, network drops, timeouts, JSON/decode errors, auth issues,
 * file-not-found / unsupported-type from the indexer, and the catch-all.
 */

/** @typedef {{title: string, hint?: string, code: string}} FriendlyError */

const NETWORK_PATTERNS = [
  /network\s*error/i,
  /failed to fetch/i,
  /could not reach/i,
  /connection\s*(refused|reset|aborted)/i,
  /err_(network|connection|internet)/i,
  /econnrefused/i,
  /enetunreach/i,
]

const TIMEOUT_PATTERNS = [
  /timeout/i,
  /timed?\s*out/i,
  /etimedout/i,
  /aborted/i,
]

const DECODE_PATTERNS = [
  /unexpected token/i,
  /json\.parse/i,
  /invalid json/i,
  /decode/i,
  /unicodedecodeerror/i,
]

const UNSUPPORTED_FILE_PATTERNS = [
  /unsupported (file )?type/i,
  /not supported/i,
  /no extractor/i,
]

const AUTH_PATTERNS = [
  /unauthorized/i,
  /forbidden/i,
  /invalid api key/i,
  /authentication/i,
]

const RATE_LIMIT_PATTERNS = [
  /rate.?limit/i,
  /too many requests/i,
  /quota/i,
]

/**
 * Best-effort extraction of an HTTP-ish status code from anything we get back.
 * Falls through to 0 if there isn't one.
 */
const extractStatus = (err) => {
  if (!err) return 0
  if (typeof err === 'number') return err
  // axios shape
  if (err.response?.status) return err.response.status
  // fetch Response shape
  if (err.status && typeof err.status === 'number') return err.status
  // Error message stamped with "HTTP 503"
  const m = String(err.message || err).match(/\bHTTP\s+(\d{3})\b/i)
  if (m) return Number(m[1])
  return 0
}

/**
 * Best-effort extraction of a textual detail.
 */
const extractMessage = (err) => {
  if (!err) return ''
  if (typeof err === 'string') return err
  // axios: response.data.detail / .error / .message
  const d = err.response?.data
  if (typeof d === 'string') return d
  if (d?.detail) return String(d.detail)
  if (d?.error) return String(d.error)
  if (d?.message) return String(d.message)
  if (err.message) return String(err.message)
  return ''
}

/**
 * Classify into one of the well-known categories. Returns a code string
 * suitable for switching on.
 *
 * @returns {'server_error' | 'network' | 'timeout' | 'auth' | 'rate_limit' | 'decode' | 'unsupported_file' | 'not_found' | 'unknown'}
 */
export function classifyError(err) {
  const status = extractStatus(err)
  const msg = extractMessage(err)

  if (status === 401 || status === 403 || AUTH_PATTERNS.some(re => re.test(msg))) {
    return 'auth'
  }
  if (status === 404) return 'not_found'
  if (status === 429 || RATE_LIMIT_PATTERNS.some(re => re.test(msg))) {
    return 'rate_limit'
  }
  if (status >= 500 && status < 600) return 'server_error'
  if (TIMEOUT_PATTERNS.some(re => re.test(msg))) return 'timeout'
  if (NETWORK_PATTERNS.some(re => re.test(msg)) || (err?.response === undefined && err?.request)) {
    return 'network'
  }
  if (UNSUPPORTED_FILE_PATTERNS.some(re => re.test(msg))) return 'unsupported_file'
  if (DECODE_PATTERNS.some(re => re.test(msg))) return 'decode'
  return 'unknown'
}

/** Plain-language copy keyed by category. Each entry has a one-line title
 *  and an optional next-action hint. */
const COPY = {
  auth: {
    title: 'Your AI provider rejected the request — the API key may be wrong or expired.',
    hint: 'Open Settings and re-test your provider connection.',
  },
  not_found: {
    title: "We couldn't find that item.",
    hint: 'It may have been deleted or moved. Try refreshing.',
  },
  rate_limit: {
    title: 'Your AI provider is rate-limiting requests right now.',
    hint: 'Wait a minute and try again.',
  },
  server_error: {
    title: 'Your AI provider is having a problem on their end.',
    hint: 'Wait a minute and try again. If it keeps happening, check the provider\'s status page.',
  },
  timeout: {
    title: 'That request took too long to finish.',
    hint: 'Try again with a shorter question, or check your internet connection.',
  },
  network: {
    title: "Couldn't reach Finn or your AI provider.",
    hint: 'Check your internet connection and try again.',
  },
  unsupported_file: {
    title: "That file type isn't supported.",
    hint: 'Finn handles PDF, DOCX, TXT, MD, CSV, XLSX, JSON, and audio recordings (MP3/WAV/M4A).',
  },
  decode: {
    title: "We couldn't read the response from your AI provider.",
    hint: 'Try the request again. If it keeps happening, switch providers in Settings.',
  },
  unknown: {
    title: 'Something went wrong.',
    hint: 'Try again. Switch to Expert mode if you need to see the technical details.',
  },
}

/**
 * Map an error to advisor-friendly copy.
 *
 * - Basic Mode: returns the friendly title (+ hint if present), no raw details.
 * - Expert Mode: returns the raw error detail when we have one, falling back
 *   to the friendly title so power users still see *something* useful when
 *   the server returned an empty body.
 *
 * @param {*} err - axios error, fetch Error, plain string, etc.
 * @param {{ expert?: boolean, fallback?: string }} [opts]
 * @returns {string}
 */
export function friendlyError(err, { expert = false, fallback = '' } = {}) {
  const raw = extractMessage(err) || fallback
  if (expert) {
    // Power-user surface — show whatever the server said. Only fall back to
    // the friendly copy when there's literally nothing to show.
    if (raw) return raw
    return COPY.unknown.title
  }
  const code = classifyError(err)
  const entry = COPY[code] || COPY.unknown
  return entry.hint ? `${entry.title} ${entry.hint}` : entry.title
}

/**
 * Same as friendlyError but returns the structured object so callers can
 * style title and hint differently if they want. Used by the upload toast
 * which renders multi-error lists.
 */
export function friendlyErrorParts(err, { expert = false, fallback = '' } = {}) {
  const raw = extractMessage(err) || fallback
  const code = classifyError(err)
  if (expert) {
    return { title: raw || COPY.unknown.title, hint: '', code, raw }
  }
  const entry = COPY[code] || COPY.unknown
  return { title: entry.title, hint: entry.hint || '', code, raw }
}
