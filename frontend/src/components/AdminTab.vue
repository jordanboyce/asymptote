<template>
  <div class="max-w-4xl mx-auto space-y-8">
    <header class="pb-4 border-b border-base-300/60">
      <h1 class="text-[22px] leading-none font-semibold tracking-tight">Admin</h1>
      <p class="mt-2 text-xs text-base-content/50">
        Who is using this deployment, what it's spending, and who can reach it.
      </p>
    </header>

    <!-- ═══ Live system card ═══ -->
    <section aria-labelledby="admin-system">
      <div class="flex items-center justify-between mb-3">
        <h2 id="admin-system" class="text-sm font-semibold uppercase tracking-wider text-base-content/60">System</h2>
        <button class="btn btn-ghost btn-xs gap-1" @click="loadAll" :disabled="loading">
          <RefreshCw :size="12" :class="{ 'animate-spin': loading }" aria-hidden="true" />
          Refresh
        </button>
      </div>

      <div v-if="statsError" class="alert alert-error py-2">
        <span class="text-sm">{{ statsError }}</span>
        <button class="btn btn-xs btn-ghost" @click="loadAll">Retry</button>
      </div>

      <div v-else class="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div class="rounded-lg border border-base-300/60 bg-base-100 p-4">
          <div class="text-[10px] uppercase tracking-wider text-base-content/45">In flight</div>
          <div class="mt-1 text-xl font-semibold tabular-nums">{{ sys.in_flight ?? '—' }}</div>
          <div class="text-[11px] text-base-content/45">requests right now</div>
        </div>
        <div class="rounded-lg border border-base-300/60 bg-base-100 p-4">
          <div class="text-[10px] uppercase tracking-wider text-base-content/45">Requests</div>
          <div class="mt-1 text-xl font-semibold tabular-nums">{{ formatNum(sys.requests_total) }}</div>
          <div class="text-[11px] text-base-content/45">
            since start · {{ formatNum(sys.errors_total) }} errors
          </div>
        </div>
        <div class="rounded-lg border border-base-300/60 bg-base-100 p-4">
          <div class="text-[10px] uppercase tracking-wider text-base-content/45">Rate limited</div>
          <div class="mt-1 text-xl font-semibold tabular-nums">{{ rateLimitRejected }}</div>
          <div class="text-[11px] text-base-content/45">
            {{ sys.rate_limit?.enabled ? 'requests rejected' : 'limiter disabled' }}
          </div>
        </div>
        <div class="rounded-lg border border-base-300/60 bg-base-100 p-4">
          <div class="text-[10px] uppercase tracking-wider text-base-content/45">Index jobs</div>
          <div class="mt-1 text-xl font-semibold tabular-nums">
            {{ sys.index_jobs_active ?? 0 }}<span class="text-sm text-base-content/40">/{{ sys.max_concurrent_index_jobs ?? '∞' }}</span>
          </div>
          <div class="text-[11px] text-base-content/45">uptime {{ formatUptime(sys.uptime_seconds) }}</div>
        </div>
      </div>

      <div v-if="sys.usage_today" class="mt-3 rounded-lg border border-base-300/60 bg-base-100 p-4 flex flex-wrap items-baseline gap-x-6 gap-y-1">
        <span class="text-[10px] uppercase tracking-wider text-base-content/45">Today</span>
        <span class="text-sm tabular-nums"><strong>{{ formatNum(sys.usage_today.turns) }}</strong> chat turns</span>
        <span class="text-sm tabular-nums"><strong>{{ formatNum(totalTokens(sys.usage_today)) }}</strong> tokens</span>
        <span class="text-sm tabular-nums"><strong>{{ formatNum(sys.usage_today.cache_hits) }}</strong> cache hits</span>
        <span v-if="sys.daily_token_budget" class="text-sm text-base-content/55 tabular-nums">
          budget {{ formatNum(sys.daily_token_budget) }}/user/day
        </span>
        <span v-else class="text-sm text-base-content/45">no daily budget set</span>
      </div>
    </section>

    <!-- ═══ Usage by person ═══ -->
    <section aria-labelledby="admin-usage">
      <div class="flex items-center justify-between mb-3">
        <h2 id="admin-usage" class="text-sm font-semibold uppercase tracking-wider text-base-content/60">Usage — last {{ usageDays }} days</h2>
        <div class="join" role="group" aria-label="Usage window">
          <button
            v-for="d in [7, 30, 90]"
            :key="d"
            class="btn btn-xs join-item"
            :class="usageDays === d ? 'btn-active' : 'btn-ghost'"
            @click="usageDays = d; loadUsage()"
            :aria-pressed="usageDays === d"
          >{{ d }}d</button>
        </div>
      </div>

      <div v-if="usageError" class="alert alert-error py-2">
        <span class="text-sm">{{ usageError }}</span>
        <button class="btn btn-xs btn-ghost" @click="loadUsage">Retry</button>
      </div>

      <div v-else-if="byUser.length === 0" class="text-center py-10 text-sm text-base-content/45 border border-dashed border-base-300 rounded-lg">
        No chat activity recorded in this window yet.
      </div>

      <div v-else class="overflow-x-auto rounded-lg border border-base-300/60">
        <table class="table table-sm">
          <thead>
            <tr class="text-[10px] uppercase tracking-wider text-base-content/45">
              <th>Person</th>
              <th class="text-right">Turns</th>
              <th class="text-right">Tokens in</th>
              <th class="text-right">Tokens out</th>
              <th class="text-right">Cache hits</th>
              <th class="text-right">Last active</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in byUser" :key="row.grouped_by">
              <td class="font-medium truncate max-w-[22ch]">{{ row.grouped_by }}</td>
              <td class="text-right tabular-nums">{{ formatNum(row.turns) }}</td>
              <td class="text-right tabular-nums">{{ formatNum(row.input_tokens) }}</td>
              <td class="text-right tabular-nums">{{ formatNum(row.output_tokens) }}</td>
              <td class="text-right tabular-nums">
                {{ formatNum(row.cache_hits) }}
                <span class="text-base-content/40">({{ cacheHitRate(row) }})</span>
              </td>
              <td class="text-right text-base-content/55 whitespace-nowrap">{{ formatDay(row.last_active) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Daily series as compact bars: enough to spot a spike, no chart lib -->
      <div v-if="byDay.length > 1" class="mt-3 rounded-lg border border-base-300/60 bg-base-100 p-4">
        <div class="text-[10px] uppercase tracking-wider text-base-content/45 mb-2">Tokens per day</div>
        <div class="flex items-end gap-[3px] h-16" role="img" :aria-label="`Daily token usage over ${byDay.length} days`">
          <div
            v-for="day in byDay"
            :key="day.grouped_by"
            class="flex-1 bg-primary/70 rounded-t-sm min-w-[3px]"
            :style="{ height: dayBarHeight(day) }"
            :title="`${day.grouped_by}: ${formatNum(totalTokens(day))} tokens, ${formatNum(day.turns)} turns`"
          ></div>
        </div>
      </div>
    </section>

    <!-- ═══ Edge access (relocated from Settings) ═══ -->
    <section v-if="userStore.canInviteNewPeople" aria-labelledby="admin-access">
      <h2 id="admin-access" class="text-sm font-semibold uppercase tracking-wider text-base-content/60 mb-3">Access</h2>
      <AccessAdmin />
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { RefreshCw } from 'lucide-vue-next'
import http from '../utils/http'
import AccessAdmin from './AccessAdmin.vue'
import { useUserStore } from '../stores/userStore'

const userStore = useUserStore()

const loading = ref(false)
const sys = ref({})
const statsError = ref('')
const usageDays = ref(30)
const byUser = ref([])
const byDay = ref([])
const usageError = ref('')

const rateLimitRejected = computed(() => {
  const classes = sys.value.rate_limit?.classes || {}
  return formatNum(Object.values(classes).reduce((n, c) => n + (c.rejected || 0), 0))
})

const totalTokens = (row) => (row?.input_tokens || 0) + (row?.output_tokens || 0)

const maxDayTokens = computed(() =>
  Math.max(1, ...byDay.value.map((d) => totalTokens(d)))
)
const dayBarHeight = (day) =>
  `${Math.max(4, Math.round((totalTokens(day) / maxDayTokens.value) * 100))}%`

const cacheHitRate = (row) =>
  row.turns ? `${Math.round(((row.cache_hits || 0) / row.turns) * 100)}%` : '0%'

const formatNum = (n) => (n ?? 0).toLocaleString()
const formatDay = (iso) => (iso ? iso.slice(0, 10) : '—')
const formatUptime = (s) => {
  if (s == null) return '—'
  if (s < 3600) return `${Math.floor(s / 60)}m`
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
  return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`
}

const loadStats = async () => {
  statsError.value = ''
  try {
    const resp = await http.get('/api/admin/stats')
    sys.value = resp.data
  } catch (err) {
    statsError.value = err.message || 'Could not load system stats'
  }
}

const loadUsage = async () => {
  usageError.value = ''
  try {
    const resp = await http.get(`/api/admin/usage?days=${usageDays.value}`)
    byUser.value = resp.data.by_user || []
    byDay.value = resp.data.by_day || []
  } catch (err) {
    usageError.value = err.message || 'Could not load usage data'
  }
}

const loadAll = async () => {
  loading.value = true
  try {
    await Promise.all([loadStats(), loadUsage()])
  } finally {
    loading.value = false
  }
}

onMounted(loadAll)
</script>
