import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import axios from 'axios'
import { useSearchStore } from './searchStore'

export const useCollectionStore = defineStore('collection', () => {
  // State
  const collections = ref([])
  // No hard-coded default — in multi-user mode the "default" collection is
  // owned by the single-user sentinel and not accessible to authenticated
  // users. We populate this from localStorage or the first response.
  const currentCollectionId = ref('')
  const loading = ref(false)
  const error = ref(null)
  const multiUser = ref(false)
  const userId = ref('')

  // Computed
  const currentCollection = computed(() => {
    return collections.value.find(c => c.id === currentCollectionId.value) || null
  })

  const sortedCollections = computed(() => {
    // Owned first (default at top), then shared
    const owned = collections.value.filter(c => !c.shared)
    const shared = collections.value.filter(c => c.shared)
    owned.sort((a, b) => {
      if (a.id === 'default') return -1
      if (b.id === 'default') return 1
      return a.name.localeCompare(b.name)
    })
    shared.sort((a, b) => a.name.localeCompare(b.name))
    return [...owned, ...shared]
  })

  const ownedCollections = computed(() => collections.value.filter(c => !c.shared))
  const sharedCollections = computed(() => collections.value.filter(c => c.shared))

  const currentPermission = computed(() => {
    const c = currentCollection.value
    return c?.permission || 'owner'
  })

  const canEditCurrent = computed(() => {
    return currentPermission.value === 'owner' || currentPermission.value === 'readwrite'
  })

  // Actions
  async function loadCollections() {
    loading.value = true
    error.value = null
    try {
      const response = await axios.get('/api/collections')
      collections.value = response.data.collections || []
      multiUser.value = response.data.multi_user || false
      userId.value = response.data.user_id || ''

      // Ensure current collection still exists. If not, fall back to the
      // first available — works for both single-user (where "default" is
      // present) and multi-user (where the user's auto-provisioned starter
      // collection has a UUID-style ID).
      const exists = collections.value.some(c => c.id === currentCollectionId.value)
      if (!exists) {
        currentCollectionId.value = collections.value[0]?.id || ''
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

      // If we deleted the current collection, switch to the first one left
      // (or empty if the user has no more collections).
      if (currentCollectionId.value === collectionId) {
        currentCollectionId.value = collections.value[0]?.id || ''
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
    localStorage.setItem('finn_current_collection', collectionId)

    // Clear search state when switching collections
    const searchStore = useSearchStore()
    searchStore.setQuery('')
    searchStore.clearResults()
    searchStore.syncCacheCount()  // Update cache count for new collection
  }

  function initializeFromStorage() {
    const saved = localStorage.getItem('finn_current_collection')
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
    multiUser,
    userId,
    // Computed
    currentCollection,
    sortedCollections,
    ownedCollections,
    sharedCollections,
    currentPermission,
    canEditCurrent,
    // Actions
    loadCollections,
    createCollection,
    updateCollection,
    deleteCollection,
    setCurrentCollection,
  }
})
