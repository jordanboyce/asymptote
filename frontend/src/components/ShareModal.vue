<template>
  <dialog ref="modal" class="modal" :class="{ 'modal-open': visible }" aria-labelledby="share-modal-title">
    <div class="modal-box max-w-lg">
      <h3 id="share-modal-title" class="font-bold text-lg flex items-center gap-2">
        <Share2 :size="18" class="text-primary" aria-hidden="true" />
        {{ collectionId ? 'Share Collection' : 'Join a Shared Collection' }}
      </h3>
      <p v-if="collectionId" class="text-sm text-base-content/70 mt-1">{{ collectionName }}</p>
      <p v-else class="text-sm text-base-content/70 mt-1">
        Paste a share token someone sent you to get access to their collection.
      </p>

      <!-- Create new share -->
      <div v-if="collectionId" class="mt-4 space-y-3">
        <div class="flex gap-2 items-end">
          <div class="form-control flex-1">
            <label class="label py-1" for="share-permission"><span class="label-text text-xs">Permission</span></label>
            <select id="share-permission" v-model="newPermission" class="select select-bordered select-sm w-full">
              <option value="read">Read only</option>
              <option value="readwrite">Read + Write</option>
            </select>
          </div>
          <div class="form-control w-24">
            <label class="label py-1" for="share-expires"><span class="label-text text-xs">Expires</span></label>
            <select id="share-expires" v-model="newExpiresDays" class="select select-bordered select-sm w-full">
              <option :value="null">Never</option>
              <option :value="7">7 days</option>
              <option :value="30">30 days</option>
              <option :value="90">90 days</option>
            </select>
          </div>
          <button class="btn btn-primary btn-sm" @click="createNewShare" :disabled="creating">
            <span v-if="creating" class="loading loading-spinner loading-xs"></span>
            <Plus v-else :size="14" />
            Share
          </button>
        </div>

        <!-- Copied link notification -->
        <div v-if="copiedLink" class="alert alert-success py-2 text-sm">
          <CheckCircle :size="16" />
          Share link copied to clipboard!
        </div>

        <!-- Email an invitation (uses the permission/expiry selected above) -->
        <div class="flex gap-2">
          <input
            v-model="inviteEmail"
            type="email"
            placeholder="Or email the invitation to..."
            class="input input-bordered input-sm flex-1"
            aria-label="Recipient email for share invitation"
            @keyup.enter="emailInvite"
          />
          <button class="btn btn-sm btn-outline gap-1" @click="emailInvite" :disabled="!inviteEmail || sendingInvite">
            <span v-if="sendingInvite" class="loading loading-spinner loading-xs"></span>
            <Mail v-else :size="14" />
            Send
          </button>
        </div>
        <div v-if="inviteSent" class="alert alert-success py-2 text-sm">
          <CheckCircle :size="16" />
          <span>
            Invitation emailed to {{ inviteSent }}<span v-if="inviteAdmitted">, and they can now sign in</span>.
          </span>
        </div>
        <!-- Says plainly when the invitation will dead-end at the login, so
             nobody sends one expecting it to work. -->
        <div v-if="inviteNote" class="alert alert-warning py-2 text-sm">
          <AlertTriangle :size="16" />
          <span>{{ inviteNote }}</span>
        </div>
        <div v-if="inviteError" class="alert alert-error py-2 text-sm">{{ inviteError }}</div>
        <p v-else-if="!userStore.canInviteNewPeople" class="text-xs text-base-content/50">
          Invitations reach people who can already sign in to this deployment.
        </p>
      </div>

      <!-- Active shares list -->
      <div v-if="collectionId" class="mt-4">
        <div class="text-xs font-semibold text-base-content/60 mb-2">Active Share Links</div>
        <div v-if="loadingShares" class="flex justify-center py-4">
          <span class="loading loading-spinner loading-sm"></span>
        </div>
        <div v-else-if="shares.length === 0" class="text-xs text-base-content/40 py-4 text-center">
          No active shares yet
        </div>
        <div v-else class="space-y-2 max-h-48 overflow-y-auto">
          <div
            v-for="share in shares"
            :key="share.id"
            class="flex items-center gap-2 p-2 rounded bg-base-200 text-xs"
          >
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-1.5">
                <span class="badge badge-xs" :class="share.permission === 'readwrite' ? 'badge-warning' : 'badge-info'">
                  {{ share.permission === 'readwrite' ? 'R/W' : 'Read' }}
                </span>
                <span class="text-base-content/50">{{ share.accepted_users?.length || 0 }} user(s)</span>
                <span v-if="share.expires_at" class="text-base-content/40">
                  exp {{ formatDate(share.expires_at) }}
                </span>
              </div>
              <div class="font-mono text-base-content/40 mt-0.5 truncate" :title="share.id">{{ share.id }}</div>
            </div>
            <button
              class="btn btn-ghost btn-xs"
              @click="copyShareLink(share.id)"
              title="Copy link"
              aria-label="Copy share link to clipboard"
            >
              <Copy :size="12" aria-hidden="true" />
            </button>
            <button
              class="btn btn-ghost btn-xs text-error"
              @click="revokeShareLink(share.id)"
              title="Revoke share"
              aria-label="Revoke share link"
            >
              <Trash2 :size="12" aria-hidden="true" />
            </button>
          </div>
        </div>
      </div>

      <!-- Accept share section -->
      <div class="mt-4" :class="collectionId ? 'border-t border-base-300 pt-4' : ''">
        <label for="accept-share-token" class="text-xs font-semibold text-base-content/60 mb-2 block">Accept a Share Link</label>
        <div class="flex gap-2">
          <input
            id="accept-share-token"
            v-model="acceptToken"
            type="text"
            placeholder="Paste share token here..."
            class="input input-bordered input-sm flex-1"
            @keyup.enter="acceptShareLink"
          />
          <button class="btn btn-sm btn-outline" @click="acceptShareLink" :disabled="!acceptToken || accepting">
            <span v-if="accepting" class="loading loading-spinner loading-xs"></span>
            Accept
          </button>
        </div>
        <div v-if="acceptResult" class="alert alert-success py-2 text-sm mt-2">
          Joined "{{ acceptResult.collection_name }}" ({{ acceptResult.permission }})
        </div>
        <div v-if="acceptError" class="alert alert-error py-2 text-sm mt-2">{{ acceptError }}</div>
      </div>

      <div class="modal-action">
        <button class="btn" @click="close">Close</button>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop"><button @click="close">close</button></form>
  </dialog>
</template>

<script setup>
import { ref, watch } from 'vue'
import { Share2, Plus, Copy, Trash2, CheckCircle, Mail, AlertTriangle } from 'lucide-vue-next'
import { createShare, listShares, revokeShare, acceptShare } from '../utils/sharingApi'
import { useUserStore } from '../stores/userStore'

const userStore = useUserStore()

const props = defineProps({
  visible: Boolean,
  collectionId: String,
  collectionName: String,
  // Prefills the accept box — set by the ?share_token= deep link in emails.
  initialToken: { type: String, default: '' },
})

const emit = defineEmits(['close', 'shared'])

const shares = ref([])
const loadingShares = ref(false)
const creating = ref(false)
const copiedLink = ref(false)
const newPermission = ref('read')
const newExpiresDays = ref(null)

const acceptToken = ref('')
const accepting = ref(false)
const acceptResult = ref(null)
const acceptError = ref('')

const inviteEmail = ref('')
const sendingInvite = ref(false)
const inviteSent = ref('')
const inviteError = ref('')
const inviteNote = ref('')
const inviteAdmitted = ref(false)

watch(() => props.visible, async (v) => {
  if (v && props.initialToken) {
    acceptToken.value = props.initialToken
  }
  if (v && props.collectionId) {
    await loadShareList()
  }
})

async function emailInvite() {
  if (!inviteEmail.value.trim()) return
  sendingInvite.value = true
  inviteSent.value = ''
  inviteError.value = ''
  inviteNote.value = ''
  inviteAdmitted.value = false
  try {
    const share = await createShare(
      props.collectionId, newPermission.value, newExpiresDays.value, inviteEmail.value.trim()
    )
    inviteAdmitted.value = share.edge_admitted === true
    if (share.edge_note) inviteNote.value = share.edge_note
    if (share.edge_error) inviteNote.value = `Could not admit them at the login: ${share.edge_error}`
    if (share.email_sent) {
      inviteSent.value = inviteEmail.value.trim()
      inviteEmail.value = ''
    } else {
      inviteError.value = share.email_error || 'Email could not be sent'
    }
    await loadShareList()
    emit('shared')
  } catch (err) {
    inviteError.value = err.response?.data?.detail || 'Failed to send invitation'
  } finally {
    sendingInvite.value = false
  }
}

async function loadShareList() {
  loadingShares.value = true
  try {
    shares.value = await listShares(props.collectionId)
  } catch (err) {
    console.error('Failed to load shares:', err)
  } finally {
    loadingShares.value = false
  }
}

async function createNewShare() {
  creating.value = true
  try {
    const share = await createShare(props.collectionId, newPermission.value, newExpiresDays.value)
    await copyShareLink(share.share_id)
    await loadShareList()
    emit('shared')
  } catch (err) {
    console.error('Failed to create share:', err)
  } finally {
    creating.value = false
  }
}

async function copyShareLink(shareId) {
  try {
    await navigator.clipboard.writeText(shareId)
    copiedLink.value = true
    setTimeout(() => { copiedLink.value = false }, 3000)
  } catch {
    // Fallback for non-HTTPS
    prompt('Copy this share token:', shareId)
  }
}

async function revokeShareLink(shareId) {
  try {
    await revokeShare(shareId)
    await loadShareList()
  } catch (err) {
    console.error('Failed to revoke share:', err)
  }
}

async function acceptShareLink() {
  accepting.value = true
  acceptError.value = ''
  acceptResult.value = null
  try {
    acceptResult.value = await acceptShare(acceptToken.value.trim())
    acceptToken.value = ''
    emit('shared')
  } catch (err) {
    acceptError.value = err.response?.data?.detail || 'Failed to accept share'
  } finally {
    accepting.value = false
  }
}

function formatDate(iso) {
  try {
    return new Date(iso).toLocaleDateString()
  } catch {
    return iso
  }
}

function close() {
  emit('close')
}
</script>
