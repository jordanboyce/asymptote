import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import axios from 'axios'

export const useUserStore = defineStore('user', () => {
  const userId = ref('')
  const displayName = ref('')
  const multiUser = ref(false)
  const dbBackend = ref('sqlite')
  const loaded = ref(false)
  const authFailed = ref(false)

  const isMultiUser = computed(() => multiUser.value)

  async function loadCurrentUser() {
    try {
      const response = await axios.get('/api/user/me')
      userId.value = response.data.user_id
      displayName.value = response.data.display_name
      multiUser.value = response.data.multi_user
      dbBackend.value = response.data.db_backend
      authFailed.value = false
      loaded.value = true
    } catch (err) {
      // A 401 is not a transient failure — it means multi-user mode is on and
      // the request carried no verified identity. Falling back to a synthetic
      // "Default User" here used to make that look like a working single-user
      // session: the shell rendered, the header showed a signed-in name, and
      // then every data call 401'd with no explanation. Surface it instead.
      if (err?.response?.status === 401) {
        authFailed.value = true
        userId.value = ''
        displayName.value = ''
        multiUser.value = true
        loaded.value = true
        return
      }
      console.error('Failed to load user info:', err)
      // Any other error (network, 5xx) genuinely is transient/unknown; the
      // single-user default is the safe read since single-user mode never 401s.
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
    authFailed,
    isMultiUser,
    loadCurrentUser,
  }
})
