<template>
  <div class="min-h-dvh bg-base-200 text-base-content flex flex-col">
    <!-- Brand bar -->
    <header class="px-5 sm:px-8 h-14 flex items-center justify-between">
      <a href="/" class="flex items-center gap-2.5 no-underline text-base-content" aria-label="Clio home">
        <img :src="brandIcon" alt="" class="h-6 w-6 brand-mark" />
        <span class="font-semibold tracking-tight">Clio</span>
      </a>
      <a href="/" class="btn btn-ghost btn-sm normal-case font-medium">
        Sign in
        <ArrowRight :size="14" aria-hidden="true" />
      </a>
    </header>

    <main class="flex-1 flex items-start sm:items-center justify-center px-4 py-8 sm:py-12">
      <div class="w-full max-w-[26rem]">

        <!-- Intro -->
        <div class="mb-7">
          <p class="text-[11px] uppercase tracking-[0.16em] font-semibold text-base-content/45 mb-3">Request access</p>
          <h1 class="text-[26px] leading-[1.15] font-semibold tracking-tight">
            Answers from your sources.
          </h1>
          <p class="mt-3 text-[15px] leading-relaxed text-base-content/65">
            Ask for a seat on this workspace. Sign-in is by one-time code to your email — there is no password to set.
          </p>
        </div>

        <!-- Loading -->
        <div v-if="state === 'loading'" class="flex items-center gap-2 text-sm text-base-content/55 py-6" role="status">
          <span class="loading loading-spinner loading-sm"></span>
          Checking availability…
        </div>

        <!-- Unreachable -->
        <div v-else-if="state === 'unreachable'" class="card bg-base-100 border border-base-300 shadow-sm">
          <div class="card-body gap-2 p-6">
            <h2 class="text-base font-semibold">Can't reach the server</h2>
            <p class="text-sm text-base-content/65">The workspace may be restarting. Try again in a moment.</p>
            <div class="card-actions mt-2">
              <button class="btn btn-sm" @click="loadConfig">Retry</button>
            </div>
          </div>
        </div>

        <!-- Closed -->
        <div v-else-if="state === 'closed'" class="card bg-base-100 border border-base-300 shadow-sm">
          <div class="card-body gap-2 p-6">
            <h2 class="text-base font-semibold">Registration is closed</h2>
            <p class="text-sm text-base-content/65 leading-relaxed">
              This workspace admits people by invitation. Ask its administrator to add your address, then sign in.
            </p>
            <div class="card-actions mt-2">
              <a href="/" class="btn btn-sm btn-primary normal-case">Sign in</a>
            </div>
          </div>
        </div>

        <!-- Done -->
        <div v-else-if="state === 'done'" class="card bg-base-100 border border-base-300 shadow-sm" role="status" aria-live="polite">
          <div class="card-body gap-3 p-6">
            <div class="flex items-start gap-3">
              <span class="mt-0.5 inline-flex h-7 w-7 items-center justify-center rounded-full bg-success/15 text-success flex-shrink-0">
                <Check :size="15" aria-hidden="true" />
              </span>
              <div class="min-w-0">
                <h2 class="text-base font-semibold">{{ resultTitle }}</h2>
                <p class="mt-1 text-sm text-base-content/65 leading-relaxed">{{ result.message }}</p>
                <p v-if="result.email" class="mt-2 text-xs text-base-content/50 font-mono truncate">{{ result.email }}</p>
              </div>
            </div>
            <div class="card-actions mt-1">
              <a v-if="result.status !== 'pending'" href="/" class="btn btn-sm btn-primary normal-case">
                Sign in
                <ArrowRight :size="14" aria-hidden="true" />
              </a>
              <a v-else href="/" class="btn btn-sm btn-ghost normal-case">Back to sign in</a>
            </div>
          </div>
        </div>

        <!-- Form -->
        <form v-else class="card bg-base-100 border border-base-300 shadow-sm" novalidate @submit.prevent="submit">
          <div class="card-body gap-4 p-6">
            <div class="form-control">
              <label class="label pt-0 pb-1.5" for="reg-name"><span class="label-text font-medium">Name</span></label>
              <input
                id="reg-name"
                v-model.trim="form.name"
                type="text"
                autocomplete="name"
                class="input input-bordered w-full"
                placeholder="Your name"
                maxlength="200"
                :disabled="busy"
              />
            </div>

            <div class="form-control">
              <label class="label pt-0 pb-1.5" for="reg-email">
                <span class="label-text font-medium">Work email</span>
                <span v-if="domainHint" class="label-text-alt text-base-content/50">{{ domainHint }}</span>
              </label>
              <input
                id="reg-email"
                v-model.trim="form.email"
                type="email"
                autocomplete="email"
                inputmode="email"
                required
                class="input input-bordered w-full"
                :class="{ 'input-error': fieldError }"
                placeholder="you@example.com"
                maxlength="320"
                :disabled="busy"
                :aria-invalid="fieldError ? 'true' : undefined"
                aria-describedby="reg-email-help"
              />
              <p id="reg-email-help" class="mt-1.5 text-xs text-base-content/50">
                You'll sign in with a one-time code sent to this address.
              </p>
            </div>

            <div class="form-control">
              <label class="label pt-0 pb-1.5" for="reg-org">
                <span class="label-text font-medium">Organization</span>
                <span class="label-text-alt text-base-content/45">Optional</span>
              </label>
              <input
                id="reg-org"
                v-model.trim="form.organization"
                type="text"
                autocomplete="organization"
                class="input input-bordered w-full"
                placeholder="Team or company"
                maxlength="200"
                :disabled="busy"
              />
            </div>

            <div v-if="config.mode === 'approval'" class="form-control">
              <label class="label pt-0 pb-1.5" for="reg-note">
                <span class="label-text font-medium">What will you use it for?</span>
                <span class="label-text-alt text-base-content/45">Optional</span>
              </label>
              <textarea
                id="reg-note"
                v-model.trim="form.note"
                class="textarea textarea-bordered w-full leading-relaxed"
                rows="3"
                placeholder="A sentence helps the reviewer."
                maxlength="1000"
                :disabled="busy"
              ></textarea>
            </div>

            <!-- Honeypot: invisible to people, irresistible to form bots. -->
            <div class="hp" aria-hidden="true">
              <label for="reg-website">Website</label>
              <input id="reg-website" v-model="form.website" type="text" tabindex="-1" autocomplete="off" />
            </div>

            <div v-if="error" class="alert alert-error py-2.5 text-sm" role="alert">
              <AlertTriangle :size="16" aria-hidden="true" />
              <span>{{ error }}</span>
            </div>

            <button type="submit" class="btn btn-primary w-full normal-case" :disabled="busy || !form.email">
              <span v-if="busy" class="loading loading-spinner loading-sm" aria-hidden="true"></span>
              {{ busy ? 'Sending…' : (config.mode === 'open' ? 'Get access' : 'Request access') }}
            </button>

            <p class="text-xs text-base-content/50 leading-relaxed text-center">
              <template v-if="config.mode === 'open'">Matching addresses are admitted right away.</template>
              <template v-else>An administrator reviews each request; you'll be emailed when it's approved.</template>
            </p>
          </div>
        </form>

        <p class="mt-8 text-center text-[11px] leading-relaxed text-base-content/45">
          Access is granted at the network edge by Cloudflare Access. Your address is stored only to process this request.
        </p>
      </div>
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { AlertTriangle, ArrowRight, Check } from 'lucide-vue-next'
import http from '../utils/http'

// Bound rather than a literal src so the SFC compiler leaves the public
// asset path alone (a literal is rewritten to an import, which breaks
// under the test runner).
const brandIcon = '/icon_black.svg'
const state = ref('loading') // loading | unreachable | closed | form | done
const busy = ref(false)
const error = ref('')
const fieldError = ref(false)
const config = reactive({ enabled: false, mode: 'off', allowed_domains: [], product: 'Clio' })
const form = reactive({ name: '', email: '', organization: '', note: '', website: '' })
const result = reactive({ status: '', message: '', email: '' })

const domainHint = computed(() => {
  const d = config.allowed_domains || []
  if (!d.length) return ''
  return d.length === 1 ? `@${d[0]} addresses` : `${d.slice(0, 2).map(x => '@' + x).join(', ')}${d.length > 2 ? '…' : ''}`
})

const resultTitle = computed(() => ({
  approved: "You're in",
  admitted: 'You already have access',
  pending: 'Request received',
}[result.status] || 'Done'))

async function loadConfig() {
  state.value = 'loading'
  try {
    const { data } = await http.get('/api/register/config')
    Object.assign(config, data)
    state.value = data.enabled ? 'form' : 'closed'
  } catch (err) {
    state.value = err?.isNetwork ? 'unreachable' : 'closed'
  }
}

async function submit() {
  error.value = ''
  fieldError.value = false
  const email = form.email.trim().toLowerCase()
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    fieldError.value = true
    error.value = 'Enter a valid email address.'
    return
  }
  busy.value = true
  try {
    const { data } = await http.post('/api/register', {
      email,
      name: form.name,
      organization: form.organization,
      note: form.note,
      website: form.website,
    })
    Object.assign(result, data)
    state.value = 'done'
  } catch (err) {
    if (err?.isRateLimit) {
      error.value = 'Too many attempts from this connection. Wait a minute and try again.'
    } else if (err?.isNetwork) {
      error.value = "Can't reach the server right now. Try again in a moment."
    } else {
      error.value = err?.message || 'Something went wrong.'
      fieldError.value = /email|address/i.test(error.value)
    }
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  document.title = 'Request access · Clio'
  loadConfig()
})
</script>

<style scoped>
/* The brand mark is a black SVG; let it follow the text color in dark themes. */
.brand-mark { filter: brightness(0); opacity: 0.9; }
[data-theme="dark"] .brand-mark,
[data-theme="dracula"] .brand-mark { filter: brightness(0) invert(1); }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme]) .brand-mark { filter: brightness(0) invert(1); }
}
/* Honeypot: off-canvas, not display:none (some bots skip hidden fields). */
.hp { position: absolute; left: -10000px; top: auto; width: 1px; height: 1px; overflow: hidden; }
</style>
