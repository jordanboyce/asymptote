<template>
  <div
    v-if="show && filtered.length > 0"
    class="slash-picker absolute left-0 right-0 bottom-full mb-2 z-50 rounded-xl border border-base-300 bg-base-100 shadow-lg overflow-hidden"
    role="listbox"
    aria-label="Slash commands"
  >
    <div class="px-3 py-1.5 text-[10px] uppercase tracking-wide text-base-content/50 bg-base-200/60 border-b border-base-300">
      Slash commands
    </div>
    <ul class="max-h-56 overflow-y-auto py-1">
      <li
        v-for="([cmd, desc], i) in filtered"
        :key="cmd"
        :id="`slash-option-${i}`"
        role="option"
        :aria-selected="i === activeIndex"
        class="px-3 py-2 cursor-pointer flex items-center gap-3 text-sm"
        :class="i === activeIndex ? 'bg-primary/15 text-primary' : 'hover:bg-base-200'"
        @mousedown.prevent="select(cmd)"
        @mouseenter="activeIndex = i"
      >
        <code class="font-mono text-xs px-1.5 py-0.5 rounded bg-base-200 border border-base-300 text-base-content">{{ cmd }}</code>
        <span class="text-base-content/70 text-xs">{{ desc }}</span>
      </li>
    </ul>
    <div class="px-3 py-1.5 text-[10px] text-base-content/40 bg-base-200/60 border-t border-base-300">
      ↑↓ navigate · ↵ select · Esc close
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { filterCommands } from '../utils/slashCommands'

const props = defineProps({
  show: { type: Boolean, default: false },
  modelValue: { type: String, default: '' },
  // Per-Collection kind from /api/collections/{id}/summary. Drives which
  // financial-only commands (/brief, /tlh) the picker offers. Defaults to
  // null → no filtering → all commands surface (back-compat).
  collectionKind: { type: String, default: null },
})

const emit = defineEmits(['select', 'close'])

const activeIndex = ref(0)

const filtered = computed(() =>
  filterCommands(props.modelValue, { collectionKind: props.collectionKind }),
)

watch(
  () => props.modelValue,
  () => {
    activeIndex.value = 0
  },
)
watch(
  () => props.show,
  (v) => {
    if (v) activeIndex.value = 0
  },
)

const select = (cmd) => {
  emit('select', cmd)
}

// Keyboard handlers exposed for parent to call from their input keydown.
// Returns true if the key was consumed.
const handleKeydown = (e) => {
  if (!props.show || filtered.value.length === 0) return false
  if (e.key === 'ArrowDown') {
    activeIndex.value = (activeIndex.value + 1) % filtered.value.length
    e.preventDefault()
    return true
  }
  if (e.key === 'ArrowUp') {
    activeIndex.value =
      (activeIndex.value - 1 + filtered.value.length) % filtered.value.length
    e.preventDefault()
    return true
  }
  if (e.key === 'Enter' || e.key === 'Tab') {
    const entry = filtered.value[activeIndex.value]
    if (entry) {
      select(entry[0])
      e.preventDefault()
      return true
    }
  }
  if (e.key === 'Escape') {
    emit('close')
    e.preventDefault()
    return true
  }
  return false
}

defineExpose({ handleKeydown })
</script>
