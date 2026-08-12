<!--
  ClientProfileModal — the client's Investment Policy Statement, as a form.

  This is the typed cousin of the collection guide. The guide says how to read
  a client's files; this says what their portfolio is supposed to look like.
  Without it, meeting prep can report a portfolio but cannot judge it — "28%
  in tech" is a fact, "8 points over target" is a conversation.

  The completeness strip at the top is the design's whole argument: it names,
  in plain language, what prep still cannot say. A half-filled profile is
  normal and fine; a half-filled profile that pretends to be complete is not.
-->
<template>
  <dialog ref="dialogEl" class="modal">
    <div class="modal-box max-w-3xl p-0 overflow-hidden">

      <header class="flex items-start justify-between gap-3 px-5 py-4 border-b border-base-300">
        <div class="min-w-0">
          <h2 class="text-base font-semibold tracking-tight flex items-center gap-2">
            <UserCog :size="16" class="text-primary shrink-0" aria-hidden="true" />
            Client profile
          </h2>
          <p class="mt-1 text-xs text-base-content/55">
            What this portfolio is supposed to look like. Meeting prep measures against it.
          </p>
        </div>
        <button class="btn btn-sm btn-ghost btn-circle" @click="close" aria-label="Close">
          <X :size="16" />
        </button>
      </header>

      <div class="max-h-[70vh] overflow-y-auto px-5 py-4 flex flex-col gap-5">

        <div v-if="error" role="alert" class="alert alert-error text-xs py-2">
          <AlertTriangle :size="14" aria-hidden="true" />
          <span>{{ error }}</span>
        </div>

        <div v-if="loading" class="flex flex-col gap-2">
          <div v-for="i in 4" :key="i" class="h-12 rounded-lg bg-base-200 animate-pulse"></div>
        </div>

        <template v-else>
          <!-- Completeness -->
          <section
            v-if="completeness.blocked && completeness.blocked.length"
            class="rounded-lg border border-warning/35 bg-warning/5 px-3 py-2.5"
          >
            <p class="text-xs font-medium flex items-center gap-1.5">
              <AlertTriangle :size="12" class="text-warning shrink-0" aria-hidden="true" />
              With what's filled in now, meeting prep cannot:
            </p>
            <ul class="mt-1.5 list-disc pl-5 flex flex-col gap-0.5 text-[11px] text-base-content/65">
              <li v-for="(b, i) in completeness.blocked" :key="i">{{ b }}</li>
            </ul>
          </section>
          <p v-else-if="completeness.note" class="text-xs text-success flex items-center gap-1.5">
            <CheckCircle2 :size="12" aria-hidden="true" />
            {{ completeness.note }}
          </p>

          <!-- Basics -->
          <section class="flex flex-col gap-3">
            <h3 class="text-xs font-semibold uppercase tracking-wider text-base-content/55">Basics</h3>
            <div class="grid gap-3 sm:grid-cols-2">
              <label class="form-control">
                <span class="label-text text-xs mb-1">Household name</span>
                <input v-model="form.display_name" type="text" class="input input-sm input-bordered w-full" placeholder="Henderson Household" />
              </label>
              <label class="form-control">
                <span class="label-text text-xs mb-1">Risk tolerance</span>
                <select v-model="form.risk_tolerance" class="select select-sm select-bordered w-full">
                  <option :value="null">Not set</option>
                  <option v-for="r in RISK_LEVELS" :key="r.value" :value="r.value">{{ r.label }}</option>
                </select>
              </label>
              <label class="form-control">
                <span class="label-text text-xs mb-1">Time horizon (years)</span>
                <input v-model.number="form.time_horizon_years" type="number" min="0" max="100" class="input input-sm input-bordered w-full" />
              </label>
              <label class="form-control">
                <span class="label-text text-xs mb-1">Next review</span>
                <input v-model="form.next_review_date" type="date" class="input input-sm input-bordered w-full" />
              </label>
            </div>
          </section>

          <!-- Target allocation -->
          <section class="flex flex-col gap-2">
            <div class="flex items-center justify-between">
              <h3 class="text-xs font-semibold uppercase tracking-wider text-base-content/55">Target allocation</h3>
              <span
                v-if="targetSum !== null"
                class="text-xs tabular-nums"
                :class="Math.abs(targetSum - 100) > 0.5 ? 'text-warning' : 'text-base-content/45'"
              >
                sums to {{ targetSum }}%
              </span>
            </div>

            <div v-for="(t, i) in form.ips.allocation_targets" :key="i" class="flex items-center gap-2">
              <input v-model="t.asset_class" type="text" class="input input-sm input-bordered flex-1" placeholder="Equities" />
              <input v-model.number="t.target_pct" type="number" min="0" max="100" step="0.5" class="input input-sm input-bordered w-24" placeholder="60" />
              <span class="text-xs text-base-content/45">%</span>
              <button class="btn btn-sm btn-ghost btn-circle" @click="form.ips.allocation_targets.splice(i, 1)" aria-label="Remove line">
                <Trash2 :size="14" />
              </button>
            </div>

            <button class="btn btn-xs btn-ghost gap-1 self-start" @click="form.ips.allocation_targets.push({ asset_class: '', target_pct: 0 })">
              <Plus :size="12" /> Add asset class
            </button>

            <p class="text-[11px] text-base-content/45 leading-relaxed">
              Match the labels your export uses. Common equivalents (Stocks /
              Equities, Bonds / Fixed Income) are matched automatically and
              flagged so you can check them.
            </p>

            <label class="form-control max-w-xs">
              <span class="label-text text-xs mb-1">Rebalance band (± %)</span>
              <input v-model.number="form.ips.rebalance_band_pct" type="number" min="0" max="100" step="0.5" class="input input-sm input-bordered w-full" />
            </label>
          </section>

          <!-- Limits -->
          <section class="flex flex-col gap-3">
            <h3 class="text-xs font-semibold uppercase tracking-wider text-base-content/55">Limits</h3>
            <div class="grid gap-3 sm:grid-cols-2">
              <label class="form-control">
                <span class="label-text text-xs mb-1">Max single position (%)</span>
                <input v-model.number="form.ips.max_single_position_pct" type="number" min="0" max="100" step="0.5" class="input input-sm input-bordered w-full" placeholder="10" />
              </label>
              <label class="form-control">
                <span class="label-text text-xs mb-1">Max cash (%)</span>
                <input v-model.number="form.ips.max_cash_pct" type="number" min="0" max="100" step="0.5" class="input input-sm input-bordered w-full" />
              </label>
            </div>

            <label class="form-control">
              <span class="label-text text-xs mb-1">Prohibited holdings</span>
              <input
                v-model="prohibitedText"
                type="text"
                class="input input-sm input-bordered w-full"
                placeholder="XOM, tobacco, private prison"
              />
              <span class="text-[11px] text-base-content/45 mt-1">
                Comma-separated. Short entries are read as tickers and matched
                exactly; longer ones match anywhere in a holding's name.
              </span>
            </label>
          </section>

          <!-- Tax + liquidity -->
          <section class="flex flex-col gap-3">
            <h3 class="text-xs font-semibold uppercase tracking-wider text-base-content/55">Tax &amp; liquidity</h3>
            <div class="grid gap-3 sm:grid-cols-2">
              <label class="form-control">
                <span class="label-text text-xs mb-1">Federal bracket (%)</span>
                <input v-model.number="form.tax.federal_bracket_pct" type="number" min="0" max="100" step="0.5" class="input input-sm input-bordered w-full" placeholder="32" />
              </label>
              <label class="form-control">
                <span class="label-text text-xs mb-1">State bracket (%)</span>
                <input v-model.number="form.tax.state_bracket_pct" type="number" min="0" max="100" step="0.5" class="input input-sm input-bordered w-full" />
              </label>
              <label class="form-control">
                <span class="label-text text-xs mb-1">Cash reserve target</span>
                <input v-model.number="form.liquidity.cash_reserve_target" type="number" min="0" step="1000" class="input input-sm input-bordered w-full" placeholder="150000" />
              </label>
              <label class="form-control">
                <span class="label-text text-xs mb-1">Next liquidity need</span>
                <input v-model="form.liquidity.next_liquidity_event" type="text" class="input input-sm input-bordered w-full" placeholder="Q2 2027 tuition" />
              </label>
            </div>
            <p class="text-[11px] text-base-content/45 leading-relaxed">
              A reserve target and a known liquidity need are what let prep tell
              a deliberate cash balance apart from cash drag.
            </p>
          </section>

          <!-- Notes -->
          <label class="form-control">
            <span class="label-text text-xs mb-1">Notes</span>
            <textarea v-model="form.notes" rows="3" class="textarea textarea-bordered textarea-sm w-full" placeholder="Anything that doesn't fit a field. Surfaced verbatim in prep."></textarea>
          </label>
        </template>
      </div>

      <footer class="px-5 py-3 border-t border-base-300 flex items-center justify-between gap-3">
        <span v-if="saved" class="text-xs text-success flex items-center gap-1.5">
          <CheckCircle2 :size="13" aria-hidden="true" /> Saved
        </span>
        <span v-else></span>
        <div class="flex items-center gap-2">
          <button class="btn btn-sm btn-ghost" @click="close">Cancel</button>
          <button class="btn btn-sm btn-primary gap-1.5" @click="save" :disabled="saving || loading">
            <Loader2 v-if="saving" :size="13" class="animate-spin" />
            Save profile
          </button>
        </div>
      </footer>
    </div>
    <form method="dialog" class="modal-backdrop"><button>close</button></form>
  </dialog>
</template>

<script setup>
import { ref, computed } from 'vue'
import axios from 'axios'
import { X, UserCog, AlertTriangle, CheckCircle2, Trash2, Plus, Loader2 } from 'lucide-vue-next'

const props = defineProps({
  collectionId: { type: String, default: '' },
})

const emit = defineEmits(['saved'])

const RISK_LEVELS = [
  { value: 'conservative', label: 'Conservative' },
  { value: 'moderately_conservative', label: 'Moderately conservative' },
  { value: 'moderate', label: 'Moderate' },
  { value: 'moderately_aggressive', label: 'Moderately aggressive' },
  { value: 'aggressive', label: 'Aggressive' },
]

const emptyForm = () => ({
  display_name: null,
  household_members: [],
  risk_tolerance: null,
  risk_notes: null,
  time_horizon_years: null,
  goals: [],
  ips: {
    allocation_targets: [],
    rebalance_band_pct: 5,
    max_single_position_pct: null,
    max_sector_pct: null,
    min_cash_pct: null,
    max_cash_pct: null,
    prohibited_holdings: [],
    notes: null,
  },
  tax: {
    filing_status: null,
    federal_bracket_pct: null,
    state: null,
    state_bracket_pct: null,
    capital_loss_carryforward: null,
    ytd_realized_gains: null,
    notes: null,
  },
  liquidity: {
    cash_reserve_target: null,
    annual_withdrawal: null,
    next_liquidity_event: null,
    next_liquidity_amount: null,
    notes: null,
  },
  review_frequency: null,
  next_review_date: null,
  notes: null,
})

const dialogEl = ref(null)
const loading = ref(false)
const saving = ref(false)
const saved = ref(false)
const error = ref('')
const form = ref(emptyForm())
const completeness = ref({})

// Prohibited holdings round-trip through a comma-separated string so the
// common case (typing three tickers) doesn't need a repeater widget.
const prohibitedText = computed({
  get: () => (form.value.ips.prohibited_holdings || []).join(', '),
  set: (v) => {
    form.value.ips.prohibited_holdings = String(v)
      .split(',')
      .map(s => s.trim())
      .filter(Boolean)
  },
})

const targetSum = computed(() => {
  const targets = form.value.ips.allocation_targets || []
  if (!targets.length) return null
  return Math.round(targets.reduce((a, t) => a + (Number(t.target_pct) || 0), 0) * 100) / 100
})

const load = async () => {
  if (!props.collectionId) return
  loading.value = true
  error.value = ''
  saved.value = false
  try {
    const { data } = await axios.get(`/api/collections/${props.collectionId}/profile`)
    form.value = { ...emptyForm(), ...data.profile }
    // Guard against a stored payload missing a nested object.
    form.value.ips = { ...emptyForm().ips, ...(data.profile.ips || {}) }
    form.value.tax = { ...emptyForm().tax, ...(data.profile.tax || {}) }
    form.value.liquidity = { ...emptyForm().liquidity, ...(data.profile.liquidity || {}) }
    completeness.value = data.completeness || {}
  } catch (e) {
    error.value = e.response?.data?.detail || e.message || 'Could not load the profile.'
  } finally {
    loading.value = false
  }
}

const save = async () => {
  if (!props.collectionId) return
  saving.value = true
  error.value = ''
  try {
    const payload = JSON.parse(JSON.stringify(form.value))
    // Drop blank allocation lines rather than failing validation on them.
    payload.ips.allocation_targets = (payload.ips.allocation_targets || [])
      .filter(t => String(t.asset_class || '').trim())
    const { data } = await axios.put(`/api/collections/${props.collectionId}/profile`, payload)
    completeness.value = data.completeness || {}
    saved.value = true
    emit('saved', data)
    setTimeout(() => { saved.value = false }, 2500)
  } catch (e) {
    const detail = e.response?.data?.detail
    error.value = Array.isArray(detail)
      ? detail.map(d => `${(d.loc || []).slice(1).join('.')}: ${d.msg}`).join('; ')
      : (detail || e.message || 'Could not save the profile.')
  } finally {
    saving.value = false
  }
}

const open = () => {
  load()
  dialogEl.value?.showModal()
}
const close = () => dialogEl.value?.close()

defineExpose({ open, close })
</script>
