import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { useCollectionStore } from './collectionStore'

export const useSearchStore = defineStore('search', () => {
  // Search state
  const query = ref('')
  const topK = ref(parseInt(localStorage.getItem('asymptote_default_top_k')) || 10)
  const results = ref([])
  const lastQuery = ref('')
  const searched = ref(false)

  // Support for multiple AI providers
  const aiResponses = ref([])  // Array of {provider, synthesis, aiUsage}

  // Search cache - now collection-aware
  const CACHE_KEY = 'asymptote_search_cache_v2'  // New key to avoid conflicts with old cache
  const MAX_CACHE_SIZE = 20
  const cacheCount = ref(0) // Reactive cache count for UI updates
  const cache = ref({}) // Reactive cache data for UI updates - keyed by collection_id

  // Actions
  // Pass { cache: false } when re-serving an already-cached entry so the
  // original timestamp (and history order) is preserved.
  function setSearchResults(data, { cache: shouldCache = true } = {}) {
    results.value = data.results || []
    lastQuery.value = data.query || ''
    searched.value = true

    // AI responses (one per provider)
    if (data.aiResponses && data.aiResponses.length > 0) {
      aiResponses.value = data.aiResponses
    } else {
      aiResponses.value = []
    }

    // Cache the search results
    if (shouldCache && data.query) {
      cacheSearchResult(data)
    }
  }

  // Get current collection ID
  function getCurrentCollectionId() {
    const collectionStore = useCollectionStore()
    return collectionStore.currentCollectionId || 'default'
  }

  function getCacheKey(query, topK) {
    return `${query.toLowerCase().trim()}|${topK}`
  }

  // Get collection-specific cache
  function getCollectionCache(collectionId = null) {
    const cid = collectionId || getCurrentCollectionId()
    if (!cache.value[cid]) {
      cache.value[cid] = {}
    }
    return cache.value[cid]
  }

  function cacheSearchResult(data) {
    try {
      const collectionId = getCurrentCollectionId()
      const cacheKey = getCacheKey(data.query, topK.value)

      // Normalize query to lowercase for consistency
      const normalizedQuery = data.query.toLowerCase().trim()

      // Create cache entry
      const entry = {
        query: normalizedQuery,
        topK: topK.value,
        results: data.results,
        aiResponses: data.aiResponses || [],
        timestamp: Date.now(),
        collectionId: collectionId
      }

      // Ensure collection cache exists
      if (!cache.value[collectionId]) {
        cache.value[collectionId] = {}
      }

      // Add to collection-specific cache
      cache.value[collectionId][cacheKey] = entry

      // Keep only the most recent MAX_CACHE_SIZE entries per collection
      const entries = Object.entries(cache.value[collectionId])
        .sort((a, b) => b[1].timestamp - a[1].timestamp)
        .slice(0, MAX_CACHE_SIZE)

      cache.value[collectionId] = Object.fromEntries(entries)
      localStorage.setItem(CACHE_KEY, JSON.stringify(cache.value))

      // Sync cache count
      syncCacheCount()
    } catch (error) {
      console.error('Failed to cache search results:', error)
    }
  }

  function getSearchCache() {
    // Return cache for current collection
    return getCollectionCache()
  }

  function getCachedResult(query, topKValue) {
    const collectionCache = getCollectionCache()
    const cacheKey = getCacheKey(query, topKValue)
    return collectionCache[cacheKey] || null
  }

  function clearSearchCache() {
    // Clear only current collection's cache
    const collectionId = getCurrentCollectionId()
    delete cache.value[collectionId]
    cache.value = { ...cache.value }
    localStorage.setItem(CACHE_KEY, JSON.stringify(cache.value))
    syncCacheCount()
  }

  function clearCollectionCache(collectionId) {
    // Clear a specific collection's cache (called when collection is deleted)
    if (cache.value[collectionId]) {
      delete cache.value[collectionId]
      cache.value = { ...cache.value }
      localStorage.setItem(CACHE_KEY, JSON.stringify(cache.value))
      syncCacheCount()
    }
  }

  function deleteCacheEntry(query, topKValue) {
    const collectionId = getCurrentCollectionId()
    const collectionCache = getCollectionCache()
    const cacheKey = getCacheKey(query, topKValue)
    delete collectionCache[cacheKey]
    // Trigger reactivity
    cache.value[collectionId] = { ...collectionCache }
    cache.value = { ...cache.value }
    localStorage.setItem(CACHE_KEY, JSON.stringify(cache.value))
    syncCacheCount()
  }

  function getCacheStats() {
    const collectionCache = getCollectionCache()
    const entries = Object.values(collectionCache)
    const actualCount = entries.length
    return {
      count: actualCount,
      totalSize: JSON.stringify(collectionCache).length,
      oldest: entries.length > 0 ? Math.min(...entries.map(e => e.timestamp)) : null,
      newest: entries.length > 0 ? Math.max(...entries.map(e => e.timestamp)) : null
    }
  }

  // Initialize cache from localStorage on store creation
  function initializeCache() {
    try {
      const cached = localStorage.getItem(CACHE_KEY)
      if (!cached) {
        cache.value = {}
        cacheCount.value = 0
        return
      }

      const loadedCache = JSON.parse(cached)

      // Handle migration from old flat cache to new collection-aware cache
      // Old format: { "query|topK": entry }
      // New format: { "collectionId": { "query|topK": entry } }

      // Check if this is old format (entries have query/timestamp directly)
      const firstKey = Object.keys(loadedCache)[0]
      const firstValue = loadedCache[firstKey]
      if (firstValue && firstValue.query && firstValue.timestamp) {
        // Old format - migrate to new format under 'default' collection
        console.log('Migrating search cache to collection-aware format')
        const migratedCache = { default: {} }
        for (const [key, entry] of Object.entries(loadedCache)) {
          if (entry && entry.query && entry.timestamp && entry.topK) {
            migratedCache.default[key] = { ...entry, collectionId: 'default' }
          }
        }
        cache.value = migratedCache
        localStorage.setItem(CACHE_KEY, JSON.stringify(migratedCache))
      } else {
        // New format - just load it
        cache.value = loadedCache
      }

      syncCacheCount()
    } catch (error) {
      console.error('Failed to load search cache:', error)
      cache.value = {}
      cacheCount.value = 0
    }
  }

  // Sync cache count from actual cache (for current collection)
  function syncCacheCount() {
    const collectionCache = getCollectionCache()
    cacheCount.value = Object.keys(collectionCache).length
  }

  initializeCache()

  function clearResults() {
    results.value = []
    aiResponses.value = []
    searched.value = false
  }

  function setQuery(newQuery) {
    query.value = newQuery
  }

  function setTopK(value) {
    topK.value = value
  }

  return {
    // State
    query,
    topK,
    results,
    lastQuery,
    searched,
    aiResponses,
    cache, // Reactive cache ref
    // Actions
    setSearchResults,
    clearResults,
    setQuery,
    setTopK,
    // Cache functions
    getCachedResult,
    getSearchCache,
    clearSearchCache,
    clearCollectionCache,
    deleteCacheEntry,
    getCacheStats,
    syncCacheCount
  }
})
