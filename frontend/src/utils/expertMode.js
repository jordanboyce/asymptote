import { ref, computed } from 'vue'

const STORAGE_KEY = 'asymptote_expert_mode'

const _expert = ref(localStorage.getItem(STORAGE_KEY) === 'true')

export const isExpertMode = computed(() => _expert.value)

export function toggleExpertMode() {
  _expert.value = !_expert.value
  localStorage.setItem(STORAGE_KEY, String(_expert.value))
}
