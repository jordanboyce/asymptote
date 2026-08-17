<template>
  <div class="flex flex-col gap-5 max-w-7xl mx-auto">

    <!-- The masthead of a client report is the client's name. The word
         "Overview" was saying nothing the active tab wasn't already saying, and
         it pushed the one piece of orienting information — whose money this is —
         down into grey subtext. Principle 6: say it once. -->
    <header class="flex items-end justify-between gap-6 pb-4">
      <div class="min-w-0 flex-1">
        <p class="label-eyebrow">Client overview</p>
        <!-- Wraps rather than truncates. A masthead that ends in "…" tells the
             advisor less than the tab already did, and household names are
             routinely long ("The Alvarez Family Revocable Trust"). -->
        <h1 class="heading-brief mt-1 text-[26px] leading-tight text-balance">
          {{ collectionName || 'No client selected' }}
        </h1>
        <p class="provenance mt-1.5">
          <span v-if="brief?.generated_at">Computed {{ relativeTime(brief.generated_at) }}</span>
          <span v-else-if="loading">Computing…</span>
          <template v-if="brief?.tables_scanned != null">
            <span class="mx-1.5 opacity-40">·</span>
            <span>{{ brief.tables_scanned }} table{{ brief.tables_scanned === 1 ? '' : 's' }} scanned</span>
          </template>
          <span class="mx-1.5 opacity-40">·</span>
          <span>Computed on this machine</span>
        </p>
      </div>
      <div class="flex items-center gap-2 shrink-0">
        <button
          class="btn btn-sm btn-ghost gap-1.5"
          @click="reload"
          :disabled="loading"
          :aria-label="loading ? 'Refreshing' : 'Refresh overview'"
          title="Refresh"
        >
          <RefreshCw :size="13" :class="{ 'animate-spin': loading }" />
          <span class="hidden sm:inline">Refresh</span>
        </button>
        <button
          class="btn btn-sm btn-primary gap-1.5"
          @click="$emit('open-brief')"
          :disabled="!brief"
          aria-label="Open the full Meeting Brief"
        >
          <FileText :size="13" />
          Open Meeting Brief
        </button>
      </div>
    </header>

    <!-- No collection selected -->
    <div v-if="!collectionId" class="text-center py-20 text-sm text-base-content/55">
      Select a collection from the header to see the overview.
    </div>

    <!-- Error -->
    <div v-else-if="error" role="alert" class="alert alert-error">
      <AlertTriangle :size="18" aria-hidden="true" />
      <span>{{ error }}</span>
    </div>

    <!-- Empty state — brief loaded but no portfolio data -->
    <div v-else-if="!loading && brief && !hasAnyData" class="text-center py-20 text-sm text-base-content/55">
      No portfolio data found in this collection. Upload a brokerage export from the Sources sidebar to get started.
    </div>

    <!-- Loading skeleton (only on first load — subsequent reloads keep the
         old data visible to avoid layout flicker) -->
    <!-- The skeleton mirrors the masthead's geometry so the page doesn't
         reflow when real figures land. -->
    <div v-else-if="loading && !brief" class="flex flex-col gap-4">
      <div class="rule-t rule-b py-5">
        <div class="grid grid-cols-1 sm:grid-cols-3 gap-x-8 gap-y-6">
          <div
            v-for="i in 3"
            :key="i"
            class="animate-pulse sm:not-first:pl-8 sm:not-first:border-l sm:not-first:border-[var(--rule)]"
          >
            <div class="h-2.5 w-24 rounded-sm bg-[var(--rule-strong)]"></div>
            <div class="mt-2.5 h-7 w-36 rounded-sm bg-[var(--rule-strong)]"></div>
            <div class="mt-2.5 h-2 w-28 rounded-sm bg-[var(--rule)]"></div>
          </div>
        </div>
      </div>
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div class="h-72 animate-pulse rounded-sm bg-[var(--surface-sunk)]"></div>
        <div class="h-72 animate-pulse rounded-sm bg-[var(--surface-sunk)]"></div>
      </div>
    </div>

    <!-- Content -->
    <template v-else-if="brief">

      <!-- Data health sits above the KPIs on purpose: if a column can't be
           totalled, the advisor needs to know that before reading a total. -->
      <IngestReportPanel
        :collection-id="collectionId"
        @send-to-chat="$emit('send-to-chat', $event)"
      />

      <!-- The masthead of the brief. Three figures set like a printed report:
           serif, tabular, separated by rules rather than boxed into tiles, each
           carrying its own provenance line.

           The withheld case is the one that matters. When the P0.6 guard can't
           trust a column, the value arrives null — and this used to render a
           bare "—" in 24px bold with no explanation, which reads as "zero" or
           "broken" rather than "we checked, and we won't tell you a number we
           can't stand behind." That refusal is the product; it gets stated. -->
      <section class="rule-t rule-b py-5">
        <dl class="grid grid-cols-1 sm:grid-cols-3 gap-x-8 gap-y-6">
          <div
            v-for="fig in headlineFigures"
            :key="fig.key"
            class="min-w-0 sm:not-first:pl-8 sm:not-first:border-l sm:not-first:border-[var(--rule)]"
          >
            <dt class="label-eyebrow">{{ fig.label }}</dt>
            <dd class="mt-1.5">
              <div v-if="fig.value !== null" class="figure figure-lg" :class="fig.tone">
                {{ fig.value }}
              </div>
              <div v-else class="figure-withheld figure-lg" aria-label="Not available">—</div>
              <p class="provenance mt-1.5 text-pretty">{{ fig.note }}</p>
            </dd>
          </div>
        </dl>
      </section>

      <!-- Open action items from meetings -->
      <section class="rule-t pt-5">
        <div class="flex items-center justify-between gap-3 flex-wrap">
          <h3 class="overview-card-title">
            <CheckSquare :size="14" class="text-primary" aria-hidden="true" />
            Open action items
            <span class="overview-card-meta">from recent meetings</span>
          </h3>
          <button
            v-if="actionItems.length > 0"
            class="btn btn-xs btn-ghost gap-1.5"
            @click="downloadFollowupInvite"
            title="Download a .ics calendar invite with these items as the agenda"
          >
            <CalendarPlus :size="13" />
            Schedule follow-up
          </button>
        </div>

        <div v-if="actionItemsLoading && actionItems.length === 0" class="text-xs text-base-content/55 py-3">
          Loading action items…
        </div>
        <div v-else-if="actionItemsError" class="text-xs text-error py-3">{{ actionItemsError }}</div>
        <ul v-else-if="actionItems.length > 0" class="mt-2 flex flex-col divide-y divide-base-300/70">
          <li
            v-for="(item, i) in actionItems"
            :key="i"
            class="py-2 flex items-start justify-between gap-3"
          >
            <div class="min-w-0 flex-1">
              <div class="text-sm font-medium">{{ item.description || '(no description)' }}</div>
              <div class="text-[11px] text-base-content/60 mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5">
                <span v-if="item.assignee" class="capitalize">
                  <span class="text-base-content/45">Assignee:</span> {{ item.assignee }}
                </span>
                <span v-if="item.due_date">
                  <span class="text-base-content/45">Due:</span> {{ item.due_date }}
                </span>
                <span v-if="item.source_filename" class="italic truncate max-w-xs">
                  from {{ item.source_filename }}
                </span>
              </div>
            </div>
            <button
              class="btn btn-xs btn-ghost gap-1 flex-shrink-0"
              @click="askAboutActionItem(item)"
              :aria-label="`Discuss '${item.description}' in chat`"
              title="Open chat with a prefilled follow-up prompt"
            >
              <MessageSquare :size="12" />
              Discuss
            </button>
          </li>
        </ul>
        <p v-else class="overview-card-empty">
          No open action items recorded yet. Record a client meeting and Finn will extract action items automatically once transcription completes.
        </p>
      </section>

      <!-- Charts row: sector donut + top positions bar -->
      <section class="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <!-- Sector allocation -->
        <div class="rule-t pt-5">
          <h3 class="overview-card-title">
            <PieChart :size="14" aria-hidden="true" />
            Sector allocation
            <span v-if="brief.sector_allocation?.length" class="overview-card-meta">click a slice to discuss</span>
          </h3>
          <div v-if="brief.sector_allocation?.length" class="mt-2 chart-clickable">
            <apexchart
              type="donut"
              height="280"
              :options="sectorChartOptions"
              :series="sectorChartSeries"
            />
          </div>
          <p v-else class="overview-card-empty">
            Sector classification not available — export doesn't include a sector column.
          </p>
        </div>

        <!-- Top positions -->
        <div class="rule-t pt-5">
          <h3 class="overview-card-title">
            <BarChart3 :size="14" aria-hidden="true" />
            Top positions
            <span v-if="brief.top_positions?.length" class="overview-card-meta">click a bar to discuss</span>
          </h3>
          <div v-if="brief.top_positions?.length" class="mt-2 chart-clickable">
            <apexchart
              type="bar"
              height="280"
              :options="topPositionsOptions"
              :series="topPositionsSeries"
            />
          </div>
          <p v-else class="overview-card-empty">No positions found in this collection's holdings tables.</p>
        </div>
      </section>

      <!-- Alerts row: concentration + cash drag -->
      <section class="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <!-- Concentration -->
        <div class="rule-t pt-5">
          <h3 class="overview-card-title">
            <AlertTriangle :size="14" class="text-warning" aria-hidden="true" />
            Concentration alerts
            <span class="overview-card-meta">≥ 10% of portfolio</span>
          </h3>
          <ul v-if="brief.concentration_alerts?.length" class="mt-2 flex flex-col gap-1.5">
            <li
              v-for="(a, i) in brief.concentration_alerts"
              :key="i"
              class="flex flex-col gap-1 cursor-pointer hover:bg-base-200/60 rounded-md px-1.5 py-1 -mx-1.5 transition-colors"
              tabindex="0"
              role="button"
              :aria-label="`Discuss concentration in ${a.name} in chat`"
              @click="askAboutConcentration(a)"
              @keydown.enter.prevent="askAboutConcentration(a)"
              @keydown.space.prevent="askAboutConcentration(a)"
            >
              <div class="flex items-center justify-between gap-2 text-sm">
                <span class="truncate font-medium">{{ a.name || '—' }}</span>
                <span class="tabular-nums text-warning font-semibold flex-shrink-0">
                  {{ a.pct_of_portfolio?.toFixed(1) }}%
                </span>
              </div>
              <div class="w-full bg-base-200 rounded-full h-1.5 overflow-hidden">
                <div
                  class="h-full bg-warning"
                  :style="{ width: Math.min(100, a.pct_of_portfolio || 0) + '%' }"
                ></div>
              </div>
              <div class="flex items-center justify-between text-[11px] text-base-content/55 tabular-nums">
                <span>{{ fmtMoney(a.market_value) }}</span>
                <span class="flex items-center gap-0.5 opacity-0 group-hover:opacity-100">
                  <MessageSquare :size="10" /> Discuss
                </span>
              </div>
            </li>
          </ul>
          <p v-else class="overview-card-empty">
            No positions exceed 10% of the portfolio.
          </p>
        </div>

        <!-- Cash drag -->
        <div class="rule-t pt-5">
          <h3 class="overview-card-title">
            <Wallet :size="14" aria-hidden="true" />
            Cash drag
            <span class="overview-card-meta">≥ $50k</span>
          </h3>
          <ul v-if="brief.cash_drag_alerts?.length" class="mt-2 flex flex-col gap-1.5">
            <li
              v-for="(c, i) in brief.cash_drag_alerts"
              :key="i"
              class="flex items-center justify-between gap-2 text-sm cursor-pointer hover:bg-base-200/60 rounded-md px-1.5 py-1 -mx-1.5 transition-colors"
              tabindex="0"
              role="button"
              :aria-label="`Discuss cash position ${c.name} in chat`"
              @click="askAboutCashDrag(c)"
              @keydown.enter.prevent="askAboutCashDrag(c)"
              @keydown.space.prevent="askAboutCashDrag(c)"
            >
              <span class="truncate">{{ c.name || '—' }}</span>
              <span class="tabular-nums font-medium flex-shrink-0">{{ fmtMoney(c.market_value) }}</span>
            </li>
          </ul>
          <p v-else class="overview-card-empty">No cash positions above the threshold.</p>
        </div>
      </section>

      <!-- Tax-loss snapshot -->
      <section class="rule-t pt-5">
        <h3 class="overview-card-title">
          <TrendingDown :size="14" class="text-error" aria-hidden="true" />
          Tax-loss candidates
          <span class="overview-card-meta">≥ $500 unrealized loss</span>
        </h3>
        <div v-if="brief.tax_loss_candidates?.length" class="mt-2 flex items-center justify-between gap-3 flex-wrap">
          <div class="flex items-baseline gap-4">
            <div>
              <span class="text-2xl font-semibold tabular-nums">{{ brief.tax_loss_candidates.length }}</span>
              <span class="text-xs text-base-content/55 ml-1">positions</span>
            </div>
            <div>
              <span class="text-2xl font-semibold tabular-nums text-error">
                -{{ fmtMoney(totalUnrealizedLoss) }}
              </span>
              <span class="text-xs text-base-content/55 ml-1">total unrealized</span>
            </div>
          </div>
          <div class="flex items-center gap-1">
            <button
              class="btn btn-sm btn-ghost gap-1.5"
              @click="askAboutTaxLoss"
              aria-label="Discuss tax-loss candidates in chat"
              title="Open chat with a prefilled tax-loss harvesting prompt"
            >
              <MessageSquare :size="13" />
              Discuss
            </button>
            <button
              class="btn btn-sm btn-ghost gap-1.5"
              @click="$emit('open-brief')"
              aria-label="View tax-loss candidates in the Meeting Brief"
            >
              View details
              <ChevronRight :size="13" />
            </button>
          </div>
        </div>
        <p v-else-if="hs.total_cost_basis == null" class="overview-card-empty">
          Tax-loss analysis requires cost-basis data — export doesn't include it.
        </p>
        <p v-else class="overview-card-empty">
          No positions with unrealized losses above the threshold.
        </p>
      </section>

      <!-- Footer privacy posture -->
      <p class="text-xs text-base-content/50 flex items-center gap-1.5 pt-2">
        <ShieldCheck :size="12" class="text-success flex-shrink-0" aria-hidden="true" />
        Computed locally — no portfolio data was sent to an AI provider for this overview.
      </p>

    </template>

    <!-- The redaction boundary, made readable. Data health above answers "did
         my file land right"; this answers "what left the box".

         Deliberately outside the branch chain: a collection holding only
         meeting transcripts has no portfolio data and lands in the empty state
         above, and that is precisely the collection with the most to show here.
         Gating this on `brief` would blank it exactly where it matters.

         It sits last because it's the compliance read rather than the daily
         one — the live surface is the drawer behind the chat PII pill. -->
    <RedactionBoundaryPanel
      v-if="collectionId && !error"
      :collection-id="collectionId"
      @open-settings="$emit('switch-tab', 'settings')"
    />
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import axios from 'axios'
import {
  LayoutDashboard,
  RefreshCw,
  FileText,
  AlertTriangle,
  PieChart,
  BarChart3,
  Wallet,
  TrendingDown,
  ChevronRight,
  ShieldCheck,
  CheckSquare,
  MessageSquare,
  CalendarPlus,
  ExternalLink,
} from 'lucide-vue-next'
import { useCollectionStore } from '../stores/collectionStore'
import { useColorScheme } from '../composables/useThemeIcon'
import IngestReportPanel from './IngestReportPanel.vue'
import RedactionBoundaryPanel from './RedactionBoundaryPanel.vue'

const emit = defineEmits(['open-brief', 'send-to-chat', 'switch-tab'])

const collectionStore = useCollectionStore()
const collectionId = computed(() => collectionStore.currentCollectionId)
const collectionName = computed(() => collectionStore.currentCollection?.name || '')

const brief = ref(null)
const loading = ref(false)
const error = ref('')

const actionItems = ref([])
const actionItemsLoading = ref(false)
const actionItemsError = ref('')

const hs = computed(() => brief.value?.household_summary || {})

const hasAnyData = computed(() => {
  const b = brief.value
  if (!b) return false
  return (
    (b.household_summary?.total_market_value || 0) > 0 ||
    (b.top_positions?.length || 0) > 0 ||
    (b.accounts?.length || 0) > 0
  )
})

const totalUnrealizedLoss = computed(() =>
  (brief.value?.tax_loss_candidates || []).reduce(
    (sum, c) => sum + Math.abs(c.unrealized_loss || 0),
    0,
  ),
)

async function loadBrief() {
  if (!collectionId.value) {
    brief.value = null
    return
  }
  loading.value = true
  error.value = ''
  try {
    const { data } = await axios.post(
      `/api/collections/${collectionId.value}/brief`,
      null,
    )
    brief.value = data
  } catch (err) {
    error.value = err.response?.data?.detail || 'Failed to load overview.'
    brief.value = null
  } finally {
    loading.value = false
  }
}

async function loadActionItems() {
  if (!collectionId.value) {
    actionItems.value = []
    return
  }
  actionItemsLoading.value = true
  actionItemsError.value = ''
  try {
    const { data } = await axios.get(
      `/api/collections/${collectionId.value}/meetings/action-items`,
      { params: { status: 'open', limit: 25 } },
    )
    actionItems.value = data.items || []
  } catch (err) {
    // 422 = no meeting_notes_store yet (no transcripts indexed). Treat as empty.
    if (err.response?.status === 422) {
      actionItems.value = []
    } else {
      actionItemsError.value = err.response?.data?.detail || 'Failed to load action items.'
    }
  } finally {
    actionItemsLoading.value = false
  }
}

const reload = () => {
  loadBrief()
  loadActionItems()
}

watch(collectionId, () => {
  brief.value = null
  actionItems.value = []
  loadBrief()
  loadActionItems()
})

onMounted(() => {
  loadBrief()
  loadActionItems()
})

// ── Charts ──────────────────────────────────────────────────────────────────

const { scheme } = useColorScheme()

const sectorChartSeries = computed(
  () => (brief.value?.sector_allocation || []).map((s) => s.market_value || 0),
)

const sectorChartOptions = computed(() => ({
  chart: {
    type: 'donut',
    toolbar: { show: false },
    fontFamily: 'inherit',
    animations: { enabled: true, speed: 250 },
    events: {
      dataPointSelection: (_event, _ctx, opts) => {
        const sectors = brief.value?.sector_allocation || []
        const s = sectors[opts.dataPointIndex]
        if (!s) return
        const pct = s.pct_of_portfolio != null ? ` (${s.pct_of_portfolio.toFixed(1)}% of the portfolio)` : ''
        emit('send-to-chat',
          `Walk me through the ${s.sector || 'Unclassified'} sector exposure in this portfolio${pct}. ` +
          `Which positions are driving it, and what's the risk profile?`,
        )
      },
    },
  },
  labels: (brief.value?.sector_allocation || []).map((s) => s.sector || 'Unclassified'),
  theme: { mode: scheme.value },
  legend: {
    position: 'bottom',
    fontSize: '11px',
    itemMargin: { vertical: 3, horizontal: 6 },
  },
  dataLabels: { enabled: false },
  stroke: { width: 2 },
  tooltip: { y: { formatter: (val) => fmtMoney(val) } },
  plotOptions: {
    pie: {
      donut: {
        size: '62%',
        labels: {
          show: true,
          name: { fontSize: '11px' },
          value: {
            fontSize: '16px',
            fontWeight: 600,
            formatter: (val) => fmtMoney(parseFloat(val)),
          },
          total: {
            show: true,
            showAlways: true,
            label: 'Total',
            fontSize: '11px',
            formatter: (w) => fmtMoney(
              w.globals.seriesTotals.reduce((a, b) => a + b, 0),
            ),
          },
        },
      },
    },
  },
}))

// Top positions horizontal bar — uses ticker as the y-axis label (falls back
// to name), market_value as the bar length. Sorted descending; brief.top_n
// caps the count.
const topPositionsSeries = computed(() => [{
  name: 'Market value',
  data: (brief.value?.top_positions || []).map((p) => ({
    x: positionLabel(p),
    y: p.market_value || 0,
  })),
}])

const topPositionsOptions = computed(() => ({
  chart: {
    type: 'bar',
    toolbar: { show: false },
    fontFamily: 'inherit',
    animations: { enabled: true, speed: 250 },
    events: {
      dataPointSelection: (_event, _ctx, opts) => {
        const positions = brief.value?.top_positions || []
        const p = positions[opts.dataPointIndex]
        if (!p) return
        const label = positionLabel(p)
        emit('send-to-chat',
          `Tell me more about ${label} in this portfolio: position size, cost basis, ` +
          `recent performance, and any concerns I should raise with the client.`,
        )
      },
    },
  },
  theme: { mode: scheme.value },
  plotOptions: {
    bar: {
      horizontal: true,
      barHeight: '68%',
      borderRadius: 2,
      dataLabels: { position: 'top' },
    },
  },
  dataLabels: {
    enabled: true,
    formatter: (val) => fmtMoneyShort(val),
    offsetX: 28,
    style: { fontSize: '10px', colors: ['var(--ink-55)'] },
  },
  xaxis: {
    labels: { formatter: (val) => fmtMoneyShort(val), style: { fontSize: '10px' } },
  },
  yaxis: { labels: { style: { fontSize: '11px' } } },
  grid: { strokeDashArray: 3, padding: { right: 32 } },
  tooltip: { y: { formatter: (val) => fmtMoney(val) } },
  legend: { show: false },
}))

// ── Formatting ──────────────────────────────────────────────────────────────

const HEADER_LITERALS = new Set([
  'symbol', 'description', 'name', 'ticker', 'cusip', 'isin',
  'security identifier', 'security description', 'security id',
])
function isHeaderLiteral(v) {
  if (v == null) return false
  return HEADER_LITERALS.has(String(v).toLowerCase().trim())
}
function positionLabel(pos) {
  const ticker = pos.ticker
  const name = pos.name
  if (ticker && !isHeaderLiteral(ticker)) return ticker
  if (name && !isHeaderLiteral(name)) return name
  return '—'
}

function fmtMoney(n) {
  if (n == null || isNaN(n)) return '—'
  const abs = Math.abs(n)
  const sign = n < 0 ? '-' : ''
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toLocaleString(undefined, { maximumFractionDigits: 2 })}M`
  return `${sign}$${abs.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
}

/* The headline figures are written out in full: $2,380,000, not $2.38M.
 *
 * Abbreviation is fine on a chart axis where space is scarce and precision is
 * not the point. It is wrong on the number an advisor reads to a client —
 * "$2.38M" silently discards $80,000 of range, and a rounded headline is
 * exactly the imprecision this audience has learned to distrust in software.
 * Tabular figures keep the longer strings aligned down the row. */
function fmtMoneyExact(n) {
  if (n == null || isNaN(n)) return '—'
  const sign = n < 0 ? '-' : ''
  return `${sign}$${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 })}`
}

/* Why the coercion guard withheld a column, in the advisor's language.
 *
 * The backend warning is written for the model — it names SQLite coercion and
 * prescribes a CAST. An advisor does not need the SQL; they need to know the
 * number is unavailable, that it is the file's fault and not theirs, and what
 * to do. The engineering detail stays one click away in the Trust Report. */
function withheldReason(column) {
  const w = (brief.value?.warnings || []).find(
    x => !column || (x.column || '').toLowerCase().includes(column),
  )
  if (!w) return null
  // Column names arrive SQL-sanitised ("Market_Value"). The advisor's file says
  // "Market Value", and quoting an identifier they never typed makes Finn look
  // like it is describing a different file than the one they uploaded.
  const columnLabel = (w.column || 'source').replace(/_/g, ' ')
  const file = w.filename ? ` in ${w.filename}` : ''
  return `Withheld — the ${columnLabel} column${file} holds text in some rows, so a total would be wrong. See the data check above.`
}

/* The three figures at the top of the brief.
 *
 * `value: null` is a deliberate state, not a missing one: the template renders
 * an em dash in muted serif and prints `note` underneath. Every branch has a
 * note, so a figure is never unexplained — a blank provenance line would put
 * us right back where we started. */
const headlineFigures = computed(() => {
  const h = hs.value || {}
  const sourceCount = (h.sources || []).length
  const positions = Array.isArray(brief.value?.top_positions) ? brief.value.top_positions.length : 0

  const mvReliable = h.total_market_value_reliable !== false && h.total_market_value != null
  const cbReliable = h.total_cost_basis_reliable !== false && h.total_cost_basis != null
  const pnlReliable = h.total_unrealized_pnl_reliable !== false && h.total_unrealized_pnl != null

  return [
    {
      key: 'mv',
      label: 'Total market value',
      value: mvReliable ? fmtMoneyExact(h.total_market_value) : null,
      tone: '',
      note: mvReliable
        ? `Summed from ${sourceCount || 1} source${sourceCount === 1 ? '' : 's'}${positions ? ` · ${positions} positions` : ''}`
        : withheldReason('market') || 'Withheld — the market value column could not be totalled reliably.',
    },
    {
      key: 'cb',
      label: 'Total cost basis',
      value: cbReliable ? fmtMoneyExact(h.total_cost_basis) : null,
      tone: '',
      note: cbReliable
        ? 'Summed from the cost basis column'
        : h.total_cost_basis == null && h.total_cost_basis_reliable !== false
          ? 'Not present in this export — most position files omit it.'
          : withheldReason('cost') || 'Withheld — the cost basis column could not be totalled reliably.',
    },
    {
      key: 'pnl',
      label: 'Unrealized gain / loss',
      value: pnlReliable
        ? `${pnlPrefix(h.total_unrealized_pnl)}${fmtMoneyExact(Math.abs(h.total_unrealized_pnl))}`
        : null,
      tone: pnlReliable ? pnlToneClass(h.total_unrealized_pnl) : '',
      note: pnlReliable
        ? 'Market value less cost basis'
        : cbReliable
          ? withheldReason('') || 'Withheld — cannot be derived from the columns in this export.'
          : 'Needs a cost basis column to compute.',
    },
  ]
})

function fmtMoneyShort(n) {
  if (n == null || isNaN(n)) return '—'
  const abs = Math.abs(n)
  const sign = n < 0 ? '-' : ''
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(1)}M`
  if (abs >= 1_000) return `${sign}$${(abs / 1_000).toFixed(0)}k`
  return `${sign}$${abs.toFixed(0)}`
}

function pnlToneClass(value) {
  if (value == null) return ''
  if (value > 0) return 'text-success'
  if (value < 0) return 'text-error'
  return ''
}

function pnlPrefix(value) {
  if (value == null) return ''
  return value > 0 ? '+' : (value < 0 ? '-' : '')
}

function relativeTime(iso) {
  try {
    const d = new Date(iso)
    const seconds = Math.floor((Date.now() - d.getTime()) / 1000)
    if (seconds < 60) return 'just now'
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
    return d.toLocaleDateString()
  } catch {
    return iso
  }
}

// ── Chat handoff helpers ────────────────────────────────────────────────────

function askAboutConcentration(alert) {
  emit('send-to-chat',
    `${alert.name} is ${alert.pct_of_portfolio?.toFixed(1)}% of the portfolio ` +
    `(${fmtMoney(alert.market_value)}). Is this concentration appropriate, and ` +
    `what trimming or hedging options should I consider before the next review?`,
  )
}

function askAboutCashDrag(item) {
  emit('send-to-chat',
    `${item.name} is sitting at ${fmtMoney(item.market_value)} in cash. ` +
    `What's a reasonable deployment plan given the rest of the portfolio?`,
  )
}

function askAboutTaxLoss() {
  emit('send-to-chat',
    `Walk me through the best tax-loss harvesting candidates in this portfolio. ` +
    `For each, give me suggested replacement securities that avoid wash-sale issues.`,
  )
}

function askAboutActionItem(item) {
  const source = item.source_filename ? ` (from ${item.source_filename})` : ''
  const due = item.due_date ? ` Due: ${item.due_date}.` : ''
  emit('send-to-chat',
    `Help me follow up on this open action item${source}: "${item.description}".${due} ` +
    `Draft a concise note I can send to the client and suggest any portfolio data I should reference.`,
  )
}

// ── Calendar invite (.ics) for a client follow-up meeting ──────────────────
// All client-side: builds a minimal VEVENT and triggers a file download.
// The advisor opens the downloaded .ics in Outlook/Apple Calendar/Google
// Calendar and edits attendees / time before sending.

function escapeIcs(s) {
  return String(s ?? '')
    .replace(/\\/g, '\\\\')
    .replace(/;/g, '\\;')
    .replace(/,/g, '\\,')
    .replace(/\n/g, '\\n')
}

function fmtIcsDate(d) {
  return d.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '')
}

function downloadFollowupInvite() {
  const start = new Date()
  start.setDate(start.getDate() + 7)
  start.setHours(10, 0, 0, 0)
  const end = new Date(start.getTime() + 30 * 60 * 1000)

  const client = collectionName.value || 'Client'
  const agendaLines = [
    'Agenda — open items from prior meetings:',
    ...actionItems.value.map((a) => {
      const who = a.assignee ? ` [${a.assignee}]` : ''
      const due = a.due_date ? ` (due ${a.due_date})` : ''
      return `- ${a.description}${who}${due}`
    }),
  ]
  if (actionItems.value.length === 0) {
    agendaLines.push('- (no open action items recorded — set the agenda before sending)')
  }
  agendaLines.push('')
  agendaLines.push('Drafted in Finn — edit attendees and time before sending.')

  const ics = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//Finn//Client Follow-up//EN',
    'CALSCALE:GREGORIAN',
    'METHOD:PUBLISH',
    'BEGIN:VEVENT',
    `UID:${Date.now()}-${Math.random().toString(36).slice(2, 10)}@finn`,
    `DTSTAMP:${fmtIcsDate(new Date())}`,
    `DTSTART:${fmtIcsDate(start)}`,
    `DTEND:${fmtIcsDate(end)}`,
    `SUMMARY:${escapeIcs(`Follow-up: ${client}`)}`,
    `DESCRIPTION:${escapeIcs(agendaLines.join('\n'))}`,
    'END:VEVENT',
    'END:VCALENDAR',
  ].join('\r\n')

  const blob = new Blob([ics], { type: 'text/calendar;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `followup-${(client || 'client').toLowerCase().replace(/[^a-z0-9]+/g, '-')}.ics`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  setTimeout(() => URL.revokeObjectURL(url), 5000)
}
</script>

<style scoped>
/* Section headings in the brief voice: serif, quiet, sitting on a hairline
 * rather than floating inside a boxed card header. Restyled centrally so
 * every section on this tab picks it up without touching each template. */
.overview-card-title {
  font-family: var(--font-serif);
  font-size: 1rem;
  font-weight: 600;
  letter-spacing: -0.005em;
  display: flex;
  align-items: baseline;
  gap: 0.45rem;
}
/* The icon each heading carries wants to sit on the text baseline, not the
 * flex baseline, or it rides high next to a serif cap-height. */
.overview-card-title :deep(svg) { align-self: center; }

.overview-card-meta {
  font-family: var(--font-sans);
  font-size: var(--text-micro);
  font-weight: 500;
  color: var(--ink-55);
  letter-spacing: 0.02em;
  margin-left: 0.15rem;
}

/* Empty states are statements, not apologies — no italics, no shrug. */
.overview-card-empty {
  font-size: var(--text-small);
  color: var(--ink-55);
  padding: 0.75rem 0;
  max-width: 62ch;
}
.chart-clickable :deep(.apexcharts-pie-area),
.chart-clickable :deep(.apexcharts-bar-area),
.chart-clickable :deep(.apexcharts-series path) {
  cursor: pointer;
}
</style>
