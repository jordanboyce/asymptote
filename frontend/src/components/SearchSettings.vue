<template>
  <dialog :ref="modal.dialogRef" class="modal modal-end" aria-labelledby="search-settings-title" @close="modal.onClosed">
    <div class="modal-box h-full max-h-full w-full max-w-md rounded-none p-0 flex flex-col">
      <header class="flex items-center justify-between gap-4 border-b border-base-300 p-5">
        <div>
          <h2 id="search-settings-title" class="text-lg font-semibold">Search settings</h2>
          <p class="text-sm text-base-content/75 mt-1">Applies to your next search.</p>
        </div>
        <button class="btn btn-ghost btn-circle min-h-11 min-w-11" aria-label="Close search settings" @click="modal.close()"><X :size="20" /></button>
      </header>
      <div class="flex-1 overflow-y-auto p-5 space-y-7">
        <fieldset class="space-y-4">
          <legend class="font-semibold px-0">AI assistance</legend>
          <p v-if="!configuredProviders.length" class="text-sm text-base-content/75 leading-relaxed">Search works without AI. <button class="link link-primary" @click="openProviderSettings">Connect a provider</button> to add answers and AI ranking.</p>
          <label class="flex items-start justify-between gap-4 cursor-pointer">
            <span><span class="block text-sm font-medium">Write an answer</span><span class="block text-sm text-base-content/75 mt-1">Summarize the matching passages with source references.</span></span>
            <input v-model="synthesize" type="checkbox" class="toggle toggle-primary shrink-0" :disabled="!configuredProviders.length" />
          </label>
          <label class="flex items-start justify-between gap-4 cursor-pointer">
            <span><span class="block text-sm font-medium">Improve result order</span><span class="block text-sm text-base-content/75 mt-1">Ask AI to put the most relevant passages first. Adds processing time.</span></span>
            <input v-model="rerank" type="checkbox" class="toggle toggle-primary shrink-0" :disabled="!configuredProviders.length" />
          </label>

          <div v-if="configuredProviders.length" class="space-y-3">
            <p class="text-sm font-medium">Providers <span class="font-normal text-base-content/75">· Select one or compare several</span></p>
            <div v-for="pid in configuredProviders" :key="pid" class="border border-base-300 rounded-lg p-3 space-y-3">
              <label class="flex items-center gap-3 cursor-pointer min-h-8">
                <input type="checkbox" class="checkbox checkbox-sm checkbox-primary" :checked="selectedProviders.includes(pid)" @change="toggleProvider(pid)" />
                <span class="text-sm font-medium flex-1">{{ getProviderDisplayName(pid) }}</span>
                <span class="text-xs text-base-content/75">{{ isLocalProvider(pid) ? 'Local' : 'Cloud' }}</span>
              </label>
              <label v-if="selectedProviders.includes(pid) && getProviderModels(pid).length" class="block text-sm">
                <span class="block mb-1 text-base-content/75">Model</span>
                <select class="select w-full" :value="modelOverrides[pid] || ''" :aria-label="`Model for ${getProviderDisplayName(pid)}`" @change="modelOverrides = { ...modelOverrides, [pid]: $event.target.value }">
                  <option value="">Provider default</option>
                  <option v-if="modelOverrides[pid] && !getProviderModels(pid).some(m => m.id === modelOverrides[pid])" :value="modelOverrides[pid]">{{ modelOverrides[pid] }}</option>
                  <option v-for="model in getProviderModels(pid)" :key="model.id" :value="model.id">{{ model.label }}</option>
                </select>
              </label>
            </div>
            <p v-if="!selectedProviders.length" class="text-sm text-base-content/75">No provider selected. Your next search will return passages without AI assistance.</p>
            <p v-else-if="(synthesize || rerank) && selectedProviders.some(pid => !isLocalProvider(pid))" class="text-sm text-base-content/75">Selected cloud providers receive your query and matching passages.</p>
          </div>
        </fieldset>

        <section class="border-t border-base-300 pt-5" aria-labelledby="search-tune-title">
          <h3 id="search-tune-title" class="font-semibold">Matching balance</h3>
          <template v-if="searchMode === 'hybrid'">
            <p class="text-sm text-base-content/75 mt-1 leading-relaxed">How much “Meaning + keywords” leans on related ideas versus exact words.</p>
            <label class="block mt-4">
              <span class="block text-sm font-medium mb-3">{{ Math.round(semanticWeight * 100) }}% meaning · {{ Math.round((1 - semanticWeight) * 100) }}% keywords</span>
              <input v-model.number="semanticWeight" type="range" min="0" max="1" step="0.1" aria-label="Meaning weight" class="range range-primary range-sm w-full" />
              <span class="flex justify-between text-sm text-base-content/75 mt-2"><span>More keywords</span><span>More meaning</span></span>
            </label>
          </template>
          <p v-else class="text-sm text-base-content/75 mt-1 leading-relaxed">Only applies to “Meaning + keywords”. Pick that mode in the toolbar to adjust the balance.</p>
        </section>
      </div>
      <footer class="border-t border-base-300 p-4 flex justify-end"><button class="btn btn-primary min-h-11" @click="modal.close()">Done</button></footer>
    </div>
    <form method="dialog" class="modal-backdrop"><button>Close search settings</button></form>
  </dialog>
</template>

<script setup>
import { watch } from 'vue'
import { X } from 'lucide-vue-next'
import { useModal } from '../composables/useModal'
import { getProviderDisplayName, getProviderModels, isLocalProvider } from '../utils/aiProviders'

// Mode and result count are set in the Find toolbar; this drawer holds the
// settings that need explanation: providers, models, and the hybrid balance.
defineProps({
  configuredProviders: { type: Array, default: () => [] },
  searchMode: { type: String, default: 'hybrid' },
})
const emit = defineEmits(['switch-tab'])
const open = defineModel('open', { type: Boolean, default: false })
const selectedProviders = defineModel('selectedProviders', { type: Array, default: () => [] })
const semanticWeight = defineModel('semanticWeight', { type: Number, default: 0.7 })
const rerank = defineModel('rerank', { type: Boolean, default: true })
const synthesize = defineModel('synthesize', { type: Boolean, default: true })
const modelOverrides = defineModel('modelOverrides', { type: Object, default: () => ({}) })
const modal = useModal({ onClose: () => { open.value = false } })
watch(open, value => value ? modal.open() : modal.close(), { flush: 'post' })
function toggleProvider(pid) {
  selectedProviders.value = selectedProviders.value.includes(pid) ? selectedProviders.value.filter(p => p !== pid) : [...selectedProviders.value, pid]
}
function openProviderSettings() {
  modal.close()
  emit('switch-tab', 'settings')
}
</script>
