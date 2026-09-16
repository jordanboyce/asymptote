<template>
  <div class="space-y-6">
    <div>
      <h3 class="text-sm font-semibold">Who can reach this deployment</h3>
      <p class="text-xs text-base-content/60 mt-1">
        Addresses on the Cloudflare Access policy. Anyone here can sign in;
        what they can actually see is still governed by collection sharing.
      </p>
    </div>

    <div v-if="loading" class="flex items-center gap-2 text-sm text-base-content/60">
      <span class="loading loading-spinner loading-xs"></span> Loading…
    </div>

    <div v-else-if="error" class="alert alert-error py-2 text-sm">
      <AlertTriangle :size="16" />
      <span>{{ error }}</span>
      <button class="btn btn-xs btn-ghost" @click="load">Retry</button>
    </div>

    <template v-else-if="data">
      <!-- Seat usage. Cloudflare bills per person who signs in once past the
           free tier, so this is the number that turns into money. -->
      <div class="rounded-lg border border-base-300 p-3 space-y-2">
        <div class="flex items-baseline justify-between">
          <span class="text-xs text-base-content/60">Seats used</span>
          <span class="text-sm font-semibold tabular-nums">
            {{ data.counts.admitted }} / {{ data.counts.free_seat_limit }}
          </span>
        </div>
        <progress
          class="progress w-full"
          :class="seatClass"
          :value="data.counts.admitted"
          :max="data.counts.free_seat_limit"
          :aria-label="seatLabel"
        ></progress>
        <p class="text-xs" :class="overSeats ? 'text-warning' : 'text-base-content/50'">
          <template v-if="overSeats">
            Past Cloudflare's free tier — additional users are billed per seat.
          </template>
          <template v-else-if="nearSeats">
            Approaching Cloudflare's free tier of
            {{ data.counts.free_seat_limit }}; beyond it, users are billed per seat.
          </template>
          <template v-else>
            Cloudflare Zero Trust is free up to {{ data.counts.free_seat_limit }} users.
            The policy itself holds up to {{ ruleCapLabel }} addresses.
          </template>
        </p>
      </div>

      <!-- ── Online registration ─────────────────────────────────────── -->
      <section aria-labelledby="access-requests" class="space-y-3">
        <div class="flex items-start justify-between gap-3 flex-wrap">
          <div>
            <h4 id="access-requests" class="text-sm font-semibold flex items-center gap-2">
              Registration requests
              <span v-if="pendingRequests.length" class="badge badge-sm badge-warning">{{ pendingRequests.length }}</span>
            </h4>
            <p class="text-xs text-base-content/60 mt-1">
              <template v-if="registrationMode === 'off'">
                Online registration is off. Set <code class="font-mono">REGISTRATION_MODE=approval</code> to let people ask for access at
                <a :href="registerUrl" class="link link-hover font-mono" target="_blank" rel="noopener">/register</a>.
              </template>
              <template v-else-if="registrationMode === 'open'">
                Open registration: matching addresses at
                <a :href="registerUrl" class="link link-hover font-mono" target="_blank" rel="noopener">/register</a>
                are admitted immediately<span v-if="allowedDomains.length"> ({{ allowedDomains.join(', ') }})</span>.
                Requests below are the ones that could not be admitted automatically.
              </template>
              <template v-else>
                People ask for access at
                <a :href="registerUrl" class="link link-hover font-mono" target="_blank" rel="noopener">/register</a><span v-if="allowedDomains.length"> ({{ allowedDomains.join(', ') }} only)</span>;
                approving admits them at the edge.
              </template>
            </p>
          </div>
          <button class="btn btn-ghost btn-xs gap-1" @click="loadRegistrations" :disabled="regLoading">
            <RefreshCw :size="12" :class="{ 'animate-spin': regLoading }" /> Refresh
          </button>
        </div>

        <div v-if="regError" class="alert alert-error py-2 text-sm">{{ regError }}</div>

        <ul v-if="pendingRequests.length" class="divide-y divide-base-300/60 rounded-lg border border-base-300">
          <li v-for="r in pendingRequests" :key="r.id" class="p-3 flex items-start gap-3 flex-wrap sm:flex-nowrap">
            <div class="flex-1 min-w-0">
              <div class="flex items-baseline gap-2 flex-wrap">
                <span class="font-mono text-xs truncate">{{ r.email }}</span>
                <span v-if="r.name" class="text-sm font-medium truncate">{{ r.name }}</span>
                <span v-if="r.organization" class="text-xs text-base-content/55 truncate">{{ r.organization }}</span>
              </div>
              <p v-if="r.note" class="text-xs text-base-content/65 mt-1 whitespace-pre-line line-clamp-3">{{ r.note }}</p>
              <p class="text-[11px] text-base-content/45 mt-1 tabular-nums">
                {{ formatDateTime(r.requested_at) }}<span v-if="r.attempts > 1"> · asked {{ r.attempts }} times</span>
              </p>
            </div>
            <div class="flex items-center gap-1.5 flex-shrink-0">
              <button
                class="btn btn-xs btn-primary gap-1"
                :disabled="regBusy === r.id"
                @click="approve(r)"
              >
                <Check :size="12" /> Approve
              </button>
              <button
                class="btn btn-xs btn-ghost"
                :disabled="regBusy === r.id"
                @click="deny(r)"
              >Deny</button>
            </div>
          </li>
        </ul>
        <p v-else-if="registrationMode !== 'off'" class="text-xs text-base-content/50">No requests waiting.</p>

        <details v-if="decidedRequests.length" class="text-xs">
          <summary class="cursor-pointer text-base-content/60 select-none">
            Decided ({{ decidedRequests.length }})
          </summary>
          <div class="overflow-x-auto mt-2">
            <table class="table table-xs">
              <thead>
                <tr><th>Address</th><th>Decision</th><th>By</th><th>When</th></tr>
              </thead>
              <tbody>
                <tr v-for="r in decidedRequests" :key="r.id">
                  <td class="font-mono">{{ r.email }}</td>
                  <td>
                    <span class="badge badge-xs" :class="r.status === 'approved' ? 'badge-success' : 'badge-ghost'">{{ r.status }}</span>
                    <span v-if="r.decision_note" class="ml-1 text-base-content/55">{{ r.decision_note }}</span>
                  </td>
                  <td class="text-base-content/60">{{ r.decided_by || '—' }}</td>
                  <td class="text-base-content/60 tabular-nums">{{ formatDateTime(r.decided_at) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </details>
      </section>

      <!-- Drift: the two ways this list stops matching reality -->
      <div v-if="data.missing.length" class="alert alert-warning py-2 text-sm items-start">
        <AlertTriangle :size="16" class="mt-0.5" />
        <div class="flex-1">
          <div class="font-medium">Shared with, but cannot sign in</div>
          <p class="text-xs opacity-80 mt-0.5">
            These people hold a share but are not admitted, so their invitation
            stops at the Cloudflare login.
          </p>
          <div class="flex flex-wrap gap-1.5 mt-2">
            <button
              v-for="email in data.missing" :key="email"
              class="btn btn-xs" :disabled="busy === email"
              @click="admit(email)"
            >
              <Plus :size="12" /> {{ email }}
            </button>
          </div>
        </div>
      </div>

      <div v-if="data.orphans.length" class="alert py-2 text-sm items-start">
        <Info :size="16" class="mt-0.5" />
        <div class="flex-1">
          <div class="font-medium">Admitted with nothing shared</div>
          <p class="text-xs opacity-80 mt-0.5">
            These can sign in but hold no collection. Usually access that was
            never withdrawn — each one still occupies a seat.
          </p>
          <div class="flex flex-wrap gap-1.5 mt-2">
            <button
              v-for="email in data.orphans" :key="email"
              class="btn btn-xs btn-outline" :disabled="busy === email"
              @click="withdraw(email)"
            >
              <Trash2 :size="12" /> {{ email }}
            </button>
          </div>
        </div>
      </div>

      <!-- Admit someone directly -->
      <div class="flex gap-2">
        <input
          v-model="newEmail"
          type="email"
          placeholder="Admit an address…"
          class="input input-bordered input-sm flex-1"
          aria-label="Email address to admit"
          @keyup.enter="admit(newEmail)"
        />
        <button
          class="btn btn-sm btn-primary gap-1"
          :disabled="!newEmail.trim() || busy === newEmail.trim()"
          @click="admit(newEmail)"
        >
          <Plus :size="14" /> Admit
        </button>
      </div>

      <!-- The list -->
      <div class="overflow-x-auto">
        <table class="table table-sm">
          <thead>
            <tr>
              <th>Address</th>
              <th class="text-right">Shares</th>
              <th>Last seen</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in data.people" :key="p.email">
              <td class="font-mono text-xs">
                {{ p.email }}
                <span v-if="p.is_admin" class="badge badge-xs badge-primary ml-1">admin</span>
              </td>
              <td class="text-right tabular-nums">
                <span :class="p.shares === 0 && !p.is_admin ? 'text-base-content/40' : ''">
                  {{ p.shares }}
                </span>
              </td>
              <td class="text-xs text-base-content/60">
                {{ p.has_signed_in ? formatDate(p.last_seen_at) : 'never' }}
              </td>
              <td class="text-right">
                <button
                  class="btn btn-ghost btn-xs"
                  :disabled="p.email === userStore.userId || busy === p.email"
                  :title="p.email === userStore.userId
                    ? 'You cannot withdraw your own access here'
                    : 'Withdraw access'"
                  @click="withdraw(p.email)"
                >
                  <Trash2 :size="13" />
                </button>
              </td>
            </tr>
            <tr v-if="!data.people.length">
              <td colspan="4" class="text-center text-xs text-base-content/50 py-4">
                Nobody is admitted yet.
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="actionError" class="alert alert-error py-2 text-sm">{{ actionError }}</div>

      <div class="flex items-center gap-2 text-xs text-base-content/50">
        <button class="btn btn-ghost btn-xs gap-1" @click="load" :disabled="loading">
          <RefreshCw :size="12" /> Refresh
        </button>
        <span class="font-mono">policy {{ data.policy_id }}</span>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { AlertTriangle, Info, Plus, Trash2, RefreshCw, Check } from 'lucide-vue-next'
import {
  listAdmissions, admitEmail, withdrawEmail,
  listRegistrations, approveRegistration, denyRegistration,
} from '../utils/sharingApi'
import { useUserStore } from '../stores/userStore'
import { useUiStore } from '../stores/uiStore'

const emit = defineEmits(['pending-changed'])
const userStore = useUserStore()
const ui = useUiStore()

const data = ref(null)
const loading = ref(false)
const error = ref('')
const actionError = ref('')
const newEmail = ref('')
const busy = ref('')

// Registration requests
const registrations = ref([])
const registrationMode = ref(userStore.registrationMode || 'off')
const allowedDomains = ref([])
const regLoading = ref(false)
const regError = ref('')
const regBusy = ref('')
const registerUrl = `${window.location.origin}/register`

const pendingRequests = computed(() => registrations.value.filter(r => r.status === 'pending'))
const decidedRequests = computed(() => registrations.value.filter(r => r.status !== 'pending'))

const seatRatio = computed(() =>
  data.value ? data.value.counts.admitted / data.value.counts.free_seat_limit : 0
)
const nearSeats = computed(() => seatRatio.value >= 0.8)
const overSeats = computed(() => seatRatio.value > 1)
const seatClass = computed(() =>
  overSeats.value ? 'progress-error' : nearSeats.value ? 'progress-warning' : 'progress-primary'
)
const seatLabel = computed(() =>
  data.value
    ? data.value.counts.admitted + ' of ' + data.value.counts.free_seat_limit + ' free seats used'
    : ''
)
const ruleCapLabel = computed(() =>
  data.value ? data.value.counts.rule_cap.toLocaleString() : ''
)

function formatDate(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
    })
  } catch {
    return iso
  }
}

function formatDateTime(iso) {
  if (!iso) return '—'
  try {
    // Server timestamps are naive UTC ISO strings.
    const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z')
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
  } catch {
    return iso
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await listAdmissions()
  } catch (err) {
    error.value = err.response?.data?.detail || err.message || 'Could not load the admission list'
  } finally {
    loading.value = false
  }
  loadRegistrations()
}

async function loadRegistrations() {
  regLoading.value = true
  regError.value = ''
  try {
    const body = await listRegistrations()
    registrations.value = body.requests || []
    registrationMode.value = body.mode || 'off'
    allowedDomains.value = body.allowed_domains || []
    emit('pending-changed', body.pending || 0)
  } catch (err) {
    regError.value = err.message || 'Could not load registration requests'
  } finally {
    regLoading.value = false
  }
}

async function approve(r) {
  regBusy.value = r.id
  regError.value = ''
  try {
    await approveRegistration(r.id)
    ui.notify(`${r.email} can now sign in.`, 'success')
    await Promise.all([loadRegistrations(), load()])
  } catch (err) {
    regError.value = err.message || 'Could not approve ' + r.email
  } finally {
    regBusy.value = ''
  }
}

async function deny(r) {
  regBusy.value = r.id
  regError.value = ''
  try {
    await denyRegistration(r.id)
    await loadRegistrations()
  } catch (err) {
    regError.value = err.message || 'Could not deny ' + r.email
  } finally {
    regBusy.value = ''
  }
}

async function admit(email) {
  const target = (email || '').trim()
  if (!target) return
  busy.value = target
  actionError.value = ''
  try {
    await admitEmail(target)
    if (target === newEmail.value.trim()) newEmail.value = ''
    await load()
  } catch (err) {
    actionError.value = err.response?.data?.detail || err.message || 'Could not admit ' + target
  } finally {
    busy.value = ''
  }
}

async function withdraw(email) {
  busy.value = email
  actionError.value = ''
  try {
    await withdrawEmail(email)
    await load()
  } catch (err) {
    actionError.value = err.response?.data?.detail || err.message || 'Could not withdraw ' + email
  } finally {
    busy.value = ''
  }
}

onMounted(load)
</script>
