import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import http from '../utils/http'

export const useUserStore = defineStore('user', () => {
  const userId = ref('')
  const displayName = ref('')
  const privateCollections = ref(false)
  const dbBackend = ref('sqlite')
  const edgeAdmission = ref(false)
  const isAdmin = ref(false)
  // Whether the Admin tab is available: admins under private collections,
  // everyone on an open (shared-appliance) deployment.
  const adminConsole = ref(false)
  const loaded = ref(false)

  // Governance state from /api/user/me: whether this person still has to
  // accept the acceptable-use policy, what the scanner does on upload, and
  // the label vocabulary (server-authoritative, mirrored in utils/governance).
  const aup = ref({ enabled: false, required: false, version: '1', accepted: false, accepted_at: null })
  const contentPolicyAction = ref('flag')
  const sensitivityLevels = ref(['public', 'internal', 'confidential', 'restricted'])
  // Set by anything that wants the policy shown on demand (sidebar link).
  const aupOpen = ref(false)

  const isPrivateMode = computed(() => privateCollections.value)
  // Only an admin on a deployment with edge admission configured can invite
  // someone who cannot already reach the app.
  const canInviteNewPeople = computed(() => edgeAdmission.value && isAdmin.value)
  // The modal takes over until accepted; it can also be opened voluntarily.
  const mustAcceptAup = computed(() => aup.value.required && !aup.value.accepted)

  async function loadCurrentUser() {
    try {
      const response = await http.get('/api/user/me')
      userId.value = response.data.user_id
      displayName.value = response.data.display_name
      privateCollections.value = response.data.private_collections
      dbBackend.value = response.data.db_backend
      edgeAdmission.value = response.data.edge_admission || false
      isAdmin.value = response.data.is_admin || false
      adminConsole.value = response.data.admin_console ?? isAdmin.value
      if (response.data.aup) aup.value = response.data.aup
      contentPolicyAction.value = response.data.content_policy_action || 'flag'
      if (Array.isArray(response.data.sensitivity_levels)) {
        sensitivityLevels.value = response.data.sensitivity_levels
      }
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
      adminConsole.value = false
      loaded.value = true
    }
  }

  async function acceptAup() {
    const response = await http.post('/api/aup/accept')
    aup.value = { ...aup.value, ...response.data }
    aupOpen.value = false
    return response.data
  }

  function openAup() {
    aupOpen.value = true
  }

  return {
    userId,
    displayName,
    privateCollections,
    dbBackend,
    edgeAdmission,
    isAdmin,
    adminConsole,
    loaded,
    aup,
    aupOpen,
    contentPolicyAction,
    sensitivityLevels,
    isPrivateMode,
    canInviteNewPeople,
    mustAcceptAup,
    loadCurrentUser,
    acceptAup,
    openAup,
  }
})
