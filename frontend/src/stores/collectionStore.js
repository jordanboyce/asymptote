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

  // ── Collection Groups (v4.5) ─────────────────────────────────────────────

  const groups = ref([])
  const currentGroupId = ref(null)

  const currentGroup = computed(() =>
    groups.value.find(g => g.id === currentGroupId.value) || null
  )

  async function loadGroups() {
    try {
      const res = await axios.get('/api/groups')
      groups.value = res.data.groups || []
    } catch (err) {
      console.error('Failed to load groups:', err)
    }
  }

  async function createGroup(name, color = '#8b5cf6') {
    const res = await axios.post('/api/groups', { name, color })
    await loadGroups()
    return res.data
  }

  async function updateGroup(groupId, updates) {
    const res = await axios.patch(`/api/groups/${groupId}`, updates)
    await loadGroups()
    return res.data
  }

  async function deleteGroup(groupId) {
    await axios.delete(`/api/groups/${groupId}`)
    if (currentGroupId.value === groupId) currentGroupId.value = null
    await loadGroups()
  }

  async function addCollectionToGroup(groupId, collectionId) {
    await axios.post(`/api/groups/${groupId}/members/${collectionId}`)
    await loadGroups()
  }

  async function removeCollectionFromGroup(groupId, collectionId) {
    await axios.delete(`/api/groups/${groupId}/members/${collectionId}`)
    await loadGroups()
  }

  function setCurrentGroup(groupId) {
    currentGroupId.value = groupId
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
    groups,
    currentGroupId,
    // Computed
    currentCollection,
    sortedCollections,
    ownedCollections,
    sharedCollections,
    currentPermission,
    canEditCurrent,
    currentGroup,
    // Actions
    loadCollections,
    createCollection,
    updateCollection,
    deleteCollection,
    setCurrentCollection,
    loadGroups,
    createGroup,
    updateGroup,
    deleteGroup,
    addCollectionToGroup,
    removeCollectionFromGroup,
    setCurrentGroup,
  }
})
