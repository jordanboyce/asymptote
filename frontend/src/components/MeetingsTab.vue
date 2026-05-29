<template>
  <div class="flex flex-col gap-5 max-w-6xl mx-auto">

    <!-- Header -->
    <header class="flex items-end justify-between gap-4 flex-wrap pb-3 border-b border-base-300/60">
      <div class="min-w-0">
        <h1 class="text-[22px] leading-none font-semibold tracking-tight flex items-center gap-2">
          <Mic :size="20" class="text-primary" aria-hidden="true" />
          Meetings
        </h1>
        <p class="mt-2 text-xs text-base-content/55">
          <span v-if="collectionName" class="font-medium text-base-content/70">{{ collectionName }}</span>
          <span v-if="collectionName" class="mx-1.5 text-base-content/25">·</span>
          <span v-if="!loading">
            {{ meetings.length }} meeting{{ meetings.length === 1 ? '' : 's' }} ·
            {{ openCount }} open action item{{ openCount === 1 ? '' : 's' }}
          </span>
          <span v-else>Loading…</span>
        </p>
      </div>
      <div class="flex items-center gap-2">
        <button
          class="btn btn-sm btn-ghost gap-1.5"
          @click="reload"
          :disabled="loading"
          title="Refresh meetings and action items"
        >
          <RefreshCw :size="13" :class="{ 'animate-spin': loading }" />
          <span class="hidden sm:inline">Refresh</span>
        </button>
        <button
          class="btn btn-sm btn-primary gap-1.5"
          @click="openCreateModal"
          :disabled="!collectionId"
          title="Create a new action item"
        >
          <Plus :size="13" />
          New action item
        </button>
      </div>
    </header>

    <!-- No collection -->
    <div v-if="!collectionId" class="text-center py-20 text-sm text-base-content/55">
      Select a collection from the header to see meetings.
    </div>

    <!-- Error -->
    <div v-else-if="error" role="alert" class="alert alert-error">
      <AlertTriangle :size="18" aria-hidden="true" />
      <span>{{ error }}</span>
    </div>

    <!-- Loading skeleton (first load only) -->
    <div v-else-if="loading && !loaded" class="flex flex-col gap-3">
      <div v-for="i in 3" :key="i" class="rounded-lg border border-base-300 bg-base-100 h-20 animate-pulse"></div>
    </div>

    <template v-else>

      <!-- Action items panel — surfaced above the meetings list because the
           open list is the daily-driver and meetings is mostly archival. -->
      <section>
        <div class="flex items-center justify-between mb-2">
          <h2 class="text-sm font-semibold uppercase tracking-wider text-base-content/70">
            Action items
          </h2>
          <div class="flex items-center gap-2">
            <label class="text-xs text-base-content/55 flex items-center gap-1.5">
              <input
                type="checkbox"
                class="checkbox checkbox-xs"
                v-model="showClosed"
              />
              Show closed
            </label>
          </div>
        </div>

        <div v-if="!filteredActionItems.length" class="rounded-lg border border-dashed border-base-300 px-4 py-6 text-center text-sm text-base-content/55">
          {{ showClosed ? 'No action items yet.' : 'No open action items. Nice.' }}
        </div>

        <ul v-else class="flex flex-col gap-1.5">
          <li
            v-for="item in filteredActionItems"
            :key="item.item_id"
            class="rounded-lg border border-base-300 bg-base-100 px-3 py-2 hover:border-base-content/30 transition-colors"
            :class="{ 'opacity-60': item.status === 'closed' }"
          >
            <div class="flex items-start gap-2">
              <input
                type="checkbox"
                class="checkbox checkbox-sm mt-0.5"
                :checked="item.status === 'closed'"
                @change="toggleStatus(item)"
                :aria-label="item.status === 'closed' ? 'Mark open' : 'Mark closed'"
                :disabled="busyItemIds.has(item.item_id)"
              />
              <div class="flex-1 min-w-0">
                <div class="flex items-start justify-between gap-2">
                  <p class="text-sm text-base-content/90 break-words" :class="{ 'line-through text-base-content/55': item.status === 'closed' }">
                    {{ item.description }}
                  </p>
                  <div class="flex items-center gap-1 flex-shrink-0">
                    <button
                      v-if="item.origin !== 'meeting'"
                      class="btn btn-ghost btn-xs btn-circle"
                      @click="openEditModal(item)"
                      title="Edit"
                    >
                      <Pencil :size="12" />
                    </button>
                    <button
                      v-if="item.origin !== 'meeting'"
                      class="btn btn-ghost btn-xs btn-circle text-base-content/55 hover:text-error"
                      @click="confirmDelete(item)"
                      title="Delete"
                      :disabled="busyItemIds.has(item.item_id)"
                    >
                      <Trash2 :size="12" />
                    </button>
                  </div>
                </div>
                <div class="text-[11px] text-base-content/55 flex flex-wrap items-center gap-x-2 mt-0.5">
                  <span
                    class="badge badge-xs"
                    :class="originBadgeClass(item.origin)"
                    :title="originTitle(item)"
                  >
                    {{ originLabel(item.origin) }}
                  </span>
                  <span v-if="item.assignee">{{ item.assignee }}</span>
                  <span v-if="item.due_date">due {{ item.due_date }}</span>
                  <span v-if="item.source_filename" class="truncate max-w-[20rem]">
                    from {{ item.source_filename }}
                  </span>
                </div>
                <div v-if="item.source_excerpt" class="mt-1 text-[11px] text-base-content/55 italic border-l border-base-300 pl-2 line-clamp-2">
                  {{ item.source_excerpt }}
                </div>
              </div>
            </div>
          </li>
        </ul>
      </section>

      <!-- Meetings list -->
      <section>
        <h2 class="text-sm font-semibold uppercase tracking-wider text-base-content/70 mb-2">
          Meeting history
        </h2>
        <div v-if="!meetings.length" class="rounded-lg border border-dashed border-base-300 px-4 py-6 text-center text-sm text-base-content/55">
          No meeting transcripts have been processed yet. Record a meeting from the header or upload an audio file from Sources.
        </div>
        <ul v-else class="flex flex-col gap-2">
          <li v-for="m in meetings" :key="m.document_id">
            <details class="rounded-lg border border-base-300 bg-base-100 overflow-hidden">
              <summary class="px-3 py-2 flex items-center justify-between gap-3 cursor-pointer hover:bg-base-200/40 list-none">
                <div class="min-w-0 flex-1">
                  <div class="text-sm font-medium truncate">
                    {{ m.filename || m.document_id }}
                  </div>
                  <div class="text-[11px] text-base-content/55 flex flex-wrap items-center gap-x-2 mt-0.5">
                    <span v-if="m.extracted_at">{{ formatDate(m.extracted_at) }}</span>
                    <span v-if="m.action_items_count">
                      {{ m.open_action_items_count }} open / {{ m.action_items_count }} action items
                    </span>
                    <span v-if="m.decisions_count">{{ m.decisions_count }} decisions</span>
                    <span v-if="m.client_concerns_count">{{ m.client_concerns_count }} concerns</span>
                  </div>
                </div>
                <ChevronDown :size="14" class="text-base-content/55 flex-shrink-0" />
              </summary>
              <MeetingDetail :collection-id="collectionId" :document-id="m.document_id" />
            </details>
          </li>
        </ul>
      </section>
    </template>

    <!-- Create / edit action item modal -->
    <dialog ref="itemModalRef" class="modal" @close="onModalClose">
      <div class="modal-box max-w-md">
        <h3 class="font-semibold text-base mb-3">
          {{ modalMode === 'edit' ? 'Edit action item' : 'New action item' }}
        </h3>
        <form @submit.prevent="submitModal" class="flex flex-col gap-3">
          <label class="form-control">
            <span class="label-text text-xs mb-1">Description</span>
            <textarea
              v-model="modalDescription"
              class="textarea textarea-bordered textarea-sm w-full"
              rows="3"
              placeholder="What needs to be done?"
              required
              maxlength="500"
            ></textarea>
          </label>
          <div class="grid grid-cols-2 gap-2">
            <label class="form-control">
              <span class="label-text text-xs mb-1">Assignee</span>
              <input
                v-model="modalAssignee"
                type="text"
                class="input input-bordered input-sm"
                placeholder="advisor / client / name"
                maxlength="80"
              />
            </label>
            <label class="form-control">
              <span class="label-text text-xs mb-1">Due date</span>
              <input
                v-model="modalDueDate"
                type="text"
                class="input input-bordered input-sm"
                placeholder="2026-06-15 or 'next meeting'"
                maxlength="80"
              />
            </label>
          </div>
          <div v-if="modalError" class="alert alert-error text-xs py-2">
            {{ modalError }}
          </div>
          <div class="modal-action mt-1">
            <button type="button" class="btn btn-ghost btn-sm" @click="closeModal" :disabled="modalSaving">
              Cancel
            </button>
            <button type="submit" class="btn btn-primary btn-sm" :disabled="modalSaving || !modalDescription.trim()">
              {{ modalSaving ? 'Saving…' : (modalMode === 'edit' ? 'Save' : 'Create') }}
            </button>
          </div>
        </form>
      </div>
      <form method="dialog" class="modal-backdrop">
        <button>close</button>
      </form>
    </dialog>

  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import {
  AlertTriangle,
  ChevronDown,
  Mic,
  Pencil,
  Plus,
  RefreshCw,
  Trash2,
} from 'lucide-vue-next'
import { useCollectionStore } from '../stores/collectionStore'
import {
  listMeetings,
  listActionItems,
  createActionItem,
  updateActionItem,
  deleteActionItem,
} from '../utils/meetingsApi'
import MeetingDetail from './MeetingDetail.vue'

const collectionStore = useCollectionStore()

const collectionId = computed(() => collectionStore.currentCollectionId)
const collectionName = computed(() => collectionStore.currentCollection?.name || '')

const meetings = ref([])
const actionItems = ref([])
const loading = ref(false)
const loaded = ref(false)
const error = ref('')
const showClosed = ref(false)

// Track in-flight per-item edits so the row's controls disable themselves
// while the network call is outstanding (prevents double-clicks toggling
// status back to where it started).
const busyItemIds = ref(new Set())

const openCount = computed(
  () => actionItems.value.filter(it => it.status === 'open').length,
)

const filteredActionItems = computed(() => {
  if (showClosed.value) return actionItems.value
  return actionItems.value.filter(it => it.status === 'open')
})

const reload = async () => {
  if (!collectionId.value) return
  loading.value = true
  error.value = ''
  try {
    const [m, ai] = await Promise.all([
      listMeetings(collectionId.value),
      // status=all so the "Show closed" toggle is a pure client filter and
      // we don't need a round-trip to switch.
      listActionItems(collectionId.value, { status: 'all', limit: 200 }),
    ])
    meetings.value = m.meetings || []
    actionItems.value = ai.items || []
    loaded.value = true
  } catch (e) {
    error.value = e?.response?.data?.detail || e?.message || 'Failed to load meetings.'
  } finally {
    loading.value = false
  }
}

watch(collectionId, () => {
  // Reset before reloading so a slower-loading next collection doesn't
  // briefly render the previous one's state.
  meetings.value = []
  actionItems.value = []
  loaded.value = false
  reload()
})

onMounted(() => {
  reload()
})

// ── Status toggle ─────────────────────────────────────────────────────────

const toggleStatus = async (item) => {
  if (!collectionId.value) return
  const next = item.status === 'closed' ? 'open' : 'closed'
  busyItemIds.value.add(item.item_id)
  // Optimistic update — server confirms or we revert below.
  const previousStatus = item.status
  item.status = next
  try {
    await updateActionItem(collectionId.value, item.item_id, { status: next })
  } catch (e) {
    console.warn('Failed to update action item:', e)
    item.status = previousStatus
    error.value = 'Could not update that action item. Please try again.'
  } finally {
    busyItemIds.value.delete(item.item_id)
  }
}

// ── Create / edit modal ───────────────────────────────────────────────────

const itemModalRef = ref(null)
const modalMode = ref('create')  // 'create' | 'edit'
const modalEditingItem = ref(null)
const modalDescription = ref('')
const modalAssignee = ref('')
const modalDueDate = ref('')
const modalSaving = ref(false)
const modalError = ref('')

const openCreateModal = () => {
  modalMode.value = 'create'
  modalEditingItem.value = null
  modalDescription.value = ''
  modalAssignee.value = ''
  modalDueDate.value = ''
  modalError.value = ''
  itemModalRef.value?.showModal()
}

const openEditModal = (item) => {
  modalMode.value = 'edit'
  modalEditingItem.value = item
  modalDescription.value = item.description || ''
  modalAssignee.value = item.assignee || ''
  modalDueDate.value = item.due_date || ''
  modalError.value = ''
  itemModalRef.value?.showModal()
}

const closeModal = () => {
  itemModalRef.value?.close()
}

const onModalClose = () => {
  modalSaving.value = false
  modalError.value = ''
}

const submitModal = async () => {
  if (!collectionId.value) return
  const desc = modalDescription.value.trim()
  if (!desc) {
    modalError.value = 'Description is required.'
    return
  }
  modalSaving.value = true
  modalError.value = ''
  try {
    if (modalMode.value === 'edit' && modalEditingItem.value) {
      const updated = await updateActionItem(
        collectionId.value,
        modalEditingItem.value.item_id,
        {
          description: desc,
          assignee: modalAssignee.value.trim() || '',
          due_date: modalDueDate.value.trim() || '',
        },
      )
      // Splice in place so the order stays stable.
      const idx = actionItems.value.findIndex(it => it.item_id === modalEditingItem.value.item_id)
      if (idx >= 0) actionItems.value.splice(idx, 1, updated)
    } else {
      const created = await createActionItem(collectionId.value, {
        description: desc,
        assignee: modalAssignee.value.trim() || null,
        due_date: modalDueDate.value.trim() || null,
        status: 'open',
        source_kind: 'manual',
      })
      actionItems.value.unshift(created)
    }
    closeModal()
  } catch (e) {
    modalError.value = e?.response?.data?.detail || e?.message || 'Save failed.'
  } finally {
    modalSaving.value = false
  }
}

// ── Delete ────────────────────────────────────────────────────────────────

const confirmDelete = async (item) => {
  if (!collectionId.value) return
  const ok = window.confirm(`Delete "${item.description}"?`)
  if (!ok) return
  busyItemIds.value.add(item.item_id)
  try {
    await deleteActionItem(collectionId.value, item.item_id)
    actionItems.value = actionItems.value.filter(it => it.item_id !== item.item_id)
  } catch (e) {
    error.value = e?.response?.data?.detail || e?.message || 'Delete failed.'
  } finally {
    busyItemIds.value.delete(item.item_id)
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────

const originLabel = (origin) => {
  if (origin === 'meeting') return 'Meeting'
  if (origin === 'chat') return 'Chat'
  return 'Manual'
}

const originBadgeClass = (origin) => {
  if (origin === 'meeting') return 'badge-outline'
  if (origin === 'chat') return 'badge-primary badge-outline'
  return 'badge-ghost'
}

const originTitle = (item) => {
  if (item.origin === 'meeting' && item.source_filename) {
    return `From transcript: ${item.source_filename}`
  }
  if (item.origin === 'chat') {
    return 'Saved from a chat answer'
  }
  return 'Created manually'
}

const formatDate = (iso) => {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return iso
    return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
  } catch {
    return iso
  }
}
</script>
