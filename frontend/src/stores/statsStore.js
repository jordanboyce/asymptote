import { ref } from 'vue'
import { defineStore } from 'pinia'
import http from '../utils/http'
import { useCollectionStore } from './collectionStore'

// Collection stats (documents/pages/chunks + offline flag) for the footer,
// the tab empty-states, and anything else that needs them.
//
// Previously App.vue owned this object, re-fetched it from four separate
// watchers, and prop-drilled chunkCount/documentCount into SearchTab and
// ChatTab. One store, one debounced fetch; consumers read reactively.
export const useStatsStore = defineStore('stats', () => {
  const documents = ref(0)
  const pages = ref(0)
  const chunks = ref(0)
  const offline = ref(false)
  const loaded = ref(false)

  let inFlight = null
  let debounceTimer = null

  async function fetchStats() {
    const collectionStore = useCollectionStore()
    const collectionId = collectionStore.currentCollectionId
    // Coalesce: concurrent callers share one request.
    if (inFlight) return inFlight
    inFlight = (async () => {
      try {
        // SQL-backed aggregates — never the unpaginated /documents list,
        // which is a ~25MB response at 63k documents.
        const [statsResponse, healthResponse] = await Promise.all([
          http.get(`/api/collections/${encodeURIComponent(collectionId)}/stats`),
          http.get(`/health?collection_id=${encodeURIComponent(collectionId)}`),
        ])
        documents.value = statsResponse.data.total_documents || 0
        pages.value = statsResponse.data.total_pages || 0
        chunks.value = statsResponse.data.total_chunks || 0
        offline.value = !!healthResponse.data.offline_mode
        loaded.value = true
      } catch {
        // Stats are decorative; the http interceptor already surfaced any
        // real connectivity problem via the offline banner.
      } finally {
        inFlight = null
      }
    })()
    return inFlight
  }

  // For high-frequency triggers (job progress ticks): trailing-edge debounce.
  function fetchStatsDebounced(delay = 500) {
    clearTimeout(debounceTimer)
    debounceTimer = setTimeout(fetchStats, delay)
  }

  return { documents, pages, chunks, offline, loaded, fetchStats, fetchStatsDebounced }
})
