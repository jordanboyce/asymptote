import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import axios from 'axios'

export const useUserStore = defineStore('user', () => {
  const userId = ref('')
  const displayName = ref('')
  const multiUser = ref(false)
  const dbBackend = ref('sqlite')
  const loaded = ref(false)

  const isMultiUser = computed(() => multiUser.value)

  async function loadCurrentUser() {
    try {
      const response = await axios.get('/api/user/me')
      userId.value = response.data.user_id
      displayName.value = response.data.display_name
      multiUser.value = response.data.multi_user
      dbBackend.value = response.data.db_backend
      loaded.value = true
    } catch (err) {
      console.error('Failed to load user info:', err)
      // Fallback for single-user mode
      userId.value = 'default'
      displayName.value = 'Default User'
      multiUser.value = false
      dbBackend.value = 'sqlite'
      loaded.value = true
    }
  }

  return {
    userId,
    displayName,
    multiUser,
    dbBackend,
    loaded,
    isMultiUser,
    loadCurrentUser,
  }
})
