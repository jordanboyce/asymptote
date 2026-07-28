<template>
  <div class="flex flex-col h-full">

    <!-- Header bar -->
    <div class="flex items-center gap-2 px-3 py-2.5 border-b border-base-300 flex-shrink-0 bg-base-100" role="region" aria-label="Studio">
      <Sparkles :size="15" class="text-base-content/60 flex-shrink-0" aria-hidden="true" />
      <span class="font-semibold text-sm flex-1">Studio</span>
      <button
        class="btn btn-ghost btn-xs btn-circle"
        @click="$emit('close')"
        title="Close Studio sidebar"
        aria-label="Close Studio sidebar"
      >
        <X :size="13" />
      </button>
    </div>

    <!-- Scrollable body -->
    <div class="flex-1 overflow-y-auto">

      <!-- Generate -->
      <section class="border-b border-base-300">
        <button
          class="w-full flex items-center gap-2 px-3 py-2.5 hover:bg-base-200 transition-colors text-left"
          @click="open.generate = !open.generate"
          :aria-expanded="open.generate"
        >
          <FileText :size="13" class="text-primary flex-shrink-0" />
          <span class="text-xs font-semibold flex-1">Generate</span>
          <ChevronDown :size="12" class="text-base-content/40 transition-transform" :class="open.generate ? 'rotate-180' : ''" />
        </button>
        <div v-show="open.generate" class="px-3 pb-3 space-y-1.5">
          <p class="text-[11px] text-base-content/50 leading-snug">
            Turn this collection into a source-grounded document — every claim cites its sources.
          </p>
          <div class="flex flex-col gap-1.5">
            <button class="btn btn-outline btn-xs gap-1" @click="$emit('switch-tab', 'generate')">
              <Sparkles :size="11" />
              Open Generate
            </button>
            <button class="btn btn-outline btn-xs gap-1" @click="sendToChat('Give me a briefing of this collection: the key themes, what each source covers, and any open questions, with citations.')">
              <MessageSquare :size="11" />
              Collection briefing
            </button>
          </div>
        </div>
      </section>

      <!-- Structured Tables -->
      <section class="border-b border-base-300">
        <button
          class="w-full flex items-center gap-2 px-3 py-2.5 hover:bg-base-200 transition-colors text-left"
          @click="open.tables = !open.tables"
          :aria-expanded="open.tables"
        >
          <Table2 :size="13" class="text-primary flex-shrink-0" />
          <span class="text-xs font-semibold flex-1">Structured Tables</span>
          <ChevronDown :size="12" class="text-base-content/40 transition-transform" :class="open.tables ? 'rotate-180' : ''" />
        </button>
        <div v-show="open.tables" class="px-3 pb-3 space-y-1.5">
          <p class="text-[11px] text-base-content/50 leading-snug">
            Query a CSV/Excel table loaded into this collection in plain English.
          </p>
          <div class="flex gap-1.5">
            <button class="btn btn-outline btn-xs flex-1 gap-1" @click="sendToChat('List all structured tables in this collection and describe their schemas.')">
              <List :size="11" />
              List tables
            </button>
            <button class="btn btn-outline btn-xs flex-1 gap-1" @click="sendToChat('Show me a 10-row sample from each structured table in this collection.')">
              <Eye :size="11" />
              Preview
            </button>
          </div>
        </div>
      </section>

      <!-- Notes -->
      <section class="border-b border-base-300">
        <button
          class="w-full flex items-center gap-2 px-3 py-2.5 hover:bg-base-200 transition-colors text-left"
          @click="open.notes = !open.notes"
          :aria-expanded="open.notes"
        >
          <StickyNote :size="13" class="text-primary flex-shrink-0" />
          <span class="text-xs font-semibold flex-1">Notes</span>
          <span v-if="notes.trim()" class="badge badge-xs badge-neutral">{{ noteCount }}</span>
          <ChevronDown :size="12" class="text-base-content/40 transition-transform" :class="open.notes ? 'rotate-180' : ''" />
        </button>
        <div v-show="open.notes" class="px-3 pb-3 space-y-1.5">
          <p class="text-[11px] text-base-content/50 leading-snug">
            Scratch pad scoped to this collection. Saved locally.
          </p>
          <textarea
            v-model="notes"
            class="textarea textarea-bordered textarea-xs w-full text-xs leading-snug min-h-[120px]"
            placeholder="Jot down findings, hypotheses, follow-ups…"
          ></textarea>
        </div>
      </section>

      <!-- Export -->
      <section>
        <button
          class="w-full flex items-center gap-2 px-3 py-2.5 hover:bg-base-200 transition-colors text-left"
          @click="open.export = !open.export"
          :aria-expanded="open.export"
        >
          <Download :size="13" class="text-primary flex-shrink-0" />
          <span class="text-xs font-semibold flex-1">Export</span>
          <ChevronDown :size="12" class="text-base-content/40 transition-transform" :class="open.export ? 'rotate-180' : ''" />
        </button>
        <div v-show="open.export" class="px-3 pb-3 space-y-1.5">
          <button class="btn btn-outline btn-xs w-full gap-1" @click="exportNotes">
            <FileText :size="11" />
            Notes (.md)
          </button>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch } from 'vue'
import {
  X, ChevronDown, Table2, StickyNote,
  Download, Sparkles, FileText, List, Eye, MessageSquare,
} from 'lucide-vue-next'
import { useCollectionStore } from '../stores/collectionStore'

const emit = defineEmits(['close', 'send-to-chat', 'switch-tab'])
const collectionStore = useCollectionStore()

const open = reactive({
  generate: true,
  tables: true,
  notes: false,
  export: false,
})

const sendToChat = (prompt) => {
  emit('send-to-chat', prompt)
}

// Notes (per-collection localStorage)
const notesKey = computed(() => `analysis_notes:${collectionStore.currentCollectionId || 'default'}`)
const notes = ref(localStorage.getItem(notesKey.value) || '')
const noteCount = computed(() => notes.value.trim().split(/\s+/).filter(Boolean).length)

watch(notesKey, (key) => {
  notes.value = localStorage.getItem(key) || ''
})
watch(notes, (v) => {
  localStorage.setItem(notesKey.value, v)
})

const exportNotes = () => {
  const name = collectionStore.currentCollection?.name || 'collection'
  const blob = new Blob([notes.value || ''], { type: 'text/markdown' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${name.replace(/[^a-z0-9-_]+/gi, '_')}-notes.md`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}
</script>
