import { ref } from 'vue'
import { defineStore } from 'pinia'
import http from '../utils/http'

export const useExpertiseStore = defineStore('expertise', () => {
  // ── State ────────────────────────────────────────────────────────────────
  const packs = ref([])          // all expertise packs (library)
  const loading = ref(false)
  const error = ref(null)

  // Per-collection attachment cache: collectionId → [packId, ...]
  const attachedPackIds = ref({})

  // ── Pack library actions ─────────────────────────────────────────────────

  async function fetchPacks() {
    loading.value = true
    error.value = null
    try {
      const res = await http.get('/api/expertise/packs')
      packs.value = res.data
    } catch (err) {
      error.value = err.response?.data?.detail || err.message
    } finally {
      loading.value = false
    }
  }

  async function createPack(data) {
    // data: { name, description, body }
    const res = await http.post('/api/expertise/packs', data)
    packs.value.push(res.data)
    packs.value.sort((a, b) => a.name.localeCompare(b.name))
    return res.data
  }

  async function updatePack(packId, data) {
    const res = await http.put(`/api/expertise/packs/${packId}`, data)
    const idx = packs.value.findIndex(p => p.id === packId)
    if (idx !== -1) packs.value[idx] = res.data
    packs.value.sort((a, b) => a.name.localeCompare(b.name))
    return res.data
  }

  async function deletePack(packId) {
    await http.delete(`/api/expertise/packs/${packId}`)
    packs.value = packs.value.filter(p => p.id !== packId)
    // Remove from all cached attachment lists
    for (const collId in attachedPackIds.value) {
      attachedPackIds.value[collId] = attachedPackIds.value[collId].filter(id => id !== packId)
    }
  }

  // ── Collection attachment actions ────────────────────────────────────────

  async function fetchAttached(collectionId) {
    const res = await http.get(`/api/collections/${collectionId}/expertise`)
    attachedPackIds.value[collectionId] = res.data.packs.map(p => p.id)
    return attachedPackIds.value[collectionId]
  }

  async function setAttached(collectionId, packIds) {
    const res = await http.put(`/api/collections/${collectionId}/expertise`, { pack_ids: packIds })
    attachedPackIds.value[collectionId] = res.data.packs.map(p => p.id)
    return attachedPackIds.value[collectionId]
  }

  function getAttachedPackObjects(collectionId) {
    const ids = attachedPackIds.value[collectionId] || []
    return packs.value.filter(p => ids.includes(p.id))
  }

  return {
    packs,
    loading,
    error,
    attachedPackIds,
    fetchPacks,
    createPack,
    updatePack,
    deletePack,
    fetchAttached,
    setAttached,
    getAttachedPackObjects,
  }
})
