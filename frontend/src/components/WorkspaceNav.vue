<template>
  <nav :class="mobile ? 'flex items-stretch' : 'flex items-center gap-1'" aria-label="Workspace">
    <button
      v-for="tab in primaryTabs"
      :key="tab.id"
      :class="[buttonClass, activeTab === tab.id ? 'bg-base-200 font-semibold text-base-content' : 'text-base-content/65']"
      :aria-current="activeTab === tab.id ? 'page' : undefined"
      @click="$emit('navigate', tab.id)"
    >
      <component :is="tab.icon" :size="mobile ? 19 : 14" aria-hidden="true" />
      <span>{{ tab.label }}</span>
    </button>
    <details ref="menu" class="dropdown dropdown-end" :class="mobile ? 'dropdown-top flex-1' : ''" @keydown.esc="closeMenu">
      <summary :class="[buttonClass, 'list-none', mobile ? 'w-full h-full' : '', secondaryActive ? 'bg-base-200 font-semibold' : 'text-base-content/65']">
        <Ellipsis :size="mobile ? 19 : 14" aria-hidden="true" />
        <span>{{ secondaryLabel }}</span>
      </summary>
      <ul class="dropdown-content menu z-[70] w-56 rounded-box bg-base-100 border border-base-300 p-2 shadow-lg">
        <li v-for="tab in secondaryTabs" :key="tab.id">
          <button :aria-current="activeTab === tab.id ? 'page' : undefined" @click="navigate(tab.id)">
            <component :is="tab.icon" :size="15" aria-hidden="true" />{{ tab.label }}
          </button>
        </li>
        <li><button @click="openNotes"><StickyNote :size="15" aria-hidden="true" />Notes and tools</button></li>
      </ul>
    </details>
  </nav>
</template>

<script setup>
import { computed, ref } from 'vue'
import { MessageSquare, Search, Plug, Ellipsis, FileText, BookOpen, Gauge, StickyNote } from 'lucide-vue-next'

const props = defineProps({
  activeTab: { type: String, required: true },
  chatEnabled: { type: Boolean, default: true },
  adminConsole: { type: Boolean, default: false },
  mobile: { type: Boolean, default: false },
})
const emit = defineEmits(['navigate', 'notes'])
const menu = ref(null)
const buttonClass = computed(() => props.mobile
  ? 'flex-1 min-w-0 flex flex-col items-center justify-center gap-1 min-h-14 px-2 py-2 text-xs cursor-pointer'
  : 'btn btn-xs btn-ghost gap-1.5 rounded-md font-normal')
const primaryTabs = computed(() => [
  ...(props.chatEnabled ? [{ id: 'chat', label: 'Ask', icon: MessageSquare }] : []),
  { id: 'search', label: 'Find', icon: Search },
  { id: 'mcp', label: 'Connect', icon: Plug },
])
const secondaryTabs = computed(() => [
  { id: 'generate', label: 'Reports', icon: FileText },
  { id: 'expertise', label: 'Saved instructions', icon: BookOpen },
  ...(props.adminConsole ? [{ id: 'admin', label: 'Administration', icon: Gauge }] : []),
])
const secondaryActive = computed(() => secondaryTabs.value.some(tab => tab.id === props.activeTab))
const secondaryLabel = computed(() => secondaryTabs.value.find(tab => tab.id === props.activeTab)?.label || 'More')
function closeMenu() {
  if (menu.value) {
    menu.value.open = false
    menu.value.querySelector('summary')?.focus()
  }
}
function navigate(id) { closeMenu(); emit('navigate', id) }
function openNotes() { closeMenu(); emit('notes') }
</script>
