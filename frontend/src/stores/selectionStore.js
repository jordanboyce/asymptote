import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { useCollectionStore } from './collectionStore'

// The user's source selection: which documents of the current collection a
// conversation (and a generated report) is limited to.
//
// The sidebar checkboxes write it; Chat and Reports read it and send the
// ids with every request, where the backend enforces them end-to-end
// (retrieval, tool calls, tables, overview, cache key). It lives here
// rather than in the sidebar because the sidebar and the chat surface are
// siblings, and because the selection must survive the sidebar being
// closed. Kept per collection and in sessionStorage: it is a working
// scope for this sitting, not a preference.
const STORAGE_KEY = 'clio_source_selection'

function load() {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    const parsed = raw ? JSON.parse(raw) : {}
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

export const useSelectionStore = defineStore('selection', () => {
  // { [collectionId]: string[] }
  const byCollection = ref(load())

  function persist() {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(byCollection.value))
    } catch {
      // Storage is a convenience; the in-memory copy is authoritative.
    }
  }

  function idsFor(collectionId) {
    return byCollection.value[collectionId] || []
  }

  const currentIds = computed(() => idsFor(useCollectionStore().currentCollectionId))
  const count = computed(() => currentIds.value.length)
  const active = computed(() => count.value > 0)

  function isSelected(documentId, collectionId = useCollectionStore().currentCollectionId) {
    return idsFor(collectionId).includes(documentId)
  }

  function set(ids, collectionId = useCollectionStore().currentCollectionId) {
    const unique = Array.from(new Set(ids))
    if (unique.length) byCollection.value = { ...byCollection.value, [collectionId]: unique }
    else {
      const next = { ...byCollection.value }
      delete next[collectionId]
      byCollection.value = next
    }
    persist()
  }

  function toggle(documentId, collectionId = useCollectionStore().currentCollectionId) {
    const ids = idsFor(collectionId)
    set(ids.includes(documentId) ? ids.filter(id => id !== documentId) : [...ids, documentId], collectionId)
  }

  function remove(documentIds, collectionId = useCollectionStore().currentCollectionId) {
    const drop = new Set(documentIds)
    set(idsFor(collectionId).filter(id => !drop.has(id)), collectionId)
  }

  function clear(collectionId = useCollectionStore().currentCollectionId) {
    set([], collectionId)
  }

  return { byCollection, currentIds, count, active, idsFor, isSelected, set, toggle, remove, clear }
})
