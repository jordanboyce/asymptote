<template>
  <dialog ref="modalEl" class="modal" aria-labelledby="brief-title">
    <div class="modal-box max-w-5xl max-h-[92vh] flex flex-col gap-3 overflow-hidden brief-print-root">

      <!-- Header -->
      <header class="flex items-start justify-between gap-3 brief-header">
        <div class="flex-1 min-w-0">
          <h2 id="brief-title" class="text-xl font-bold flex items-center gap-2">
            <FileText :size="20" class="text-primary brief-no-print" aria-hidden="true" />
            <span>Meeting Brief<span v-if="collectionName" class="text-base-content/60 font-normal"> — {{ collectionName }}</span></span>
          </h2>
          <p class="text-xs text-base-content/55 mt-0.5 brief-no-print">
            Pre-meeting portfolio summary, computed locally with no LLM call.
          </p>
        </div>
        <div class="flex items-center gap-1 brief-no-print">
          <button class="btn btn-ghost btn-sm gap-1" @click="printBrief" :disabled="loading || !brief">
            <Printer :size="14" aria-hidden="true" /> Print
          </button>
          <button class="btn btn-ghost btn-sm btn-circle" @click="close" aria-label="Close brief">
            <X :size="16" aria-hidden="true" />
          </button>
        </div>
      </header>

      <!-- Threshold strip -->
      <div class="flex flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2 bg-base-200/60 rounded-lg text-xs brief-no-print">
        <label class="flex items-center gap-1.5">
          <span class="text-base-content/65">Tax-loss min ($)</span>
          <input
            v-model.number="thresholds.tax_loss_min"
            type="number" min="0" step="50"
            class="input input-bordered input-xs w-24 tabular-nums"
            @input="scheduleRegenerate"
          />
        </label>
        <label class="flex items-center gap-1.5">
          <span class="text-base-content/65">Concentration ≥ (%)</span>
          <input
            v-model.number="thresholds.concentration_pct"
            type="number" min="0" max="100" step="0.5"
            class="input input-bordered input-xs w-20 tabular-nums"
            @input="scheduleRegenerate"
          />
        </label>
        <label class="flex items-center gap-1.5">
          <span class="text-base-content/65">Cash drag min ($)</span>
          <input
            v-model.number="thresholds.cash_drag_min"
            type="number" min="0" step="1000"
            class="input input-bordered input-xs w-28 tabular-nums"
            @input="scheduleRegenerate"
          />
        </label>
        <label class="flex items-center gap-1.5">
          <span class="text-base-content/65">Top N</span>
          <input
            v-model.number="thresholds.top_n"
            type="number" min="1" max="50" step="1"
            class="input input-bordered input-xs w-16 tabular-nums"
            @input="scheduleRegenerate"
          />
        </label>
        <span v-if="loading && brief" class="ml-auto text-xs text-base-content/55 flex items-center gap-1">
          <span class="loading loading-spinner loading-xs"></span> Updating…
        </span>
      </div>

      <!-- Body -->
      <div class="flex-1 overflow-y-auto pr-1 brief-body">

        <div v-if="error" class="alert alert-error brief-no-print">
          <AlertTriangle :size="18" aria-hidden="true" />
          <span>{{ error }}</span>
        </div>

        <div v-else-if="!brief && loading" class="flex flex-col items-center justify-center gap-3 py-20 brief-no-print">
          <span class="loading loading-spinner loading-lg text-primary"></span>
          <p class="text-sm text-base-content/60">Computing brief…</p>
        </div>

        <div v-else-if="brief" class="flex flex-col gap-6 print:gap-4">

          <!-- Household summary -->
          <section>
            <h3 class="brief-section-title">Household summary</h3>
            <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <div class="rounded-lg border border-base-300 bg-base-100 px-3 py-2">
                <div class="text-[11px] uppercase tracking-wider text-base-content/55">Total market value</div>
                <div class="text-lg font-semibold tabular-nums">{{ fmtMoney(hs.total_market_value) }}</div>
              </div>
              <div class="rounded-lg border border-base-300 bg-base-100 px-3 py-2">
                <div class="text-[11px] uppercase tracking-wider text-base-content/55">Total cost basis</div>
                <div v-if="hs.total_cost_basis != null" class="text-lg font-semibold tabular-nums">
                  {{ fmtMoney(hs.total_cost_basis) }}
                </div>
                <div v-else class="text-sm text-base-content/45 italic mt-1">
                  Not available — exports don't include cost basis.
                </div>
              </div>
              <div class="rounded-lg border border-base-300 bg-base-100 px-3 py-2">
                <div class="text-[11px] uppercase tracking-wider text-base-content/55">Unrealized P&amp;L</div>
                <div
                  v-if="hs.total_unrealized_pnl != null"
                  class="text-lg font-semibold tabular-nums"
                  :class="pnlToneClass(hs.total_unrealized_pnl)"
                >
                  {{ pnlPrefix(hs.total_unrealized_pnl) }}{{ fmtMoney(Math.abs(hs.total_unrealized_pnl)) }}
                </div>
                <div v-else class="text-sm text-base-content/45 italic mt-1">
                  Not available — needs cost basis or P&amp;L column.
                </div>
              </div>
            </div>
          </section>

          <!-- Accounts -->
          <section>
            <h3 class="brief-section-title">Accounts</h3>
            <div v-if="brief.accounts && brief.accounts.length > 0" class="overflow-x-auto">
              <table class="table table-sm">
                <thead>
                  <tr>
                    <th>Account</th>
                    <th class="text-right">Market value</th>
                    <th class="text-right">% of portfolio</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(acc, i) in brief.accounts" :key="i">
                    <td class="truncate max-w-md">{{ acc.account || '—' }}</td>
                    <td class="text-right tabular-nums">{{ fmtMoney(acc.market_value) }}</td>
                    <td class="text-right tabular-nums text-base-content/60">{{ pctOfTotal(acc.market_value) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p v-else class="brief-empty">
              No account breakdown — exports don't tag positions by account.
            </p>
          </section>

          <!-- Top positions -->
          <section>
            <h3 class="brief-section-title">Top positions <span class="brief-section-meta">top {{ thresholds.top_n }}</span></h3>
            <div v-if="brief.top_positions && brief.top_positions.length > 0" class="overflow-x-auto">
              <table class="table table-sm">
                <thead>
                  <tr>
                    <th>Position</th>
                    <th class="text-right">Market value</th>
                    <th class="text-right">% of portfolio</th>
                    <th class="text-right">Unrealized P&amp;L</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(pos, i) in brief.top_positions" :key="i">
                    <td>
                      <div class="font-medium truncate max-w-sm">{{ positionLabel(pos) }}</div>
                      <div v-if="pos.ticker && pos.name && pos.name !== pos.ticker && !isHeaderLiteral(pos.name)" class="text-xs text-base-content/55 truncate max-w-sm">
                        {{ pos.name }}
                      </div>
                      <div v-if="pos.sector" class="text-[11px] text-base-content/50">{{ pos.sector }}</div>
                    </td>
                    <td class="text-right tabular-nums">{{ fmtMoney(pos.market_value) }}</td>
                    <td class="text-right tabular-nums text-base-content/60">{{ pctOfTotal(pos.market_value) }}</td>
                    <td class="text-right tabular-nums" :class="pnlToneClass(pos.unrealized_pnl)">
                      <span v-if="pos.unrealized_pnl != null">
                        {{ pnlPrefix(pos.unrealized_pnl) }}{{ fmtMoney(Math.abs(pos.unrealized_pnl)) }}
                      </span>
                      <span v-else class="text-base-content/30">—</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p v-else class="brief-empty">No positions found in this collection's holdings tables.</p>
          </section>

          <!-- Tax-loss candidates -->
          <section>
            <h3 class="brief-section-title">
              Tax-loss candidates
              <span class="brief-section-meta">≥ {{ fmtMoney(thresholds.tax_loss_min) }} loss</span>
            </h3>
            <div v-if="brief.tax_loss_candidates && brief.tax_loss_candidates.length > 0" class="overflow-x-auto">
              <table class="table table-sm">
                <thead>
                  <tr>
                    <th>Position</th>
                    <th class="text-right">Market value</th>
                    <th class="text-right">Cost basis</th>
                    <th class="text-right">Unrealized loss</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(p, i) in brief.tax_loss_candidates" :key="i">
                    <td class="truncate max-w-sm">{{ positionLabel(p) }}</td>
                    <td class="text-right tabular-nums">{{ fmtMoney(p.market_value) }}</td>
                    <td class="text-right tabular-nums">{{ fmtMoney(p.cost_basis) }}</td>
                    <td class="text-right tabular-nums text-error">-{{ fmtMoney(Math.abs(p.unrealized_loss || 0)) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p v-else-if="hs.total_cost_basis == null" class="brief-empty">
              Tax-loss analysis requires cost-basis data — your export doesn't include it.
            </p>
            <p v-else class="brief-empty">
              No positions with unrealized losses ≥ {{ fmtMoney(thresholds.tax_loss_min) }}.
            </p>
          </section>

          <!-- Concentration alerts -->
          <section>
            <h3 class="brief-section-title">
              Concentration alerts
              <span class="brief-section-meta">≥ {{ thresholds.concentration_pct }}% of portfolio</span>
            </h3>
            <div v-if="brief.concentration_alerts && brief.concentration_alerts.length > 0" class="overflow-x-auto">
              <table class="table table-sm">
                <thead>
                  <tr>
                    <th>Position</th>
                    <th class="text-right">Market value</th>
                    <th class="text-right">% of portfolio</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(a, i) in brief.concentration_alerts" :key="i">
                    <td class="truncate max-w-sm">{{ positionLabel(a) }}</td>
                    <td class="text-right tabular-nums">{{ fmtMoney(a.market_value) }}</td>
                    <td class="text-right tabular-nums text-warning">{{ a.pct_of_portfolio?.toFixed(1) }}%</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p v-else class="brief-empty">
              No positions exceed {{ thresholds.concentration_pct }}% of the portfolio.
            </p>
          </section>

          <!-- Cash drag alerts -->
          <section>
            <h3 class="brief-section-title">
              Cash drag
              <span class="brief-section-meta">≥ {{ fmtMoney(thresholds.cash_drag_min) }}</span>
            </h3>
            <div v-if="brief.cash_drag_alerts && brief.cash_drag_alerts.length > 0" class="overflow-x-auto">
              <table class="table table-sm">
                <thead>
                  <tr>
                    <th>Position</th>
                    <th class="text-right">Market value</th>
                    <th class="text-right">% of portfolio</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(c, i) in brief.cash_drag_alerts" :key="i">
                    <td class="truncate max-w-md">{{ positionLabel(c) }}</td>
                    <td class="text-right tabular-nums">{{ fmtMoney(c.market_value) }}</td>
                    <td class="text-right tabular-nums text-base-content/60">{{ pctOfTotal(c.market_value) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p v-else class="brief-empty">
              No cash positions ≥ {{ fmtMoney(thresholds.cash_drag_min) }}.
            </p>
          </section>

          <!-- Sector allocation -->
          <section>
            <h3 class="brief-section-title">Sector allocation</h3>
            <div v-if="brief.sector_allocation && brief.sector_allocation.length > 0" class="overflow-x-auto">
              <table class="table table-sm">
                <thead>
                  <tr>
                    <th>Sector</th>
                    <th class="text-right">Market value</th>
                    <th class="text-right">% of portfolio</th>
                    <th class="text-right">Positions</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(s, i) in brief.sector_allocation" :key="i">
                    <td>{{ s.sector || '—' }}</td>
                    <td class="text-right tabular-nums">{{ fmtMoney(s.market_value) }}</td>
                    <td class="text-right tabular-nums text-base-content/60">
                      {{ s.pct_of_portfolio != null ? s.pct_of_portfolio.toFixed(1) + '%' : pctOfTotal(s.market_value) }}
                    </td>
                    <td class="text-right tabular-nums text-base-content/60">{{ s.position_count }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p v-else class="brief-empty">
              Sector classification not available — exports don't include a sector column.
            </p>
          </section>

          <!-- Footer: sources + generated_at -->
          <footer class="border-t border-base-300 pt-3 mt-2 text-xs text-base-content/60 flex flex-col gap-1">
            <div v-if="sourceFilenames.length > 0">
              <span class="font-semibold text-base-content/70">Sources:</span>
              <span>{{ sourceFilenames.join(', ') }}</span>
            </div>
            <div v-if="brief.tables_scanned != null">
              <span class="font-semibold text-base-content/70">Tables scanned:</span>
              {{ brief.tables_scanned }}
            </div>
            <div v-if="brief.generated_at">
              <span class="font-semibold text-base-content/70">Generated:</span>
              {{ formatTimestamp(brief.generated_at) }}
            </div>
          </footer>
        </div>

        <div v-else class="text-center py-16 text-sm text-base-content/55 brief-no-print">
          No portfolio data found in this collection.
        </div>
      </div>
    </div>

    <form method="dialog" class="modal-backdrop brief-no-print" @submit.prevent="close">
      <button>close</button>
    </form>
  </dialog>
</template>

<script setup>
import { ref, reactive, computed, onBeforeUnmount } from 'vue'
import { FileText, Printer, X, AlertTriangle } from 'lucide-vue-next'
import axios from 'axios'

const props = defineProps({
  collectionId: { type: String, default: '' },
  collectionName: { type: String, default: '' },
})

const modalEl = ref(null)
const loading = ref(false)
const error = ref('')
const brief = ref(null)

const thresholds = reactive({
  tax_loss_min: 500,
  concentration_pct: 10.0,
  cash_drag_min: 50000,
  top_n: 10,
})

const hs = computed(() => brief.value?.household_summary || {})

const sourceFilenames = computed(() => {
  const sources = brief.value?.household_summary?.sources || []
  const seen = new Set()
  for (const s of sources) {
    if (s.filename) seen.add(s.filename)
  }
  return Array.from(seen)
})

async function open() {
  brief.value = null
  error.value = ''
  modalEl.value?.showModal()
  await loadBrief()
}

function close() {
  modalEl.value?.close()
  cancelPendingRegenerate()
}

async function loadBrief() {
  if (!props.collectionId) {
    error.value = 'No collection selected.'
    return
  }
  loading.value = true
  error.value = ''
  try {
    const params = {
      tax_loss_min: thresholds.tax_loss_min,
      concentration_pct: thresholds.concentration_pct,
      cash_drag_min: thresholds.cash_drag_min,
      top_n: thresholds.top_n,
    }
    const { data } = await axios.post(
      `/api/collections/${props.collectionId}/brief`,
      null,
      { params },
    )
    brief.value = data
  } catch (err) {
    error.value = err.response?.data?.detail || 'Failed to generate brief.'
  } finally {
    loading.value = false
  }
}

let regenerateTimer = null
function scheduleRegenerate() {
  cancelPendingRegenerate()
  regenerateTimer = setTimeout(() => {
    loadBrief()
  }, 500)
}
function cancelPendingRegenerate() {
  if (regenerateTimer) {
    clearTimeout(regenerateTimer)
    regenerateTimer = null
  }
}
onBeforeUnmount(cancelPendingRegenerate)

// Browsers render <dialog open> in the top layer, which Chromium especially
// refuses to print reliably. Sidestep it: clone the brief content into a
// body-level <div>, hide everything else via @media print, restore after.
function printBrief() {
  const root = modalEl.value?.querySelector('.brief-print-root')
  if (!root) return
  const clone = root.cloneNode(true)
  clone.id = 'brief-print-region'
  clone.classList.remove('modal-box')
  document.body.appendChild(clone)
  document.body.classList.add('brief-printing')
  const cleanup = () => {
    document.body.classList.remove('brief-printing')
    if (clone.parentNode) clone.parentNode.removeChild(clone)
    window.removeEventListener('afterprint', cleanup)
  }
  window.addEventListener('afterprint', cleanup)
  setTimeout(() => window.print(), 50)
}

// Defensive label for a position row. When a brokerage profile doesn't match
// (or ingestion picks a quirky column for the `name`/`ticker` role), the
// `name` column can contain literal column-header text like "Symbol" or
// "Description" for every row. Detect those and fall back to a clearly-broken
// label so the advisor can spot the column-mapping issue at a glance.
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
  if (ticker || name) return '(unlabeled — check column mapping)'
  return '—'
}

function fmtMoney(n) {
  if (n == null || isNaN(n)) return '—'
  const abs = Math.abs(n)
  const sign = n < 0 ? '-' : ''
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toLocaleString(undefined, { maximumFractionDigits: 2 })}M`
  return `${sign}$${abs.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
}

function pctOfTotal(value) {
  const total = hs.value?.total_market_value
  if (!total || !value) return '—'
  return ((value / total) * 100).toFixed(1) + '%'
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

function formatTimestamp(iso) {
  try {
    const d = new Date(iso)
    return d.toLocaleString()
  } catch {
    return iso
  }
}

defineExpose({ open, close })
</script>

<style scoped>
.brief-section-title {
  font-size: 0.95rem;
  font-weight: 600;
  margin-bottom: 0.5rem;
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
}
.brief-section-meta {
  font-size: 0.7rem;
  font-weight: 500;
  color: hsl(var(--bc) / 0.5);
  letter-spacing: 0.02em;
}
.brief-empty {
  font-size: 0.8rem;
  font-style: italic;
  color: hsl(var(--bc) / 0.55);
  padding: 0.5rem 0;
}
</style>

<style>
/* Print stylesheet — drops everything except the cloned brief content.
   The original dialog stays in the DOM but is hidden; we print a clone
   that lives directly under <body> to dodge the <dialog> top-layer
   rendering quirk that left the printed page blank in Chromium. */
@media print {
  /* Print page geometry — letter w/ ~0.4in margin gives a usable width
     well under the modal's max-w-5xl (1024px), which would otherwise cause
     horizontal overflow and force scrollbars in the print preview. */
  @page {
    size: letter;
    margin: 0.4in;
  }
  html, body {
    width: auto !important;
    background: white !important;
    color: black !important;
  }
  body.brief-printing > *:not(#brief-print-region) {
    display: none !important;
  }
  #brief-print-region {
    display: block !important;
    position: static !important;
    width: 100% !important;
    max-width: 100% !important;
    max-height: none !important;
    height: auto !important;
    overflow: visible !important;
    padding: 0 !important;
    margin: 0 !important;
    background: white !important;
    color: black !important;
    box-shadow: none !important;
    border: none !important;
    font-size: 10pt;
    line-height: 1.35;
  }
  /* Hide chrome */
  #brief-print-region .brief-no-print {
    display: none !important;
  }
  #brief-print-region .brief-body {
    overflow: visible !important;
    padding-right: 0 !important;
  }
  /* Defeat layout primitives that would push content beyond page width */
  #brief-print-region * {
    max-width: 100% !important;
    box-sizing: border-box;
  }
  #brief-print-region .truncate,
  #brief-print-region [class*="truncate"] {
    overflow: visible !important;
    text-overflow: clip !important;
    white-space: normal !important;
    word-break: break-word;
  }
  #brief-print-region .overflow-x-auto,
  #brief-print-region .overflow-y-auto {
    overflow: visible !important;
  }
  /* Tables: full width, fixed layout so long names wrap instead of widening */
  #brief-print-region table {
    page-break-inside: auto;
    width: 100% !important;
    table-layout: fixed;
    border-collapse: collapse;
  }
  #brief-print-region tr {
    page-break-inside: avoid;
  }
  #brief-print-region section {
    page-break-inside: avoid;
    margin-bottom: 0.9rem;
  }
  #brief-print-region th,
  #brief-print-region td {
    border-bottom: 1px solid #d1d5db;
    padding: 0.2rem 0.4rem;
    word-break: break-word;
    overflow-wrap: anywhere;
  }
  /* Right-align numeric columns regardless of source class */
  #brief-print-region th.text-right,
  #brief-print-region td.text-right {
    text-align: right;
  }
  /* Header KPI grid — drop borders to a clean print look and wrap to 3 columns */
  #brief-print-region .grid {
    display: grid !important;
  }
}
</style>
