import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import axios from 'axios'

export const useUserStore = defineStore('user', () => {
  const userId = ref('')
  const displayName = ref('')
  const privateCollections = ref(false)
  const dbBackend = ref('sqlite')
  const edgeAdmission = ref(false)
  const isAdmin = ref(false)
  const loaded = ref(false)

  const isPrivateMode = computed(() => privateCollections.value)
  // Only an admin on a deployment with edge admission configured can invite
  // someone who cannot already reach the app.
  const canInviteNewPeople = computed(() => edgeAdmission.value && isAdmin.value)

  async function loadCurrentUser() {
    try {
      const response = await axios.get('/api/user/me')
      userId.value = response.data.user_id
      displayName.value = response.data.display_name
      privateCollections.value = response.data.private_collections
      dbBackend.value = response.data.db_backend
      edgeAdmission.value = response.data.edge_admission || false
      isAdmin.value = response.data.is_admin || false
      loaded.value = true
    } catch (err) {
      console.error('Failed to load user info:', err)
      // Fallback for single-user mode
      userId.value = 'default'
      displayName.value = 'Default User'
      privateCollections.value = false
      dbBackend.value = 'sqlite'
      edgeAdmission.value = false
      isAdmin.value = false
      loaded.value = true
    }
  }

  return {
    userId,
    displayName,
    privateCollections,
    dbBackend,
    edgeAdmission,
    isAdmin,
    loaded,
    isPrivateMode,
    canInviteNewPeople,
    loadCurrentUser,
  }
})
