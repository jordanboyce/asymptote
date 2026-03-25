import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import axios from 'axios'
import { useSearchStore } from './searchStore'

export const useCollectionStore = defineStore('collection', () => {
  // State
  const collections = ref([])
  const currentCollectionId = ref('default')
  const loading = ref(false)
  const error = ref(null)

  // Computed
  const currentCollection = computed(() => {
    return collections.value.find(c => c.id === currentCollectionId.value) || null
  })

  const sortedCollections = computed(() => {
    // Put default first, then sort by name
    return [...collections.value].sort((a, b) => {
      if (a.id === 'default') return -1
      if (b.id === 'default') return 1
      return a.name.localeCompare(b.name)
    })
  })

  // Actions
  async function loadCollections() {
    loading.value = true
    error.value = null
    try {
      const response = await axios.get('/api/collections')
      collections.value = response.data.collections || []

      // Ensure current collection still exists
      const exists = collections.value.some(c => c.id === currentCollectionId.value)
      if (!exists && collections.value.length > 0) {
        currentCollectionId.value = 'default'
      }
    } catch (err) {
      error.value = err.response?.data?.detail || 'Failed to load collections'
      console.error('Failed to load collections:', err)
    } finally {
      loading.value = false
    }
  }

  async function createCollection(data) {
    loading.value = true
    error.value = null
    try {
      const response = await axios.post('/api/collections', data)
      collections.value.push(response.data)
      return response.data
    } catch (err) {
      error.value = err.response?.data?.detail || 'Failed to create collection'
      throw err
    } finally {
      loading.value = false
    }
  }

  async function updateCollection(collectionId, updates) {
    loading.value = true
    error.value = null
    try {
      const response = await axios.put(`/api/collections/${collectionId}`, updates)
      const index = collections.value.findIndex(c => c.id === collectionId)
      if (index !== -1) {
        collections.value[index] = response.data
      }
      return response.data
    } catch (err) {
      error.value = err.response?.data?.detail || 'Failed to update collection'
      throw err
    } finally {
      loading.value = false
    }
  }

  async function deleteCollection(collectionId) {
    if (collectionId === 'default') {
      error.value = 'Cannot delete the default collection'
      return false
    }

    loading.value = true
    error.value = null
    try {
      await axios.delete(`/api/collections/${collectionId}`)
      collections.value = collections.value.filter(c => c.id !== collectionId)

      // If we deleted the current collection, switch to default
      if (currentCollectionId.value === collectionId) {
        currentCollectionId.value = 'default'
      }
      return true
    } catch (err) {
      error.value = err.response?.data?.detail || 'Failed to delete collection'
      throw err
    } finally {
      loading.value = false
    }
  }

  function setCurrentCollection(collectionId) {
    // Only do something if the collection is actually changing
    if (collectionId === currentCollectionId.value) {
      return
    }

    currentCollectionId.value = collectionId
    // Persist selection
    localStorage.setItem('asymptote_current_collection', collectionId)

    // Clear search state when switching collections
    const searchStore = useSearchStore()
    searchStore.setQuery('')
    searchStore.clearResults()
    searchStore.syncCacheCount()  // Update cache count for new collection
  }

  function initializeFromStorage() {
    const saved = localStorage.getItem('asymptote_current_collection')
    if (saved) {
      currentCollectionId.value = saved
    }
  }

  // Initialize
  initializeFromStorage()

  return {
    // State
    collections,
    currentCollectionId,
    loading,
    error,
    // Computed
    currentCollection,
    sortedCollections,
    // Actions
    loadCollections,
    createCollection,
    updateCollection,
    deleteCollection,
    setCurrentCollection,
  }
})
