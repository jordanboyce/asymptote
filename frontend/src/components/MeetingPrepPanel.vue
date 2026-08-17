<!--
  MeetingPrepPanel — the page an advisor reads walking into a meeting.

  The agenda leads. Everything else is evidence for it, collapsed by default,
  one disclosure away.

  Two things here are deliberate and worth not "cleaning up":

  1. `gaps` renders near the top, not in a footnote. Every other section
     describes what Finn found; this one describes what it could not check.
     An advisor who reads "no concentration issues" when the screen never ran
     is worse off than one who reads nothing. It gets the same visual weight
     as a finding.

  2. Nothing on this page came from a language model. The whole page is
     computed from the client's own files and their stated policy, so the
     same data renders the same page every time. The footer says so, because
     in a room full of compliance officers that is the interesting part.
-->
<template>
  <section :class="variant === 'inline' ? 'rule-t rule-b py-4' : ''">

    <!-- Header -->
    <header
      class="flex items-start justify-between gap-3 flex-wrap"
      :class="variant === 'inline' ? '' : 'px-4 py-3 border-b border-base-300/60'"
    >
      <div class="min-w-0">
        <h2 class="label-eyebrow flex items-center gap-1.5">
          <ClipboardList :size="12" class="shrink-0" aria-hidden="true" />
          Meeting prep
        </h2>
        <p v-if="page" class="mt-1 text-xs text-base-content/60 tabular-nums">
          <span v-if="page.header.client" class="font-medium text-base-content/75">{{ page.header.client }}</span>
          <span v-if="page.header.client" class="mx-1.5 text-base-content/25">·</span>
          <!--
            A withheld total must never render as a blank or a zero — both read
            as "nothing here" when the truth is "we don't trust this number."
          -->
          <span v-if="page.header.total_market_value_reliable === false" class="text-error font-medium">
            total withheld — see below
          </span>
          <span v-else-if="page.header.total_market_value != null">{{ currency(page.header.total_market_value) }}</span>
          <template v-if="page.header.days_since_last_meeting != null">
            <span class="mx-1.5 text-base-content/25">·</span>
            {{ page.header.days_since_last_meeting }} days since last meeting
          </template>
        </p>
        <p v-else-if="loading" class="mt-1 text-xs text-base-content/55">Building the page…</p>
      </div>

      <div class="flex items-center gap-2 shrink-0">
        <span v-if="page && page.gaps.length" class="badge badge-sm badge-warning gap-1">
          <AlertTriangle :size="11" aria-hidden="true" />
          {{ page.gaps.length }} not checked
        </span>
        <button
          class="btn btn-xs btn-ghost gap-1"
          @click="load"
          :disabled="loading"
          title="Rebuild"
          aria-label="Rebuild meeting prep"
        >
          <RefreshCw :size="12" :class="{ 'animate-spin': loading }" />
        </button>
      </div>
    </header>

    <div class="flex flex-col gap-5" :class="variant === 'inline' ? 'pt-4' : 'px-4 py-3'">

      <!-- Error -->
      <div v-if="error" role="alert" class="alert alert-error text-xs py-2">
        <AlertTriangle :size="14" aria-hidden="true" />
        <span>{{ error }}</span>
      </div>

      <!-- Loading -->
      <div v-else-if="loading && !page" class="flex flex-col gap-2">
        <div v-for="i in 3" :key="i" class="h-16 rounded-lg bg-base-200 animate-pulse"></div>
      </div>

      <template v-else-if="page">

        <!-- ── Agenda ───────────────────────────────────────────────── -->
        <section>
          <h3 class="label-eyebrow mb-2.5">
            What to raise
          </h3>

          <p v-if="!page.talking_points.length" class="text-xs text-base-content/55 py-2">
            Nothing surfaced against this client's policy or open items. Check
            what wasn't verified below before treating that as all-clear.
          </p>

          <!-- A numbered agenda, rule-separated, in the order prep decided.
               Priority was previously encoded three times over — border colour,
               icon colour, and a badge on every row — which made every item
               look flagged and left the one thing that genuinely cannot wait
               competing with a low-priority note about SEC filings. The order
               is the priority; only "high" is called out in words, and only
               "high" tints its icon. Alarm is scarce (principle 4). -->
          <ol v-else class="rule-list">
            <li
              v-for="(point, i) in page.talking_points"
              :key="i"
              class="flex items-baseline gap-3.5 py-3"
            >
              <span class="figure text-[0.9375rem] w-5 shrink-0 text-right" :class="point.priority === 'high' ? '' : 'opacity-40'">
                {{ i + 1 }}
              </span>
              <div class="min-w-0 flex-1">
                <div class="flex items-baseline gap-2 flex-wrap">
                  <component
                    :is="categoryIcon(point.category)"
                    :size="13"
                    class="shrink-0 self-center"
                    :class="point.category === 'compliance' ? 'text-error' : 'text-base-content/35'"
                    aria-hidden="true"
                  />
                  <p class="text-sm font-semibold leading-snug min-w-0">{{ point.headline }}</p>
                  <!-- Only a policy breach gets a word. `priority: high` covers
                       five of nine items on a typical book, so labelling all of
                       them "cannot wait" made the phrase mean nothing and left
                       a held-against-exclusion-list position looking exactly
                       like an allocation drift. The ordering already carries
                       priority; this marks the one category an advisor is
                       personally exposed on. -->
                  <span
                    v-if="point.category === 'compliance'"
                    class="label-eyebrow text-error/90 shrink-0"
                  >Policy breach</span>
                </div>
                <p class="mt-1 text-sm text-base-content/70 leading-relaxed max-w-[68ch]">{{ point.detail }}</p>
                <p class="provenance mt-1">{{ point.source }}</p>
              </div>
            </li>
          </ol>
        </section>

        <!-- ── What wasn't checked ──────────────────────────────────── -->
        <section v-if="page.gaps.length">
          <h3 class="label-eyebrow text-warning/90 mb-2.5 flex items-center gap-1.5">
            <AlertTriangle :size="12" aria-hidden="true" />
            What Finn could not check
          </h3>
          <ul class="rounded-lg border border-warning/35 bg-warning/5 divide-y divide-warning/15">
            <li v-for="(gap, i) in page.gaps" :key="i" class="px-3 py-2">
              <p class="text-xs leading-relaxed">{{ gap.reason }}</p>
              <p v-if="gap.remedy" class="mt-0.5 text-[11px] text-base-content/50">{{ gap.remedy }}</p>
            </li>
          </ul>
        </section>

        <!-- ── Against their policy ─────────────────────────────────── -->
        <details v-if="drift && drift.lines.length" class="group" open>
          <summary class="cursor-pointer text-xs font-semibold uppercase tracking-wider text-base-content/55 mb-2 flex items-center gap-1.5">
            <ChevronRight :size="12" class="transition-transform group-open:rotate-90" aria-hidden="true" />
            Against their policy
            <span v-if="!drift.authoritative" class="badge badge-xs badge-warning normal-case font-normal">partial</span>
          </summary>

          <div class="overflow-x-auto rounded-lg border border-base-300">
            <table class="table table-xs">
              <thead>
                <tr>
                  <th>Asset class</th>
                  <th class="text-right">Target</th>
                  <th class="text-right">Actual</th>
                  <th class="text-right">Drift</th>
                  <th class="text-right">To target</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="line in drift.lines" :key="line.asset_class">
                  <td>
                    {{ line.asset_class }}
                    <span
                      v-if="line.matched_by === 'alias'"
                      class="ml-1 text-[10px] text-base-content/40"
                      :title="`Matched the export's ${line.matched_labels.join(', ')}`"
                    >
                      ≈{{ line.matched_labels.join(', ') }}
                    </span>
                    <span v-else-if="!line.matched_by" class="ml-1 text-[10px] text-warning">not held</span>
                  </td>
                  <td class="text-right tabular-nums">{{ line.target_pct }}%</td>
                  <td class="text-right tabular-nums">{{ line.actual_pct }}%</td>
                  <td class="text-right tabular-nums" :class="driftText(line.status)">
                    {{ line.drift_pct > 0 ? '+' : '' }}{{ line.drift_pct }}
                  </td>
                  <td class="text-right tabular-nums text-base-content/60">
                    {{ line.status === 'in_band' ? '—' : currency(line.to_target_dollars) }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <p v-if="policy.concentration && policy.concentration.exclusion_note" class="mt-2 text-[11px] text-base-content/45 leading-relaxed">
            {{ policy.concentration.exclusion_note }}
          </p>
        </details>

        <!-- ── Open items ───────────────────────────────────────────── -->
        <details v-if="page.open_items.count" class="group">
          <summary class="cursor-pointer text-xs font-semibold uppercase tracking-wider text-base-content/55 mb-2 flex items-center gap-1.5">
            <ChevronRight :size="12" class="transition-transform group-open:rotate-90" aria-hidden="true" />
            Open items ({{ page.open_items.count }})
            <span v-if="page.open_items.overdue_count" class="badge badge-xs badge-error normal-case font-normal">
              {{ page.open_items.overdue_count }} overdue
            </span>
          </summary>
          <ul class="rounded-lg border border-base-300 divide-y divide-base-300/50">
            <li v-for="(item, i) in page.open_items.items" :key="i" class="px-3 py-2 flex items-start gap-2.5">
              <div class="min-w-0 flex-1">
                <p class="text-xs leading-snug">{{ item.description }}</p>
                <p class="mt-0.5 text-[11px] text-base-content/45">
                  {{ item.assignee || 'unassigned' }}
                  <template v-if="item.days_open != null">
                    <span class="mx-1 text-base-content/25">·</span>open {{ item.days_open }}d
                  </template>
                  <template v-if="item.due_date">
                    <span class="mx-1 text-base-content/25">·</span>due {{ item.due_date }}
                  </template>
                </p>
              </div>
              <span v-if="item.is_overdue" class="badge badge-xs badge-error shrink-0">overdue</span>
              <span v-else-if="item.is_stale" class="badge badge-xs badge-warning shrink-0">stale</span>
            </li>
          </ul>
        </details>

        <!-- ── Last meeting ─────────────────────────────────────────── -->
        <details v-if="page.since_last_meeting.has_prior_meeting" class="group">
          <summary class="cursor-pointer text-xs font-semibold uppercase tracking-wider text-base-content/55 mb-2 flex items-center gap-1.5">
            <ChevronRight :size="12" class="transition-transform group-open:rotate-90" aria-hidden="true" />
            Last meeting
          </summary>
          <div class="rounded-lg border border-base-300 px-3 py-2.5 flex flex-col gap-2.5 text-xs">
            <div v-if="page.since_last_meeting.decisions.length">
              <p class="font-medium text-base-content/70 mb-1">Decided</p>
              <ul class="list-disc pl-4 flex flex-col gap-0.5 text-base-content/70">
                <li v-for="(d, i) in page.since_last_meeting.decisions" :key="i">{{ d }}</li>
              </ul>
            </div>
            <div v-if="page.since_last_meeting.client_concerns.length">
              <p class="font-medium text-base-content/70 mb-1">They raised</p>
              <ul class="list-disc pl-4 flex flex-col gap-0.5 text-base-content/70">
                <li v-for="(c, i) in page.since_last_meeting.client_concerns" :key="i">{{ c }}</li>
              </ul>
            </div>
            <p v-if="page.since_last_meeting.sentiment_notes" class="text-base-content/55 italic">
              {{ page.since_last_meeting.sentiment_notes }}
            </p>
          </div>
        </details>

        <!-- ── Portfolio ────────────────────────────────────────────── -->
        <details v-if="page.portfolio.top_positions.length" class="group">
          <summary class="cursor-pointer text-xs font-semibold uppercase tracking-wider text-base-content/55 mb-2 flex items-center gap-1.5">
            <ChevronRight :size="12" class="transition-transform group-open:rotate-90" aria-hidden="true" />
            Portfolio
          </summary>
          <div class="overflow-x-auto rounded-lg border border-base-300">
            <table class="table table-xs">
              <thead>
                <tr>
                  <th>Position</th>
                  <th class="text-right">Market value</th>
                  <th class="text-right">Unrealized</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(pos, i) in page.portfolio.top_positions" :key="i">
                  <td class="truncate max-w-[16rem]">
                    {{ pos.name || pos.ticker || '—' }}
                    <span v-if="pos.ticker && pos.name" class="text-base-content/40">{{ pos.ticker }}</span>
                  </td>
                  <td class="text-right tabular-nums">{{ currency(pos.market_value) }}</td>
                  <td
                    class="text-right tabular-nums"
                    :class="pos.unrealized_pnl == null ? 'text-base-content/35' : (pos.unrealized_pnl < 0 ? 'text-error' : 'text-success')"
                  >
                    {{ pos.unrealized_pnl == null ? '—' : currency(pos.unrealized_pnl) }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </details>

        <!-- ── Tax ──────────────────────────────────────────────────── -->
        <details v-if="taxLoss && taxLoss.candidates && taxLoss.candidates.length" class="group">
          <summary class="cursor-pointer text-xs font-semibold uppercase tracking-wider text-base-content/55 mb-2 flex items-center gap-1.5">
            <ChevronRight :size="12" class="transition-transform group-open:rotate-90" aria-hidden="true" />
            Harvestable losses ({{ taxLoss.candidates.length }})
          </summary>
          <div class="overflow-x-auto rounded-lg border border-base-300">
            <table class="table table-xs">
              <thead>
                <tr>
                  <th>Position</th>
                  <th class="text-right">Loss</th>
                  <th>Period</th>
                  <th>Wash sale</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(c, i) in taxLoss.candidates" :key="i">
                  <td class="truncate max-w-[14rem]">{{ c.symbol || c.name }}</td>
                  <td class="text-right tabular-nums text-error">{{ currency(c.unrealized_loss) }}</td>
                  <td class="text-base-content/60">{{ (c.holding_period || '').replace('_', ' ') }}</td>
                  <td>
                    <span
                      v-if="c.wash_sale && c.wash_sale.status !== 'clear'"
                      class="badge badge-xs badge-warning"
                    >{{ c.wash_sale.status }}</span>
                    <span v-else class="text-base-content/35">clear</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-if="taxLoss.estimated_savings_note" class="mt-2 text-[11px] text-base-content/45 leading-relaxed">
            {{ taxLoss.estimated_savings_note }}
          </p>
        </details>

        <!-- Provenance -->
        <p class="text-xs text-base-content/40 leading-relaxed">
          Assembled on this machine from this client's files and their saved
          profile. No AI provider was called and nothing left your computer to
          produce it — the same data always produces the same page.
        </p>
      </template>
    </div>
  </section>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import axios from 'axios'
import {
  ClipboardList, AlertTriangle, RefreshCw, ChevronRight, ShieldAlert,
  PieChart, CircleDollarSign, ListChecks, MessageCircleQuestion, Newspaper,
  Wallet, Receipt,
} from 'lucide-vue-next'

const props = defineProps({
  collectionId: { type: String, default: '' },
  variant: { type: String, default: 'inline' },
  when: { type: String, default: '' },
})

const emit = defineEmits(['loaded'])

const loading = ref(false)
const error = ref('')
const page = ref(null)

const policy = computed(() => page.value?.policy || {})
const drift = computed(() => policy.value.allocation_drift || null)
const taxLoss = computed(() => page.value?.opportunities?.tax_loss || null)

const currency = (n) => {
  if (n == null || Number.isNaN(Number(n))) return '—'
  return Number(n).toLocaleString(undefined, {
    style: 'currency', currency: 'USD', maximumFractionDigits: 0,
  })
}

const priorityBorder = (p) => ({
  high: 'border-error/40',
  medium: 'border-base-300',
  low: 'border-base-300/60',
}[p] || 'border-base-300')

const priorityText = (p) => ({
  high: 'text-error',
  medium: 'text-warning',
  low: 'text-base-content/45',
}[p] || 'text-base-content/45')

const priorityBadge = (p) => ({
  high: 'badge-error',
  medium: 'badge-warning',
  low: 'badge-ghost',
}[p] || 'badge-ghost')

const categoryIcon = (c) => ({
  compliance: ShieldAlert,
  concentration: PieChart,
  allocation: PieChart,
  commitments: ListChecks,
  follow_up: MessageCircleQuestion,
  client_concern: MessageCircleQuestion,
  cash: Wallet,
  tax: Receipt,
  market: Newspaper,
}[c] || CircleDollarSign)

const driftText = (status) => ({
  over: 'text-warning',
  under: 'text-warning',
  in_band: 'text-base-content/45',
}[status] || 'text-base-content/60')

const load = async () => {
  if (!props.collectionId) return
  loading.value = true
  error.value = ''
  try {
    const body = {}
    if (props.when) body.when = props.when
    const { data } = await axios.post(`/api/collections/${props.collectionId}/prep`, body)
    page.value = data
    emit('loaded', data)
  } catch (e) {
    error.value = e.response?.data?.detail || e.message || 'Could not build the prep page.'
  } finally {
    loading.value = false
  }
}

watch(() => props.collectionId, () => { page.value = null; load() })
onMounted(load)

defineExpose({ load })
</script>
