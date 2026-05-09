// Resolve the absolute URL for a relative API path.
//
// Why: under Electron the renderer loads via file://, so relative paths
// passed to fetch() / EventSource resolve to file:///api/... and fail. The
// Electron preload bridge exposes window.finn.apiUrl (e.g. "http://127.0.0.1:57384")
// — prefix that for streaming endpoints which can't share axios's baseURL.
//
// In a browser context (Vite dev server, FastAPI-served static build) the
// bridge is undefined and we return the path unchanged so it stays same-origin.
export function apiUrl(path) {
  const base = (typeof window !== 'undefined' && window.finn?.apiUrl) || ''
  if (!base) return path
  if (!path.startsWith('/')) return `${base}/${path}`
  return `${base}${path}`
}
